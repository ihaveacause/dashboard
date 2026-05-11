"""
I Have a Cause — Video Pipeline
================================
- Vertex AI Chirp3 HD Tamil voice (Callirrhoe, female, 0.89x)
- PIL composites text directly onto image frames (no FFmpeg overlay)
- 4-line centered text, font size 20, adaptive color
- SVG infographic as second-to-last scene
- FFmpeg pan/zoom + audio assembly
"""

import os
import re
import json
import base64
import requests
import subprocess
import tempfile
import shutil
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

W, H, FPS = 1280, 720, 24

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

def upload_video(path, storage_path):
    mb = Path(path).stat().st_size / 1024 / 1024
    print(f"   Uploading {mb:.1f} MB...")
    with open(path, "rb") as f:
        data = f.read()
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/episode-videos/{storage_path}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "video/mp4",
            "x-upsert":      "true"
        },
        data=data, timeout=600
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/episode-videos/{storage_path}"
    print(f"   ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

def fetch_episode():
    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"
    })
    return rows[0] if rows else None

# ── Clean script for TTS ─────────────────────────────────────
def clean_script(text):
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}([^_]+)_{1,2}',   r'\1', text)
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'_+',  '', text)
    text = re.sub(r'\s+[-–—]\s+', ', ', text)
    text = re.sub(r'^[A-Z][A-Z\s&/]+\s*\(\d+:\d+[^)]*\)\s*:?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[A-Z][A-Z\s&/]{2,}:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\*\-•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-=]{3,}$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\(([^)]{1,30})\)', r'\1', text)
    text = re.sub(r'\(\d+:\d+(?:\s*[-–]\s*\d+:\d+)?\)', '', text)
    text = re.sub(r'^:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r' {2,}',  ' ',    text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ── Text chunk splitting ─────────────────────────────────────
def split_into_chunks(text, words_per_line=7, lines_per_chunk=4):
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        lines = []
        for _ in range(lines_per_chunk):
            if i >= len(words):
                break
            lines.append(' '.join(words[i:i + words_per_line]))
            i += words_per_line
        if lines:
            chunks.append('\n'.join(lines))
    return chunks

# ── Image brightness detection ───────────────────────────────
def detect_brightness(image_path):
    try:
        img = Image.open(image_path).convert('L')
        return 'dark' if float(np.array(img).mean()) < 128 else 'light'
    except:
        return 'dark'

# ── Find best available font ─────────────────────────────────
def find_font(size=20):
    # Prefer fonts with broad Unicode + Tamil coverage
    candidates = [
        '/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf',
        '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
        '/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf',
        '/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size)
                print(f"   Font: {Path(path).name} @ {size}px")
                return font
            except:
                continue
    print("   ⚠️  Using default PIL font")
    return ImageFont.load_default()

# ── PIL: composite text directly onto image ──────────────────
def composite_text_on_image(image_path, chunk, brightness, font):
    """Open image, draw semi-transparent text block in center, return RGB image."""
    base = Image.open(image_path).convert('RGBA').resize((W, H))

    # Transparent overlay layer
    overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    lines    = chunk.split('\n')
    line_gap = 6
    line_h   = 20 + line_gap   # font size + gap
    total_h  = len(lines) * line_h
    pad      = 14

    # Measure widths
    widths = []
    for line in lines:
        try:
            bb = draw.textbbox((0, 0), line, font=font)
            widths.append(bb[2] - bb[0])
        except:
            widths.append(len(line) * 10)
    max_w = max(widths) if widths else 200

    # Background rect (semi-transparent, tighter)
    cx     = W // 2
    cy     = H // 2
    bg_x1  = cx - max_w // 2 - pad
    bg_y1  = cy - total_h // 2 - pad
    bg_x2  = cx + max_w // 2 + pad
    bg_y2  = cy + total_h // 2 + pad

    if brightness == 'dark':
        bg_fill   = (0,   0,   0,   120)   # subtle dark tint
        text_fill = (255, 255, 255, 255)   # white text
    else:
        bg_fill   = (255, 255, 255, 120)   # subtle light tint
        text_fill = (15,  15,  15,  255)   # near-black text

    draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=bg_fill)

    # Draw each line centered
    y = cy - total_h // 2
    for line, lw in zip(lines, widths):
        x = cx - lw // 2
        draw.text((x, y), line, font=font, fill=text_fill)
        y += line_h

    # Composite onto original image — background fully visible
    result = Image.alpha_composite(base, overlay)
    return result.convert('RGB')

# ── Build one video clip from a PIL image ────────────────────
def build_clip(pil_image, duration, pan_dir, out_path):
    """Save PIL image to PNG then create pan/zoom video clip."""
    png = WORK_DIR / f"_tmp_{out_path.stem}.png"
    pil_image.save(str(png))

    zoom   = 1.06
    big_w  = int(W * zoom)
    big_h  = int(H * zoom)
    pad_x  = big_w - W
    pad_y  = big_h - H
    frames = max(int(duration * FPS), 1)
    xs, ys, xe, ye = pan_dir
    x0, y0 = int(xs * pad_x), int(ys * pad_y)
    x1, y1 = int(xe * pad_x), int(ye * pad_y)

    vf = (
        f"scale={big_w}:{big_h},"
        f"crop={W}:{H}:"
        f"'({x0}+({x1}-{x0})*n/{frames})':"
        f"'({y0}+({y1}-{y0})*n/{frames})'"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(png),
        "-vf", vf, "-t", str(duration), "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p", str(out_path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # static fallback
        cmd2 = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(png),
            "-vf", f"scale={W}:{H}", "-t", str(duration), "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", str(out_path)
        ]
        subprocess.run(cmd2, capture_output=True)
    png.unlink(missing_ok=True)
    return out_path.exists()

# ── Download SVG as PNG ──────────────────────────────────────
def download_svg(svg_data):
    if not svg_data:
        return None
    try:
        raw = svg_data if isinstance(svg_data, dict) else json.loads(svg_data)
        url = raw.get('url') if isinstance(raw, dict) else None
    except:
        url = svg_data if isinstance(svg_data, str) and svg_data.startswith('http') else None
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None
        out = WORK_DIR / "infographic.png"
        ct  = r.headers.get('content-type', '')
        if 'svg' in ct or url.lower().endswith('.svg'):
            import cairosvg
            cairosvg.svg2png(bytestring=r.content, write_to=str(out),
                             output_width=W, output_height=H)
        else:
            out.write_bytes(r.content)
        print(f"   ✅ Infographic downloaded")
        return str(out)
    except Exception as e:
        print(f"   ⚠️  Infographic failed: {e}")
        return None

# ── Download episode images ──────────────────────────────────
def download_images(image_urls_json):
    print(f"\n📥 Downloading images...")
    imgs = json.loads(image_urls_json) if isinstance(image_urls_json, str) else image_urls_json
    paths = []
    for img in sorted(imgs, key=lambda x: x.get('id', 0)):
        p = WORK_DIR / f"scene_{img['id']:02d}.jpg"
        try:
            r = requests.get(img["url"], timeout=60)
            if r.status_code == 200:
                p.write_bytes(r.content)
                paths.append(str(p))
                print(f"   ✅ Scene {img['id']}: {img.get('label','')}")
        except Exception as e:
            print(f"   ❌ Scene {img['id']}: {e}")
    return paths

# ── Retry helper ─────────────────────────────────────────────
def with_retry(fn, max_retries=3, wait=15):
    import time
    for attempt in range(max_retries):
        try:
            return fn()
        except ValueError:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"   ⚠️  Attempt {attempt+1}: {str(e)[:80]}")
                time.sleep(wait)
            else:
                raise

# ── Generate Tamil voice ─────────────────────────────────────
def generate_voice(script_text, output_path):
    print(f"\n🎙  Generating voice (Callirrhoe, 0.89x)...")
    full_text = clean_script(script_text)
    print(f"   Cleaned: {len(full_text)} chars")

    chunks, current, current_len = [], [], 0
    for word in full_text.split():
        wl = len(word.encode('utf-8')) + 1
        if current_len + wl >= 4000 and current:
            chunks.append(' '.join(current))
            current, current_len = [], 0
        current.append(word)
        current_len += wl
    if current:
        chunks.append(' '.join(current))

    print(f"   TTS: {len(chunks)} chunks")
    token, url, files = get_token(), "https://texttospeech.googleapis.com/v1/text:synthesize", []

    for i, chunk in enumerate(chunks):
        payload = {
            "input": {"text": chunk},
            "voice": {"languageCode": "ta-IN", "name": "ta-IN-Chirp3-HD-Callirrhoe"},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.89}
        }

        def _call(c=chunk):
            res = requests.post(url, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json"
            }, json=payload, timeout=60)
            if res.status_code == 200:
                return base64.b64decode(res.json()["audioContent"])
            if res.status_code == 400:
                raise ValueError(f"Config error: {res.text[:200]}")
            raise Exception(f"TTS {res.status_code}: {res.text[:200]}")

        try:
            audio = with_retry(_call)
            cp    = WORK_DIR / f"chunk_{i:03d}.mp3"
            cp.write_bytes(audio)
            files.append(str(cp))
            print(f"   ✅ Chunk {i+1}/{len(chunks)}")
        except ValueError:
            payload["voice"] = {"languageCode": "ta-IN", "name": "ta-IN-Wavenet-A", "ssmlGender": "FEMALE"}
            try:
                audio = with_retry(_call)
                cp    = WORK_DIR / f"chunk_{i:03d}.mp3"
                cp.write_bytes(audio)
                files.append(str(cp))
                print(f"   ✅ Chunk {i+1}/{len(chunks)} (WaveNet)")
            except Exception as e:
                print(f"   ⚠️  Chunk {i+1} failed: {e}")
        except Exception as e:
            print(f"   ⚠️  Chunk {i+1} failed: {e}")

    if not files:
        return False
    if len(files) == 1:
        shutil.copy(files[0], str(output_path))
    else:
        lst = WORK_DIR / "audio_list.txt"
        lst.write_text('\n'.join(f"file '{p}'" for p in files))
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(lst), "-c", "copy", str(output_path)
        ], capture_output=True)
    print(f"   ✅ Voice ready")
    return True

