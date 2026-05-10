"""
I Have a Cause — Video Pipeline (Vertex AI Neural2 + FFmpeg)
=============================================================
Uses Vertex AI Neural2 Tamil voice for natural narration.
FFmpeg assembles images with smooth pan + crossfade.
"""

import os
import json
import asyncio
import base64
import requests
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from google.oauth2 import service_account
import google.auth.transport.requests

# ── Config ─────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"

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
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
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

# ── Download images ─────────────────────────────────────────
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

# ── Generate Tamil voice via Vertex Neural2 ─────────────────
def generate_voice_neural2(script_text, output_path):
    print(f"\n🎙  Generating Tamil voice with Vertex Neural2...")

    # Clean script for TTS
    clean_lines = []
    for line in script_text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line[:15] and len(line) > 20:
            content = line.split(':', 1)[1].strip()
            if content:
                clean_lines.append(content)
        else:
            clean_lines.append(line)

    full_text = ' '.join(clean_lines)

    # Chunk into 4500 byte pieces (Neural2 limit)
    chunks = []
    words  = full_text.split()
    current, current_len = [], 0
    for word in words:
        current_len += len(word.encode('utf-8')) + 1
        current.append(word)
        if current_len >= 4000:
            chunks.append(' '.join(current))
            current, current_len = [], 0
    if current:
        chunks.append(' '.join(current))

    print(f"   Script → {len(chunks)} chunks")

    token = get_token()
    url   = "https://texttospeech.googleapis.com/v1/text:synthesize"
    chunk_files = []

    for i, chunk in enumerate(chunks):
        payload = {
            "input": {"text": chunk},
            "voice": {
                "languageCode": "ta-IN",
                "name": "ta-IN-Neural2-A",  # Natural Tamil Neural2 voice
                "ssmlGender": "MALE"
            },
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate": 0.95,  # Slightly slower for philosophy
                "pitch": -1.0          # Slightly deeper, more authoritative
            }
        }
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json=payload, timeout=60
        )
        if r.status_code == 200:
            audio_bytes = base64.b64decode(r.json()["audioContent"])
            chunk_path  = WORK_DIR / f"chunk_{i:03d}.mp3"
            chunk_path.write_bytes(audio_bytes)
            chunk_files.append(str(chunk_path))
            print(f"   ✅ Chunk {i+1}/{len(chunks)}")
        else:
            print(f"   ⚠️  Chunk {i+1} failed: {r.text[:200]}")

    if not chunk_files:
        return False

    # Concatenate chunks
    if len(chunk_files) == 1:
        shutil.copy(chunk_files[0], str(output_path))
    else:
        concat = WORK_DIR / "audio_list.txt"
        concat.write_text("\n".join(f"file '{p}'" for p in chunk_files))
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat), "-c", "copy", str(output_path)
        ], capture_output=True)

    print(f"   ✅ Voice ready")
    return True

# ── Get audio duration ──────────────────────────────────────
def get_duration(path):
    r = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 600.0

# ── Assemble video ──────────────────────────────────────────
def assemble_video(image_paths, audio_path, output_path):
    print(f"\n🎬 Assembling video...")

    audio_dur = get_duration(audio_path)
    num       = len(image_paths)
    dur_each  = audio_dur / num
    W, H, FPS = 1280, 720, 24

    print(f"   Audio: {audio_dur:.1f}s | {num} images × {dur_each:.1f}s")
    print(f"   Output: {W}×{H} @ {FPS}fps")

    # Pan directions alternate per clip
    pan_dirs = [
        (0, 0, 1, 1),    # top-left → bottom-right
        (1, 1, 0, 0),    # bottom-right → top-left
        (0, 1, 1, 0),    # bottom-left → top-right
        (1, 0, 0, 1),    # top-right → bottom-left
        (0.5, 0, 0.5, 1) # top-center → bottom-center
    ]

    zoom    = 1.06
    big_w   = int(W * zoom)
    big_h   = int(H * zoom)
    pad_x   = big_w - W
    pad_y   = big_h - H
    clip_paths = []

    for i, img in enumerate(image_paths):
        out    = WORK_DIR / f"clip_{i:02d}.mp4"
        frames = int(dur_each * FPS)
        xs, ys, xe, ye = pan_dirs[i % len(pan_dirs)]
        x0, y0 = int(xs * pad_x), int(ys * pad_y)
        x1, y1 = int(xe * pad_x), int(ye * pad_y)

        # Simple crop pan — fast to render
        vf = (
            f"scale={big_w}:{big_h},"
            f"crop={W}:{H}:"
            f"'({x0}+({x1}-{x0})*n/{frames})':"
            f"'({y0}+({y1}-{y0})*n/{frames})'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(img),
            "-vf", vf,
            "-t", str(dur_each),
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "26",
            "-pix_fmt", "yuv420p",
            str(out)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            clip_paths.append(str(out))
            print(f"   ✅ Clip {i+1}/{num}")
        else:
            # Static fallback
            cmd2 = [
                "ffmpeg", "-y", "-loop", "1", "-i", str(img),
                "-vf", f"scale={W}:{H}",
                "-t", str(dur_each), "-r", str(FPS),
                "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "26", "-pix_fmt", "yuv420p", str(out)
            ]
            r2 = subprocess.run(cmd2, capture_output=True)
            if r2.returncode == 0:
                clip_paths.append(str(out))
                print(f"   ✅ Clip {i+1}/{num} (static fallback)")

    if not clip_paths:
        return False

    # Concatenate
    print(f"\n   🔗 Concatenating {len(clip_paths)} clips...")
    concat = WORK_DIR / "clips.txt"
    concat.write_text("\n".join(f"file '{p}'" for p in clip_paths))

    raw = WORK_DIR / "raw.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", str(raw)
    ], capture_output=True)

    # Add audio
    print(f"   🎵 Adding Neural2 narration...")
    r = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(raw),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path)
    ], capture_output=True, text=True)

    if r.returncode == 0:
        mb = Path(output_path).stat().st_size / 1024 / 1024
        print(f"   ✅ Final video: {mb:.1f} MB")
        return True

    print(f"   ❌ Assembly failed: {r.stderr[-300:]}")
    return False

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Pipeline (Vertex AI) — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    print(f"\n📖 {episode['title_english']}")

    if not episode.get("image_urls"):
        print("❌ No approved images — run image pipeline first")
        return

    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "rendering_video"})

    try:
        image_paths = download_images(episode["image_urls"])
        if not image_paths:
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        script     = episode.get("script_tamil") or episode.get("title_tamil", "")
        audio_path = WORK_DIR / "narration.mp3"
        ok_voice   = generate_voice_neural2(script, audio_path)

        if not ok_voice or not audio_path.exists():
            print("❌ Voice generation failed")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        video_path = WORK_DIR / f"ep{EPISODE_NUMBER:03d}_tamil.mp4"
        ok_video   = assemble_video(image_paths, audio_path, video_path)

        if not ok_video:
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        print(f"\n☁️  Uploading to Supabase Storage...")
        storage_path = f"ep{EPISODE_NUMBER:03d}/ep{EPISODE_NUMBER:03d}_tamil.mp4"
        video_url    = upload_video(str(video_path), storage_path)

        if video_url:
            db_patch("tamil_episodes", EPISODE_NUMBER, {
                "video_url": video_url,
                "status":    "video_ready",
            })
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — Video ready for review!")
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
