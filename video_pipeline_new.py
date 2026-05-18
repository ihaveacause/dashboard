"""
I Have a Cause — Video Pipeline
================================
- Cloud Text-to-Speech API → Chirp3 HD voices
    Tamil  : ta-IN-Chirp3-HD-Callirrhoe (female, 0.85x)
    English: en-US-Chirp3-HD-Charon     (male,   0.85x)
- PIL composites text directly onto image frames (no FFmpeg overlay)
- Three fonts pre-loaded: Tamil, Latin (English), Devanagari (Sanskrit)
- Per-character font selection — no boxes for any script
- 4-line centered text, adaptive color (bright/dark image detection)
- SVG infographic as second-to-last scene
- FFmpeg assembles final video
- Generates BOTH Tamil and English videos in one run
"""

import os
import re
import json
import base64
import requests
import subprocess
import tempfile
import shutil
import unicodedata
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from google.oauth2 import service_account
import google.auth.transport.requests

# ── Config ──────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

W, H, FPS  = 1280, 720, 24
FONT_SIZE  = 32
SPEAK_RATE = 0.85

# TTS voices
TAMIL_VOICE   = "ta-IN-Chirp3-HD-Callirrhoe"
ENGLISH_VOICE = "en-US-Chirp3-HD-Charon"
TTS_ENDPOINT  = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"

PROJECT_ID = "gen-lang-client-0078128013"

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST     = f"{SUPABASE_URL}/rest/v1"
WORK_DIR = Path(tempfile.mkdtemp(prefix="ihac_video_"))

# ── Vertex AI / GCP auth ────────────────────────────────────
creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

def get_token():
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token

# ── Supabase helpers ────────────────────────────────────────
def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, n, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{n}",
        headers=SB_HEADERS, json=data, timeout=15
    )
    return r.status_code in (200, 204)

def upload_video(filename, data_bytes):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/episode-videos/ep{EPISODE_NUMBER:03d}/{filename}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "video/mp4",
            "x-upsert":      "true"
        },
        data=data_bytes, timeout=600
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/episode-videos/ep{EPISODE_NUMBER:03d}/{filename}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

# ── Font loading (pre-loaded once at startup) ───────────────
def load_fonts(size):
    """Load Tamil, Latin, Devanagari fonts. Falls back gracefully."""
    tamil_candidates = [
        "/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerifTamil-Regular.ttf",
    ]
    latin_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    devanagari_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    ]
    universal_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def try_load(candidates):
        for path in candidates:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return None

    fonts = {
        "tamil":      try_load(tamil_candidates),
        "latin":      try_load(latin_candidates),
        "devanagari": try_load(devanagari_candidates),
        "universal":  try_load(universal_candidates),
        "default":    ImageFont.load_default()
    }

    for name, font in fonts.items():
        loaded = font is not None and font != fonts["default"]
        print(f"   Font [{name}]: {'✅' if loaded else '⚠️  fallback'}")

    return fonts

def get_font_for_char(ch, fonts):
    """Return the best font for a character based on its Unicode block."""
    try:
        name = unicodedata.name(ch, "")
    except Exception:
        name = ""

    if "TAMIL" in name:
        return fonts["tamil"] or fonts["universal"] or fonts["default"]
    elif "DEVANAGARI" in name:
        return fonts["devanagari"] or fonts["universal"] or fonts["default"]
    elif ch.isascii():
        return fonts["latin"] or fonts["universal"] or fonts["default"]
    else:
        return fonts["universal"] or fonts["default"]

# ── Script cleaning ─────────────────────────────────────────
def clean_script(text):
    """Strip all markdown and formatting before TTS."""
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#+\s*', '', text)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'_{2,}', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ── Script chunking ─────────────────────────────────────────
def chunk_script(script, words_per_chunk=28):
    """Split script into ~4-line display chunks."""
    words  = script.split()
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunks.append(" ".join(words[i:i + words_per_chunk]))
    return chunks

# ── Cloud TTS ───────────────────────────────────────────────
def generate_tts_chunk(text, chunk_idx, voice_name, language_code):
    """
    Call Cloud Text-to-Speech API (Chirp3 HD).
    Returns path to saved .wav file or None on failure.
    """
    token = get_token()

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": language_code,
            "name":         voice_name
        },
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "speakingRate":  SPEAK_RATE
        }
    }

    r = requests.post(
        TTS_ENDPOINT,
        headers={
            "Authorization":      f"Bearer {token}",
            "x-goog-user-project": PROJECT_ID,
            "Content-Type":        "application/json"
        },
        json=payload,
        timeout=120
    )

    if r.status_code != 200:
        print(f"      ❌ TTS error {r.status_code}: {r.text[:300]}")
        return None

    try:
        audio_b64  = r.json().get("audioContent", "")
        if not audio_b64:
            print(f"      ❌ No audioContent in TTS response")
            return None

        audio_bytes = base64.b64decode(audio_b64)
        chunk_path  = WORK_DIR / f"chunk_{chunk_idx:04d}.wav"
        chunk_path.write_bytes(audio_bytes)   # always write
        return str(chunk_path)

    except Exception as e:
        print(f"      ❌ TTS parse error: {e}")
        return None

