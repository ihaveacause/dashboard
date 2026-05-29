"""
generate_video.py — New YouTube Pipeline
==========================================
Combines all assets into a final YouTube video.

KEY ARCHITECTURE — TEXT BURNED INTO FRAMES:
  Instead of overlaying a separate text video (which causes seek sync issues),
  Pillow composites text directly onto each episode image frame BEFORE FFmpeg.
  FFmpeg receives pre-composited frames — one single video stream.
  Seeking works perfectly in all players.

Frame layout (1920x1080):
  ┌─────────────────────────────────┐ ← y=0
  │                                 │
  │   Blurred bg + sharp image      │ ← LOWER_TOP = 850px
  │   (all aspect ratios handled)   │
  ├─────────────────────────────────┤ ← y=850
  │   Dark bar + script text        │ ← BAR_HEIGHT = 230px
  └─────────────────────────────────┘ ← y=1080

FFmpeg only handles: narrator photo, logo, intro/outro, audio mix.

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER, LANGUAGE (ta or en)
"""

import os
import json
import subprocess
import tempfile
import requests
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

GCS_BUCKET     = "ihaveacause-media"

# ── Video settings ────────────────────────────────────────────
WIDTH          = 1920
HEIGHT         = 1080
FPS            = 24
BAR_HEIGHT     = 230               # dark text bar at bottom
LOWER_TOP      = HEIGHT - BAR_HEIGHT   # 850px — image area height
LINE_HEIGHT    = 65
FONT_SIZE      = 42
WORDS_PER_LINE = 11
MAX_LINES      = 3
MUSIC_VOL      = 0.05
INTRO_DUR      = 2.0
OUTRO_DUR      = 3.0
FADE_DUR       = 0.5
PHOTO_SIZE     = 140
PHOTO_MARGIN   = 20
LOGO_SIZE      = 120
LOGO_MARGIN    = 20

# ── Supabase helpers ──────────────────────────────────────────
SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(table, params):
    r = requests.get(f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15)
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data):
    r = requests.patch(f"{REST}/{table}?episode_number=eq.{val}",
        headers=SB_HEADERS, json=data, timeout=30)
    return r.status_code in (200, 204)

def download_file(url, dest_path, desc="file"):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    r = requests.get(url, headers=headers, stream=True, timeout=120)
    if r.status_code == 200:
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest_path)//1024}KB", flush=True)
        return True
    print(f"   ❌ {desc} failed {r.status_code}: {url[:80]}", flush=True)
    return False

def storage_url(bucket, path):
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"

