"""
shorts_video_pipeline.py — Sprint 8
=====================================
Generates a short vertical video (1080×1920) for YouTube Shorts + Instagram Reels.
- AI voice via Google Cloud TTS Neural2
- Vertical images from image_urls_vertical
- FFmpeg renders each image + crossfade transitions
- Saves video_url_short to Supabase
- Sets status_shorts → video_ready

Triggered by: generate_shorts_video.yml
Env vars: SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON,
          EPISODE_NUMBER (or IDEA_ID), LANGUAGE (ta or en)
"""

import os
import json
import base64
import tempfile
import subprocess
import requests
import time

# ── Auth ──────────────────────────────────────────────────────
CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
creds_path = "/tmp/gcp_creds.json"
with open(creds_path, "w") as f:
    f.write(CREDS_JSON)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials as SACreds

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
EPISODE_NUMBER = os.environ.get("EPISODE_NUMBER")
IDEA_ID        = os.environ.get("IDEA_ID")
LANGUAGE       = os.environ.get("LANGUAGE", "ta")   # ta or en

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

GCS_BUCKET = "ihaveacause-media"

# ── TTS config ─────────────────────────────────────────────────
TTS_VOICES = {
    "ta": {"languageCode": "ta-IN",  "name": "ta-IN-Neural2-A",  "ssmlGender": "FEMALE"},
    "en": {"languageCode": "en-IN",  "name": "en-IN-Neural2-A",  "ssmlGender": "FEMALE"},
}

# ── Helpers ────────────────────────────────────────────────────
def sb_get(table, filters=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{filters}&limit=1", headers=SB_HEADERS)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No row found in {table} with {filters}")
    return data[0]

def sb_patch(table, match_col, match_val, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{match_col}=eq.{match_val}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json=data,
    )
    r.raise_for_status()

def get_gcs_token():
    creds = SACreds.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/devstorage.read_write"]
    )
    creds.refresh(Request())
    return creds.token

def upload_to_gcs(local_path, gcs_path):
    token = get_gcs_token()
    with open(local_path, "rb") as f:
        data = f.read()
    r = requests.put(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o"
        f"?uploadType=media&name={gcs_path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
        data=data,
    )
    r.raise_for_status()
    return f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"

# ── TTS ────────────────────────────────────────────────────────
def synthesize_speech(text, language, output_path):
    creds = SACreds.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())

    voice_cfg = TTS_VOICES[language]
    payload = {
        "input": {"text": text},
        "voice": voice_cfg,
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate": 0.92,
            "pitch": 0.0,
            "effectsProfileId": ["headphone-class-device"],
        },
    }
    r = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json=payload,
    )
    r.raise_for_status()
    audio_b64 = r.json()["audioContent"]
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))
    print(f"  Audio saved: {output_path} ({os.path.getsize(output_path)} bytes)")

# ── Image download ─────────────────────────────────────────────
def download_images(image_urls, tmpdir):
    paths = []
    for i, img in enumerate(image_urls):
        url = img.get("url") if isinstance(img, dict) else img
        ext = "jpg"
        out_path = os.path.join(tmpdir, f"img_{i:02d}.{ext}")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        paths.append(out_path)
        print(f"  Downloaded image {i+1}: {out_path}")
    return paths