# ── Get audio duration ───────────────────────────────────────
def get_duration(path):
    r = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 600.0

# ── Assemble video ───────────────────────────────────────────
def assemble_video(image_paths, svg_path, audio_path, script_text, output_path):
    print(f"\n🎬 Assembling video...")
    audio_dur = get_duration(audio_path)

    # Build scene list — SVG as second-to-last
    scene_paths = list(image_paths)
    if svg_path:
        scene_paths.insert(-1, svg_path)
        print(f"   SVG inserted as scene {len(scene_paths)-1}/{len(scene_paths)}")

    num_scenes = len(scene_paths)
    scene_dur  = audio_dur / num_scenes

    # Text chunks
    cleaned    = clean_script(script_text)
    chunks     = split_into_chunks(cleaned)
    chunk_dur  = audio_dur / max(len(chunks), 1)
    print(f"   {num_scenes} scenes × {scene_dur:.1f}s | {len(chunks)} text chunks × {chunk_dur:.1f}s")

    # Brightness per scene
    brightness_map = {i: detect_brightness(sp) for i, sp in enumerate(scene_paths)}

    # Font
    font = find_font(size=20)

    # Pan directions
    pan_dirs = [
        (0, 0, 1, 1), (1, 1, 0, 0), (0, 1, 1, 0),
        (1, 0, 0, 1), (0.5, 0, 0.5, 1),
    ]

    # ── Create one clip per text chunk ──────────────────────
    # PIL composites text directly on image — background fully visible
    clip_paths = []
    for i, chunk in enumerate(chunks):
        scene_idx  = min(int(i * chunk_dur / scene_dur), num_scenes - 1)
        scene_img  = scene_paths[scene_idx]
        brightness = brightness_map[scene_idx]

        composited = composite_text_on_image(scene_img, chunk, brightness, font)
        clip_out   = WORK_DIR / f"clip_{i:03d}.mp4"
        ok         = build_clip(composited, chunk_dur, pan_dirs[i % len(pan_dirs)], clip_out)
        if ok:
            clip_paths.append(str(clip_out))
        if (i + 1) % 10 == 0 or (i + 1) == len(chunks):
            print(f"   ✅ Clips: {i+1}/{len(chunks)}")

    if not clip_paths:
        return False

    # ── Concatenate clips ────────────────────────────────────
    print(f"\n   🔗 Concatenating {len(clip_paths)} clips...")
    concat = WORK_DIR / "clips.txt"
    concat.write_text('\n'.join(f"file '{p}'" for p in clip_paths))
    raw = WORK_DIR / "raw.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", str(raw)
    ], capture_output=True)

    # ── Add audio ────────────────────────────────────────────
    print(f"   🎵 Adding narration...")
    r = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(raw), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(output_path)
    ], capture_output=True, text=True)

    if r.returncode == 0:
        mb = Path(output_path).stat().st_size / 1024 / 1024
        print(f"   ✅ Final video: {mb:.1f} MB")
        return True

    print(f"   ❌ Assembly failed: {r.stderr[-200:]}")
    return False