# ── GCS upload ────────────────────────────────────────────────
def upload_to_gcs(local_path, gcs_path, content_type="video/mp4"):
    try:
        import base64, datetime as dt
        from google.oauth2 import service_account
        import google.auth.transport.requests as google_requests
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        creds_info  = json.loads(GCP_CREDS_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(google_requests.Request())

        print(f"   📤 Uploading {os.path.getsize(local_path)//(1024*1024)}MB to GCS...", flush=True)
        with open(local_path, "rb") as f:
            r = requests.post(
                f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
                params={"uploadType": "media", "name": gcs_path},
                headers={"Authorization": f"Bearer {credentials.token}", "Content-Type": content_type},
                data=f, timeout=600)
        if r.status_code not in (200, 201):
            print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}", flush=True)
            return None
        print(f"   ✅ GCS upload complete", flush=True)

        expiry_ts      = int((dt.datetime.utcnow() + dt.timedelta(days=30)).timestamp())
        string_to_sign = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
        private_key    = serialization.load_pem_private_key(
            creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
        signature   = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")
        signed_url  = (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
                       f"?GoogleAccessId={creds_info['client_email']}"
                       f"&Expires={expiry_ts}&Signature={encoded_sig}")
        print(f"   ✅ Signed URL generated (30 days)", flush=True)
        return signed_url
    except Exception as e:
        print(f"   ❌ GCS error: {e}", flush=True)
        return None

# ── WhisperX ──────────────────────────────────────────────────
def run_whisperx(audio_path, language, tmpdir):
    print(f"\n🎙️  Running WhisperX ({language})...", flush=True)
    result = subprocess.run([
        "whisperx", audio_path,
        "--model", "medium",
        "--language", language,
        "--output_dir", tmpdir,
        "--output_format", "json",
        "--compute_type", "float32",
        # No --align_model: let WhisperX pick default — avoids download failures
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"   ⚠️  WhisperX exit {result.returncode}: {result.stderr[:400]}", flush=True)

    json_path = os.path.join(tmpdir, f"{Path(audio_path).stem}.json")
    if not os.path.exists(json_path):
        matches = list(Path(tmpdir).glob("*.json"))
        json_path = str(matches[0]) if matches else None

    if not json_path:
        print(f"   ⚠️  No WhisperX JSON — falling back to duration sync", flush=True)
        return None

    with open(json_path) as f:
        data = json.load(f)

    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            if "word" in w and "start" in w and "end" in w:
                words.append({"word": w["word"].strip(), "start": w["start"], "end": w["end"]})

    if not words:
        print(f"   ⚠️  No word timestamps — falling back to duration sync", flush=True)
        return None

    print(f"   ✅ WhisperX: {len(words)} words aligned", flush=True)
    return words

def get_audio_duration(audio_path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True)
    return float(r.stdout.strip())

def find_trigger_timestamp(words, trigger_text):
    import re as _re
    if not trigger_text or not words:
        return None
    def norm(t): return _re.sub(r"[^\w\s]", "", t.lower()).split()
    tw = norm(trigger_text)
    if not tw: return None
    wl = [norm(w["word"])[0] if norm(w["word"]) else "" for w in words]
    n  = len(tw)
    for i in range(len(wl) - n + 1):
        if wl[i:i+n] == tw:
            print(f"   🎯 Trigger '{trigger_text[:40]}' → {words[i]['start']:.2f}s", flush=True)
            return words[i]["start"]
    if n >= 3:
        for i in range(len(wl) - 3 + 1):
            if wl[i:i+3] == tw[:3]:
                print(f"   🎯 Trigger (fuzzy) '{trigger_text[:40]}' → {words[i]['start']:.2f}s", flush=True)
                return words[i]["start"]
    print(f"   ⚠️  Trigger not found: '{trigger_text[:60]}'", flush=True)
    return None

def build_image_timeline(episode_images, words, audio_duration):
    if not episode_images: return []
    n, timeline = len(episode_images), []
    for i, img in enumerate(episode_images):
        trigger = img.get("trigger", "").strip()
        if i == 0:
            start = 0.0
        else:
            ts = find_trigger_timestamp(words, trigger) if (trigger and words) else None
            if ts is not None:
                start = ts
            else:
                start = round((audio_duration / n) * i, 3)
                msg = "no trigger" if not trigger else "trigger not found"
                print(f"   Info: Image {i+1} {msg} — fallback {start:.2f}s", flush=True)
        timeline.append({"url": img.get("url",""), "local_path": img.get("local_path",""),
                         "start": start, "end": audio_duration, "trigger": trigger, "order": img.get("order", i+1)})
    for i in range(len(timeline)-1):
        timeline[i]["end"] = timeline[i+1]["start"]
    print(f"\n   Image timeline:", flush=True)
    for t in timeline:
        print(f"      Image {t['order']}: {t['start']:.1f}s to {t['end']:.1f}s ({t['end']-t['start']:.1f}s)", flush=True)
    return timeline

def build_karaoke_screens(words, script_text, audio_duration):
    """
    Build karaoke screens. Text always from script.
    WhisperX word-by-word matching for timing — screen advances when its
    first word is found in WhisperX output. Falls back to duration sync.
    """
    import re as _re
    script_words = script_text.split()
    if not script_words:
        print("   ⚠️  No script text", flush=True)
        return []

    script_lines = []
    for i in range(0, len(script_words), WORDS_PER_LINE):
        script_lines.append(" ".join(script_words[i:i+WORDS_PER_LINE]))
    script_screens = []
    for i in range(0, len(script_lines), MAX_LINES):
        block = script_lines[i:i+MAX_LINES]
        while len(block) < MAX_LINES: block.append("")
        script_screens.append(block)
    total_screens = len(script_screens)

    if words and len(words) > 0:
        print(f"   ℹ️  Script: {len(script_words)} words | WhisperX: {len(words)} — word matching", flush=True)

        def norm(w): return _re.sub(r"[^\w]", "", w.lower())
        wx_norm = [norm(w["word"]) for w in words]

        def find_ts(script_word, search_from=0):
            target = norm(script_word)
            if not target: return None
            for i in range(search_from, len(wx_norm)):
                if wx_norm[i] == target:
                    return words[i]["start"], i
            if len(target) >= 4:
                for i in range(search_from, len(wx_norm)):
                    if len(wx_norm[i]) >= 4 and (target.startswith(wx_norm[i][:4]) or wx_norm[i].startswith(target[:4])):
                        return words[i]["start"], i
            return None

        screens, wx_cursor = [], 0
        for idx, block in enumerate(script_screens):
            first_word = next((w for line in block for w in line.split() if w.strip()), None)
            start_ts = None
            if first_word:
                result = find_ts(first_word, wx_cursor)
                if result:
                    start_ts, wx_cursor = result

            if start_ts is None:
                usable  = audio_duration - INTRO_DUR - OUTRO_DUR
                start_ts = INTRO_DUR + (idx / total_screens) * usable
                if idx > 0:
                    print(f"   ⚠️  Screen {idx+1} word not found — interpolated {start_ts:.1f}s", flush=True)

            screens.append({"start": start_ts, "end": audio_duration, "lines": block})

        for i in range(len(screens)-1):
            screens[i]["end"] = screens[i+1]["start"]

        # Fix any inversions from interpolation
        for i in range(1, len(screens)):
            if screens[i]["start"] <= screens[i-1]["start"]:
                screens[i]["start"] = screens[i-1]["start"] + (1.0/FPS)
            screens[i-1]["end"] = screens[i]["start"]

        print(f"   ✅ {len(screens)} screens — word-matched timing", flush=True)

    else:
        print(f"   ℹ️  Duration-based sync (no WhisperX)", flush=True)
        usable = audio_duration - INTRO_DUR - OUTRO_DUR
        tps    = usable / max(total_screens, 1)
        screens = []
        for idx, block in enumerate(script_screens):
            start = INTRO_DUR + idx * tps
            screens.append({"start": start, "end": start + tps, "lines": block})
        print(f"   ✅ {len(screens)} screens — duration sync", flush=True)

    return screens

# ── Pillow: pre-process episode image ────────────────────────
def preprocess_base_image(src_path):
    """
    Returns a 1920×1080 PIL Image in memory:
    - Blurred version fills top LOWER_TOP px
    - Sharp original centered in top area
    - Solid dark bar at bottom BAR_HEIGHT px
    Works for any aspect ratio — no black bars.
    """
    img = Image.open(src_path).convert("RGB")
    iw, ih = img.size

    canvas = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))

    # Blurred background (top area)
    bg = img.copy().resize((WIDTH, LOWER_TOP), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=25))
    canvas.paste(bg, (0, 0))

    # Sharp image centered in top area
    scale = min(WIDTH / iw, LOWER_TOP / ih)
    new_w, new_h = int(iw * scale), int(ih * scale)
    fg = img.resize((new_w, new_h), Image.LANCZOS)
    canvas.paste(fg, ((WIDTH - new_w)//2, (LOWER_TOP - new_h)//2))

    # Dark bar
    ImageDraw.Draw(canvas).rectangle([(0, LOWER_TOP), (WIDTH, HEIGHT)], fill=(15, 15, 15))

    return canvas   # PIL Image — keep in memory

# ── Pillow: save one frame with text ─────────────────────────
def save_frame(base_img, text_lines, font, output_path):
    """
    Copy the base image, draw text lines on the dark bar, save as JPEG.
    Text lines are always from the script — never from WhisperX transcription.
    """
    frame = base_img.copy()
    draw  = ImageDraw.Draw(frame)

    for li, line in enumerate(text_lines):
        if not line.strip():
            continue
        y_pos = LOWER_TOP + 20 + li * LINE_HEIGHT
        try:
            bbox  = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * (FONT_SIZE // 2)
        x_pos = max(20, (WIDTH - text_w) // 2)
        draw.text((x_pos+2, y_pos+2), line, font=font, fill=(0,   0,   0  ))  # shadow
        draw.text((x_pos,   y_pos  ), line, font=font, fill=(255, 255, 255))  # text

    frame.save(output_path, "JPEG", quality=92)

# ── Build complete frame sequence ────────────────────────────
def build_frame_sequence(image_timeline, screens, font_path, tmpdir):
    """
    Creates one JPEG per karaoke screen (and gaps) with:
    - Episode image composited (blurred bg + sharp center)
    - Dark text bar
    - Script text burned in at the correct position

    Returns path to FFmpeg concat file covering full audio_duration.
    Single stream — no separate text overlay video — seeking works perfectly.
    """
    print(f"\n🖼️  Building frame sequence ({len(screens)} screens)...", flush=True)

    try:
        font = ImageFont.truetype(font_path, FONT_SIZE)
    except Exception as e:
        print(f"   ⚠️  Font load failed ({e}) — using default", flush=True)
        font = ImageFont.load_default()

    # Pre-process each unique episode image once (in memory)
    base_images = {}
    for item in sorted(image_timeline, key=lambda x: x["order"]):
        path = item["local_path"]
        if path not in base_images:
            print(f"   Preprocessing image {item['order']}...", flush=True)
            base_images[path] = preprocess_base_image(path)

    audio_duration = image_timeline[-1]["end"]

    def active_base(t):
        """Get base PIL Image for timestamp t."""
        for item in image_timeline:
            if item["start"] <= t < item["end"]:
                return base_images[item["local_path"]]
        return base_images[image_timeline[-1]["local_path"]]

    frames    = []
    frame_idx = 0
    cursor    = 0.0
    blank     = ["", "", ""]

    for screen in screens:
        s_start = screen["start"]
        s_end   = screen["end"]

        # Gap before this screen (no text)
        if s_start > cursor + 0.01:
            path = os.path.join(tmpdir, f"frame_{frame_idx:05d}.jpg")
            save_frame(active_base(cursor), blank, font, path)
            frames.append((path, round(s_start - cursor, 4)))
            frame_idx += 1
            cursor = s_start

        # Screen frame with text
        path = os.path.join(tmpdir, f"frame_{frame_idx:05d}.jpg")
        save_frame(active_base(s_start), screen["lines"], font, path)
        dur = max(round(s_end - s_start, 4), 1.0 / FPS)
        frames.append((path, dur))
        frame_idx += 1
        cursor = s_end

    # Tail after last screen (no text)
    if cursor < audio_duration - 0.01:
        path = os.path.join(tmpdir, f"frame_{frame_idx:05d}.jpg")
        save_frame(active_base(cursor), blank, font, path)
        frames.append((path, round(audio_duration - cursor, 4)))

    # Write FFmpeg concat file
    concat_path = os.path.join(tmpdir, "frames_concat.txt")
    with open(concat_path, "w") as f:
        for fp, dur in frames:
            f.write(f"file '{fp}'\nduration {dur:.4f}\n")
        if frames:
            f.write(f"file '{frames[-1][0]}'\n")  # required by concat demuxer

    total = sum(d for _, d in frames)
    print(f"   ✅ {len(frames)} frames built ({total:.1f}s total)", flush=True)
    return concat_path

# ── Circle mask for narrator photo ───────────────────────────
def make_circle_photo(input_path, output_path, size):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", (f"scale={size}:{size}:force_original_aspect_ratio=increase,"
                f"crop={size}:{size},format=yuva420p,"
                f"geq=lum='p(X,Y)':a='if(gt(pow(X-{size//2},2)+pow(Y-{size//2},2),pow({size//2},2)),0,255)'"),
        "-frames:v", "1", output_path,
    ], capture_output=True)
    return os.path.exists(output_path)

# ── FFmpeg render ─────────────────────────────────────────────
def render_video(
    frames_concat_path, audio_path, music_path,
    intro_path, outro_path,
    photo_path, logo_path,
    audio_duration, output_path
):
    """
    FFmpeg render using pre-composited frames.
    Text is already burned into frames by Pillow — no text in filtergraph.
    Single video stream — seeking is frame-accurate.
    """
    print(f"\n🎬 Rendering video with FFmpeg (veryfast)...", flush=True)

    inputs   = ["-f", "concat", "-safe", "0", "-i", frames_concat_path]
    next_idx = 1  # 0 = frame concat

    inputs += ["-i", audio_path]; audio_idx = next_idx; next_idx += 1
    inputs += ["-i", music_path]; music_idx = next_idx; next_idx += 1

    photo_idx = None
    if photo_path and os.path.exists(photo_path):
        inputs += ["-i", photo_path]; photo_idx = next_idx; next_idx += 1

    logo_idx = None
    if logo_path and os.path.exists(logo_path):
        inputs += ["-i", logo_path]; logo_idx = next_idx; next_idx += 1

    inputs += ["-loop", "1", "-t", str(INTRO_DUR + FADE_DUR), "-i", intro_path]
    intro_idx = next_idx; next_idx += 1
    inputs += ["-loop", "1", "-t", str(OUTRO_DUR + FADE_DUR), "-i", outro_path]
    outro_idx = next_idx

    # Frames already 1920×1080 from Pillow — just set SAR and FPS
    vf = f"[0:v]setsar=1,fps={FPS}[bg_main];"
    video_out = "[bg_main]"

    if photo_idx is not None:
        px, py = PHOTO_MARGIN, HEIGHT - PHOTO_SIZE - PHOTO_MARGIN
        vf += (f"[{photo_idx}:v]scale={PHOTO_SIZE}:{PHOTO_SIZE},"
               f"format=yuva420p[photo_s];"
               f"[{video_out[1:-1]}][photo_s]overlay={px}:{py}:format=auto[bg_ph];")
        video_out = "[bg_ph]"

    if logo_idx is not None:
        lx, ly = WIDTH - LOGO_SIZE - LOGO_MARGIN, HEIGHT - LOGO_SIZE - LOGO_MARGIN
        prev = video_out[1:-1]
        vf += (f"[{logo_idx}:v]scale={LOGO_SIZE}:{LOGO_SIZE},"
               f"format=yuva420p[logo_s];"
               f"[{prev}][logo_s]overlay={lx}:{ly}:format=auto[bg_lo];")
        video_out = "[bg_lo]"

    vf += (f"[{intro_idx}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
           f"crop={WIDTH}:{HEIGHT},fade=t=out:st={INTRO_DUR-FADE_DUR}:d={FADE_DUR},setsar=1[intro_v];"
           f"[{outro_idx}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
           f"crop={WIDTH}:{HEIGHT},fade=t=in:st=0:d={FADE_DUR},setsar=1[outro_v];"
           f"[intro_v]{video_out[1:-1]}[outro_v]concat=n=3:v=1:a=0[video_out];")

    total_dur = INTRO_DUR + audio_duration + OUTRO_DUR
    vf += (f"[{audio_idx}:a]adelay={int(INTRO_DUR*1000)}|{int(INTRO_DUR*1000)}[vd];"
           f"[{music_idx}:a]aloop=loop=-1:size=2e+09,volume={MUSIC_VOL},"
           f"atrim=duration={total_dur}[ml];"
           f"[vd][ml]amix=inputs=2:duration=first[audio_out];")

    cmd = (["ffmpeg", "-y"] + inputs + [
        "-filter_complex", vf,
        "-map", "[video_out]", "-map", "[audio_out]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-pix_fmt", "yuv420p",
        output_path,
    ])

    print("   Running FFmpeg...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ❌ FFmpeg failed:\n{result.stderr[-3000:]}", flush=True)
        return False

    print(f"   ✅ Video: {os.path.getsize(output_path)/1024/1024:.1f}MB", flush=True)
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    def log(msg): print(msg, flush=True)

    log("=" * 60)
    log(f"🎬 Video Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    log(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    table   = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    ep_rows = db_get(table, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    episode = ep_rows[0] if ep_rows else None
    if not episode:
        log(f"❌ Episode {EPISODE_NUMBER} not found in {table}"); return

    log(f"   ✅ {episode.get('title_english') or episode.get('title_tamil')}")
    db_patch(table, EPISODE_NUMBER, {"status": "generating_video"})

    with tempfile.TemporaryDirectory() as tmpdir:
        lang_code = "ta" if LANGUAGE == "ta" else "en"

        # Font
        if LANGUAGE == "ta":
            font_path = "/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        log(f"\n🔤 Font: {font_path} ({'found' if os.path.exists(font_path) else '⚠️  NOT FOUND'})")

        # Step 1 — Voice
        voice_url = episode.get("voice_url")
        if not voice_url:
            log("❌ No voice recording"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        log(f"\n🎤 Step 1/8 — Downloading voice...")
        voice_raw = os.path.join(tmpdir, "voice_raw.mp3")
        if not download_file(voice_url, voice_raw, "Voice"):
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        voice_path     = voice_raw
        audio_duration = get_audio_duration(voice_path)
        log(f"   ✅ Duration: {audio_duration:.1f}s ({audio_duration/60:.1f} mins)")

        script_col  = "script_tamil" if LANGUAGE == "ta" else "script_english"
        script_text = episode.get(script_col, "") or ""
        log(f"   ✅ Script: {len(script_text.split())} words")

        # Step 2 — WhisperX
        log(f"\n🎙️  Step 2/8 — WhisperX alignment...")
        words = run_whisperx(voice_path, "ta" if LANGUAGE == "ta" else "en", tmpdir)
        log(f"   ✅ WhisperX done — {datetime.now().strftime('%H:%M:%S')}")

        # Step 3 — Karaoke screens
        log(f"\n📝 Step 3/8 — Building karaoke screens...")
        screens = build_karaoke_screens(words, script_text, audio_duration)

        # Step 4 — Episode images
        raw_ep_images = episode.get("episode_images") or []
        if isinstance(raw_ep_images, str):
            try: raw_ep_images = json.loads(raw_ep_images)
            except: raw_ep_images = []
        if not raw_ep_images:
            log("❌ No episode images"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        log(f"\n📸 Step 4/8 — Downloading {len(raw_ep_images)} images...")
        image_paths = []
        for img in sorted(raw_ep_images, key=lambda x: x.get("order", 0)):
            dest = os.path.join(tmpdir, f"raw_{img.get('order',len(image_paths)+1)}.jpg")
            if download_file(img["url"], dest, f"Image {img.get('order','')}"):
                image_paths.append({**img, "local_path": dest})
        if not image_paths:
            log("❌ No images downloaded"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        image_timeline = build_image_timeline(image_paths, words or [], audio_duration)

        # Step 5 — Build frame sequence (Pillow compositing)
        log(f"\n🖼️  Step 5/8 — Building composited frames (Pillow)...")
        frames_concat = build_frame_sequence(image_timeline, screens, font_path, tmpdir)
        log(f"   ✅ Frame sequence ready — {datetime.now().strftime('%H:%M:%S')}")

        # Step 6 — Intro / outro / narrator / logo
        log(f"\n🖼️  Step 6/8 — Downloading channel assets...")
        intro_path = os.path.join(tmpdir, "intro.png")
        outro_path = os.path.join(tmpdir, "outro.png")
        download_file(episode.get("intro_image_url") or storage_url("channel-assets", "default_intro.png"), intro_path, "Intro")
        download_file(episode.get("outro_image_url") or storage_url("channel-assets", "default_outro.png"), outro_path, "Outro")

        photo_file   = "photo_tamil.jpg" if LANGUAGE == "ta" else "photo_english.jpg"
        photo_raw    = os.path.join(tmpdir, "narrator.jpg")
        photo_circle = os.path.join(tmpdir, "narrator_circle.png")
        photo_final  = None
        if download_file(storage_url("channel-assets", photo_file), photo_raw, f"Narrator ({photo_file})"):
            make_circle_photo(photo_raw, photo_circle, PHOTO_SIZE)
            photo_final = photo_circle if os.path.exists(photo_circle) else photo_raw

        logo_path  = os.path.join(tmpdir, "logo.png")
        logo_final = None
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests as google_requests
            ci   = json.loads(GCP_CREDS_JSON)
            lc   = service_account.Credentials.from_service_account_info(
                ci, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            lc.refresh(google_requests.Request())
            r = requests.get(
                f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/ihaveacause_logo.png?alt=media",
                headers={"Authorization": f"Bearer {lc.token}"}, timeout=15)
            if r.status_code == 200:
                with open(logo_path, "wb") as f: f.write(r.content)
                logo_final = logo_path
                log(f"   ✅ Logo: {len(r.content)//1024}KB")
            else:
                log(f"   ⚠️  Logo failed {r.status_code}")
        except Exception as e:
            log(f"   ⚠️  Logo error: {e}")

        # Step 7 — Music
        log(f"\n🎵 Step 7/8 — Downloading music...")
        music_path = os.path.join(tmpdir, "music.mp3")
        if not download_file(storage_url("episode-music", "background.mp3"), music_path, "Music"):
            log("   ⚠️  No music — using silence")
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                            "-t", "1", music_path], capture_output=True)

        # Step 8 — Render
        log(f"\n🎬 Step 8/8 — FFmpeg render...")
        log(f"   Started: {datetime.now().strftime('%H:%M:%S')}")
        output_path = os.path.join(tmpdir, f"ep{EPISODE_NUMBER:03d}_{lang_code}.mp4")

        success = render_video(
            frames_concat_path = frames_concat,
            audio_path         = voice_path,
            music_path         = music_path,
            intro_path         = intro_path,
            outro_path         = outro_path,
            photo_path         = photo_final,
            logo_path          = logo_final,
            audio_duration     = audio_duration,
            output_path        = output_path,
        )

        if not success:
            log("❌ Render failed"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        log(f"   ✅ FFmpeg done — {datetime.now().strftime('%H:%M:%S')}")

        # Upload to GCS
        log(f"\n☁️  Uploading to GCS...")
        gcs_path  = f"episodes/ep{EPISODE_NUMBER:03d}/{lang_code}/final.mp4"
        video_url = upload_to_gcs(output_path, gcs_path)
        if not video_url:
            log("❌ Upload failed"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        db_patch(table, EPISODE_NUMBER, {"video_url": video_url, "status": "video_ready"})
        log(f"\n{'='*60}")
        log(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — video ready!")
        log(f"   Finished: {datetime.now().strftime('%H:%M:%S')}")
        log(f"{'='*60}")

if __name__ == "__main__":
    main()
