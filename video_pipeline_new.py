"""
I Have a Cause — Video Pipeline
================================
- Vertex AI Chirp3 HD Tamil voice (Callirrhoe, female, 0.85x)
- PIL composites text directly onto image frames (no FFmpeg overlay)
- Three fonts pre-loaded: Tamil, Latin (English), Devanagari (Sanskrit)
- 4-line centered text, adaptive color (bright/dark image detection)
- SVG infographic as second-to-last scene
- FFmpeg assembles final video
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

W, H, FPS    = 1280, 720, 24
FONT_SIZE    = 32   # medium readable size
SPEAK_RATE   = 0.85 # Tamil pacing

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST     = f"{SUPABASE_URL}/rest/v1"
WORK_DIR = Path(tempfile.mkdtemp(prefix="ihac_video_"))

# ── Vertex AI auth ──────────────────────────────────────────
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

def upload_video(path, data_bytes):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/episode-videos/ep{EPISODE_NUMBER:03d}/{path}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "video/mp4",
            "x-upsert":      "true"
        },
        data=data_bytes, timeout=600
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/episode-videos/ep{EPISODE_NUMBER:03d}/{path}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

# ── Font loading (pre-loaded once at startup) ───────────────
def load_fonts(size):
    """
    Load three fonts covering Tamil, Latin (English), and Devanagari (Sanskrit).
    Falls back gracefully if any font is missing.
    """
    fonts = {}

    # Tamil font candidates
    tamil_candidates = [
        "/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerifTamil-Regular.ttf",
    ]
    # Latin/English font candidates
    latin_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    # Devanagari/Sanskrit font candidates
    devanagari_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    ]
    # Universal fallback — covers multiple scripts
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

    fonts["tamil"]      = try_load(tamil_candidates)
    fonts["latin"]      = try_load(latin_candidates)
    fonts["devanagari"] = try_load(devanagari_candidates)
    fonts["universal"]  = try_load(universal_candidates)
    fonts["default"]    = ImageFont.load_default()

    # Log what was found
    for name, font in fonts.items():
        status = "✅" if font and font != fonts["default"] else "⚠️  fallback"
        print(f"   Font [{name}]: {status}")

    return fonts

def get_font_for_char(ch, fonts):
    """Return the best font for a given character."""
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
    """Split script into ~4-line chunks for text overlay."""
    words  = script.split()
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i:i + words_per_chunk])
        chunks.append(chunk)
    return chunks

# ── TTS via Vertex AI Chirp3 HD ─────────────────────────────
def generate_tts_chunk(text, chunk_idx):
    """Generate audio for one text chunk. Returns path to .wav file."""
    token = get_token()
    url   = "https://us-central1-aiplatform.googleapis.com/v1/projects/gen-lang-client-0078128013/locations/us-central1/publishers/google/models/chirp-3-hd:streamingPredict"

    payload = {
        "inputs": [{
            "struct_value": {
                "fields": {
                    "text": {"string_value": text}
                }
            }
        }],
        "parameters": {
            "struct_value": {
                "fields": {
                    "voice_name":   {"string_value": "ta-IN-Chirp3-HD-Callirrhoe"},
                    "language_code":{"string_value": "ta-IN"},
                    "speaking_rate":{"number_value": SPEAK_RATE}
                }
            }
        }
    }

    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=120
    )

    if r.status_code != 200:
        print(f"      ❌ TTS error {r.status_code}: {r.text[:200]}")
        return None

    # Extract audio from response
    try:
        responses = r.json()
        if isinstance(responses, list):
            audio_b64 = "".join(
                item.get("outputs", [{}])[0].get("struct_value", {})
                    .get("fields", {}).get("audio", {}).get("string_value", "")
                for item in responses
            )
        else:
            audio_b64 = (responses.get("outputs", [{}])[0]
                         .get("struct_value", {})
                         .get("fields", {}).get("audio", {})
                         .get("string_value", ""))

        if not audio_b64:
            print(f"      ❌ No audio in TTS response")
            return None

        audio_bytes = base64.b64decode(audio_b64)
        chunk_path  = WORK_DIR / f"chunk_{chunk_idx:03d}.wav"
        chunk_path.write_bytes(audio_bytes)   # ← always write
        return str(chunk_path)

    except Exception as e:
        print(f"      ❌ TTS parse error: {e}")
        return None

def get_audio_duration(path):
    """Get duration of audio file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return 12.0  # fallback estimate