# ── Main ─────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Pipeline — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    print(f"\n📖 {episode['title_english']}")

    if not episode.get("image_urls"):
        print("❌ No approved images")
        return

    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "rendering_video"})

    try:
        image_paths = download_images(episode["image_urls"])
        if not image_paths:
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        print(f"\n🖼  Checking SVG infographic...")
        svg_path = download_svg(episode.get("infographic_svg"))
        if not svg_path:
            print("   ℹ️  No infographic")

        script     = episode.get("script_tamil") or episode.get("title_tamil", "")
        audio_path = WORK_DIR / "narration.mp3"
        ok_voice   = generate_voice(script, audio_path)

        if not ok_voice or not audio_path.exists():
            print("❌ Voice generation failed")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        video_path = WORK_DIR / f"ep{EPISODE_NUMBER:03d}_tamil.mp4"
        ok_video   = assemble_video(
            image_paths, svg_path, audio_path, script, video_path
        )

        if not ok_video:
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        print(f"\n☁️  Uploading...")
        storage_path = f"ep{EPISODE_NUMBER:03d}/ep{EPISODE_NUMBER:03d}_tamil.mp4"
        video_url    = upload_video(str(video_path), storage_path)

        if video_url:
            db_patch("tamil_episodes", EPISODE_NUMBER, {
                "video_url": video_url,
                "status":    "video_ready",
            })
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — Video ready!")
            print(f"{'='*60}")
        else:
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
