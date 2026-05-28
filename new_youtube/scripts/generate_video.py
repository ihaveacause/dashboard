"""
generate_video.py — New YouTube Pipeline
==========================================
Combines all assets into a final YouTube video:
  - Voice recording (wife for Tamil, you for English)
  - WhisperX word-level alignment for karaoke sync
  - 5 Imagen 3 script images (cycling behind voice)
    Each image: blurred version fills frame, sharp original centered on top
    — works for all aspect ratios, no black bars ever
  - Intro image (2s fade-in) — episode-specific or default
  - Outro image (3s fade-out) — episode-specific or default
  - Narrator circle photo — bottom-left corner
  - Channel logo — bottom-right corner
  - Background music at 5% volume
  - Karaoke text: rolling 3 lines, solid dark bar at bottom

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER
  LANGUAGE — ta or en
"""

import os
import json
import subprocess
import tempfile
import shutil
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
BAR_HEIGHT     = 230               # solid dark bar: 3 lines × 65px + 35px padding
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
LOGO_SIZE      = 120               # matches narrator photo size visually
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
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{val}",
        headers=SB_HEADERS, json=data, timeout=30
    )
    return r.status_code in (200, 204)

def download_file(url, dest_path, desc="file"):
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    r = requests.get(url, headers=headers, stream=True, timeout=120)
    if r.status_code == 200:
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        size_kb = os.path.getsize(dest_path) // 1024
        print(f"   ✅ {desc}: {size_kb}KB", flush=True)
        return True
    print(f"   ❌ {desc} download failed {r.status_code}: {url[:80]}", flush=True)
    return False

def storage_url(bucket, path):
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"

# ── GCS upload ────────────────────────────────────────────────
def upload_to_gcs(local_path, gcs_path, content_type="video/mp4"):
    try:
        import datetime
        from google.oauth2 import service_account
        import google.auth.transport.requests as google_requests

        creds_info  = json.loads(GCP_CREDS_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google_requests.Request())

        upload_url = f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o"
        print(f"   📤 Uploading {os.path.getsize(local_path) // (1024*1024)}MB to GCS...", flush=True)
        with open(local_path, "rb") as f:
            r = requests.post(
                upload_url,
                params={"uploadType": "media", "name": gcs_path},
                headers={"Authorization": f"Bearer {credentials.token}", "Content-Type": content_type},
                data=f, timeout=600
            )
        if r.status_code not in (200, 201):
            print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}", flush=True)
            return None
        print(f"   ✅ GCS upload complete", flush=True)

        # Sign URL
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        expiry_ts = int((datetime.datetime.utcnow() + datetime.timedelta(days=30)).timestamp())
        string_to_sign = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
        private_key = serialization.load_pem_private_key(
            creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend()
        )
        signature   = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")
        signed_url  = (
            f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
            f"?GoogleAccessId={creds_info['client_email']}"
            f"&Expires={expiry_ts}&Signature={encoded_sig}"
        )
        print(f"   ✅ Signed URL generated (valid 30 days)", flush=True)
        return signed_url

    except Exception as e:
        print(f"   ❌ GCS upload error: {e}", flush=True)
        return None

# ── Pillow: preprocess image → blurred bg + sharp center ─────
def preprocess_image(src_path, dst_path):
    """
    Create a 1920×LOWER_TOP (850px) JPEG:
    - Blurred version of the image fills the entire area (no black bars)
    - Sharp original scaled to fit, centered on top
    Works for portrait, landscape and square images.
    """
    img = Image.open(src_path).convert("RGB")
    iw, ih = img.size

    # Blurred background — scale to fill entire area
    bg = img.copy().resize((WIDTH, LOWER_TOP), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=25))

    # Sharp foreground — scale to fit within area, preserve aspect ratio
    scale = min(WIDTH / iw, LOWER_TOP / ih)
    new_w = int(iw * scale)
    new_h = int(ih * scale)
    fg    = img.resize((new_w, new_h), Image.LANCZOS)

    # Center on blurred background
    x = (WIDTH - new_w) // 2
    y = (LOWER_TOP - new_h) // 2
    bg.paste(fg, (x, y))
    bg.save(dst_path, "JPEG", quality=95)

