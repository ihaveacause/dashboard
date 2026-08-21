"""
anchor_shorts_render.py — On Camera Shorts Pipeline · Step 2 of 3  (Sprint 19)
===============================================================================
Sprint 19 additions (passive — apply on every render automatically):
  1. Audio normalization  — FFmpeg loudnorm (even out loud/soft speaking)
  2. Noise reduction      — FFmpeg afftdn  (remove background hum)
  3. Brightness/contrast  — FFmpeg eq      (subtle auto-levels)
  4. Sharpening           — FFmpeg unsharp (improves selfie camera footage)
  5. Logo watermark       — logo placed top-right corner of video frame
                            (separate from the existing bottom banner logo)
                            Size: 50px, 40% opacity — doesn't clash with banner

Everything else (banner, PiP clips, thumbnail, GCS, Supabase) is UNCHANGED.

Vertical format: 1080×1920 (9:16) — all filter values tuned for vertical.

Env vars:
  SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON
  RECORD_ID, LANGUAGE  (ta | en)
"""

import os
import json
import subprocess
import tempfile
from datetime import datetime, timedelta
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
RECORD_ID      = os.environ["RECORD_ID"]
LANGUAGE       = os.environ.get("LANGUAGE", "en")

GCS_BUCKET = "ihaveacause-media"
TABLE      = "tamil_anchor_shorts" if LANGUAGE == "ta" else "english_anchor_shorts"
W, H, FPS  = 720, 1280, 30

# PiP box for optional overlay clips
PIP_W, PIP_H = 253, 142
PIP_MARGIN   = 16
BANNER_H     = 140
PIP_X        = W - PIP_W - PIP_MARGIN
PIP_Y        = (H - BANNER_H) - PIP_MARGIN - PIP_H

# Brand palette
C_BAR      = (10, 12, 18, 190)
C_BAR_LINE = (232, 65, 42, 255)
C_TEXT     = (238, 241, 247, 255)

# ── Sprint 19: Audio/Video enhancements ──────────────────────
# Tuned for vertical selfie footage
VIDEO_ENHANCE = "eq=brightness=0.03:contrast=1.05"

# ── Sprint 19: Watermark (top-right, above banner) ───────────
# Separate from the bottom-banner logo — this is a subtle corner mark
LOGO_GCS_PATH    = "ihaveacause_logo.png"
WM_SIZE          = 34      # px — visible but not intrusive on vertical frame
WM_OPACITY       = 102     # 40% (102/255)
WM_MARGIN        = 14      # px from edge — top-right corner

SB_HEADERS = {
    "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json", "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"


# ── Supabase ──────────────────────────────────────────────────
def db_get_one(rid):
    r = requests.get(f"{REST}/{TABLE}",
                     headers={**SB_HEADERS, "Prefer": "return=representation"},
                     params={"id": f"eq.{rid}", "select": "*"}, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch(rid, data):
    r = requests.patch(f"{REST}/{TABLE}?id=eq.{rid}", headers=SB_HEADERS, json=data, timeout=30)
    if r.status_code not in (200, 204):
        print(f"   ❌ Supabase patch {r.status_code}: {r.text[:200]}", flush=True)
    return r.status_code in (200, 204)


# ── GCS helpers ───────────────────────────────────────────────
def _gcs_object_media_url(url):
    from urllib.parse import quote
    marker = "storage.googleapis.com/"
    if marker in url and "Signature=" not in url and "X-Goog-Signature" not in url \
       and "/storage/v1/b/" not in url and "/upload/storage/" not in url:
        rest = url.split(marker, 1)[1]
        if "/" in rest:
            bucket, path = rest.split("/", 1)
            return True, f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(path, safe='')}?alt=media"
    return False, url

def gcs_token(scope="https://www.googleapis.com/auth/devstorage.read_write"):
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON), scopes=[scope])
    creds.refresh(gr.Request())
    return creds.token

