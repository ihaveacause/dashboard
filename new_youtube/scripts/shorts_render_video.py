"""
shorts_render_video.py — Sprint 15 (Shorts track)
===================================================
Renders the final 1080x1920 vertical video for ONE short (by id), all motion
built with FREE local processing — no paid video-generation API, no added
per-short billing beyond what the pipeline already spends on TTS + Imagen:

- AI voice via Google Cloud TTS Chirp 3: HD — uses the SAME voice
  (episode.tts_voice) the parent long episode was rendered with, so the
  short sounds identical to the long video, not a different default voice.
- Each image gets a Ken Burns zoom/pan (direction alternates per image).
- Where a clear subject can be segmented (via rembg, running fully locally —
  no API call, no cost), the subject and background pan at different rates
  for a fake-depth parallax effect. Falls back to plain zoompan for any
  image where segmentation fails, is unavailable, or looks degenerate —
  this NEVER blocks or fails the render.
- The hook line fades onto screen over the first ~4s.
- Saves video_url, sets status -> video_ready

Chirp 3: HD voices do NOT accept speakingRate/pitch/SSML in the request —
sending them causes a 400 Bad Request, so audioConfig only sets audioEncoding.

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

# rembg is optional: if it's missing, fails to import, or its model download
# fails at runtime, parallax is simply skipped for the whole render (falls
# back to plain zoompan on every image) — never breaks the pipeline.
try:
    from rembg import remove as rembg_remove, new_session as rembg_new_session
    from PIL import Image
    import numpy as np
    REMBG_AVAILABLE = True
except Exception as e:
    print(f"  ℹ️  rembg not available ({e}) — parallax disabled, using plain zoompan for all images")
    REMBG_AVAILABLE = False

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SHORT_ID     = os.environ["SHORT_ID"]
LANGUAGE     = os.environ.get("LANGUAGE", "ta")

SHORTS_TABLE  = "tamil_shorts"   if LANGUAGE == "ta" else "english_shorts"
EPISODE_TABLE = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
GCS_BUCKET = "ihaveacause-media"

# Fallback ONLY if the parent episode never had a voice picked/approved.
# Must match DEFAULT_VOICE_TA / DEFAULT_VOICE in index.html.
DEFAULT_VOICE = {
    "ta": "ta-IN-Chirp3-HD-Callirrhoe",
    "en": "en-GB-Chirp3-HD-Charon",
}

def sb_get(table, params):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB_HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def sb_get_one(table, params):
    data = sb_get(table, params)
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
    """Uploads to GCS and returns a 30-day V2 signed URL — NOT a plain public
    URL. The bucket is private (confirmed: a plain storage.googleapis.com/...
    URL returns AccessDenied to an unauthenticated browser), so every other
    working pipeline in this repo (generate_video.py, generate_images.py,
    anchor_render.py, etc.) signs its GCS URLs instead of relying on public
    ACLs. This mirrors that exact, already-proven pattern.
    """
    import base64
    import datetime as dt
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    token = get_gcs_token()
    with open(local_path, "rb") as f:
        data = f.read()
    # GCS JSON API simple upload requires POST — the /upload/storage/v1/b/.../o
    # endpoint has no PUT route mapped, which is why the wrong verb 404s instead
    # of failing auth or bucket-lookup: it never gets that far.
    r = requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o?uploadType=media&name={gcs_path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
        data=data,
    )
    if r.status_code != 200:
        print(f"  ❌ GCS upload error {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    print(f"  ✅ GCS upload complete")

    creds_info = json.loads(CREDS_JSON)
    expiry_ts = int((dt.datetime.utcnow() + dt.timedelta(days=30)).timestamp())
    sts = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    pk = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
    sig = pk.sign(sts.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    esig = requests.utils.quote(base64.b64encode(sig).decode("utf-8"), safe="")
    signed_url = (
        f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
        f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={esig}"
    )
    print(f"  ✅ Signed URL generated (30 days)")
    return signed_url

def get_episode_voice(episode_number):
    """Reads the exact tts_voice the parent long episode was approved with,
    so the short matches it exactly. Falls back to the channel default."""
    rows = sb_get(EPISODE_TABLE, {"episode_number": f"eq.{episode_number}", "select": "tts_voice"})
    voice = (rows[0].get("tts_voice") if rows else None) or DEFAULT_VOICE[LANGUAGE]
    print(f"  Voice: {voice}")
    return voice

def synthesize_speech(text, voice_name, output_path):
    creds = SACreds.from_service_account_file(creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    lang_code = "-".join(voice_name.split("-")[:2])  # e.g. "ta-IN-Chirp3-HD-Callirrhoe" -> "ta-IN"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": lang_code, "name": voice_name},
        # Chirp 3: HD rejects speakingRate/pitch/SSML — audioEncoding only.
        "audioConfig": {"audioEncoding": "MP3"},
    }
    r = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json=payload,
    )
    if r.status_code != 200:
        print(f"  ❌ TTS error {r.status_code}: {r.text[:500]}")
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

_rembg_session = None
_rembg_session_failed = False

def get_rembg_session():
    """Lazily creates the rembg session once per run. If model download or
    init fails (network hiccup, etc.), remembers that and never retries
    within this run — every image just falls back to plain zoompan."""
    global _rembg_session, _rembg_session_failed
    if _rembg_session_failed or not REMBG_AVAILABLE:
        return None
    if _rembg_session is None:
        try:
            _rembg_session = rembg_new_session("u2net")
        except Exception as e:
            print(f"  ⚠️  rembg session init failed ({e}) — parallax disabled for this render")
            _rembg_session_failed = True
            return None
    return _rembg_session

def try_extract_subject(image_path, out_path):
    """Attempts to cut the subject out of image_path into a transparent-
    background RGBA PNG at out_path. Returns True only if segmentation
    succeeded AND looks like a real subject (not near-empty or near-total
    coverage, which usually means the model failed to find a clean subject —
    common on painterly/symbolic/abstract images). Any exception anywhere
    in this function is caught and treated as "skip parallax for this image".
    """
    session = get_rembg_session()
    if session is None:
        return False
    try:
        with open(image_path, "rb") as f:
            input_bytes = f.read()
        out_bytes = rembg_remove(input_bytes, session=session)
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        im = Image.open(out_path).convert("RGBA")
        alpha = np.array(im)[:, :, 3]
        coverage = float((alpha > 10).mean())
        if coverage < 0.03 or coverage > 0.90:
            print(f"    Subject coverage {coverage:.2f} out of usable range — skipping parallax for this image")
            return False
        print(f"    Subject cutout OK (coverage {coverage:.2f})")
        return True
    except Exception as e:
        print(f"    Subject cutout failed ({e}) — skipping parallax for this image")
        return False

# Fonts installed by the workflow (fonts-noto-tamil / fonts-noto-core) — using
# fontconfig family names via drawtext's `font=` (not a hardcoded file path)
# so this doesn't break if the runner's font paths shift.
DRAWTEXT_FONT = {"ta": "Noto Sans Tamil", "en": "Noto Sans"}

def render_vertical_video(image_paths, audio_path, hook_line, output_path, tmpdir, fps=25):
    """
    Renders a 1080x1920 vertical video with FREE motion (no AI video generation,
    no extra API cost — pure local processing on the already-generated Imagen
    stills):
      - Each image gets a slow Ken Burns zoom + pan (direction alternates per
        image so consecutive beats don't feel identical).
      - Where rembg can cleanly cut out a subject, that subject and its
        background pan at different rates for a fake-depth parallax effect.
        Any image where segmentation isn't available/clean just gets plain
        zoompan — never blocks the render.
      - Crossfade between images, same as before.
      - The hook line is punched onto screen over the first ~4s with a quick
        fade in/out.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    audio_dur = float(probe.stdout.strip())
    n = len(image_paths)
    per_img = audio_dur / n
    fade_dur = min(0.5, per_img * 0.15)
    seg_dur = per_img + fade_dur
    frames = max(1, round(seg_dur * fps))

    print(f"  Audio duration: {audio_dur:.1f}s | {n} images | {per_img:.1f}s each | fade {fade_dur:.2f}s")

    # zoompan expressions run per OUTPUT FRAME — use 'on' (frame index), NOT
    # 't' (seconds), which zoompan doesn't expose. Verified against a real render.
    BG_PAN_VARIANTS = [
        ("min(zoom+0.0009,1.18)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)"),
        ("min(zoom+0.0009,1.18)", "iw/2-(iw/zoom/2)+on*1.4",  "ih/2-(ih/zoom/2)"),
        ("min(zoom+0.0009,1.18)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)-on*1.0"),
    ]
    # Foreground (subject layer) moves at a visibly different rate/direction
    # from its background — that mismatch IS the parallax effect.
    FG_PAN_VARIANTS = [
        ("min(zoom+0.0016,1.30)", "iw/2-(iw/zoom/2)-on*1.6",  "ih/2-(ih/zoom/2)"),
        ("min(zoom+0.0016,1.30)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)+on*1.3"),
        ("min(zoom+0.0016,1.30)", "iw/2-(iw/zoom/2)-on*1.2",  "ih/2-(ih/zoom/2)-on*0.8"),
    ]

    inputs = []       # flat ffmpeg -i args
    input_count = 0    # logical input index counter (for filter references)
    chain = ""

    for i, path in enumerate(image_paths):
        bg_z, bg_x, bg_y = BG_PAN_VARIANTS[i % len(BG_PAN_VARIANTS)]
        fg_path = os.path.join(tmpdir, f"fg_{i:02d}.png")
        has_subject = try_extract_subject(path, fg_path)

        bg_idx = input_count
        inputs += ["-loop", "1", "-t", str(seg_dur), "-i", path]
        input_count += 1

        if has_subject:
            fg_idx = input_count
            inputs += ["-loop", "1", "-t", str(seg_dur), "-i", fg_path]
            input_count += 1
            fg_z, fg_x, fg_y = FG_PAN_VARIANTS[i % len(FG_PAN_VARIANTS)]
            chain += (
                f"[{bg_idx}:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
                f"zoompan=z='{bg_z}':x='{bg_x}':y='{bg_y}':d={frames}:s=1080x1920:fps={fps},"
                f"format=yuva420p[bg{i}];"
                f"[{fg_idx}:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
                f"zoompan=z='{fg_z}':x='{fg_x}':y='{fg_y}':d={frames}:s=1080x1920:fps={fps},"
                f"format=yuva420p[fg{i}];"
                f"[bg{i}][fg{i}]overlay=format=auto,setsar=1,format=yuva420p[v{i}];"
            )
        else:
            chain += (
                f"[{bg_idx}:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
                f"zoompan=z='{bg_z}':x='{bg_x}':y='{bg_y}':d={frames}:s=1080x1920:fps={fps},"
                f"setsar=1,format=yuva420p[v{i}];"
            )

    inputs += ["-i", audio_path]
    audio_idx = input_count

    if n == 1:
        filtergraph = chain.rstrip(";") + ";[v0]setpts=PTS-STARTPTS[vbase]"
    else:
        xfade_chain = ""
        for i in range(1, n):
            offset = per_img * i - fade_dur * (i - 1)
            out_label = f"xf{i}" if i < n - 1 else "vbase"
            src = "v0" if i == 1 else f"xf{i-1}"
            xfade_chain += f"[{src}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset:.3f}[{out_label}];"
        filtergraph = chain + xfade_chain.rstrip(";")

    # Hook-line overlay: fade in at 0.3s, hold, fade out by 4s (or 80% of total
    # duration for very short clips), safe-area margins for Shorts UI overlap.
    vout_label = "vout"
    if hook_line and hook_line.strip():
        hook_end = min(4.0, audio_dur * 0.8)
        hook_hold = max(hook_end - 0.6, 0.3)
        lang = LANGUAGE if LANGUAGE in DRAWTEXT_FONT else "en"
        font = DRAWTEXT_FONT[lang]
        textfile = os.path.join(tmpdir, "hook.txt")
        with open(textfile, "w", encoding="utf-8") as f:
            f.write(hook_line.strip())
        alpha_expr = f"if(lt(t,0.3),t/0.3,if(lt(t,{hook_hold}),1,if(lt(t,{hook_end}),({hook_end}-t)/0.6,0)))"
        filtergraph += (
            f";[vbase]drawtext=textfile='{textfile}':font='{font}':fontcolor=white:fontsize=64:"
            f"line_spacing=10:box=1:boxcolor=black@0.45:boxborderw=24:"
            f"x=(w-text_w)/2:y=h*0.62:alpha='{alpha_expr}':enable='lt(t,{hook_end})'[{vout_label}]"
        )
    else:
        filtergraph += f";[vbase]null[{vout_label}]"

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

    voice_name = get_episode_voice(short["episode_number"])

    sb_patch(SHORTS_TABLE, SHORT_ID, {"status": "rendering"})

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "narration.mp3")
            print("Synthesizing speech...")
            synthesize_speech(script, voice_name, audio_path)

            print("Downloading images...")
            image_paths = download_images(raw_images, tmpdir)

            video_path = os.path.join(tmpdir, "short.mp4")
            print("Rendering vertical video (Ken Burns motion + hook overlay)...")
            render_vertical_video(image_paths, audio_path, short.get("hook_line", ""), video_path, tmpdir)

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