# ── Image brightness detection ──────────────────────────────
def is_dark_image(img):
    """Return True if image is dark (use light text), False if bright (use dark text)."""
    arr  = np.array(img.convert("L"))
    mean = arr.mean()
    return mean < 128

# ── Text frame rendering ────────────────────────────────────
def render_text_frame(base_img, text, fonts, duration_secs):
    """
    Composite text onto base_img.
    Returns list of (PIL Image, duration) tuples — one per fade segment.
    Uses per-character font selection for Tamil/English/Sanskrit.
    """
    img      = base_img.copy().convert("RGBA")
    overlay  = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw     = ImageDraw.Draw(overlay)

    dark     = is_dark_image(base_img)
    text_col = (255, 255, 255, 240) if dark else (20, 20, 20, 240)
    bg_col   = (0, 0, 0, 100)       if dark else (255, 255, 255, 100)

    # Wrap text into lines of ~40 chars
    words    = text.split()
    lines    = []
    cur_line = []
    cur_len  = 0
    for word in words:
        if cur_len + len(word) + 1 > 40 and cur_line:
            lines.append(" ".join(cur_line))
            cur_line = [word]
            cur_len  = len(word)
        else:
            cur_line.append(word)
            cur_len += len(word) + 1
    if cur_line:
        lines.append(" ".join(cur_line))
    lines = lines[:4]  # max 4 lines

    line_h   = FONT_SIZE + 8
    total_h  = len(lines) * line_h + 20
    box_y    = H - total_h - 60
    box_x    = 80

    # Semi-transparent background box
    draw.rectangle(
        [box_x - 10, box_y - 10, W - box_x + 10, box_y + total_h + 10],
        fill=bg_col
    )

    # Draw each line with per-character font selection
    for li, line in enumerate(lines):
        y   = box_y + li * line_h
        x   = box_x
        for ch in line:
            font = get_font_for_char(ch, fonts)
            draw.text((x, y), ch, font=font, fill=text_col)
            try:
                bbox = font.getbbox(ch)
                x   += (bbox[2] - bbox[0]) + 1
            except Exception:
                x += FONT_SIZE

    composited = Image.alpha_composite(img, overlay).convert("RGB")
    return composited, duration_secs

# ── Download image ──────────────────────────────────────────
def download_image(url):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            tmp = WORK_DIR / f"img_{abs(hash(url))}.jpg"
            tmp.write_bytes(r.content)
            return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ Image download failed: {e}")
    return Image.new("RGB", (W, H), (10, 10, 30))  # dark fallback

def download_svg_as_image(svg_data):
    """Convert SVG to PIL Image via cairosvg."""
    try:
        import cairosvg
        png_bytes = cairosvg.svg2png(bytestring=svg_data.encode("utf-8"),
                                     output_width=W, output_height=H)
        tmp = WORK_DIR / "infographic.png"
        tmp.write_bytes(png_bytes)
        return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ SVG conversion failed: {e}")
        return Image.new("RGB", (W, H), (5, 5, 20))

# ── Build one video clip from frames ───────────────────────
def frames_to_clip(frames_dir, audio_path, clip_idx, duration):
    """Create an mp4 clip from a single composited frame + audio."""
    frame_path = frames_dir / f"frame_{clip_idx:04d}.jpg"
    clip_path  = WORK_DIR   / f"clip_{clip_idx:04d}.mp4"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(frame_path),
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(clip_path)
    ]
    subprocess.run(cmd, check=True, timeout=300)
    return str(clip_path)