# ── WhisperX alignment ────────────────────────────────────────
def run_whisperx(audio_path, language, tmpdir):
    print(f"\n🎙️  Running WhisperX alignment ({language})...", flush=True)
    result = subprocess.run(
        [
            "whisperx", audio_path,
            "--model",        "medium",
            "--language",     language,
            "--output_dir",   tmpdir,
            "--output_format","json",
            "--compute_type", "float32",
        ],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"   ⚠️  WhisperX exit code {result.returncode}", flush=True)
        print(f"   ⚠️  stderr: {result.stderr[:800]}", flush=True)
    else:
        if result.stdout.strip():
            print(f"   ℹ️  WhisperX stdout: {result.stdout[:300]}", flush=True)

    audio_stem = Path(audio_path).stem
    json_path  = os.path.join(tmpdir, f"{audio_stem}.json")
    if not os.path.exists(json_path):
        json_files = list(Path(tmpdir).glob("*.json"))
        if json_files:
            json_path = str(json_files[0])
        else:
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
        print(f"   ⚠️  No word-level timestamps — falling back to duration sync", flush=True)
        return None

    print(f"   ✅ WhisperX: {len(words)} words aligned", flush=True)
    return words

def get_audio_duration(audio_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())

def find_trigger_timestamp(words, trigger_text):
    import re as _re
    if not trigger_text or not words:
        return None
    def normalize(text):
        return _re.sub(r"[^\w\s]", "", text.lower()).split()
    trigger_words = normalize(trigger_text)
    if not trigger_words:
        return None
    n = len(trigger_words)
    word_list = [normalize(w["word"])[0] if normalize(w["word"]) else "" for w in words]
    for i in range(len(word_list) - n + 1):
        if word_list[i:i+n] == trigger_words:
            ts = words[i]["start"]
            print(f"   🎯 Trigger '{trigger_text[:40]}' → {ts:.2f}s", flush=True)
            return ts
    if n >= 3:
        short = trigger_words[:3]
        for i in range(len(word_list) - 3 + 1):
            if word_list[i:i+3] == short:
                ts = words[i]["start"]
                print(f"   🎯 Trigger (fuzzy) '{trigger_text[:40]}' → {ts:.2f}s", flush=True)
                return ts
    print(f"   ⚠️  Trigger not found: '{trigger_text[:60]}'", flush=True)
    return None

def build_image_timeline(episode_images, words, audio_duration):
    if not episode_images:
        return []
    n = len(episode_images)
    timeline = []
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
                if not trigger:
                    print(f"   Info: Image {i+1} no trigger — equal spacing {start:.2f}s", flush=True)
                else:
                    print(f"   Warning: Image {i+1} trigger not found — fallback {start:.2f}s", flush=True)
        timeline.append({
            "url":        img.get("url", ""),
            "local_path": img.get("local_path", ""),
            "start":      start,
            "end":        audio_duration,
            "trigger":    trigger,
            "order":      img.get("order", i + 1),
        })
    for i in range(len(timeline) - 1):
        timeline[i]["end"] = timeline[i + 1]["start"]
    print(f"\n   Image timeline:", flush=True)
    for t in timeline:
        dur = t["end"] - t["start"]
        print(f"      Image {t['order']}: {t['start']:.1f}s to {t['end']:.1f}s ({dur:.1f}s)", flush=True)
    return timeline