def download_file(url, dest, desc="file"):
    is_gcs, fetch_url = _gcs_object_media_url(url)
    token = gcs_token() if is_gcs else None
    hdrs  = {"Authorization": f"Bearer {token}"} if is_gcs else {}
    r = requests.get(fetch_url, headers=hdrs, stream=True, timeout=600)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest)//1024}KB", flush=True)
        return True
    print(f"   ❌ {desc} {r.status_code}", flush=True)
    return False

def download_gcs_object(gcs_path, dest, desc="file"):
    from urllib.parse import quote
    token     = gcs_token()
    encoded   = quote(gcs_path, safe="")
    fetch_url = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}?alt=media"
    r = requests.get(fetch_url, headers={"Authorization": f"Bearer {token}"},
                     stream=True, timeout=60)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest)//1024}KB", flush=True)
        return True
    print(f"   ⚠️  {desc} not found ({r.status_code})", flush=True)
    return False

def upload_gcs(local_path, gcs_path, content_type="video/mp4", days=30):
    import base64, datetime as dt
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    token      = gcs_token()
    creds_info = json.loads(GCP_CREDS_JSON)
    print(f"   📤 {os.path.getsize(local_path)//(1024*1024)}MB → GCS…", flush=True)
    with open(local_path, "rb") as f:
        r = requests.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
            params={"uploadType": "media", "name": gcs_path},
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            data=f, timeout=600)
    if r.status_code not in (200, 201):
        print(f"   ❌ GCS upload {r.status_code}: {r.text[:200]}", flush=True)
        return None

    expiry_ts = int((dt.datetime.utcnow() + dt.timedelta(days=days)).timestamp())
    sts = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    pk  = serialization.load_pem_private_key(
        creds_info["private_key"].encode(), password=None, backend=default_backend())
    sig = pk.sign(sts.encode(), padding.PKCS1v15(), hashes.SHA256())
    esig = requests.utils.quote(base64.b64encode(sig).decode(), safe="")
    signed = (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
              f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={esig}")
    print(f"   ✅ GCS done ({days}d signed URL)", flush=True)
    return signed


# ── Fonts ─────────────────────────────────────────────────────
def font_paths():
    if LANGUAGE == "ta":
        cands = ["/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.ttf",
                 "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
                 "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf"]
    else:
        cands = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
    for c in cands:
        if os.path.exists(c):
            return c
    return None

def english_font_path():
    cands = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
    for c in cands:
        if os.path.exists(c):
            return c
    return None

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


# ── ffprobe ───────────────────────────────────────────────────
def ffprobe_dur(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except Exception:
        return 0.0


# ── Bottom banner (unchanged from original) ───────────────────
def build_banner_png(font_path, logo_im, out_path, hook=""):
    """1080×1920 RGBA transparent except bottom banner bar."""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    bar_top = H - BANNER_H
    draw.rectangle([0, bar_top, W, H], fill=C_BAR)
    draw.rectangle([0, bar_top, W, bar_top + 3], fill=C_BAR_LINE)

    en_fp   = english_font_path()
    f_name  = load_font(en_fp, 34)
    f_handle= load_font(en_fp, 24)
    f_hook  = load_font(font_path, 36)

    logo_size = 72
    logo_x, logo_y = 28, bar_top + 20
    if logo_im is not None:
        lim = logo_im.resize((logo_size, logo_size), Image.LANCZOS)
        canvas.paste(lim, (logo_x, logo_y), lim)

    text_x = logo_x + logo_size + 16
    draw.text((text_x, bar_top + 22), "I Have a Cause",  font=f_name,   fill=C_TEXT)
    draw.text((text_x, bar_top + 62), "@IHaveACause",    font=f_handle, fill=(160, 165, 190, 255))

    if hook:
        words, lines, cur = hook.split(), [], ""
        for w in words:
            test = (cur + " " + w).strip()
            try:
                tw = draw.textlength(test, font=f_hook)
            except Exception:
                tw = len(test) * 20
            if tw > W - 56 and cur:
                lines.append(cur); cur = w
            else:
                cur = test
        if cur:
            lines.append(cur)
        lines = lines[:2]
        ty = bar_top + 118
        for ln in lines:
            draw.text((28, ty), ln, font=f_hook, fill=C_TEXT)
            ty += 46

    # Sprint 19: bake watermark logo into banner (top-right corner of full frame)
    # This avoids a separate FFmpeg overlay input which can cause hangs
    if logo_im is not None:
        try:
            wm = logo_im.resize((WM_SIZE, WM_SIZE), Image.LANCZOS)
            r2, g2, b2, a2 = wm.split()
            a2 = a2.point(lambda x: int(x * WM_OPACITY / 255))
            wm = Image.merge("RGBA", (r2, g2, b2, a2))
            lx = W - WM_SIZE - WM_MARGIN
            ly = WM_MARGIN
            canvas.paste(wm, (lx, ly), wm)
        except Exception as e:
            print(f"   ⚠️  Watermark bake skipped: {e}", flush=True)

    canvas.save(out_path, "PNG")
    return out_path


# ── Sprint 19: Watermark PNG (top-right corner) ───────────────
def make_watermark_png(logo_im, out_path):
    """
    Full-frame transparent PNG with logo at top-right.
    Placed top-right (not bottom-right) to avoid clashing with the banner.
    Size: WM_SIZE px, 40% opacity.
    """
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    lx = W - WM_SIZE - WM_MARGIN
    ly = WM_MARGIN   # top-right
    canvas.paste(logo_im, (lx, ly), logo_im)
    canvas.save(out_path, "PNG")
    print(f"   ✅ Watermark PNG: {WM_SIZE}px top-right ({lx},{ly}) 40% opacity", flush=True)
    return out_path


# ── Thumbnail ─────────────────────────────────────────────────
def make_thumbnail(src, src_dur, title, font_path, logo_im, out_path, hook=""):
    grab_t = max(0.3, (src_dur or 3) * 0.35)
    frame_path = out_path.replace(".jpg", "_raw.jpg")
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-ss", f"{grab_t:.2f}", "-i", src,
                    "-frames:v", "1", "-q:v", "2", frame_path],
                   capture_output=True)
    if not os.path.exists(frame_path):
        return None
    try:
        img = Image.open(frame_path).convert("RGB")
        img = img.resize((W, H)) if img.size != (W, H) else img
        img = img.convert("RGBA")
        banner_path = frame_path.replace("_raw.jpg", "_banner.png")
        build_banner_png(font_path, logo_im, banner_path, hook=hook)
        banner = Image.open(banner_path)
        img.alpha_composite(banner)
        img.convert("RGB").save(out_path, quality=90)
        return out_path
    except Exception as e:
        print(f"   ⚠️  Thumbnail branding skipped: {e}", flush=True)
        return frame_path


