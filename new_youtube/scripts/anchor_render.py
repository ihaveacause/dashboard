"""
anchor_render.py — On Camera (YouTube Long) Pipeline · Render Step  (Sprint 18)
================================================================================
Sprint 17: PiP image overlays, no beats/TTS/Vertex
Sprint 18 additions (all passive — apply on every render automatically):
  1. Logo downloaded from GCS — ihaveacause_logo.png
       - Thumbnail : 2× size (60px), 100% opacity, bottom-right
       - Video     : 1.5× size (50px), 40% opacity, bottom-right watermark
  2. Audio normalization  — FFmpeg loudnorm (even out loud/soft speaking)
  3. Noise reduction      — FFmpeg afftdn  (remove background hum)
  4. Brightness/contrast  — FFmpeg eq      (subtle auto-levels)
  5. Sharpening           — FFmpeg unsharp (subtle, improves webcam/phone footage)

Everything else from Sprint 17 is unchanged.

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
PIP_X   = W - PIP_W - MARGIN
PIP_Y   = (H - PIP_H) // 2

# ── Logo settings (Sprint 18) ─────────────────────────────────
LOGO_GCS_PATH     = "ihaveacause_logo.png"          # root of bucket
LOGO_SIZE_THUMB   = 60                              # 2× — thumbnail (bottom-right)
LOGO_SIZE_VIDEO   = 50                              # 1.5× — video watermark (bottom-right)
LOGO_OPACITY_VIDEO = 100                            # 40% opacity → 102/255
LOGO_MARGIN       = 20                              # px from edge

# ── Audio/Video enhancement settings (Sprint 18) ─────────────
# All subtle — improve quality without artifacts
# loudnorm: I=-16 integrated loudness target, TP=-1.5 true peak, LRA=11
AUDIO_FILTERS = "afftdn=nf=-25,loudnorm=I=-16:TP=-1.5:LRA=11"
# eq: slight brightness lift + contrast boost; unsharp: mild sharpen
VIDEO_ENHANCE = "eq=brightness=0.03:contrast=1.05,unsharp=5:5:0.8:3:3:0"

# Brand
C_ACCENT      = (90, 220, 168)
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
    hdrs  = {"Authorization": f"Bearer {token}"} if is_gcs else {}
    r = requests.get(fetch_url, headers=hdrs, stream=True, timeout=600)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest)//1024}KB", flush=True)
        return True
    print(f"   ❌ {desc} failed {r.status_code}", flush=True)
    return False

def download_gcs_object(gcs_path, dest, desc="file"):
    """Download a GCS object by its bucket path (not a full URL)."""
    from urllib.parse import quote
    token    = _gcs_token()
    encoded  = quote(gcs_path, safe="")
    fetch_url = (f"https://storage.googleapis.com/storage/v1/b/"
                 f"{GCS_BUCKET}/o/{encoded}?alt=media")
    r = requests.get(fetch_url, headers={"Authorization": f"Bearer {token}"},
                     stream=True, timeout=60)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest)//1024}KB", flush=True)
        return True
    print(f"   ⚠️  {desc} not found ({r.status_code}) — continuing without logo", flush=True)
    return False

def upload_to_gcs(local_path, gcs_path, content_type="video/mp4", days=30):
    import base64, datetime as dt
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    token      = _gcs_token()
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
    pk  = serialization.load_pem_private_key(
        creds_info["private_key"].encode(), password=None, backend=default_backend())
    sig = pk.sign(sts.encode(), padding.PKCS1v15(), hashes.SHA256())
    esig = requests.utils.quote(base64.b64encode(sig).decode(), safe="")
    signed = (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
              f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={esig}")
    print(f"   ✅ GCS upload done ({days}d signed URL)", flush=True)
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

# ── Logo loader ───────────────────────────────────────────────
def load_logo(tmp, size, opacity=255):
    """
    Download logo from GCS, resize to `size`×`size`, apply opacity.
    Returns PIL RGBA Image or None if logo unavailable.
    opacity: 0-255 (255 = fully opaque, 102 = 40%)
    """
    logo_path = os.path.join(tmp, "logo.png")
    if not download_gcs_object(LOGO_GCS_PATH, logo_path, "logo"):
        return None
    try:
        logo = Image.open(logo_path).convert("RGBA")
        logo = logo.resize((size, size), Image.LANCZOS)
        if opacity < 255:
            r, g, b, a = logo.split()
            a = a.point(lambda x: int(x * opacity / 255))
            logo = Image.merge("RGBA", (r, g, b, a))
        print(f"   ✅ Logo loaded: {size}px opacity={opacity}/255", flush=True)
        return logo
    except Exception as e:
        print(f"   ⚠️  Logo load error: {e}", flush=True)
        return None

# ── Thumbnail ─────────────────────────────────────────────────
def make_thumbnail(src_path, src_dur, title, out_path, logo_im=None):
    """
    Extract a frame, brand it with title + OPINION tag + logo (2×, bottom-right).
    logo_im: PIL RGBA image at LOGO_SIZE_THUMB, 100% opacity
    """
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
        draw    = ImageDraw.Draw(overlay)

        # Dark gradient at bottom
        grad_h = 240
        for i in range(grad_h):
            a = int(190 * (i / grad_h))
            draw.line([(0, TH - grad_h + i), (TW, TH - grad_h + i)], fill=(6, 7, 10, a))
        draw.rectangle([0, TH - 36, TW, TH], fill=(6, 7, 10, 220))

        # Fonts
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", 52)
            font_tag   = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", 26)
        except Exception:
            font_title = ImageFont.load_default()
            font_tag   = font_title

        # Title text (max 2 lines)
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

        final = Image.alpha_composite(im, overlay).convert("RGBA")

        # Logo — 2× size, bottom-right, 100% opacity (Sprint 18)
        if logo_im is not None:
            lx = TW - LOGO_SIZE_THUMB - LOGO_MARGIN
            ly = TH - LOGO_SIZE_THUMB - LOGO_MARGIN
            final.paste(logo_im, (lx, ly), logo_im)
            print(f"   ✅ Logo placed on thumbnail at ({lx},{ly})", flush=True)

        final.convert("RGB").save(out_path, "JPEG", quality=92)
        print(f"   ✅ Thumbnail: frame @ {t:.1f}s", flush=True)
        return out_path
    except Exception as e:
        print(f"   ⚠️  Thumbnail branding error: {e}", flush=True)
        return raw

# ── Build watermark PNG ───────────────────────────────────────
def make_watermark_png(logo_im, out_path):
    """
    Create a full-frame transparent PNG with logo at bottom-right.
    This is overlaid on every frame of the video via FFmpeg.
    logo_im: PIL RGBA image at LOGO_SIZE_VIDEO, 40% opacity
    """
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    lx = W - LOGO_SIZE_VIDEO - LOGO_MARGIN
    ly = H - LOGO_SIZE_VIDEO - LOGO_MARGIN
    canvas.paste(logo_im, (lx, ly), logo_im)
    canvas.save(out_path, "PNG")
    print(f"   ✅ Watermark PNG: logo at ({lx},{ly}) size={LOGO_SIZE_VIDEO}px opacity=40%", flush=True)
    return out_path

# ── FFmpeg render ─────────────────────────────────────────────
def render_video(src_path, image_overlays, out_path, watermark_png=None):
    """
    Sprint 17: PiP image overlays (full screen image + PiP your video)
    Sprint 18: + watermark overlay on every frame
               + audio normalization + noise reduction
               + brightness/contrast + sharpening

    watermark_png: path to full-frame transparent PNG with logo (or None)
    """
    # ── Audio filters (Sprint 18) ─────────────────────────────
    # afftdn: noise reduction | loudnorm: normalize volume
    audio_filter = AUDIO_FILTERS

    # ── Video base filter (Sprint 18) ────────────────────────
    # Scale + crop + fps + eq (brightness/contrast) + unsharp (sharpen)
    base_vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS},setsar=1,"
        f"{VIDEO_ENHANCE}"
    )

    # ── No images — passthrough with enhancements ────────────
    if not image_overlays and watermark_png is None:
        print("   ℹ️  No images, no watermark — enhanced passthrough render", flush=True)
        cmd = [
            "ffmpeg", "-y", "-i", src_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-vf", base_vf,
            "-af", audio_filter,
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            out_path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"   ❌ FFmpeg failed:\n{r.stderr[-2000:]}", flush=True)
            return False
        print(f"   ✅ Render: {os.path.getsize(out_path)/1024/1024:.1f}MB", flush=True)
        return True

    # ── No images — passthrough with watermark + enhancements ─
    if not image_overlays and watermark_png is not None:
        print("   ℹ️  No images — enhanced render with watermark", flush=True)
        cmd = [
            "ffmpeg", "-y",
            "-i", src_path,
            "-loop", "1", "-i", watermark_png,
            "-filter_complex",
            f"[0:v]{base_vf}[base];"
            f"[base][1:v]overlay=0:0[vout];"
            f"[0:a]{audio_filter}[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            "-shortest",
            out_path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"   ❌ FFmpeg failed:\n{r.stderr[-2000:]}", flush=True)
            return False
        print(f"   ✅ Render: {os.path.getsize(out_path)/1024/1024:.1f}MB", flush=True)
        return True

    # ── With images: PiP + watermark + enhancements ──────────
    print(f"   🎬 Rendering with {len(image_overlays)} image(s) + watermark…", flush=True)

    # inputs: 0=source, 1..N=images, N+1=watermark (if any)
    inputs = ["-i", src_path]
    for ov in image_overlays:
        inputs += ["-loop", "1", "-i", ov["local_path"]]
    wm_idx = None
    if watermark_png:
        inputs += ["-loop", "1", "-i", watermark_png]
        wm_idx = 1 + len(image_overlays)

    fc_parts = []

    # Base video with enhancements applied
    fc_parts.append(
        f"[0:v]{base_vf},format=yuva420p[basev];"
    )
    # PiP version (also enhanced, shrinks with the video naturally)
    fc_parts.append(
        f"[0:v]{base_vf},"
        f"scale={PIP_W}:{PIP_H}:force_original_aspect_ratio=increase,"
        f"crop={PIP_W}:{PIP_H},"
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
        out_l = f"[out{i}]"

        fc_parts.append(
            f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,format=yuva420p{img_l};"
        )
        fc_parts.append(
            f"{cur}{img_l}overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{mix_l};"
        )
        fc_parts.append(
            f"{mix_l}[pipv]overlay={PIP_X}:{PIP_Y}:enable='between(t,{s:.3f},{e:.3f})'{out_l};"
        )
        cur = out_l

    # Watermark on top of everything (always visible, every frame)
    if wm_idx is not None:
        fc_parts.append(f"{cur}[{wm_idx}:v]overlay=0:0[vout];")
    else:
        fc_parts.append(f"{cur}format=yuv420p[vout];")

    # Audio enhancement
    fc_parts.append(f"[0:a]{audio_filter}[aout]")

    fc = "".join(fc_parts)

    cmd = (
        ["ffmpeg", "-y"] + inputs +
        ["-filter_complex", fc,
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-pix_fmt", "yuv420p",
         "-shortest",
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
    print(f"🎥 Anchor Render (Sprint 18) — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
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

        # 2) Download logo (Sprint 18)
        print("\n🎨 Loading logo…", flush=True)
        logo_thumb = load_logo(tmp, LOGO_SIZE_THUMB, opacity=255)      # 2× full opacity for thumbnail
        logo_video = load_logo(tmp, LOGO_SIZE_VIDEO, opacity=LOGO_OPACITY_VIDEO)  # 1.5× 40% for video

        # Build watermark PNG for FFmpeg overlay (Sprint 18)
        watermark_png = None
        if logo_video is not None:
            wm_path = os.path.join(tmp, "watermark.png")
            make_watermark_png(logo_video, wm_path)
            watermark_png = wm_path

        # 3) Download image overlays
        raw_overlays = row.get("image_overlays") or []
        if isinstance(raw_overlays, str):
            try:
                raw_overlays = json.loads(raw_overlays)
            except Exception:
                raw_overlays = []

        image_overlays = []
        for i, ov in enumerate(raw_overlays):
            url      = ov.get("url")
            start    = float(ov.get("start", 0))
            duration = float(ov.get("duration", 5))
            if not url:
                print(f"   ⚠️  Image {i+1}: no URL, skipping", flush=True)
                continue
            if start >= src_dur:
                print(f"   ⚠️  Image {i+1}: start {start}s >= video duration {src_dur:.1f}s, skipping", flush=True)
                continue
            duration = min(duration, src_dur - start)
            ext = os.path.splitext(url.split("?")[0])[-1] or ".jpg"
            local_path = os.path.join(tmp, f"img_{i:02d}{ext}")
            if download_file(url, local_path, f"image {i+1}"):
                image_overlays.append({"local_path": local_path, "start": start, "duration": duration})
                print(f"   📸 Image {i+1}: start={start}s duration={duration}s", flush=True)

        print(f"   📊 {len(image_overlays)} image overlay(s) ready", flush=True)

        # 4) Render
        out = os.path.join(tmp, "final.mp4")
        ok = render_video(src, image_overlays, out, watermark_png=watermark_png)
        if not ok:
            db_patch(RECORD_ID, {"status": "error"}); return

        # 5) Thumbnail with logo (Sprint 18)
        thumb_path = os.path.join(tmp, "thumbnail.jpg")
        title      = (row.get("title") or row.get("working_title") or "On Camera").strip()
        thumb_url  = None
        thumb = make_thumbnail(src, src_dur, title, thumb_path, logo_im=logo_thumb)
        if thumb:
            print("\n☁️  Uploading thumbnail…", flush=True)
            thumb_url = upload_to_gcs(thumb, f"anchor/{RECORD_ID}/{LANGUAGE}/thumbnail.jpg",
                                      content_type="image/jpeg")

        # 6) Upload render
        print("\n☁️  Uploading final render…", flush=True)
        video_url = upload_to_gcs(out, f"anchor/{RECORD_ID}/{LANGUAGE}/studio_final.mp4")
        if not video_url:
            db_patch(RECORD_ID, {"status": "error"}); return

        # 7) Update Supabase
        updates = {"video_url": video_url, "status": "rendered"}
        if thumb_url:
            updates["thumbnail_url"] = thumb_url
        db_patch(RECORD_ID, updates)

    print(f"\n{'='*60}", flush=True)
    print(f"✅ Sprint 18 render complete — preview in dashboard, then Publish.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