# ── FFmpeg render ──────────────────────────────────────────────
def render_vertical_video(image_paths, audio_path, output_path):
    """
    Renders a 1080×1920 vertical video:
    - Each image shown for equal duration
    - Fade transitions between images
    - Audio track underneath
    """
    # Get audio duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    audio_dur = float(probe.stdout.strip())
    n = len(image_paths)
    per_img = audio_dur / n
    fade_dur = min(0.5, per_img * 0.15)

    print(f"  Audio duration: {audio_dur:.1f}s | {n} images | {per_img:.1f}s each | fade {fade_dur:.2f}s")

    # Build filtergraph
    # Each image scaled to 1080×1920, then faded
    inputs = []
    for path in image_paths:
        inputs += ["-loop", "1", "-t", str(per_img + fade_dur), "-i", path]
    inputs += ["-i", audio_path]

    # Scale filter for each image
    scale_chain = ""
    for i in range(n):
        scale_chain += f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuva420p[v{i}];"

    # Xfade chain
    xfade_chain = "[v0]"
    prev = "v0"
    for i in range(1, n):
        offset = per_img * i - fade_dur * (i - 1)
        out_label = f"xf{i}" if i < n - 1 else "vout"
        xfade_chain += f"[v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset:.3f}[{out_label}];"
        prev = out_label

    if n == 1:
        vout_label = "v0"
        filtergraph = scale_chain.rstrip(";")
        filtergraph += f";[v0]setpts=PTS-STARTPTS[vout]"
        vout_label = "vout"
    else:
        filtergraph = scale_chain + xfade_chain.rstrip(";")
        vout_label = "vout"

    audio_idx = n
    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filtergraph,
            "-map", f"[{vout_label}]",
            "-map", f"{audio_idx}:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("FFmpeg stderr:", result.stderr[-2000:])
        raise RuntimeError("FFmpeg failed")
    print(f"  Video rendered: {output_path} ({os.path.getsize(output_path)//1024}KB)")

# ── Main ───────────────────────────────────────────────────────
def main():
    is_idea = bool(IDEA_ID)
    print(f"Sprint 8 | Shorts Video Pipeline")
    print(f"  Source: {'idea ' + IDEA_ID if is_idea else 'episode ' + EPISODE_NUMBER}")
    print(f"  Language: {LANGUAGE}")

    # 1. Fetch episode or idea
    if is_idea:
        row = sb_get("ideas", f"id=eq.{IDEA_ID}")
        table = "ideas"
        match_col, match_val = "id", IDEA_ID
        status_col = "status_shorts"
        script_col = f"script_shorts_{LANGUAGE}"
    else:
        table = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
        row = sb_get(table, f"episode_number=eq.{EPISODE_NUMBER}")
        match_col, match_val = "episode_number", EPISODE_NUMBER
        status_col = "status_shorts"
        script_col = "script_shorts_tamil" if LANGUAGE == "ta" else "script_shorts_english"

    # 2. Get script
    script = row.get(script_col, "")
    if not script:
        raise ValueError(f"No short script found in column '{script_col}'")
    print(f"  Script length: {len(script)} chars")

    # 3. Get vertical images
    raw_images = row.get("image_urls_vertical") or []
    if isinstance(raw_images, str):
        raw_images = json.loads(raw_images)
    if not raw_images:
        raise ValueError("No vertical images found — run image generation first")
    print(f"  Images: {len(raw_images)} vertical images found")

    with tempfile.TemporaryDirectory() as tmpdir:
        # 4. TTS
        audio_path = os.path.join(tmpdir, "narration.mp3")
        print("Synthesizing speech...")
        synthesize_speech(script, LANGUAGE, audio_path)

        # 5. Download images
        print("Downloading images...")
        image_paths = download_images(raw_images, tmpdir)

        # 6. Render video
        video_path = os.path.join(tmpdir, "short.mp4")
        print("Rendering vertical video...")
        render_vertical_video(image_paths, audio_path, video_path)

        # 7. Upload to GCS
        print("Uploading to GCS...")
        if is_idea:
            gcs_path = f"ideas/{IDEA_ID}/shorts_{LANGUAGE}.mp4"
        else:
            gcs_path = f"episodes/{EPISODE_NUMBER}/shorts_{LANGUAGE}.mp4"
        video_url = upload_to_gcs(video_path, gcs_path)
        print(f"  Uploaded: {video_url}")

    # 8. Save to Supabase
    lang_suffix = "tamil" if LANGUAGE == "ta" else "english"
    patch_data = {
        f"video_url_short_{lang_suffix}": video_url,
        status_col: "video_ready",
    }
    sb_patch(table, match_col, match_val, patch_data)
    print(f"✅ Supabase updated — status_shorts → video_ready")
    print(f"✅ Shorts video done: {video_url}")

if __name__ == "__main__":
    main()