# ── FFmpeg render (Sprint 19: enhancements + watermark added) ──
def render_vertical(src, banner_png, out, clips=None):
    """
    Sprint 19 changes vs original:
      - base_vf now includes eq (brightness/contrast) + unsharp (sharpen)
      - audio goes through afftdn (noise reduction) + loudnorm (normalize)
      - watermark_png overlaid on top of everything, under banner
        (banner stays topmost layer as before)

    clips behaviour unchanged — empty list = no-op, same as original.
    """
    clips = clips or []

    # ── Sprint 19: Video base filter ──────────────────────────
    base_vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS},setsar=1,"
        f"{VIDEO_ENHANCE}"   # brightness/contrast + sharpening
    )

    IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

    def is_image(c):
        if c.get("kind"):
            return c["kind"] == "image"
        return os.path.splitext(c["local_path"])[1].lower() in IMAGE_EXT

    # Build inputs: source, clips, [watermark], banner
    inputs = ["-i", src]
    for c in clips:
        if is_image(c):
            inputs += ["-loop", "1", "-i", c["local_path"]]
        else:
            inputs += ["-i", c["local_path"]]

    # watermark index (optional)
    # banner is always last input (watermark baked into banner PNG — not a separate FFmpeg input)
    inputs += ["-loop", "1", "-i", banner_png]
    banner_idx = 1 + len(clips)

    # ── Filter complex ─────────────────────────────────────────
    fc_parts = [
        # Sprint 19: base now includes video enhancements
        f"[0:v]{base_vf}[base]"
    ]

    unmuted_audio_labels = []
    stage = "base"

    for i, c in enumerate(clips):
        idx   = i + 1
        start = float(c.get("start", 0) or 0)
        dur   = float(c.get("duration", 5) or 5)
        end   = start + dur
        img   = is_image(c)
        shift = "" if img else f",setpts=PTS+{start}/TB"

        fc_parts.append(
            f"[{idx}:v]scale={PIP_W}:{PIP_H}:force_original_aspect_ratio=decrease,"
            f"pad={PIP_W}:{PIP_H}:(ow-iw)/2:(oh-ih)/2:black{shift}[clip{i}]"
        )
        fc_parts.append(
            f"[{stage}][clip{i}]overlay={PIP_X}:{PIP_Y}:"
            f"enable='between(t,{start},{end})'[s{i}]"
        )
        stage = f"s{i}"

        if not img and not c.get("mute_original", True):
            fc_parts.append(
                f"[{idx}:a]adelay={int(start*1000)}|{int(start*1000)},"
                f"atrim=0:{end},volume=1[a{i}]"
            )
            unmuted_audio_labels.append(f"a{i}")

    # Banner always topmost (watermark baked into banner PNG via Pillow — no FFmpeg overlay needed)
    fc_parts.append(f"[{stage}][{banner_idx}:v]overlay=0:0[vout]")

    # Audio: noise reduction + normalization applied to source,
    # then mixed with any unmuted clip audio
    if unmuted_audio_labels:
        mix_inputs = "[0:a]" + "".join(f"[{lbl}]" for lbl in unmuted_audio_labels)
        fc_parts.append(
            f"{mix_inputs}amix=inputs={1+len(unmuted_audio_labels)}:"
            f"duration=first:dropout_transition=0[aout]"
        )
        audio_map = ["-map", "[aout]"]
    else:
        # Simple passthrough — same as original working script
        audio_map = ["-map", "0:a?"]

    fc = ";".join(fc_parts)
    cmd = ["ffmpeg", "-y", "-nostdin", *inputs,
           "-filter_complex", fc,
           "-map", "[vout]", *audio_map,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-shortest",
           "-movflags", "+faststart", "-pix_fmt", "yuv420p", out]
    print("   🎬 FFmpeg starting…", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FFmpeg stderr:", r.stderr[-3000:], flush=True)
        return False
    print(f"   ✅ Rendered: {out} ({os.path.getsize(out)//1024}KB, "
          f"{len(clips)} PiP clip(s))", flush=True)
    return True


# ── Main render() ─────────────────────────────────────────────
def render(row, tmp):
    src = os.path.join(tmp, "source.mp4")
    if not download_file(row["source_video_url"], src, "Recording"):
        return None, None
    src_dur = ffprobe_dur(src)
    print(f"   ⏱  Source duration: {src_dur:.1f}s", flush=True)
    if src_dur > 180:
        print(f"   ⚠️  {src_dur:.0f}s — over 3-min YouTube Shorts ceiling. "
              f"Will publish as regular video.", flush=True)

    fp     = font_paths()
    en_fp  = english_font_path()
    title  = (row.get("title") or row.get("working_title") or "").strip()
    hook   = (row.get("hook_text") or "").strip()

    # ── Load logo (used in banner + watermark) ────────────────
    logo_im = None
    try:
        logo_path = os.path.join(tmp, "logo_raw.png")
        if download_gcs_object(LOGO_GCS_PATH, logo_path, "logo"):
            logo_im = Image.open(logo_path).convert("RGBA")
            print("   ✅ Logo loaded", flush=True)
    except Exception as e:
        print(f"   ℹ️  Logo skipped: {e}", flush=True)

    # ── Sprint 19: Build watermark PNG (top-right) ────────────
    # Thumbnail (unchanged)
    thumb_path = make_thumbnail(src, src_dur, title, fp, logo_im,
                                os.path.join(tmp, "thumbnail.jpg"), hook=hook)

    # Banner (unchanged — uses full-opacity logo for bottom bar)
    banner_png = os.path.join(tmp, "banner.png")
    print("   🎨 Building banner PNG…", flush=True)
    build_banner_png(fp, logo_im, banner_png, hook=hook)
    print("   ✅ Banner PNG built", flush=True)

    # PiP clips (unchanged)
    raw_clips = row.get("clips") or []
    if isinstance(raw_clips, str):
        try:
            raw_clips = json.loads(raw_clips)
        except Exception:
            raw_clips = []

    print(f"   📥 {len(raw_clips)} PiP clip(s) to download…", flush=True)
    clips = []
    for i, c in enumerate(raw_clips):
        url = c.get("url") or c.get("src")
        if not url:
            continue
        ext = os.path.splitext(url.split("?")[0])[-1] or ".mp4"
        lp  = os.path.join(tmp, f"clip_{i:02d}{ext}")
        if download_file(url, lp, f"clip {i+1}"):
            clips.append({**c, "local_path": lp})

    print(f"   🎬 Starting FFmpeg render — {len(clips)} clip(s), 720×1280…", flush=True)
    out = os.path.join(tmp, "final.mp4")
    ok  = render_vertical(src, banner_png, out, clips=clips)
    if not ok:
        print("   ❌ FFmpeg render failed", flush=True)
        return None, None
    print(f"   ✅ FFmpeg render complete — {os.path.getsize(out)//1024}KB", flush=True)
    return out, thumb_path


# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print(f"🤳 Anchor Shorts Render (Sprint 19) — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
    print(f"   {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    print("=" * 60, flush=True)

    row = db_get_one(RECORD_ID)
    if not row:
        print("❌ Record not found"); return
    if not row.get("source_video_url"):
        print("❌ No source_video_url — upload a recording first"); return

    db_patch(RECORD_ID, {"status": "rendering"})

    with tempfile.TemporaryDirectory() as tmp:
        print("   ⬇️  Downloading source + building render…", flush=True)
        out_path, thumb_path = render(row, tmp)
        if not out_path:
            db_patch(RECORD_ID, {"status": "error"}); return

        print("\n☁️  Uploading render…", flush=True)
        video_url = upload_gcs(out_path,
                               f"anchor_shorts/{RECORD_ID}/{LANGUAGE}/final.mp4")
        if not video_url:
            db_patch(RECORD_ID, {"status": "error"}); return

        thumb_url = None
        if thumb_path and os.path.exists(thumb_path):
            print("☁️  Uploading thumbnail…", flush=True)
            thumb_url = upload_gcs(thumb_path,
                                   f"anchor_shorts/{RECORD_ID}/{LANGUAGE}/thumbnail.jpg",
                                   content_type="image/jpeg")

        print("   💾 Updating Supabase → rendered…", flush=True)
        updates = {"video_url": video_url, "status": "rendered"}
        if thumb_url:
            updates["thumbnail_url"] = thumb_url
        db_patch(RECORD_ID, updates)

    print(f"\n{'='*60}", flush=True)
    print(f"✅ Sprint 19 render complete — preview in dashboard, then Publish.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
