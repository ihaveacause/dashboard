"""
I Have a Cause — Video Pipeline
================================
- Vertex AI Chirp3 HD Tamil voice (Callirrhoe, female, 0.89x)
- PIL text overlay: 4 lines centered, adaptive color, fade transitions
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
    """Split cleaned script into 4-line display chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        lines = []
        for _ in range(lines_per_chunk):
            if i >= len(words):
                break
            line = ' '.join(words[i:i + words_per_line])
            lines.append(line)
            i += words_per_line
        if lines:
            chunks.append('\n'.join(lines))
    return chunks

# ── Image brightness detection ───────────────────────────────
def detect_brightness(image_path):
    try:
        img = Image.open(image_path).convert('L')
        brightness = float(np.array(img).mean())
        return 'dark' if brightness < 128 else 'light'
    except:
        return 'dark'

# ── Find Tamil font ──────────────────────────────────────────
def find_tamil_font():
    candidates = [
        '/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf',
        '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
        '/usr/share/fonts/noto/NotoSansTamil-Regular.ttf',
        '/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
    ]
    for path in candidates:
        if Path(path).exists():
            print(f"   Font found: {path}")
            return path
    print("   ⚠️  No Tamil font found, using default")
    return None

# ── Render text overlay PNG ──────────────────────────────────
def render_text_png(chunk, brightness, font_path, font_size=40):
    """Render 4-line text block as RGBA PNG centered on frame."""
    frame  = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(frame)

    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except:
        font = ImageFont.load_default()

    lines = chunk.split('\n')
    line_h = font_size + 10
    total_h = len(lines) * line_h
    pad = 22

    # Measure max width
    max_w = 0
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            max_w = max(max_w, bbox[2] - bbox[0])
        except:
            max_w = max(max_w, len(line) * (font_size // 2))

    # Background rectangle
    bg_x1 = (W - max_w) // 2 - pad
    bg_y1 = (H - total_h) // 2 - pad
    bg_x2 = (W + max_w) // 2 + pad
    bg_y2 = (H + total_h) // 2 + pad

    if brightness == 'dark':
        bg_color   = (0,   0,   0,   150)
        text_color = (255, 255, 255, 255)
    else:
        bg_color   = (255, 255, 255, 150)
        text_color = (20,  20,  20,  255)

    draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=bg_color)

    # Draw lines
    y = (H - total_h) // 2
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            lw   = bbox[2] - bbox[0]
        except:
            lw = len(line) * (font_size // 2)
        x = (W - lw) // 2
        draw.text((x, y), line, font=font, fill=text_color)
        y += line_h

    return frame

# ── Create text overlay video ────────────────────────────────
def create_text_overlay(chunks, chunk_dur, brightness_map, image_dur, num_images, out_path):
    """Create RGBA text overlay video — one clip per chunk with fade in/out."""
    print(f"\n📝 Creating text overlay ({len(chunks)} chunks × {chunk_dur:.1f}s)...")
    font_path   = find_tamil_font()
    fade_frames = int(0.5 * FPS)   # 0.5s fade
    clip_paths  = []

    for i, chunk in enumerate(chunks):
        img_idx    = min(int(i * chunk_dur / image_dur), num_images - 1)
        brightness = brightness_map.get(img_idx, 'dark')

        # Render PNG
        png_path = WORK_DIR / f"txt_{i:03d}.png"
        frame    = render_text_png(chunk, brightness, font_path)
        frame.save(str(png_path), 'PNG')

        # Build clip with fade in/out (alpha channel)
        clip_path  = WORK_DIR / f"txt_clip_{i:03d}.webm"
        n_frames   = max(int(chunk_dur * FPS), fade_frames * 3)
        fade_out_s = max(0, chunk_dur - 0.5)

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(png_path),
            "-t", str(chunk_dur),
            "-r", str(FPS),
            "-vf", (
                f"format=yuva420p,"
                f"fade=in:0:{fade_frames}:alpha=1,"
                f"fade=out:st={fade_out_s}:d=0.5:alpha=1"
            ),
            "-c:v", "libvpx-vp9",
            "-auto-alt-ref", "0",
            str(clip_path)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            clip_paths.append(str(clip_path))
            if (i + 1) % 10 == 0:
                print(f"   ✅ Text clips: {i+1}/{len(chunks)}")
        else:
            print(f"   ⚠️  Text clip {i} failed: {r.stderr[-100:]}")

    if not clip_paths:
        return False

    # Concatenate text clips
    concat = WORK_DIR / "txt_list.txt"
    concat.write_text('\n'.join(f"file '{p}'" for p in clip_paths))
    r = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat),
        "-c:v", "libvpx-vp9", "-auto-alt-ref", "0",
        str(out_path)
    ], capture_output=True, text=True)

    if r.returncode == 0:
        print(f"   ✅ Text overlay video ready")
        return True
    print(f"   ❌ Text overlay concat failed: {r.stderr[-200:]}")
    return False