# ── Main pipeline ───────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Pipeline — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── Fetch episode ──────────────────────────────────────
    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"
    })
    if not rows:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return
    episode = rows[0]
    print(f"\n📖 {episode['title_english']}")

    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "rendering_video"})

    try:
        # ── Load fonts once ────────────────────────────────
        print(f"\n🔤 Loading fonts...")
        fonts = load_fonts(FONT_SIZE)

        # ── Get script ─────────────────────────────────────
        script_raw = episode.get("script_tamil", "") or ""
        script     = clean_script(script_raw)
        if not script:
            print("❌ No Tamil script found")
            return
        print(f"\n📝 Script: {len(script.split())} words")

        # ── Get images ─────────────────────────────────────
        image_urls_raw = episode.get("image_urls", "[]")
        image_urls     = json.loads(image_urls_raw) if isinstance(image_urls_raw, str) else image_urls_raw
        image_urls     = sorted(image_urls, key=lambda x: x["id"])

        # ── Get SVG ────────────────────────────────────────
        svg_img = None
        svg_raw = episode.get("infographic_svg")
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

        # Build scene list: images + SVG second-to-last
        scene_images = [download_image(s["url"]) for s in image_urls]
        all_scenes   = scene_images.copy()
        if svg_img:
            all_scenes.insert(-1, svg_img)  # second-to-last
        print(f"\n🖼  Scenes: {len(all_scenes)} total")

        # ── Chunk script ───────────────────────────────────
        chunks = chunk_script(script)
        print(f"\n📦 Script chunks: {len(chunks)}")

        # ── Generate TTS + render frames ───────────────────
        print(f"\n🔊 Generating TTS + rendering frames...")
        frames_dir = WORK_DIR / "frames"
        frames_dir.mkdir()

        clip_paths    = []
        chunks_done   = 0
        scene_count   = len(all_scenes)

        for i, chunk in enumerate(chunks):
            # Pick scene image based on chunk position
            scene_idx = min(int(i / len(chunks) * scene_count), scene_count - 1)
            base_img  = all_scenes[scene_idx]

            print(f"   Chunk {i+1}/{len(chunks)} (scene {scene_idx+1})...")

            # Generate TTS audio
            audio_path = generate_tts_chunk(chunk, i)
            if not audio_path:
                print(f"      ⚠️  TTS failed for chunk {i} — skipping")
                continue

            # Get actual audio duration for perfect sync
            duration = get_audio_duration(audio_path)

            # Render composited frame
            composited, _ = render_text_frame(base_img, chunk, fonts, duration)
            frame_path    = frames_dir / f"frame_{i:04d}.jpg"
            composited.save(str(frame_path), "JPEG", quality=95)

            # Build clip
            clip_path = frames_to_clip(frames_dir, audio_path, i, duration)
            clip_paths.append(clip_path)
            chunks_done += 1

        if not clip_paths:
            print("❌ No clips generated")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        print(f"\n   ✅ {chunks_done}/{len(chunks)} clips rendered")

        # ── Concatenate all clips ───────────────────────────
        print(f"\n🎞  Assembling final video...")
        concat_list = WORK_DIR / "concat.txt"
        with open(concat_list, "w") as f:
            for cp in clip_paths:
                f.write(f"file '{cp}'\n")

        output_path = WORK_DIR / f"ep{EPISODE_NUMBER:03d}_tamil.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy",
            str(output_path)
        ], check=True, timeout=600)

        # ── Upload ─────────────────────────────────────────
        print(f"\n☁️  Uploading video...")
        video_bytes = output_path.read_bytes()
        video_url   = upload_video(f"ep{EPISODE_NUMBER:03d}_tamil.mp4", video_bytes)

        if video_url:
            db_patch("tamil_episodes", EPISODE_NUMBER, {
                "video_url_tamil": video_url,
                "status":          "video_ready"
            })
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — Video ready!")
            print(f"   {video_url}")
            print(f"{'='*60}")
        else:
            print("❌ Upload failed")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
    finally:
        shutil.rmtree(str(WORK_DIR), ignore_errors=True)

if __name__ == "__main__":
    main()
