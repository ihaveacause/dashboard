"""
anchor_render.py — On Camera (YouTube Long) Pipeline · Render Step  (Sprint 19)
================================================================================
Sprint 17: PiP image overlays, no beats/TTS/Vertex
Sprint 18: Logo watermark, audio normalization, noise reduction,
           brightness/contrast, sharpening
Sprint 19: Thumbnail generation REMOVED — you set thumbnail manually
           in YouTube Studio after publishing.

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
from PIL import Image

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON  = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
RECORD_ID       = os.environ["RECORD_ID"]
LANGUAGE        = os.environ.get("LANGUAGE", "en")

GCS_BUCKET = "ihaveacause-media"
TABLE      = "tamil_anchor" if LANGUAGE == "ta" else "english_anchor"

W, H, FPS = 1280, 720, 24

# PiP — right-middle from viewer's perspective
PIP_W  = 320
PIP_H  = 180
MARGIN = 24
PIP_X  = W - PIP_W - MARGIN
PIP_Y  = (H - PIP_H) // 2

# Logo (Sprint 18)
LOGO_GCS_PATH      = "ihaveacause_logo.png"
LOGO_SIZE_VIDEO    = 100      # watermark — visible on 1280x720
LOGO_OPACITY_VIDEO = 204      # 80% opacity (204/255)
LOGO_MARGIN        = 20

# Audio/Video enhancements (Sprint 18)
# 75/25 split layout
IMG_W   = 960   # image panel width (75% of 1280)
STRIP_W = 320   # your video strip width (25% of 1280)

AUDIO_FILTERS = ""  # no audio filter — preserve original audio timing exactly
VIDEO_ENHANCE = "eq=brightness=0.05:contrast=1.1"

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
    from urllib.parse import quote
    token     = _gcs_token()
    encoded   = quote(gcs_path, safe="")
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

# ── Logo / watermark ──────────────────────────────────────────
def load_logo(tmp, size, opacity=255):
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
        print(f"   ✅ Logo: {size}px opacity={opacity}/255", flush=True)
        return logo
    except Exception as e:
        print(f"   ⚠️  Logo error: {e}", flush=True)
        return None

def make_watermark_png(logo_im, out_path):
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    lx = W - LOGO_SIZE_VIDEO - LOGO_MARGIN
    ly = H - LOGO_SIZE_VIDEO - LOGO_MARGIN
    canvas.paste(logo_im, (lx, ly), logo_im)
    canvas.save(out_path, "PNG")
    print(f"   ✅ Watermark PNG: {LOGO_SIZE_VIDEO}px @ ({lx},{ly}) 40% opacity", flush=True)
    return out_path

# ── FFmpeg render ─────────────────────────────────────────────

def make_branded_card(logo_im, out_path, duration=2, logo_raw_path=None):
    """
    Intro/outro card: logo image fills full 1280x720 frame.
    Falls back to dark card if logo_raw_path not available.
    """
    card_jpg = out_path.replace('.mp4', '_card.jpg')

    # Try full-screen logo first
    if logo_raw_path and os.path.exists(logo_raw_path):
        try:
            logo_full = Image.open(logo_raw_path).convert('RGB')
            logo_full = logo_full.resize((W, H), Image.LANCZOS)
            logo_full.save(card_jpg, 'JPEG', quality=95)
            print(f"   ✅ Full-screen logo card: {logo_full.size}", flush=True)
        except Exception as e:
            print(f"   ⚠️  Full-screen failed: {e} — using dark card", flush=True)
            logo_raw_path = None

    # Fallback: dark card with centered logo
    if not logo_raw_path or not os.path.exists(card_jpg):
        from PIL import ImageDraw
        img = Image.new('RGB', (W, H), (6, 7, 10))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 4], fill=(90, 220, 168))
        draw.rectangle([0, H-4, W, H], fill=(90, 220, 168))
        if logo_im is not None:
            logo_size = 200
            logo = logo_im.resize((logo_size, logo_size), Image.LANCZOS)
            img.paste(logo, ((W-logo_size)//2, (H-logo_size)//2), logo)
        img.save(card_jpg, 'JPEG', quality=95)

    r = subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", card_jpg,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-pix_fmt", "yuv420p", out_path
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ⚠️  Card failed: {r.stderr[-200:]}", flush=True)
        return None
    print(f"   ✅ Branded card: {duration}s", flush=True)
    return out_path
def render_video(src_path, image_overlays, out_path, watermark_png=None, banner_png=None):
    """
    75/25 split layout:
    - No images → your video full screen
    - During image window → image fills left 75% (960x720),
      your video fills right 25% (320x720) on black — no overlap
    - Audio via -af flag to avoid stream conflicts
    - Source split into N+1 copies (1 full + N strip copies per image)
    """
    audio_filter = AUDIO_FILTERS
    base_vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS},setsar=1,{VIDEO_ENHANCE}"
    )
    IMG_W = int(W * 0.75)   # 960
    STRIP_W = W - IMG_W      # 320

    # ── No images — simple passthrough ────────────────────────
    if not image_overlays and watermark_png is None:
        print("   ℹ️  No images — passthrough render", flush=True)
        # Add watermark + banner overlays if present
        if watermark_png or banner_png:
            extra_inputs = []
            fc_parts_pt  = [f"[0:v]{base_vf}[base]"]
            cur_pt = "[base]"
            idx_pt = 1
            if watermark_png:
                extra_inputs += ["-loop", "1", "-i", watermark_png]
                fc_parts_pt.append(f"[cur_pt][{idx_pt}:v]overlay=0:0[wm_pt]".replace("cur_pt", cur_pt))
                cur_pt = "[wm_pt]"; idx_pt += 1
            if banner_png:
                extra_inputs += ["-loop", "1", "-i", banner_png]
                fc_parts_pt.append(f"[cur_pt][{idx_pt}:v]overlay=0:0[vout_pt]".replace("cur_pt", cur_pt))
                cur_pt = "[vout_pt]"
            cmd = (
                ["ffmpeg", "-y", "-i", src_path] + extra_inputs +
                ["-filter_complex", ";".join(fc_parts_pt),
                 "-map", cur_pt, "-map", "0:a",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                 "-movflags", "+faststart", "-pix_fmt", "yuv420p", "-shortest",
                 out_path]
            )
        else:
            cmd = [
                "ffmpeg", "-y", "-i", src_path,
                "-vf", base_vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", "-pix_fmt", "yuv420p",
                out_path
            ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"   ❌ FFmpeg failed:\n{r.stderr[-2000:]}", flush=True)
            return False
        print(f"   ✅ {os.path.getsize(out_path)/1024/1024:.1f}MB", flush=True)
        return True

    # ── No images, watermark only ─────────────────────────────
    if not image_overlays and watermark_png is not None:
        print("   ℹ️  No images — watermark passthrough", flush=True)
        cmd = [
            "ffmpeg", "-y",
            "-i", src_path, "-loop", "1", "-i", watermark_png,
            "-filter_complex",
            f"[0:v]{base_vf}[base];[base][1:v]overlay=0:0[vout]",
            "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p", "-shortest",
            out_path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"   ❌ FFmpeg failed:\n{r.stderr[-2000:]}", flush=True)
            return False
        print(f"   ✅ {os.path.getsize(out_path)/1024/1024:.1f}MB", flush=True)
        return True

    # ── Images: 75/25 split layout ────────────────────────────
    n = len(image_overlays)
    print(f"   🎬 Building 75/25 split filter for {n} image(s)…", flush=True)

    # Pre-scale images to 960x720 using Pillow (faster than FFmpeg scaling each frame)
    print("   🖼  Pre-scaling images to 960×720…", flush=True)
    from PIL import Image as PILImage
    scaled_paths = []
    for i, ov in enumerate(image_overlays):
        try:
            img = PILImage.open(ov["local_path"]).convert("RGB")
            img_scaled = img.resize((IMG_W, H), PILImage.LANCZOS)
            scaled_path = ov["local_path"].replace(
                os.path.splitext(ov["local_path"])[1], f"_scaled{i}.jpg"
            )
            img_scaled.save(scaled_path, "JPEG", quality=92)
            scaled_paths.append(scaled_path)
            print(f"   ✅ Image {i+1} pre-scaled: {img.size} → {img_scaled.size}", flush=True)
        except Exception as e:
            print(f"   ⚠️  Image {i+1} scale failed: {e}, using original", flush=True)
            scaled_paths.append(ov["local_path"])

    # Build inputs
    inputs = ["-i", src_path]
    for sp in scaled_paths:
        inputs += ["-loop", "1", "-i", sp]
    wm_idx = None
    if watermark_png:
        inputs += ["-loop", "1", "-i", watermark_png]
        wm_idx = 1 + n

    # Filter complex:
    # Split source into: 1 full screen + N strip copies (one per image)
    split_labels = "[fullv]" + "".join(f"[sraw{i}]" for i in range(n))
    fc_parts = [f"[0:v]{base_vf},split={n+1}{split_labels};"]

    # Each strip: crop center 320x720 from the scaled-down source
    for i in range(n):
        fc_parts.append(
            f"[sraw{i}]crop={STRIP_W}:{H}:{(W-STRIP_W)//2}:0,setsar=1[strip{i}];"
        )

    # Build split frame for each image: hstack(image960 + strip320) = 1280x720
    for i in range(n):
        idx = i + 1
        fc_parts.append(
            f"[{idx}:v]scale={IMG_W}:{H},setsar=1[img{i}];"
        )
        fc_parts.append(f"[img{i}][strip{i}]hstack=inputs=2[sf{i}];")

    # Chain: start with full video, switch to split frame during each image window
    cur = "[fullv]"
    for i, ov in enumerate(image_overlays):
        s, e = ov["start"], ov["start"] + ov["duration"]
        fc_parts.append(
            f"{cur}[sf{i}]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'[sw{i}];"
        )
        cur = f"[sw{i}]"

    # Watermark then banner (topmost) overlays
    if wm_idx is not None:
        fc_parts.append(f"{cur}[{wm_idx}:v]overlay=0:0[wm_out]")
        cur = "[wm_out]"
    bn_idx = None
    if banner_png:
        inputs += ["-loop", "1", "-i", banner_png]
        bn_idx = len(inputs) // 2 - 1
        fc_parts.append(f"{cur}[{bn_idx}:v]overlay=0:0[vout]")
        cur = "[vout]"
    if cur != "[vout]":
        fc_parts.append(f"{cur}format=yuv420p[vout]")

    fc = ";".join(p.rstrip(";") for p in fc_parts)

    cmd = (
        ["ffmpeg", "-y"] + inputs +
        ["-filter_complex", fc,
         "-map", "[vout]", "-map", "0:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart", "-pix_fmt", "yuv420p", "-shortest",
         out_path]
    )
    print("   ⚙️  FFmpeg processing… (this takes time, please wait)", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ❌ FFmpeg failed:\n{r.stderr[-3000:]}", flush=True)
        return False
    print(f"   ✅ FFmpeg complete: {os.path.getsize(out_path)/1024/1024:.1f}MB", flush=True)
    return True


# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print(f"🎥 Anchor Render (Sprint 19) — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
    print(f"   {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    print("=" * 60, flush=True)

    row = db_get(RECORD_ID)
    if not row:
        print("❌ Record not found"); return
    if not row.get("source_video_url"):
        print("❌ No source video URL"); return

    db_patch(RECORD_ID, {"status": "rendering"})
    print("   📋 Status set to rendering", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        # 1) Download source video
        src = os.path.join(tmp, "source.mp4")
        if not download_file(row["source_video_url"], src, "source video"):
            db_patch(RECORD_ID, {"status": "error"}); return

        src_dur = get_duration(src)
        print(f"   ⏱  Duration: {src_dur:.1f}s", flush=True)
        print(f"   📐 Estimated render time: {src_dur/60*3:.0f}-{src_dur/60*4:.0f} mins on GitHub Actions", flush=True)

        # 2) Logo watermark + slim banner
        print("\n🎨 Loading logo…", flush=True)
        logo_video    = load_logo(tmp, LOGO_SIZE_VIDEO, opacity=LOGO_OPACITY_VIDEO)
        watermark_png = None
        banner_png    = None
        if logo_video:
            wm = os.path.join(tmp, "watermark.png")
            make_watermark_png(logo_video, wm)
            watermark_png = wm
            bn = os.path.join(tmp, "slim_banner.png")
            make_slim_banner(logo_video, bn)
            banner_png = bn

        # 3) Image overlays
        raw_overlays = row.get("image_overlays") or []
        if isinstance(raw_overlays, str):
            try:
                raw_overlays = json.loads(raw_overlays)
            except Exception:
                raw_overlays = []

        print(f"   📥 Downloading {len(raw_overlays)} image(s)…", flush=True)
        image_overlays = []
        for i, ov in enumerate(raw_overlays):
            url      = ov.get("url")
            start    = float(ov.get("start", 0))
            duration = float(ov.get("duration", 5))
            if not url: continue
            if start >= src_dur:
                print(f"   ⚠️  Image {i+1}: start {start}s >= duration, skipping", flush=True)
                continue
            duration = min(duration, src_dur - start)
            ext = os.path.splitext(url.split("?")[0])[-1] or ".jpg"
            lp  = os.path.join(tmp, f"img_{i:02d}{ext}")
            if download_file(url, lp, f"image {i+1}"):
                image_overlays.append({"local_path": lp, "start": start, "duration": duration})
                print(f"   📸 Image {i+1}: {start}s for {duration}s", flush=True)

        # 4) Create branded intro + outro cards (2s each)
        print('   🎬 Creating intro/outro branded cards…', flush=True)
        logo_full = load_logo(tmp, 200, opacity=255)
        intro_path = os.path.join(tmp, 'intro.mp4')
        outro_path = os.path.join(tmp, 'outro.mp4')
        intro_ok = make_branded_card(logo_full, intro_path, duration=2, logo_raw_path=os.path.join(tmp, 'logo_raw.png'))
        outro_ok = make_branded_card(logo_full, outro_path, duration=2, logo_raw_path=os.path.join(tmp, 'logo_raw.png'))

        # 5) Render main video with watermark + PiP images
        print(f'   🎬 Starting FFmpeg render — {len(image_overlays)} image(s), {src_dur:.0f}s video…', flush=True)
        main_out = os.path.join(tmp, 'main.mp4')
        ok = render_video(src, image_overlays, main_out, watermark_png=watermark_png, banner_png=banner_png)
        if not ok:
            db_patch(RECORD_ID, {'status': 'error'}); return
        print(f'   ✅ FFmpeg done: {os.path.getsize(main_out)/1024/1024:.1f}MB', flush=True)

        # 6) Concat: intro + main + outro
        print('   🔗 Concatenating intro + main + outro…', flush=True)
        concat_list = []
        if intro_ok: concat_list.append(f"file '{intro_path}'")
        concat_list.append(f"file '{main_out}'")
        if outro_ok: concat_list.append(f"file '{outro_path}'")
        out = os.path.join(tmp, 'final.mp4')
        concat_file = os.path.join(tmp, 'concat.txt')
        with open(concat_file, 'w') as f:
            f.write('\n'.join(concat_list))
        rc = subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', concat_file,
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
            '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
            '-movflags', '+faststart', '-pix_fmt', 'yuv420p',
            out
        ], capture_output=True, text=True)
        if rc.returncode != 0:
            print(f'   ⚠️  Concat failed, using main only: {rc.stderr[-300:]}', flush=True)
            out = main_out
        else:
            print(f'   ✅ Final with intro+outro: {os.path.getsize(out)/1024/1024:.1f}MB', flush=True)

        # 7) Upload render
        print(f'\n☁️  Uploading render to GCS…', flush=True)
        video_url = upload_to_gcs(out, f'anchor/{RECORD_ID}/{LANGUAGE}/studio_final.mp4')
        if not video_url:
            db_patch(RECORD_ID, {'status': 'error'}); return

        print('   💾 Updating Supabase → rendered…', flush=True)
        db_patch(RECORD_ID, {'video_url': video_url, 'status': 'rendered'})

    print(f"\n{'='*60}", flush=True)
    print(f"✅ Done — preview in dashboard, then Publish.", flush=True)
    print(f"   Add your custom thumbnail in YouTube Studio after publishing.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
