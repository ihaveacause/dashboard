"""
anchor_render.py — On Camera (YouTube Long) Pipeline · Render Step  (Sprint 17)
================================================================================
Simplified render: no beats, no TTS, no Vertex images.

WHAT IT DOES:
  1. Downloads your recording from GCS
  2. For each image overlay (from row.image_overlays):
       - At the specified start time, your video shrinks to PiP (right-middle,
         viewer's perspective)
       - The image fills the full 1280×720 frame behind the PiP
       - After the duration, image fades out, your video returns full screen
  3. If no images → your video passes through as-is (just re-encodes cleanly)
  4. Makes a branded thumbnail
  5. Uploads final render + thumbnail to GCS
  6. Updates Supabase status → rendered

PiP position: right-middle from viewer's perspective
  - PiP size: 320×180 (quarter screen)
  - PiP position: x = W - PIP_W - MARGIN, y = (H - PIP_H) / 2

image_overlays column format (set by the dashboard):
  [
    {"url": "https://...", "start": 45.0, "duration": 8.0, "filename": "image1.jpg"},
    ...
  ]

Env vars:
  SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON
  RECORD_ID, LANGUAGE  (ta | en)
"""

import os
import json
import subprocess
import tempfile
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON  = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
RECORD_ID       = os.environ["RECORD_ID"]
LANGUAGE        = os.environ.get("LANGUAGE", "en")

GCS_BUCKET = "ihaveacause-media"
TABLE      = "tamil_anchor" if LANGUAGE == "ta" else "english_anchor"

# Output dimensions (landscape YouTube long)
W, H, FPS = 1280, 720, 24

# PiP settings — right-middle from viewer's perspective
PIP_W   = 320
PIP_H   = 180
MARGIN  = 24
PIP_X   = W - PIP_W - MARGIN          # right side
PIP_Y   = (H - PIP_H) // 2            # vertical centre

# Crossfade duration for image in/out
FADE    = 0.4   # seconds

# Brand
C_ACCENT  = (90, 220, 168)
OPINION_LABEL = {"ta": "கருத்து", "en": "OPINION"}.get(LANGUAGE, "OPINION")

# ── Supabase ──────────────────────────────────────────────────
SB_HDR = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(rid):
    r = requests.get(f"{REST}/{TABLE}", headers={**SB_HDR, "Prefer": "return=representation"},
                     params={"id": f"eq.{rid}", "select": "*"}, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch(rid, data):
    r = requests.patch(f"{REST}/{TABLE}?id=eq.{rid}", headers=SB_HDR, json=data, timeout=30)
    ok = r.status_code in (200, 204)
    if not ok:
        print(f"   ⚠️  patch {r.status_code}: {r.text[:200]}", flush=True)
    return ok

# ── GCS helpers ───────────────────────────────────────────────
def _gcs_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON),
        scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    creds.refresh(gr.Request())
    return creds.token

def _gcs_media_url(url):
    """Convert an unsigned GCS object URL to the authenticated media endpoint."""
    from urllib.parse import quote
    marker = "storage.googleapis.com/"
    if (marker in url and "Signature=" not in url
            and "X-Goog-Signature" not in url
            and "/storage/v1/b/" not in url
            and "/upload/storage/" not in url):
        rest = url.split(marker, 1)[1]
        if "/" in rest:
            bucket, path = rest.split("/", 1)
            return True, (f"https://storage.googleapis.com/storage/v1/b/"
                          f"{bucket}/o/{quote(path, safe='')}?alt=media")
    return False, url

def download_file(url, dest, desc="file"):
    is_gcs, fetch_url = _gcs_media_url(url)
    token = _gcs_token() if is_gcs else None
    hdrs = {"Authorization": f"Bearer {token}"} if is_gcs else {}
    r = requests.get(fetch_url, headers=hdrs, stream=True, timeout=600)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest)//1024}KB", flush=True)
        return True
    print(f"   ❌ {desc} failed {r.status_code}", flush=True)
    return False