def build_karaoke_screens(words, script_text, audio_duration):
    """
    Always show text from the actual script.
    Timing: word-by-word matching — each script word is located in
    WhisperX output to get its exact timestamp. Screen advances
    when WhisperX speaks the first word of the next block.
    Script text is NEVER replaced by WhisperX transcription.
    Falls back to duration-based timing if WhisperX unavailable.
    """
    import re as _re

    script_words = script_text.split()
    if not script_words:
        print("   ⚠️  No script text — skipping karaoke", flush=True)
        return []

    # Build script lines (WORDS_PER_LINE words each)
    script_lines = []
    for i in range(0, len(script_words), WORDS_PER_LINE):
        script_lines.append(" ".join(script_words[i:i + WORDS_PER_LINE]))

    # Group into screens of MAX_LINES lines each
    script_screens = []
    for i in range(0, len(script_lines), MAX_LINES):
        block = script_lines[i:i + MAX_LINES]
        while len(block) < MAX_LINES:
            block.append("")
        script_screens.append(block)

    total_screens = len(script_screens)

    # ── Word-by-word timestamp matching ──────────────────────
    if words and len(words) > 0:
        print(f"   ℹ️  Script: {len(script_words)} words | WhisperX: {len(words)} words — word matching", flush=True)

        def norm(w):
            """Normalise a word for matching — lowercase, strip punctuation."""
            return _re.sub(r"[^\w]", "", w.lower())

        # Build normalised WhisperX word list with positions
        wx_norm = [norm(w["word"]) for w in words]

        def find_word_timestamp(script_word, search_from=0):
            """
            Find the timestamp of script_word in WhisperX output.
            Searches forward from search_from to avoid going backwards.
            Returns (timestamp, wx_index) or None.
            """
            target = norm(script_word)
            if not target:
                return None

            # Exact match first
            for i in range(search_from, len(wx_norm)):
                if wx_norm[i] == target:
                    return words[i]["start"], i

            # Fuzzy: target starts with or ends with the wx word (handles
            # common transcription truncations e.g. "consciousness" → "consciou")
            for i in range(search_from, len(wx_norm)):
                wx = wx_norm[i]
                if len(wx) >= 4 and len(target) >= 4:
                    if target.startswith(wx[:4]) or wx.startswith(target[:4]):
                        return words[i]["start"], i

            return None

        # For each screen find the timestamp of its FIRST script word
        screens   = []
        wx_cursor = 0   # always search forward — never go backwards

        for idx, block in enumerate(script_screens):
            # First non-empty word in this block
            first_word = None
            for line in block:
                for w in line.split():
                    if w.strip():
                        first_word = w.strip()
                        break
                if first_word:
                    break

            start_ts = None
            if first_word:
                result = find_word_timestamp(first_word, wx_cursor)
                if result:
                    start_ts, wx_cursor = result
                    # Don't advance cursor past here — next screen searches from here+1
                    wx_cursor = max(wx_cursor, result[1])

            if start_ts is None:
                # Interpolate: use ratio as fallback for this screen only
                usable = audio_duration - INTRO_DUR - OUTRO_DUR
                start_ts = INTRO_DUR + (idx / total_screens) * usable
                if idx > 0:
                    print(f"   ⚠️  Screen {idx+1} word not found — interpolated at {start_ts:.1f}s", flush=True)

            screens.append({"start": start_ts, "end": audio_duration, "lines": block})

        # Set end time of each screen = start time of next screen
        for i in range(len(screens) - 1):
            screens[i]["end"] = screens[i + 1]["start"]

        # Verify forward-only (fix any rare inversions from interpolation)
        for i in range(1, len(screens)):
            if screens[i]["start"] <= screens[i-1]["start"]:
                screens[i]["start"] = screens[i-1]["start"] + (1.0 / FPS)
                screens[i-1]["end"] = screens[i]["start"]

        matched = sum(1 for s in screens if s["start"] > 0)
        print(f"   ✅ {len(screens)} karaoke screens — word-matched timing", flush=True)

    else:
        # ── Duration-based fallback ───────────────────────────
        print(f"   ℹ️  No WhisperX timestamps — using duration-based sync", flush=True)
        usable_dur      = audio_duration - INTRO_DUR - OUTRO_DUR
        time_per_screen = usable_dur / max(total_screens, 1)
        screens = []
        for idx, block in enumerate(script_screens):
            start = INTRO_DUR + idx * time_per_screen
            screens.append({"start": start, "end": start + time_per_screen, "lines": block})
        print(f"   ✅ {len(screens)} karaoke screens built from script (duration sync)", flush=True)

    return screens

