"""
I Have a Cause — Idea Video Pipeline (Vertex AI Neural2 TTS)
=============================================================
Mirrors video_pipeline.py (AI voice) — NOT video_pipeline_new.py (human voice).
Ideas use AI-generated Neural2 voice since there are no human recordings.
Generates both Tamil and English videos from the idea's scripts.
"""

import os
import json
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
IDEA_ID        = os.environ["IDEA_ID"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"
W, H, FPS  = 1280, 720, 24

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST     = f"{SUPABASE_URL}/rest/v1"
WORK_DIR = Path(tempfile.mkdtemp(prefix="ihac_idea_video_"))

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
def db_get_idea():
    r = requests.get(
        f"{REST}/ideas",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params={"id": f"eq.{IDEA_ID}", "select": "*"}, timeout=15
    )
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch_idea(data):
    r = requests.patch(
        f"{REST}/ideas?id=eq.{IDEA_ID}",
        headers=SB_HEADERS, json=data, timeout=15
    )
    if r.status_code not in (200, 204):
        print(f"   ⚠️  db_patch failed: {r.status_code} {r.text[:200]}")
    return r.status_code in (200, 204)

def upload_video(filename, data_bytes):
    idea_slug = IDEA_ID[:8]
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/episode-videos/ideas/{idea_slug}/{filename}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "video/mp4",
            "x-upsert":      "true"
        },
        data=data_bytes, timeout=600
    )
    idea_slug = IDEA_ID[:8]
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/episode-videos/ideas/{idea_slug}/{filename}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

# ── Download images ─────────────────────────────────────────
def download_images(image_urls_json):
    print(f"\n📥 Downloading images...")
    imgs  = json.loads(image_urls_json) if isinstance(image_urls_json, str) else image_urls_json
    paths = []
    for img in sorted(imgs, key=lambda x: x.get("id", 0)):
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

# ── Neural2 TTS ─────────────────────────────────────────────
def generate_voice_neural2(script_text, language, output_path):
    """Generate AI voice using Vertex Neural2 TTS — same as video_pipeline.py"""
    print(f"\n🎙  Generating {language} voice with Vertex Neural2...")

    # Clean script
    clean_lines = []
    for line in script_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line[:15] and len(line) > 20:
            content = line.split(":", 1)[1].strip()
            if content:
                clean_lines.append(content)
        else:
            clean_lines.append(line)
    full_text = " ".join(clean_lines)

    # Chunk into 4500 byte pieces
    chunks = []
    words  = full_text.split()
    current, current_len = [], 0
    for word in words:
        current_len += len(word.encode("utf-8")) + 1
        current.append(word)
        if current_len >= 4000:
            chunks.append(" ".join(current))
            current, current_len = [], 0
    if current:
        chunks.append(" ".join(current))

    print(f"   Script → {len(chunks)} chunks")

    token = get_token()
    url   = "https://texttospeech.googleapis.com/v1/text:synthesize"

    # Voice config per language
    if language == "tamil":
        voice_cfg = {"languageCode": "ta-IN", "name": "ta-IN-Neural2-A", "ssmlGender": "MALE"}
    else:
        voice_cfg = {"languageCode": "en-IN", "name": "en-IN-Neural2-A", "ssmlGender": "MALE"}

    chunk_files = []
    for i, chunk in enumerate(chunks):
        payload = {
            "input": {"text": chunk},
            "voice": voice_cfg,
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate":  0.95,
                "pitch":         -1.0
            }
        }
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=60
        )
        if r.status_code == 200:
            audio_bytes = base64.b64decode(r.json()["audioContent"])
            chunk_path  = WORK_DIR / f"chunk_{language}_{i:03d}.mp3"
            chunk_path.write_bytes(audio_bytes)
            chunk_files.append(str(chunk_path))
            print(f"   ✅ Chunk {i+1}/{len(chunks)}")
        else:
            print(f"   ⚠️  Chunk {i+1} failed: {r.text[:200]}")

    if not chunk_files:
        return False

    if len(chunk_files) == 1:
        shutil.copy(chunk_files[0], str(output_path))
    else:
        concat = WORK_DIR / f"audio_list_{language}.txt"
        concat.write_text("\n".join(f"file '{p}'" for p in chunk_files))
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat), "-c", "copy", str(output_path)
        ], capture_output=True)

    print(f"   ✅ Voice ready")
    return True