def upload_to_gcs(local_path, gcs_path, content_type="video/mp4", days=30):
    """Upload file to GCS and return a 30-day V2 signed URL."""
    import base64, datetime as dt
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    token = _gcs_token()
    creds_info = json.loads(GCP_CREDS_JSON)
    print(f"   📤 Uploading {os.path.getsize(local_path)//(1024*1024)}MB to GCS…", flush=True)
    with open(local_path, "rb") as f:
        r = requests.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
            params={"uploadType": "media", "name": gcs_path},
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            data=f, timeout=600)
    if r.status_code not in (200, 201):
        print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}", flush=True)
        return None

    expiry_ts = int((dt.datetime.utcnow() + dt.timedelta(days=days)).timestamp())
    sts = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    pk = serialization.load_pem_private_key(
        creds_info["private_key"].encode(), password=None, backend=default_backend())
    sig = pk.sign(sts.encode(), padding.PKCS1v15(), hashes.SHA256())
    esig = requests.utils.quote(base64.b64encode(sig).decode(), safe="")
    signed = (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
              f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={esig}")
    print(f"   ✅ GCS upload done — signed URL generated ({days}d)", flush=True)
    return signed

# ── Video duration ─────────────────────────────────────────────
def get_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 600.0

# ── Thumbnail ─────────────────────────────────────────────────
def make_thumbnail(src_path, src_dur, title, out_path):
    """Extract a frame and brand it with title + OPINION tag."""
    t = max(2.0, min(src_dur * 0.30, src_dur - 2.0)) if src_dur and src_dur > 4 else 2.0
    raw = out_path + ".raw.jpg"
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", src_path,
         "-frames:v", "1", "-q:v", "2", raw],
        capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(raw):
        print(f"   ⚠️  Thumbnail frame extraction failed", flush=True)
        return None

    try:
        TW, TH = W, H
        im = Image.open(raw).convert("RGB")
        scale = max(TW / im.width, TH / im.height)
        im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1), Image.LANCZOS)
        x0 = (im.width - TW) // 2; y0 = (im.height - TH) // 2
        im = im.crop((x0, y0, x0 + TW, y0 + TH)).convert("RGBA")

        overlay = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Dark gradient at bottom
        grad_h = 240
        for i in range(grad_h):
            a = int(190 * (i / grad_h))
            draw.line([(0, TH - grad_h + i), (TW, TH - grad_h + i)], fill=(6, 7, 10, a))
        draw.rectangle([0, TH - 36, TW, TH], fill=(6, 7, 10, 220))

        # Title text
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", 52)
            font_tag   = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", 26)
        except Exception:
            font_title = ImageFont.load_default()
            font_tag   = font_title

        # Wrap title
        words = (title or "On Camera").split()
        lines, cur = [], ""
        for w in words:
            test = (cur + " " + w).strip()
            try:
                tw = draw.textlength(test, font=font_title)
            except Exception:
                tw = len(test) * 30
            if tw > TW - 120 and cur:
                lines.append(cur); cur = w
            else:
                cur = test
        if cur:
            lines.append(cur)
        lines = lines[:2]

        ty = TH - 44 - len(lines) * 64
        for ln in lines:
            draw.text((64, ty), ln, font=font_title, fill=(255, 255, 255, 255))
            ty += 64

        # OPINION tag
        tag_w = 140
        draw.rounded_rectangle([36, 30, 36 + tag_w, 72], radius=18, fill=(200, 60, 60, 230))
        draw.text((56, 40), OPINION_LABEL, font=font_tag, fill=(255, 255, 255, 255))

        final = Image.alpha_composite(im, overlay).convert("RGB")
        final.save(out_path, "JPEG", quality=92)
        print(f"   ✅ Thumbnail: frame @ {t:.1f}s", flush=True)
        return out_path
    except Exception as e:
        print(f"   ⚠️  Thumbnail branding error: {e}", flush=True)
        return raw