# ── Pillow: render karaoke screen → PNG (solid dark bar) ─────
def render_screen_png(lines, font_path, output_path):
    img  = Image.new("RGB", (WIDTH, BAR_HEIGHT), (15, 15, 15))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(font_path, FONT_SIZE)
    except Exception as e:
        print(f"   ⚠️  Font load failed ({e}) — using default font", flush=True)
        font = ImageFont.load_default()
    for li, line in enumerate(lines):
        if not line.strip():
            continue
        y = 20 + li * LINE_HEIGHT
        try:
            bbox   = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * (FONT_SIZE // 2)
        x = max(20, (WIDTH - text_w) // 2)
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))        # shadow
        draw.text((x, y), line, font=font, fill=(255, 255, 255))           # text
    img.save(output_path, "PNG")

# ── Build text overlay video (H264, fast) ────────────────────
def build_text_overlay_video(screens, audio_duration, font_path, tmpdir):
    print(f"\n✏️  Rendering {len(screens)} karaoke screen PNGs...", flush=True)
    blank_path = os.path.join(tmpdir, "text_blank.png")
    Image.new("RGB", (WIDTH, BAR_HEIGHT), (15, 15, 15)).save(blank_path, "PNG")

    screen_paths = []
    for idx, screen in enumerate(screens):
        png_path = os.path.join(tmpdir, f"screen_{idx:04d}.png")
        render_screen_png(screen["lines"], font_path, png_path)
        screen_paths.append(png_path)
    print(f"   ✅ {len(screen_paths)} PNGs rendered", flush=True)

    concat_path = os.path.join(tmpdir, "text_concat.txt")
    with open(concat_path, "w") as f:
        cursor = 0.0
        for idx, screen in enumerate(screens):
            s_start = screen["start"]
            s_end   = screen["end"]
            if s_start > cursor + 0.001:
                f.write(f"file '{blank_path}'\nduration {s_start - cursor:.4f}\n")
            dur = max(s_end - s_start, 1.0 / FPS)
            f.write(f"file '{screen_paths[idx]}'\nduration {dur:.4f}\n")
            cursor = s_end
        if cursor < audio_duration - 0.001:
            f.write(f"file '{blank_path}'\nduration {audio_duration - cursor:.4f}\n")
        f.write(f"file '{blank_path}'\n")

    text_video = os.path.join(tmpdir, "text_overlay.mp4")
    print(f"   🎬 Encoding text overlay video (H264)...", flush=True)
    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_path,
        "-vf", f"scale={WIDTH}:{BAR_HEIGHT},setsar=1,fps={FPS}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        text_video,
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"   ❌ Text overlay video failed:", flush=True)
        print(result.stderr[-1000:], flush=True)
        return None

    size_mb = os.path.getsize(text_video) / 1024 / 1024
    print(f"   ✅ Text overlay video ready ({size_mb:.1f}MB)", flush=True)
    return text_video

# ── Circle mask for narrator photo ───────────────────────────
def make_circle_photo(input_path, output_path, size):
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", (
            f"scale={size}:{size}:force_original_aspect_ratio=increase,"
            f"crop={size}:{size},"
            f"format=yuva420p,"
            f"geq=lum='p(X,Y)':a='if(gt(pow(X-{size//2},2)+pow(Y-{size//2},2),pow({size//2},2)),0,255)'"
        ),
        "-frames:v", "1", output_path,
    ]
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(output_path)

