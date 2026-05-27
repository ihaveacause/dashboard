"""
generate_video.py — New YouTube Pipeline
==========================================
Combines all assets into a final YouTube video:
  - Voice recording (wife for Tamil, you for English)
  - WhisperX word-level alignment for karaoke sync
  - English voice: -3 semitone pitch shift for deeper sound
  - 5 Imagen 3 script images (cycling behind voice)
  - Intro image (2s fade-in) — episode-specific or default
  - Outro image (3s fade-out) — episode-specific or default
  - Narrator circle photo — bottom-left corner, always visible
  - Channel logo — bottom-right corner, always visible
  - Background music at 12% volume
  - Karaoke text: rolling 3 lines, lower third, dark semi-transparent bar
    Rendered via Pillow (not FFmpeg drawtext) — works for Tamil + English,
    zero special-character escaping issues.

Layout:
  ┌─────────────────────────────────┐
  │                                 │
  │   Imagen 3 images (top 70%)     │
  │                                 │
  ├─────────────────────────────────┤
  │[photo] Tamil/English script  [logo]│  ← lower third (30%)
  │        rolling 3-line karaoke       │
  └─────────────────────────────────┘

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
from PIL import Image, ImageDraw, ImageFont

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
LOWER_THIRD    = 0.30
BAR_HEIGHT     = int(HEIGHT * LOWER_THIRD)   # 324px
LOWER_TOP      = HEIGHT - BAR_HEIGHT         # 756px
LINE_HEIGHT    = 65
FONT_SIZE      = 42
WORDS_PER_LINE = 6
MAX_LINES      = 3
MUSIC_VOL      = 0.12
INTRO_DUR      = 2.0
OUTRO_DUR      = 3.0
FADE_DUR       = 0.5
PITCH_SHIFT    = -3
PHOTO_SIZE     = 140
PHOTO_MARGIN   = 20
LOGO_HEIGHT    = 55
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
        print(f"   ✅ {desc}: {size_kb}KB")
        return True
    print(f"   ❌ {desc} download failed {r.status_code}: {url[:80]}")
    return False

def storage_url(bucket, path):
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"

# ── GCS upload via REST API ───────────────────────────────────
def upload_to_gcs(local_path, gcs_path, content_type="video/mp4"):
    """
    Upload a file to GCS using the JSON API + google-auth.
    Returns a signed URL valid for 30 days (bucket is not public,
    so signed URL is required for dashboard preview and YouTube upload).
    """
    try:
        import datetime
        from google.oauth2 import service_account
        import google.auth.transport.requests as google_requests

        creds_info  = json.loads(GCP_CREDS_JSON)

        # ── 1. Upload the file ────────────────────────────────
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google_requests.Request())

        upload_url = (
            f"https://storage.googleapis.com/upload/storage/v1/b"
            f"/{GCS_BUCKET}/o"
        )
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type":  content_type,
        }
        params = {"uploadType": "media", "name": gcs_path}

        print(f"   📤 Uploading {os.path.getsize(local_path) // (1024*1024)}MB to GCS...")
        with open(local_path, "rb") as f:
            r = requests.post(
                upload_url,
                params=params,
                headers=headers,
                data=f,
                timeout=600
            )

        if r.status_code not in (200, 201):
            print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}")
            return None

        print(f"   ✅ GCS upload complete")

        # ── 2. Generate signed URL valid for 30 days ─────────
        signing_creds = service_account.Credentials.from_service_account_info(
            creds_info
        )
        expiry    = datetime.timedelta(days=30)
        sign_url  = (
            f"https://storage.googleapis.com/storage/v1/b"
            f"/{GCS_BUCKET}/o/{requests.utils.quote(gcs_path, safe='')}?"
            f"alt=media"
        )

        # Use GCS signing API — no extra SDK needed
        now        = datetime.datetime.utcnow()
        expiry_dt  = now + expiry
        expiry_ts  = int(expiry_dt.timestamp())

        string_to_sign = "\n".join([
            "GET",
            "",
            "",
            str(expiry_ts),
            f"/{GCS_BUCKET}/{gcs_path}",
        ])

        import base64
        import hashlib
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        private_key_data = creds_info["private_key"].encode("utf-8")
        private_key = serialization.load_pem_private_key(
            private_key_data, password=None, backend=default_backend()
        )
        signature = private_key.sign(
            string_to_sign.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")

        client_email = creds_info["client_email"]
        signed_url = (
            f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
            f"?GoogleAccessId={client_email}"
            f"&Expires={expiry_ts}"
            f"&Signature={encoded_sig}"
        )

        print(f"   ✅ Signed URL generated (valid 30 days)")
        return signed_url

    except Exception as e:
        print(f"   ❌ GCS upload error: {e}")
        return None

# ── WhisperX alignment ────────────────────────────────────────
def run_whisperx(audio_path, language, tmpdir):
    print(f"\n🎙️  Running WhisperX alignment ({language})...")

    result = subprocess.run(
        [
            "whisperx", audio_path,
            "--model",        "medium",
            "--language",     language,
            "--output_dir",   tmpdir,
            "--output_format","json",
            "--compute_type", "float32",
            # --align_model intentionally omitted — default is more reliable
        ],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"   ⚠️  WhisperX exit code {result.returncode}")
        print(f"   ⚠️  stderr: {result.stderr[:800]}")
    else:
        if result.stdout.strip():
            print(f"   ℹ️  WhisperX stdout: {result.stdout[:300]}")

    audio_stem = Path(audio_path).stem
    json_path  = os.path.join(tmpdir, f"{audio_stem}.json")
    if not os.path.exists(json_path):
        json_files = list(Path(tmpdir).glob("*.json"))
        if json_files:
            json_path = str(json_files[0])
            print(f"   ℹ️  Found JSON: {Path(json_path).name}")
        else:
            print(f"   ⚠️  No WhisperX JSON produced — falling back to duration sync")
            return None

    with open(json_path) as f:
        data = json.load(f)

    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            if "word" in w and "start" in w and "end" in w:
                words.append({
                    "word":  w["word"].strip(),
                    "start": w["start"],
                    "end":   w["end"],
                })

    if not words:
        print(f"   ⚠️  WhisperX produced no word-level timestamps")
        print(f"   ℹ️  Segments in JSON: {len(data.get('segments', []))}")
        segs = data.get("segments", [])
        if segs:
            print(f"   ℹ️  First segment sample: {segs[0]}")
        print(f"   ℹ️  Falling back to duration-based sync")
        return None

    print(f"   ✅ WhisperX: {len(words)} words aligned")
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
            print(f"   🎯 Trigger '{trigger_text[:40]}' → {ts:.2f}s")
            return ts

    if n >= 3:
        short = trigger_words[:3]
        for i in range(len(word_list) - 3 + 1):
            if word_list[i:i+3] == short:
                ts = words[i]["start"]
                print(f"   🎯 Trigger (fuzzy) '{trigger_text[:40]}' → {ts:.2f}s")
                return ts

    print(f"   ⚠️  Trigger not found: '{trigger_text[:60]}'")
    return None

def build_image_timeline(episode_images, words, audio_duration):
    if not episode_images:
        return []

    n        = len(episode_images)
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
                    print(f"   Info: Image {i+1} no trigger — equal spacing {start:.2f}s")
                else:
                    print(f"   Warning: Image {i+1} trigger not found — fallback {start:.2f}s")

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

    print(f"\n   Image timeline:")
    for t in timeline:
        dur = t["end"] - t["start"]
        print(f"      Image {t['order']}: {t['start']:.1f}s to {t['end']:.1f}s ({dur:.1f}s)")

    return timeline

def build_karaoke_screens(words, script_text, audio_duration):
    if words:
        lines        = []
        current_line = []
        for w in words:
            current_line.append(w)
            if len(current_line) >= WORDS_PER_LINE:
                lines.append(current_line)
                current_line = []
        if current_line:
            lines.append(current_line)

        screens = []
        for i in range(0, len(lines), MAX_LINES):
            screen_lines = lines[i:i + MAX_LINES]
            start      = screen_lines[0][0]["start"]
            end        = screen_lines[-1][-1]["end"]
            text_lines = [" ".join(w["word"] for w in line) for line in screen_lines]
            while len(text_lines) < MAX_LINES:
                text_lines.append("")
            screens.append({"start": start, "end": end, "lines": text_lines})

    else:
        print("   ℹ️  Using duration-based sync (no WhisperX timestamps)")
        words_list = script_text.split()
        lines      = []
        for i in range(0, len(words_list), WORDS_PER_LINE):
            lines.append(" ".join(words_list[i:i + WORDS_PER_LINE]))

        total_lines   = len(lines)
        time_per_line = (audio_duration - INTRO_DUR - OUTRO_DUR) / max(total_lines, 1)

        screens = []
        for i in range(0, len(lines), MAX_LINES):
            screen_lines = lines[i:i + MAX_LINES]
            start = INTRO_DUR + (i * time_per_line)
            end   = start + (len(screen_lines) * time_per_line)
            while len(screen_lines) < MAX_LINES:
                screen_lines.append("")
            screens.append({"start": start, "end": end, "lines": screen_lines})

    print(f"   ✅ {len(screens)} karaoke screens built")
    return screens

# ── Pillow: render one karaoke screen → PNG ───────────────────
def render_screen_png(lines, font_path, output_path):
    img  = Image.new("RGB", (WIDTH, BAR_HEIGHT), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (WIDTH, BAR_HEIGHT)], fill=(18, 18, 18))

    try:
        font = ImageFont.truetype(font_path, FONT_SIZE)
    except Exception as e:
        print(f"   ⚠️  Font load failed ({e}) — using default font")
        font = ImageFont.load_default()

    for li, line in enumerate(lines):
        if not line.strip():
            continue
        y = 30 + li * LINE_HEIGHT
        try:
            bbox   = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * (FONT_SIZE // 2)
        x = max(20, (WIDTH - text_w) // 2)
        draw.text((x, y), line, font=font, fill=(255, 255, 255))

    img.save(output_path, "PNG")

# ── Build text overlay video from Pillow PNGs ─────────────────
def build_text_overlay_video(screens, audio_duration, font_path, tmpdir):
    print(f"\n✏️  Rendering {len(screens)} karaoke screen PNGs (Pillow)...")

    blank_path = os.path.join(tmpdir, "text_blank.png")
    Image.new("RGB", (WIDTH, BAR_HEIGHT), (0, 0, 0)).save(blank_path, "PNG")

    screen_paths = []
    for idx, screen in enumerate(screens):
        png_path = os.path.join(tmpdir, f"screen_{idx:04d}.png")
        render_screen_png(screen["lines"], font_path, png_path)
        screen_paths.append(png_path)

    print(f"   ✅ {len(screen_paths)} PNGs rendered")

    concat_path = os.path.join(tmpdir, "text_concat.txt")
    with open(concat_path, "w") as f:
        cursor = 0.0
        for idx, screen in enumerate(screens):
            s_start = screen["start"]
            s_end   = screen["end"]

            if s_start > cursor + 0.001:
                gap = s_start - cursor
                f.write(f"file '{blank_path}'\nduration {gap:.4f}\n")

            dur = max(s_end - s_start, 1.0 / FPS)
            f.write(f"file '{screen_paths[idx]}'\nduration {dur:.4f}\n")
            cursor = s_end

        if cursor < audio_duration - 0.001:
            f.write(f"file '{blank_path}'\nduration {audio_duration - cursor:.4f}\n")

        f.write(f"file '{blank_path}'\n")

    text_video = os.path.join(tmpdir, "text_overlay.mp4")
    print(f"   🎬 Encoding text overlay video ({WIDTH}×{BAR_HEIGHT})...")
    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_path,
        "-vf", f"scale={WIDTH}:{BAR_HEIGHT},setsar=1,fps={FPS}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        text_video,
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"   ❌ Text overlay video failed:")
        print(result.stderr[-1000:])
        return None

    size_mb = os.path.getsize(text_video) / 1024 / 1024
    print(f"   ✅ Text overlay video ready ({size_mb:.1f}MB)")
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

# ── FFmpeg: pitch shift ───────────────────────────────────────
def pitch_shift_audio(input_path, output_path, semitones):
    factor = 2 ** (semitones / 12.0)
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"asetrate=44100*{factor:.6f},aresample=44100",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(f"   ⚠️  Pitch shift failed, using original audio")
        shutil.copy(input_path, output_path)

# ── Main FFmpeg render ────────────────────────────────────────
def render_video(
    image_timeline, audio_path, music_path,
    intro_path, outro_path,
    photo_path, logo_path,
    text_video_path,
    audio_duration, output_path
):
    print(f"\n🎬 Rendering video with FFmpeg...")

    n_images = len(image_timeline)
    inputs   = []
    next_idx = 0

    for item in image_timeline:
        dur = item["end"] - item["start"]
        inputs += ["-loop", "1", "-t", str(dur + 0.1), "-i", item["local_path"]]
    next_idx = n_images

    inputs   += ["-i", audio_path]
    audio_idx = next_idx; next_idx += 1

    inputs   += ["-i", music_path]
    music_idx = next_idx; next_idx += 1

    inputs   += ["-i", text_video_path]
    text_idx  = next_idx; next_idx += 1

    photo_idx = None
    if photo_path and os.path.exists(photo_path):
        inputs   += ["-i", photo_path]
        photo_idx = next_idx; next_idx += 1

    logo_idx = None
    if logo_path and os.path.exists(logo_path):
        inputs  += ["-i", logo_path]
        logo_idx = next_idx; next_idx += 1

    inputs   += ["-loop", "1", "-t", str(INTRO_DUR + FADE_DUR), "-i", intro_path]
    intro_idx = next_idx; next_idx += 1

    inputs   += ["-loop", "1", "-t", str(OUTRO_DUR + FADE_DUR), "-i", outro_path]
    outro_idx = next_idx

    scale_filters = ""
    for i in range(n_images):
        dur = image_timeline[i]["end"] - image_timeline[i]["start"]
        scale_filters += (
            f"[{i}:v]scale={WIDTH}:{HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
            f"trim=duration={dur},setpts=PTS-STARTPTS[sv{i}];"
        )

    concat_in     = "".join(f"[sv{i}]" for i in range(n_images))
    concat_filter = f"{concat_in}concat=n={n_images}:v=1:a=0[bg_raw];"

    text_filter = (
        f"[{text_idx}:v]scale={WIDTH}:{BAR_HEIGHT},setsar=1,fps={FPS}[text_scaled];"
        f"[bg_raw][text_scaled]overlay=0:{LOWER_TOP}[bg_text];"
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
        logo_w = int(LOGO_HEIGHT * 2.0)
        lx     = WIDTH - logo_w - LOGO_MARGIN
        ly     = HEIGHT - LOGO_HEIGHT - LOGO_MARGIN
        prev   = video_out[1:-1]
        logo_filter = (
            f"[{logo_idx}:v]scale={logo_w}:{LOGO_HEIGHT},"
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

    final_concat = (
        f"[intro_v]{video_out}[outro_v]"
        f"concat=n=3:v=1:a=0[video_out];"
    )

    total_dur    = INTRO_DUR + audio_duration + OUTRO_DUR
    audio_filter = (
        f"[{audio_idx}:a]adelay={int(INTRO_DUR*1000)}|{int(INTRO_DUR*1000)}[voice_delayed];"
        f"[{music_idx}:a]aloop=loop=-1:size=2e+09,volume={MUSIC_VOL},"
        f"atrim=duration={total_dur}[music_loop];"
        f"[voice_delayed][music_loop]amix=inputs=2:duration=first[audio_out];"
    )

    filtergraph = (
        scale_filters +
        concat_filter +
        text_filter   +
        photo_filter  +
        logo_filter   +
        intro_filter  +
        outro_filter  +
        final_concat  +
        audio_filter
    )

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filtergraph,
            "-map", "[video_out]",
            "-map", "[audio_out]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
    )

    print("   Running FFmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ❌ FFmpeg failed:")
        print(result.stderr[-3000:])
        return False

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"   ✅ Video rendered: {size_mb:.1f}MB")
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    table   = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    ep_rows = db_get(table, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    episode = ep_rows[0] if ep_rows else None
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found in {table}")
        return

    print(f"\n📖 {episode.get('title_english') or episode.get('title_tamil')}")
    db_patch(table, EPISODE_NUMBER, {"status": "generating_video"})

    with tempfile.TemporaryDirectory() as tmpdir:
        lang_code = "ta" if LANGUAGE == "ta" else "en"

        # 1. Font path (used by Pillow)
        if LANGUAGE == "ta":
            font_path = "/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"

        print(f"\n🔤 Font: {font_path} ({'found' if os.path.exists(font_path) else '⚠️  NOT FOUND — will use default'})")

        # 2. Download voice recording
        voice_url = episode.get("voice_url")
        if not voice_url:
            print("❌ No voice recording found — upload voice first")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        voice_raw = os.path.join(tmpdir, "voice_raw.mp3")
        if not download_file(voice_url, voice_raw, "Voice recording"):
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 3. Pitch shift for English
        if LANGUAGE == "en":
            print(f"\n🎚️  Applying {PITCH_SHIFT} semitone pitch shift...")
            voice_path = os.path.join(tmpdir, "voice.mp3")
            pitch_shift_audio(voice_raw, voice_path, PITCH_SHIFT)
        else:
            voice_path = voice_raw

        audio_duration = get_audio_duration(voice_path)
        print(f"\n   Audio duration: {audio_duration:.1f}s")

        # 4. Script text for fallback sync
        script_col  = "script_tamil" if LANGUAGE == "ta" else "script_english"
        script_text = episode.get(script_col, "") or ""

        # 5. WhisperX alignment
        whisper_lang    = "ta" if LANGUAGE == "ta" else "en"
        words           = run_whisperx(voice_path, whisper_lang, tmpdir)
        screens         = build_karaoke_screens(words, script_text, audio_duration)

        # 6. Build Pillow text overlay video
        text_video_path = build_text_overlay_video(
            screens, audio_duration, font_path, tmpdir
        )
        if not text_video_path:
            print("❌ Text overlay video failed — aborting")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 7. Load episode images
        raw_ep_images = episode.get("episode_images") or []
        if isinstance(raw_ep_images, str):
            try:
                raw_ep_images = json.loads(raw_ep_images)
            except:
                raw_ep_images = []

        if not raw_ep_images:
            print("❌ No episode images found — upload images first")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        print(f"\n📸 Downloading {len(raw_ep_images)} episode images...")
        image_paths = []
        for img in sorted(raw_ep_images, key=lambda x: x.get("order", 0)):
            dest = os.path.join(tmpdir, f"ep_img_{img.get('order', len(image_paths)+1)}.jpg")
            if download_file(img["url"], dest, f"Image {img.get('order','')} — trigger: '{img.get('trigger','(start)')[:30]}'"):
                image_paths.append({**img, "local_path": dest})

        if not image_paths:
            print("❌ Could not download any images")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 8. Intro / Outro images
        print(f"\n🖼️  Downloading intro/outro images...")
        intro_path = os.path.join(tmpdir, "intro.png")
        outro_path = os.path.join(tmpdir, "outro.png")
        intro_url  = episode.get("intro_image_url") or storage_url("channel-assets", "default_intro.png")
        outro_url  = episode.get("outro_image_url") or storage_url("channel-assets", "default_outro.png")
        download_file(intro_url, intro_path, "Intro image")
        download_file(outro_url, outro_path, "Outro image")

        # 9. Narrator photo
        print(f"\n👤 Downloading narrator photo...")
        photo_file   = "photo_tamil.jpg" if LANGUAGE == "ta" else "photo_english.jpg"
        photo_raw    = os.path.join(tmpdir, "narrator.jpg")
        photo_circle = os.path.join(tmpdir, "narrator_circle.png")
        photo_url    = storage_url("channel-assets", photo_file)
        if download_file(photo_url, photo_raw, f"Narrator photo ({photo_file})"):
            make_circle_photo(photo_raw, photo_circle, PHOTO_SIZE)
            photo_final = photo_circle if os.path.exists(photo_circle) else photo_raw
        else:
            photo_final = None

        # 10. Channel logo
        print(f"\n🔱 Downloading channel logo...")
        logo_path  = os.path.join(tmpdir, "logo.png")
        logo_url   = f"https://storage.googleapis.com/{GCS_BUCKET}/assets/ihaveacause_logo.png"
        logo_final = None
        try:
            r = requests.get(logo_url, timeout=15)
            if r.status_code == 200:
                with open(logo_path, "wb") as f:
                    f.write(r.content)
                logo_final = logo_path
                print(f"   ✅ Logo downloaded")
        except Exception as e:
            print(f"   ⚠️  Logo download failed: {e} — continuing without logo")

        # 11. Background music
        print(f"\n🎵 Downloading background music...")
        music_path = os.path.join(tmpdir, "music.mp3")
        music_url  = storage_url("episode-music", "background.mp3")
        if not download_file(music_url, music_path, "Background music"):
            print("   ⚠️  Music not found — using silence")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "1", music_path
            ], capture_output=True)

        # 12. Build image timeline
        image_timeline = build_image_timeline(image_paths, words or [], audio_duration)

        # 13. Render final video
        output_path = os.path.join(tmpdir, f"ep{EPISODE_NUMBER:03d}_{lang_code}.mp4")
        success = render_video(
            image_timeline  = image_timeline,
            audio_path      = voice_path,
            music_path      = music_path,
            intro_path      = intro_path,
            outro_path      = outro_path,
            photo_path      = photo_final,
            logo_path       = logo_final,
            text_video_path = text_video_path,
            audio_duration  = audio_duration,
            output_path     = output_path,
        )

        if not success:
            print("❌ Video rendering failed")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 14. Upload to GCS via REST API (no extra packages needed)
        print(f"\n☁️  Uploading video to GCS...")
        gcs_video_path = f"episodes/ep{EPISODE_NUMBER:03d}/{lang_code}/final.mp4"
        video_url = upload_to_gcs(output_path, gcs_video_path)

        if not video_url:
            print("❌ Video upload failed")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 15. Save URL and status to Supabase database
        db_patch(table, EPISODE_NUMBER, {
            "video_url": video_url,
            "status":    "video_ready",
        })

        print(f"\n{'='*60}")
        print(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — video ready!")
        print(f"   URL: {video_url}")
        print(f"{'='*60}")

if __name__ == "__main__":
    main()
