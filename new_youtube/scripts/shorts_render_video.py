"""
shorts_render_video.py — Sprint 15 (Shorts track)
===================================================
Renders the final 1080x1920 vertical video for ONE short (by id):
- AI voice via Google Cloud TTS Neural2 (same voice config as the channel
  already uses, so the short sounds identical to the long episode)
- The 3 approved vertical images, crossfaded under the narration
- Saves video_url, sets status -> video_ready

Mirrors shorts_video_pipeline.py's TTS + FFmpeg logic exactly — only the
source table/row and GCS path differ.

Triggered by: shorts_render_video.yml
Env vars: SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON,
          SHORT_ID, LANGUAGE (ta or en)
"""

import os
import json
import base64
import tempfile
import subprocess
import requests

CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
creds_path = "/tmp/gcp_creds.json"
with open(creds_path, "w") as f:
    f.write(CREDS_JSON)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials as SACreds

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SHORT_ID     = os.environ["SHORT_ID"]
LANGUAGE     = os.environ.get("LANGUAGE", "ta")

SHORTS_TABLE = "tamil_shorts" if LANGUAGE == "ta" else "english_shorts"

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
GCS_BUCKET = "ihaveacause-media"

TTS_VOICES = {
    "ta": {"languageCode": "ta-IN", "name": "ta-IN-Neural2-A", "ssmlGender": "FEMALE"},
    "en": {"languageCode": "en-IN", "name": "en-IN-Neural2-A", "ssmlGender": "FEMALE"},
}

def sb_get_one(table, params):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB_HEADERS, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No row found in {table} with {params}")
    return data[0]

def sb_patch(table, id_, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{id_}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json=data,
    )
    r.raise_for_status()

def get_gcs_token():
    creds = SACreds.from_service_account_file(creds_path, scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    creds.refresh(Request())
    return creds.token

def upload_to_gcs(local_path, gcs_path):
    token = get_gcs_token()
    with open(local_path, "rb") as f:
        data = f.read()
    r = requests.put(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o?uploadType=media&name={gcs_path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
        data=data,
    )
    r.raise_for_status()
    return f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"

def synthesize_speech(text, language, output_path):
    creds = SACreds.from_service_account_file(creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    voice_cfg = TTS_VOICES[language]
    payload = {
        "input": {"text": text},
        "voice": voice_cfg,
        "audioConfig": {
            "audioEncoding": "MP3", "speakingRate": 0.96, "pitch": 0.0,
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

def download_images(image_urls, tmpdir):
    paths = []
    for i, img in enumerate(image_urls):
        url = img.get("url") if isinstance(img, dict) else img
        out_path = os.path.join(tmpdir, f"img_{i:02d}.jpg")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        paths.append(out_path)
        print(f"  Downloaded image {i+1}: {out_path}")
    return paths

def render_vertical_video(image_paths, audio_path, output_path):
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

    inputs = []
    for path in image_paths:
        inputs += ["-loop", "1", "-t", str(per_img + fade_dur), "-i", path]
    inputs += ["-i", audio_path]

    scale_chain = ""
    for i in range(n):
        scale_chain += f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuva420p[v{i}];"

    if n == 1:
        filtergraph = scale_chain.rstrip(";") + ";[v0]setpts=PTS-STARTPTS[vout]"
        vout_label = "vout"
    else:
        xfade_chain = ""
        for i in range(1, n):
            offset = per_img * i - fade_dur * (i - 1)
            out_label = f"xf{i}" if i < n - 1 else "vout"
            src = "v0" if i == 1 else f"xf{i-1}"
            xfade_chain += f"[{src}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset:.3f}[{out_label}];"
        filtergraph = scale_chain + xfade_chain.rstrip(";")
        vout_label = "vout"

    audio_idx = n
    cmd = (
        ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filtergraph,
            "-map", f"[{vout_label}]", "-map", f"{audio_idx}:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            output_path,
        ]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("FFmpeg stderr:", result.stderr[-2000:])
        raise RuntimeError("FFmpeg failed")
    print(f"  Video rendered: {output_path} ({os.path.getsize(output_path)//1024}KB)")

def main():
    print(f"Sprint 15 | Shorts Render Pipeline — {SHORT_ID} ({LANGUAGE})")

    short = sb_get_one(SHORTS_TABLE, {"id": f"eq.{SHORT_ID}", "select": "*"})

    script = short.get("script", "")
    if not script:
        raise ValueError("No script found on this short")
    print(f"  Script length: {len(script)} chars")

    raw_images = short.get("image_urls_vertical") or []
    if isinstance(raw_images, str):
        raw_images = json.loads(raw_images)
    if not raw_images:
        raise ValueError("No approved vertical images — approve images first")
    print(f"  Images: {len(raw_images)} vertical images found")

    sb_patch(SHORTS_TABLE, SHORT_ID, {"status": "rendering"})

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "narration.mp3")
            print("Synthesizing speech...")
            synthesize_speech(script, LANGUAGE, audio_path)

            print("Downloading images...")
            image_paths = download_images(raw_images, tmpdir)

            video_path = os.path.join(tmpdir, "short.mp4")
            print("Rendering vertical video...")
            render_vertical_video(image_paths, audio_path, video_path)

            print("Uploading to GCS...")
            gcs_path = f"shorts/{short['episode_number']}/{LANGUAGE}_{short['short_index']}.mp4"
            video_url = upload_to_gcs(video_path, gcs_path)
            print(f"  Uploaded: {video_url}")

        sb_patch(SHORTS_TABLE, SHORT_ID, {"video_url": video_url, "status": "video_ready"})
        print(f"✅ Shorts video done: {video_url}")
    except Exception as e:
        sb_patch(SHORTS_TABLE, SHORT_ID, {"status": "images_approved"})
        raise

if __name__ == "__main__":
    main()