# ── Download SVG as image ────────────────────────────────────
def download_svg(svg_data):
    """Download infographic SVG/PNG and return local path."""
    if not svg_data:
        return None
    url = None
    if isinstance(svg_data, dict):
        url = svg_data.get('url')
    elif isinstance(svg_data, str):
        try:
            url = json.loads(svg_data).get('url')
        except:
            url = svg_data

    if not url:
        return None

    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None

        content_type = r.headers.get('content-type', '')
        out_path = WORK_DIR / "infographic.png"

        if 'svg' in content_type or url.lower().endswith('.svg'):
            try:
                import cairosvg
                cairosvg.svg2png(
                    bytestring=r.content,
                    write_to=str(out_path),
                    output_width=W,
                    output_height=H
                )
            except Exception as e:
                print(f"   ⚠️  SVG convert failed: {e}")
                return None
        else:
            out_path.write_bytes(r.content)

        print(f"   ✅ Infographic downloaded")
        return str(out_path)
    except Exception as e:
        print(f"   ⚠️  Infographic download failed: {e}")
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
                print(f"   ⚠️  Attempt {attempt+1} failed: {str(e)[:80]}")
                time.sleep(wait)
            else:
                raise

# ── Generate Tamil voice ─────────────────────────────────────
def generate_voice(script_text, output_path):
    print(f"\n🎙  Generating voice (Callirrhoe, 0.89x)...")
    full_text = clean_script(script_text)
    print(f"   Cleaned script: {len(full_text)} chars")

    chunks, current, current_len = [], [], 0
    for word in full_text.split():
        word_len = len(word.encode('utf-8')) + 1
        if current_len + word_len >= 4000 and current:
            chunks.append(' '.join(current))
            current, current_len = [], 0
        current.append(word)
        current_len += word_len
    if current:
        chunks.append(' '.join(current))

    print(f"   TTS chunks: {len(chunks)}")
    token, url, chunk_files = get_token(), "https://texttospeech.googleapis.com/v1/text:synthesize", []

    for i, chunk in enumerate(chunks):
        payload = {
            "input": {"text": chunk},
            "voice": {"languageCode": "ta-IN", "name": "ta-IN-Chirp3-HD-Callirrhoe"},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.89}
        }

        def _call(chunk=chunk):
            r = requests.post(url, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json"
            }, json=payload, timeout=60)
            if r.status_code == 200:
                return base64.b64decode(r.json()["audioContent"])
            if r.status_code == 400:
                raise ValueError(f"TTS config error: {r.text[:200]}")
            raise Exception(f"TTS error {r.status_code}: {r.text[:200]}")

        try:
            audio    = with_retry(_call)
            cp       = WORK_DIR / f"chunk_{i:03d}.mp3"
            cp.write_bytes(audio)
            chunk_files.append(str(cp))
            print(f"   ✅ Chunk {i+1}/{len(chunks)}")
        except ValueError:
            print(f"   ↩️  Fallback to WaveNet for chunk {i+1}")
            payload["voice"] = {"languageCode": "ta-IN", "name": "ta-IN-Wavenet-A", "ssmlGender": "FEMALE"}
            try:
                audio = with_retry(_call)
                cp    = WORK_DIR / f"chunk_{i:03d}.mp3"
                cp.write_bytes(audio)
                chunk_files.append(str(cp))
            except Exception as e:
                print(f"   ⚠️  Chunk {i+1} failed: {e}")
        except Exception as e:
            print(f"   ⚠️  Chunk {i+1} failed: {e}")

    if not chunk_files:
        return False

    if len(chunk_files) == 1:
        shutil.copy(chunk_files[0], str(output_path))
    else:
        lst = WORK_DIR / "audio_list.txt"
        lst.write_text('\n'.join(f"file '{p}'" for p in chunk_files))
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

# ── Build one image clip with pan/zoom ───────────────────────
def build_image_clip(img_path, duration, pan_dir, out_path):
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
        "ffmpeg", "-y", "-loop", "1", "-i", str(img_path),
        "-vf", vf, "-t", str(duration), "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p", str(out_path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # static fallback
        cmd2 = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(img_path),
            "-vf", f"scale={W}:{H}", "-t", str(duration), "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", str(out_path)
        ]
        subprocess.run(cmd2, capture_output=True)
    return out_path.exists()