def get_audio_duration(path):
    """Get duration of audio file using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return 12.0

# ── Image helpers ───────────────────────────────────────────
def is_dark_image(img):
    arr = np.array(img.convert("L"))
    return arr.mean() < 128

def download_image(url):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            tmp = WORK_DIR / f"img_{abs(hash(url))}.jpg"
            tmp.write_bytes(r.content)
            return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ Image download failed: {e}")
    return Image.new("RGB", (W, H), (10, 10, 30))

def download_svg_as_image(svg_text):
    try:
        import cairosvg
        png_bytes = cairosvg.svg2png(
            bytestring=svg_text.encode("utf-8"),
            output_width=W, output_height=H
        )
        tmp = WORK_DIR / "infographic.png"
        tmp.write_bytes(png_bytes)
        return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ SVG conversion failed: {e}")
        return Image.new("RGB", (W, H), (5, 5, 20))

# ── Text frame rendering ────────────────────────────────────
def render_text_frame(base_img, text, fonts):
    """Composite text directly onto base_img using per-character font selection."""
    img     = base_img.copy().convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    dark    = is_dark_image(base_img)
    text_col = (255, 255, 255, 240) if dark else (20, 20, 20, 240)
    bg_col   = (0,   0,   0,   100) if dark else (255, 255, 255, 100)

    # Wrap into max 4 lines of ~40 chars
    words, lines, cur, cur_len = text.split(), [], [], 0
    for word in words:
        if cur_len + len(word) + 1 > 40 and cur:
            lines.append(" ".join(cur))
            cur, cur_len = [word], len(word)
        else:
            cur.append(word)
            cur_len += len(word) + 1
    if cur:
        lines.append(" ".join(cur))
    lines = lines[:4]

    line_h  = FONT_SIZE + 8
    total_h = len(lines) * line_h + 20
    box_y   = H - total_h - 60
    box_x   = 80

    # Background box
    draw.rectangle(
        [box_x - 10, box_y - 10, W - box_x + 10, box_y + total_h + 10],
        fill=bg_col
    )

    # Draw text char-by-char with correct font
    for li, line in enumerate(lines):
        y, x = box_y + li * line_h, box_x
        for ch in line:
            font = get_font_for_char(ch, fonts)
            draw.text((x, y), ch, font=font, fill=text_col)
            try:
                bbox = font.getbbox(ch)
                x += (bbox[2] - bbox[0]) + 1
            except Exception:
                x += FONT_SIZE

    return Image.alpha_composite(img, overlay).convert("RGB")

# ── Build one video clip ────────────────────────────────────
def frames_to_clip(frame_path, audio_path, clip_idx):
    clip_path = WORK_DIR / f"clip_{clip_idx:04d}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(frame_path),
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(clip_path)
    ], check=True, timeout=300)
    return str(clip_path)

# ── Single language render ──────────────────────────────────
def render_video(script, all_scenes, fonts, voice_name, language_code, label, work_subdir):
    """Render a full video for one language. Returns path to mp4 or None."""
    print(f"\n{'─'*50}")
    print(f"🎬 Rendering {label} video...")
    print(f"   Voice: {voice_name} | Rate: {SPEAK_RATE}x")

    subdir = work_subdir
    subdir.mkdir(exist_ok=True)

    chunks     = chunk_script(script)
    clip_paths = []
    scene_count = len(all_scenes)

    print(f"   Chunks: {len(chunks)} | Scenes: {scene_count}")

    for i, chunk in enumerate(chunks):
        scene_idx = min(int(i / len(chunks) * scene_count), scene_count - 1)
        base_img  = all_scenes[scene_idx]

        # TTS
        audio_path = generate_tts_chunk(chunk, i, voice_name, language_code)
        if not audio_path:
            print(f"   ⚠️  Chunk {i+1} TTS failed — skipping")
            continue

        duration = get_audio_duration(audio_path)

        # Render frame with text
        composited = render_text_frame(base_img, chunk, fonts)
        frame_path = subdir / f"frame_{i:04d}.jpg"
        composited.save(str(frame_path), "JPEG", quality=95)

        # Build clip
        clip_path = frames_to_clip(frame_path, audio_path, i)
        clip_paths.append(clip_path)

        if (i + 1) % 10 == 0:
            print(f"   ✅ {i+1}/{len(chunks)} chunks done...")

    if not clip_paths:
        print(f"   ❌ No clips generated for {label}")
        return None

    # Concatenate
    concat_file = subdir / "concat.txt"
    with open(concat_file, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{cp}'\n")

    output_path = subdir / f"output_{label.lower()}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy",
        str(output_path)
    ], check=True, timeout=600)

    print(f"   ✅ {label} video assembled — {len(clip_paths)}/{len(chunks)} clips")
    return str(output_path)

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Pipeline — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── Fetch episodes ─────────────────────────────────────
    tamil_rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"
    })
    english_rows = db_get("english_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"
    })

    if not tamil_rows:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    tamil_ep   = tamil_rows[0]
    english_ep = english_rows[0] if english_rows else None

    print(f"\n📖 {tamil_ep['title_english']}")
    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "rendering_video"})

    try:
        # ── Load fonts once ────────────────────────────────
        print(f"\n🔤 Loading fonts...")
        fonts = load_fonts(FONT_SIZE)

        # ── Get images ─────────────────────────────────────
        image_urls_raw = tamil_ep.get("image_urls", "[]")
        image_urls     = json.loads(image_urls_raw) if isinstance(image_urls_raw, str) else image_urls_raw
        image_urls     = sorted(image_urls, key=lambda x: x["id"])
        scene_images   = [download_image(s["url"]) for s in image_urls]
        print(f"\n🖼  Downloaded {len(scene_images)} scene images")

        # ── Get SVG ────────────────────────────────────────
        svg_img = None
        svg_raw = tamil_ep.get("infographic_svg")
        if svg_raw:
            try:
                parsed  = json.loads(svg_raw) if isinstance(svg_raw, str) else svg_raw
                svg_txt = parsed.get("svg", "")
                if svg_txt:
                    print(f"\n📊 Converting SVG infographic...")
                    svg_img = download_svg_as_image(svg_txt)
                    print(f"   ✅ SVG ready")
            except Exception as e:
                print(f"   ⚠️  SVG load failed: {e}")

        # Build scene list — SVG second-to-last
        all_scenes = scene_images.copy()
        if svg_img and len(all_scenes) > 1:
            all_scenes.insert(-1, svg_img)
        elif svg_img:
            all_scenes.append(svg_img)
        print(f"   Total scenes: {len(all_scenes)}")

        # ── Tamil video ────────────────────────────────────
        tamil_script_raw = tamil_ep.get("script_tamil", "") or ""
        tamil_script     = clean_script(tamil_script_raw)
        tamil_url        = None

        if tamil_script:
            print(f"\n📝 Tamil script: {len(tamil_script.split())} words")
            tamil_path = render_video(
                script        = tamil_script,
                all_scenes    = all_scenes,
                fonts         = fonts,
                voice_name    = TAMIL_VOICE,
                language_code = "ta-IN",
                label         = "Tamil",
                work_subdir   = WORK_DIR / "tamil"
            )
            if tamil_path:
                print(f"\n☁️  Uploading Tamil video...")
                tamil_url = upload_video(
                    f"ep{EPISODE_NUMBER:03d}_tamil.mp4",
                    Path(tamil_path).read_bytes()
                )
                print(f"   {'✅' if tamil_url else '❌'} Tamil upload")
        else:
            print("⚠️  No Tamil script found — skipping Tamil video")

        # ── English video ──────────────────────────────────
        english_url = None
        if english_ep:
            english_script_raw = english_ep.get("script_english", "") or ""
            english_script     = clean_script(english_script_raw)

            if english_script:
                print(f"\n📝 English script: {len(english_script.split())} words")
                english_path = render_video(
                    script        = english_script,
                    all_scenes    = all_scenes,
                    fonts         = fonts,
                    voice_name    = ENGLISH_VOICE,
                    language_code = "en-US",
                    label         = "English",
                    work_subdir   = WORK_DIR / "english"
                )
                if english_path:
                    print(f"\n☁️  Uploading English video...")
                    english_url = upload_video(
                        f"ep{EPISODE_NUMBER:03d}_english.mp4",
                        Path(english_path).read_bytes()
                    )
                    print(f"   {'✅' if english_url else '❌'} English upload")
            else:
                print("⚠️  No English script found — skipping English video")

        # ── Save results ───────────────────────────────────
        update_data = {"status": "video_ready"}
        if tamil_url:
            update_data["video_url_tamil"] = tamil_url
        db_patch("tamil_episodes", EPISODE_NUMBER, update_data)

        if english_url and english_ep:
            db_patch("english_episodes", EPISODE_NUMBER, {
                "video_url_english": english_url,
                "status":            "video_ready"
            })

        print(f"\n{'='*60}")
        print(f"✅ Episode {EPISODE_NUMBER} — Video pipeline complete!")
        if tamil_url:
            print(f"   🇮🇳 Tamil  : {tamil_url}")
        if english_url:
            print(f"   🇬🇧 English: {english_url}")
        print(f"{'='*60}")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
    finally:
        shutil.rmtree(str(WORK_DIR), ignore_errors=True)

if __name__ == "__main__":
    main()