# ── FFmpeg render ─────────────────────────────────────────────
def render_video(src_path, image_overlays, out_path):
    """
    Render with PiP effect for each image overlay.
    image_overlays: list of {"local_path": str, "start": float, "duration": float}

    For each image:
      - Image fades in full screen
      - Your video shrinks to PiP (right-middle)
      - At end of duration, image fades out, your video returns full screen

    If no overlays → simple passthrough re-encode.
    """
    if not image_overlays:
        print("   ℹ️  No images — clean passthrough render", flush=True)
        cmd = [
            "ffmpeg", "-y", "-i", src_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            out_path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"   ❌ FFmpeg passthrough failed:\n{r.stderr[-2000:]}", flush=True)
            return False
        print(f"   ✅ Passthrough render: {os.path.getsize(out_path)/1024/1024:.1f}MB", flush=True)
        return True

    print(f"   🎬 Rendering with {len(image_overlays)} image overlay(s)…", flush=True)

    # Build FFmpeg inputs
    # input 0 = source video
    # inputs 1..N = overlay images (looped)
    inputs = ["-i", src_path]
    for ov in image_overlays:
        inputs += ["-loop", "1", "-i", ov["local_path"]]

    # Filter complex:
    # [0:v] scaled to full frame → [base]
    # For each image overlay window:
    #   - scale image to full frame → [imgN]
    #   - blend image over base during window (full screen image)
    #   - scale source video to PiP size → [pipN]
    #   - overlay PiP on top of image during window (right-middle)
    # Result: during image window → image full screen + PiP your video
    #         outside window → your video full screen

    fc_parts = []
    fc_parts.append(
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS},setsar=1,format=yuva420p[basev];"
    )
    # PiP version of source video (constant, we'll enable it only during windows)
    fc_parts.append(
        f"[0:v]scale={PIP_W}:{PIP_H}:force_original_aspect_ratio=increase,"
        f"crop={PIP_W}:{PIP_H},fps={FPS},setsar=1,"
        f"pad={PIP_W}:{PIP_H}:(ow-iw)/2:(oh-ih)/2:black,"
        f"format=yuva420p[pipv];"
    )

    cur = "[basev]"
    for i, ov in enumerate(image_overlays):
        idx   = i + 1
        s     = ov["start"]
        e     = s + ov["duration"]
        img_l = f"[img{i}]"
        mix_l = f"[mix{i}]"
        pip_l = f"[pip_on{i}]"
        out_l = f"[out{i}]"

        # Scale image to full frame
        fc_parts.append(
            f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,format=yuva420p{img_l};"
        )
        # Blend image over current stream during window (full screen image)
        fc_parts.append(
            f"{cur}{img_l}overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{mix_l};"
        )
        # Overlay PiP video on top of mix during window (right-middle)
        fc_parts.append(
            f"{mix_l}[pipv]overlay={PIP_X}:{PIP_Y}:enable='between(t,{s:.3f},{e:.3f})'{out_l};"
        )
        cur = out_l

    # Final format
    fc_parts.append(f"{cur}format=yuv420p[vout]")
    fc = "".join(fc_parts)

    cmd = (
        ["ffmpeg", "-y"] + inputs +
        ["-filter_complex", fc,
         "-map", "[vout]", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart",
         out_path]
    )
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ❌ FFmpeg render failed:\n{r.stderr[-3000:]}", flush=True)
        return False
    print(f"   ✅ Render done: {os.path.getsize(out_path)/1024/1024:.1f}MB", flush=True)
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print(f"🎥 Anchor Render (Sprint 17) — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
    print(f"   {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    print("=" * 60, flush=True)

    row = db_get(RECORD_ID)
    if not row:
        print("❌ Record not found"); return
    if not row.get("source_video_url"):
        print("❌ No source video URL"); return

    db_patch(RECORD_ID, {"status": "rendering"})

    with tempfile.TemporaryDirectory() as tmp:
        # 1) Download source video
        src = os.path.join(tmp, "source.mp4")
        if not download_file(row["source_video_url"], src, "source video"):
            db_patch(RECORD_ID, {"status": "error"}); return

        src_dur = get_duration(src)
        print(f"   ⏱  Source duration: {src_dur:.1f}s", flush=True)

        # 2) Download image overlays
        raw_overlays = row.get("image_overlays") or []
        if isinstance(raw_overlays, str):
            try:
                raw_overlays = json.loads(raw_overlays)
            except Exception:
                raw_overlays = []

        image_overlays = []
        for i, ov in enumerate(raw_overlays):
            url = ov.get("url")
            start = float(ov.get("start", 0))
            duration = float(ov.get("duration", 5))
            if not url:
                print(f"   ⚠️  Image {i+1}: no URL, skipping", flush=True)
                continue
            if start >= src_dur:
                print(f"   ⚠️  Image {i+1}: start {start}s >= video duration {src_dur:.1f}s, skipping", flush=True)
                continue
            # Cap duration so it doesn't run past end
            duration = min(duration, src_dur - start)
            ext = os.path.splitext(url.split("?")[0])[-1] or ".jpg"
            local_path = os.path.join(tmp, f"img_{i:02d}{ext}")
            if download_file(url, local_path, f"image {i+1}"):
                image_overlays.append({"local_path": local_path, "start": start, "duration": duration})
                print(f"   📸 Image {i+1}: start={start}s duration={duration}s", flush=True)

        print(f"   📊 {len(image_overlays)} image overlay(s) ready", flush=True)

        # 3) Render
        out = os.path.join(tmp, "final.mp4")
        ok = render_video(src, image_overlays, out)
        if not ok:
            db_patch(RECORD_ID, {"status": "error"}); return

        # 4) Thumbnail
        thumb_path = os.path.join(tmp, "thumbnail.jpg")
        title = (row.get("title") or row.get("working_title") or "On Camera").strip()
        thumb_url = None
        thumb = make_thumbnail(src, src_dur, title, thumb_path)
        if thumb:
            print("\n☁️  Uploading thumbnail…", flush=True)
            thumb_url = upload_to_gcs(thumb, f"anchor/{RECORD_ID}/{LANGUAGE}/thumbnail.jpg",
                                      content_type="image/jpeg")

        # 5) Upload render
        print("\n☁️  Uploading final render…", flush=True)
        video_url = upload_to_gcs(out, f"anchor/{RECORD_ID}/{LANGUAGE}/studio_final.mp4")
        if not video_url:
            db_patch(RECORD_ID, {"status": "error"}); return

        # 6) Update Supabase
        updates = {"video_url": video_url, "status": "rendered"}
        if thumb_url:
            updates["thumbnail_url"] = thumb_url
        db_patch(RECORD_ID, updates)

    print(f"\n{'='*60}", flush=True)
    print(f"✅ Render complete — preview in dashboard, then Publish.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