# ── Main FFmpeg render ────────────────────────────────────────
def render_video(
    image_timeline, audio_path, music_path,
    intro_path, outro_path,
    photo_path, logo_path,
    text_video_path,
    audio_duration, output_path
):
    print(f"\n🎬 Rendering video with FFmpeg...", flush=True)

    n_images = len(image_timeline)
    inputs   = []
    next_idx = 0

    # Episode images — pre-processed to 1920×LOWER_TOP
    for item in image_timeline:
        dur = item["end"] - item["start"]
        inputs += ["-loop", "1", "-t", str(dur + 0.1), "-i", item["local_path"]]
    next_idx = n_images

    inputs += ["-i", audio_path];     audio_idx = next_idx; next_idx += 1
    inputs += ["-i", music_path];     music_idx = next_idx; next_idx += 1
    inputs += ["-i", text_video_path]; text_idx = next_idx; next_idx += 1

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

    # Scale pre-processed images (1920×850) → pad to full 1920×1080
    scale_filters = ""
    for i in range(n_images):
        dur = image_timeline[i]["end"] - image_timeline[i]["start"]
        scale_filters += (
            f"[{i}:v]scale={WIDTH}:{LOWER_TOP},"
            f"pad={WIDTH}:{HEIGHT}:0:0:black,"
            f"setsar=1,fps={FPS},"
            f"trim=duration={dur},setpts=PTS-STARTPTS[sv{i}];"
        )

    concat_in     = "".join(f"[sv{i}]" for i in range(n_images))
    concat_filter = f"{concat_in}concat=n={n_images}:v=1:a=0[bg_full];"

    # Overlay text bar at bottom
    text_filter = (
        f"[{text_idx}:v]scale={WIDTH}:{BAR_HEIGHT},setsar=1,fps={FPS}[text_scaled];"
        f"[bg_full][text_scaled]overlay=0:{LOWER_TOP}[bg_text];"
    )

    video_out    = "[bg_text]"
    photo_filter = ""
    if photo_idx is not None:
        px = PHOTO_MARGIN
        py = HEIGHT - PHOTO_SIZE - PHOTO_MARGIN
        photo_filter = (
            f"[{photo_idx}:v]scale={PHOTO_SIZE}:{PHOTO_SIZE},"
            f"format=yuva420p[photo_scaled];"
            f"[bg_text][photo_scaled]overlay={px}:{py}:format=auto[bg_photo];"
        )
        video_out = "[bg_photo]"

    logo_filter = ""
    if logo_idx is not None:
        lx   = WIDTH - LOGO_SIZE - LOGO_MARGIN
        ly   = HEIGHT - LOGO_SIZE - LOGO_MARGIN
        prev = video_out[1:-1]
        logo_filter = (
            f"[{logo_idx}:v]scale={LOGO_SIZE}:{LOGO_SIZE},"
            f"format=yuva420p[logo_scaled];"
            f"[{prev}][logo_scaled]overlay={lx}:{ly}:format=auto[bg_logo];"
        )
        video_out = "[bg_logo]"

    intro_filter = (
        f"[{intro_idx}:v]scale={WIDTH}:{HEIGHT}:"
        f"force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"fade=t=out:st={INTRO_DUR - FADE_DUR}:d={FADE_DUR},setsar=1[intro_v];"
    )
    outro_filter = (
        f"[{outro_idx}:v]scale={WIDTH}:{HEIGHT}:"
        f"force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"fade=t=in:st=0:d={FADE_DUR},setsar=1[outro_v];"
    )

    final_concat = f"[intro_v]{video_out}[outro_v]concat=n=3:v=1:a=0[video_out];"

    total_dur    = INTRO_DUR + audio_duration + OUTRO_DUR
    audio_filter = (
        f"[{audio_idx}:a]adelay={int(INTRO_DUR*1000)}|{int(INTRO_DUR*1000)}[voice_delayed];"
        f"[{music_idx}:a]aloop=loop=-1:size=2e+09,volume={MUSIC_VOL},"
        f"atrim=duration={total_dur}[music_loop];"
        f"[voice_delayed][music_loop]amix=inputs=2:duration=first[audio_out];"
    )

    filtergraph = (
        scale_filters + concat_filter + text_filter +
        photo_filter  + logo_filter   +
        intro_filter  + outro_filter  + final_concat + audio_filter
    )

    cmd = (
        ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filtergraph,
            "-map", "[video_out]", "-map", "[audio_out]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            output_path,
        ]
    )

    print("   Running FFmpeg...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ❌ FFmpeg failed:", flush=True)
        print(result.stderr[-3000:], flush=True)
        return False

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"   ✅ Video rendered: {size_mb:.1f}MB", flush=True)
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    def log(msg): print(msg, flush=True)

    log("=" * 60)
    log(f"🎬 Video Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    log(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)
    log("✅ Script started — imports OK")

    table   = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    log(f"\n📡 Fetching episode {EPISODE_NUMBER} from Supabase ({table})...")
    ep_rows = db_get(table, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    episode = ep_rows[0] if ep_rows else None
    if not episode:
        log(f"❌ Episode {EPISODE_NUMBER} not found in {table}"); return

    log(f"   ✅ Episode found: {episode.get('title_english') or episode.get('title_tamil')}")
    db_patch(table, EPISODE_NUMBER, {"status": "generating_video"})
    log(f"   ✅ Status → generating_video")

    with tempfile.TemporaryDirectory() as tmpdir:
        lang_code = "ta" if LANGUAGE == "ta" else "en"

        # 1. Font
        if LANGUAGE == "ta":
            font_path = "/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        log(f"\n🔤 Font: {font_path} ({'found' if os.path.exists(font_path) else '⚠️  NOT FOUND'})")

        # 2. Voice
        voice_url = episode.get("voice_url")
        if not voice_url:
            log("❌ No voice recording found"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        log(f"\n🎤 Step 1/9 — Downloading voice recording...")
        voice_raw = os.path.join(tmpdir, "voice_raw.mp3")
        if not download_file(voice_url, voice_raw, "Voice recording"):
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        voice_path     = voice_raw
        audio_duration = get_audio_duration(voice_path)
        log(f"   ✅ Audio duration: {audio_duration:.1f}s ({audio_duration/60:.1f} mins)")

        # 3. Script
        script_col  = "script_tamil" if LANGUAGE == "ta" else "script_english"
        script_text = episode.get(script_col, "") or ""
        log(f"   ✅ Script loaded: {len(script_text.split())} words")

        # 4. WhisperX
        log(f"\n🎙️  Step 2/9 — WhisperX alignment (3-5 mins)...")
        whisper_lang = "ta" if LANGUAGE == "ta" else "en"
        words        = run_whisperx(voice_path, whisper_lang, tmpdir)
        log(f"   ✅ WhisperX done — {datetime.now().strftime('%H:%M:%S')}")

        # 5. Karaoke screens
        log(f"\n📝 Step 3/9 — Building karaoke screens...")
        screens = build_karaoke_screens(words, script_text, audio_duration)

        # 6. Text overlay video
        log(f"\n🖼️  Step 4/9 — Rendering text overlay...")
        text_video_path = build_text_overlay_video(screens, audio_duration, font_path, tmpdir)
        if not text_video_path:
            log("❌ Text overlay failed"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        log(f"   ✅ Text overlay done — {datetime.now().strftime('%H:%M:%S')}")

        # 7. Episode images
        raw_ep_images = episode.get("episode_images") or []
        if isinstance(raw_ep_images, str):
            try: raw_ep_images = json.loads(raw_ep_images)
            except: raw_ep_images = []
        if not raw_ep_images:
            log("❌ No episode images found"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        log(f"\n📸 Step 5/9 — Downloading + preprocessing {len(raw_ep_images)} images...")
        image_paths = []
        for img in sorted(raw_ep_images, key=lambda x: x.get("order", 0)):
            raw_dest  = os.path.join(tmpdir, f"raw_{img.get('order', len(image_paths)+1)}.jpg")
            proc_dest = os.path.join(tmpdir, f"ep_img_{img.get('order', len(image_paths)+1)}.jpg")
            if download_file(img["url"], raw_dest, f"Image {img.get('order','')}"):
                preprocess_image(raw_dest, proc_dest)
                image_paths.append({**img, "local_path": proc_dest})
                log(f"   ✅ Image {img.get('order','')} preprocessed (blurred bg + sharp center)")
        if not image_paths:
            log("❌ Could not download any images"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        # 8. Intro / Outro / Photo / Logo
        log(f"\n🖼️  Step 6/9 — Downloading intro/outro + narrator + logo...")
        intro_path = os.path.join(tmpdir, "intro.png")
        outro_path = os.path.join(tmpdir, "outro.png")
        download_file(episode.get("intro_image_url") or storage_url("channel-assets", "default_intro.png"), intro_path, "Intro")
        download_file(episode.get("outro_image_url") or storage_url("channel-assets", "default_outro.png"), outro_path, "Outro")

        photo_file   = "photo_tamil.jpg" if LANGUAGE == "ta" else "photo_english.jpg"
        photo_raw    = os.path.join(tmpdir, "narrator.jpg")
        photo_circle = os.path.join(tmpdir, "narrator_circle.png")
        if download_file(storage_url("channel-assets", photo_file), photo_raw, f"Narrator ({photo_file})"):
            make_circle_photo(photo_raw, photo_circle, PHOTO_SIZE)
            photo_final = photo_circle if os.path.exists(photo_circle) else photo_raw
        else:
            photo_final = None

        logo_path  = os.path.join(tmpdir, "logo.png")
        logo_final = None
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests as google_requests
            creds_info = json.loads(GCP_CREDS_JSON)
            logo_creds = service_account.Credentials.from_service_account_info(
                creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            logo_creds.refresh(google_requests.Request())
            r = requests.get(
                f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/ihaveacause_logo.png?alt=media",
                headers={"Authorization": f"Bearer {logo_creds.token}"}, timeout=15)
            if r.status_code == 200:
                with open(logo_path, "wb") as f: f.write(r.content)
                logo_final = logo_path
                log(f"   ✅ Logo downloaded ({len(r.content)//1024}KB)")
            else:
                log(f"   ⚠️  Logo download failed {r.status_code}")
        except Exception as e:
            log(f"   ⚠️  Logo error: {e}")

        # 9. Music
        log(f"\n🎵 Step 7/9 — Downloading background music...")
        music_path = os.path.join(tmpdir, "music.mp3")
        if not download_file(storage_url("episode-music", "background.mp3"), music_path, "Background music"):
            log("   ⚠️  Music not found — using silence")
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", music_path], capture_output=True)

        # 10. Image timeline
        image_timeline = build_image_timeline(image_paths, words or [], audio_duration)

        # 11. Render
        log(f"\n🎬 Step 8/9 — FFmpeg render (veryfast preset)...")
        log(f"   Started at: {datetime.now().strftime('%H:%M:%S')}")
        output_path = os.path.join(tmpdir, f"ep{EPISODE_NUMBER:03d}_{lang_code}.mp4")
        success = render_video(
            image_timeline=image_timeline, audio_path=voice_path,
            music_path=music_path, intro_path=intro_path, outro_path=outro_path,
            photo_path=photo_final, logo_path=logo_final,
            text_video_path=text_video_path, audio_duration=audio_duration,
            output_path=output_path,
        )
        if not success:
            log("❌ Video rendering failed"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        log(f"   ✅ FFmpeg done — {datetime.now().strftime('%H:%M:%S')}")

        # 12. Upload
        log(f"\n☁️  Step 9/9 — Uploading to GCS...")
        gcs_video_path = f"episodes/ep{EPISODE_NUMBER:03d}/{lang_code}/final.mp4"
        video_url = upload_to_gcs(output_path, gcs_video_path)
        if not video_url:
            log("❌ Upload failed"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        db_patch(table, EPISODE_NUMBER, {"video_url": video_url, "status": "video_ready"})
        log(f"   ✅ Supabase updated — status → video_ready")
        log(f"\n{'='*60}")
        log(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — video ready!")
        log(f"   Finished at: {datetime.now().strftime('%H:%M:%S')}")
        log(f"{'='*60}")

if __name__ == "__main__":
    main()