# ── Assemble final video ─────────────────────────────────────
def assemble_video(image_paths, svg_path, audio_path, script_text, output_path):
    print(f"\n🎬 Assembling video...")
    audio_dur = get_duration(audio_path)

    # Build scene list: content images + SVG near end
    scene_paths = list(image_paths)
    if svg_path:
        # Insert SVG as second-to-last scene
        scene_paths.insert(-1, svg_path)
        print(f"   ✅ SVG inserted as scene {len(scene_paths)-1}")

    num_scenes = len(scene_paths)
    dur_each   = audio_dur / num_scenes

    print(f"   Audio: {audio_dur:.1f}s | {num_scenes} scenes × {dur_each:.1f}s")

    pan_dirs = [
        (0, 0, 1, 1), (1, 1, 0, 0), (0, 1, 1, 0),
        (1, 0, 0, 1), (0.5, 0, 0.5, 1),
    ]

    # ── Step 1: Brightness map ───────────────────────────────
    brightness_map = {}
    for i, sp in enumerate(scene_paths):
        brightness_map[i] = detect_brightness(sp)
    print(f"   Brightness detected for {num_scenes} scenes")

    # ── Step 2: Image pan/zoom clips ─────────────────────────
    clip_paths = []
    for i, sp in enumerate(scene_paths):
        out = WORK_DIR / f"clip_{i:02d}.mp4"
        ok  = build_image_clip(sp, dur_each, pan_dirs[i % len(pan_dirs)], out)
        if ok:
            clip_paths.append(str(out))
            print(f"   ✅ Scene clip {i+1}/{num_scenes}")

    if not clip_paths:
        return False

    # ── Step 3: Concatenate image clips → raw.mp4 ────────────
    raw = WORK_DIR / "raw.mp4"
    concat = WORK_DIR / "clips.txt"
    concat.write_text('\n'.join(f"file '{p}'" for p in clip_paths))
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", str(raw)
    ], capture_output=True)

    # ── Step 4: Add audio → raw_audio.mp4 ────────────────────
    raw_audio = WORK_DIR / "raw_audio.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(raw), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(raw_audio)
    ], capture_output=True)

    # ── Step 5: Text overlay ──────────────────────────────────
    cleaned      = clean_script(script_text)
    chunks       = split_into_chunks(cleaned)
    chunk_dur    = audio_dur / max(len(chunks), 1)
    overlay_path = WORK_DIR / "text_overlay.webm"

    ok_overlay = create_text_overlay(
        chunks, chunk_dur, brightness_map,
        dur_each, num_scenes, overlay_path
    )

    # ── Step 6: Composite text onto video ────────────────────
    if ok_overlay and overlay_path.exists():
        print(f"\n   🖼  Compositing text overlay...")
        r = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(raw_audio),
            "-i", str(overlay_path),
            "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-c:a", "copy", "-movflags", "+faststart",
            str(output_path)
        ], capture_output=True, text=True)

        if r.returncode == 0:
            mb = Path(output_path).stat().st_size / 1024 / 1024
            print(f"   ✅ Final video with text overlay: {mb:.1f} MB")
            return True
        else:
            print(f"   ⚠️  Overlay failed, using video without text: {r.stderr[-200:]}")

    # Fallback: use video without text overlay
    shutil.copy(str(raw_audio), str(output_path))
    mb = Path(output_path).stat().st_size / 1024 / 1024
    print(f"   ✅ Final video (no overlay fallback): {mb:.1f} MB")
    return True

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
        # Images
        image_paths = download_images(episode["image_urls"])
        if not image_paths:
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        # SVG infographic
        print(f"\n🖼  Downloading SVG infographic...")
        svg_path = download_svg(episode.get("infographic_svg"))
        if not svg_path:
            print("   ℹ️  No infographic — skipping SVG scene")

        # Voice
        script     = episode.get("script_tamil") or episode.get("title_tamil", "")
        audio_path = WORK_DIR / "narration.mp3"
        ok_voice   = generate_voice(script, audio_path)

        if not ok_voice or not audio_path.exists():
            print("❌ Voice generation failed")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        # Assemble
        video_path = WORK_DIR / f"ep{EPISODE_NUMBER:03d}_tamil.mp4"
        ok_video   = assemble_video(
            image_paths, svg_path, audio_path, script, video_path
        )

        if not ok_video:
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        # Upload
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