# ── Audio duration ──────────────────────────────────────────
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
    print(f"   Audio: {audio_dur:.1f}s | {num} images × {dur_each:.1f}s")

    pan_dirs = [
        (0, 0, 1, 1), (1, 1, 0, 0), (0, 1, 1, 0),
        (1, 0, 0, 1), (0.5, 0, 0.5, 1)
    ]
    zoom  = 1.06
    big_w = int(W * zoom)
    big_h = int(H * zoom)
    pad_x = big_w - W
    pad_y = big_h - H
    clip_paths = []

    for i, img in enumerate(image_paths):
        out    = WORK_DIR / f"clip_{i:02d}.mp4"
        frames = int(dur_each * FPS)
        xs, ys, xe, ye = pan_dirs[i % len(pan_dirs)]
        x0, y0 = int(xs * pad_x), int(ys * pad_y)
        x1, y1 = int(xe * pad_x), int(ye * pad_y)
        vf = (
            f"scale={big_w}:{big_h},"
            f"crop={W}:{H}:"
            f"'({x0}+({x1}-{x0})*n/{frames})':"
            f"'({y0}+({y1}-{y0})*n/{frames})'"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(img),
            "-vf", vf, "-t", str(dur_each), "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "26", "-pix_fmt", "yuv420p", str(out)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            clip_paths.append(str(out))
            print(f"   ✅ Clip {i+1}/{num}")
        else:
            # Static fallback
            cmd2 = [
                "ffmpeg", "-y", "-loop", "1", "-i", str(img),
                "-vf", f"scale={W}:{H}", "-t", str(dur_each),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "26", "-pix_fmt", "yuv420p", str(out)
            ]
            r2 = subprocess.run(cmd2, capture_output=True)
            if r2.returncode == 0:
                clip_paths.append(str(out))
                print(f"   ✅ Clip {i+1}/{num} (static fallback)")

    if not clip_paths:
        return False

    concat = WORK_DIR / "clips.txt"
    concat.write_text("\n".join(f"file '{p}'" for p in clip_paths))
    raw = WORK_DIR / "raw.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", str(raw)
    ], capture_output=True)

    r = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(raw), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(output_path)
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
    print(f"🎬 Idea Video Pipeline (Neural2 TTS) — {IDEA_ID}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    idea = db_get_idea()
    if not idea:
        print(f"❌ Idea {IDEA_ID} not found")
        return

    print(f"\n📖 {idea.get('title', '')}")

    if not idea.get("image_urls_landscape"):
        print("❌ No approved images — run idea image pipeline first")
        return

    db_patch_idea({"status": "rendering_video"})

    try:
        image_paths = download_images(idea["image_urls_landscape"])
        if not image_paths:
            db_patch_idea({"status": "images_approved"})
            return

        # ── Tamil video ─────────────────────────────────
        tamil_url   = None
        tamil_script = idea.get("script_tamil", "")
        if tamil_script:
            audio_path = WORK_DIR / "narration_tamil.mp3"
            ok_voice   = generate_voice_neural2(tamil_script, "tamil", audio_path)
            if ok_voice:
                video_path = WORK_DIR / "idea_tamil.mp4"
                ok_video   = assemble_video(image_paths, audio_path, video_path)
                if ok_video:
                    print(f"\n☁️  Uploading Tamil video...")
                    tamil_url = upload_video("idea_tamil.mp4", video_path.read_bytes())
                    print(f"   {'✅' if tamil_url else '❌'} Tamil upload")

        # ── English video ───────────────────────────────
        english_url    = None
        english_script = idea.get("script_english", "")
        if english_script:
            audio_path = WORK_DIR / "narration_english.mp3"
            ok_voice   = generate_voice_neural2(english_script, "english", audio_path)
            if ok_voice:
                video_path = WORK_DIR / "idea_english.mp4"
                ok_video   = assemble_video(image_paths, audio_path, video_path)
                if ok_video:
                    print(f"\n☁️  Uploading English video...")
                    english_url = upload_video("idea_english.mp4", video_path.read_bytes())
                    print(f"   {'✅' if english_url else '❌'} English upload")

        if tamil_url or english_url:
            db_patch_idea({
                "video_url_long": tamil_url or english_url,
                "status":         "video_ready",
            })
            print(f"\n{'='*60}")
            print(f"✅ Idea — Videos ready for review!")
            if tamil_url:   print(f"   🇮🇳 Tamil  : {tamil_url}")
            if english_url: print(f"   🇬🇧 English: {english_url}")
            print(f"{'='*60}")
        else:
            db_patch_idea({"status": "images_approved"})

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch_idea({"status": "images_approved"})
    finally:
        shutil.rmtree(str(WORK_DIR), ignore_errors=True)

if __name__ == "__main__":
    main()
