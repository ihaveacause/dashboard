"""
anchor_render.py — On Camera (Studio Desk) Pipeline · Step 3 of 3
==================================================================
Composites your recording into a news-studio video using the beat sheet.

TWO STUDIO MODES (set per record; render reads row.studio_mode, env override STUDIO_MODE):

  real_room  — NO green screen needed (your test mode).
               Your real footage fills the frame. A semi-opaque graphics PANEL
               sits on the right carrying the studio image (image beats) or
               kinetic text (text beats); a lower-third strap carries the beat
               headline; a persistent OPINION tag marks it as your view.

  green      — You shot on a green background.
               The speaker is chroma-keyed out; the studio image fills the whole
               frame behind you on image beats, a studio gradient on text beats;
               the same lower-third + bullets + OPINION tag sit on top.

HOW IT WORKS (robust + frame-exact):
  For each beat we pre-render ONE 1920x1080 RGBA overlay PNG with Pillow (panel,
  image, headline, bullets, tag, logo). FFmpeg then overlays each PNG only during
  its beat window (enable='between(t,start,end)'). In green mode we first build a
  per-beat background track and key you onto it. Your real audio is kept as-is.

Env vars:
  SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON
  RECORD_ID, LANGUAGE  (ta | en)
  STUDIO_MODE  (optional override: real_room | green)
  GREEN_KEY    (optional, default 0x00d000 ; the green to key out)
"""

import os
import json
import subprocess
import tempfile
from datetime import datetime, timedelta
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
RECORD_ID      = os.environ["RECORD_ID"]
LANGUAGE       = os.environ.get("LANGUAGE", "en")
STUDIO_OVERRIDE = os.environ.get("STUDIO_MODE", "").strip()
GREEN_KEY      = os.environ.get("GREEN_KEY", "0x00d000")

GCS_BUCKET = "ihaveacause-media"
TABLE      = "tamil_anchor" if LANGUAGE == "ta" else "english_anchor"
W, H, FPS  = 1920, 1080, 24

# Brand palette (matches the dashboard's dark editorial look)
C_PANEL    = (16, 18, 24, 205)      # right panel fill (semi-opaque)
C_PANEL_BD = (90, 200, 160, 255)    # panel accent border
C_STRAP    = (12, 14, 20, 230)      # lower-third strap fill
C_STRAP_BD = (90, 200, 160, 255)
C_TAG      = (200, 60, 60, 235)     # OPINION pill
C_TEXT     = (245, 245, 245, 255)
C_SUB      = (180, 185, 195, 255)
C_ACCENT   = (120, 220, 180, 255)

OPINION_LABEL = {"ta": "கருத்து", "en": "OPINION"}.get(LANGUAGE, "OPINION")

# ── Supabase ──────────────────────────────────────────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json", "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get_one(rid):
    r = requests.get(f"{REST}/{TABLE}",
                     headers={**SB_HEADERS, "Prefer": "return=representation"},
                     params={"id": f"eq.{rid}", "select": "*"}, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch(rid, data):
    r = requests.patch(f"{REST}/{TABLE}?id=eq.{rid}", headers=SB_HEADERS, json=data, timeout=30)
    if r.status_code not in (200, 204):
        print(f"   ❌ patch {r.status_code}: {r.text[:200]}", flush=True)
    return r.status_code in (200, 204)

def _gcs_object_media_url(url):
    """If url is an UNSIGNED GCS object URL, return (True, authenticated media url)."""
    from urllib.parse import quote
    marker = "storage.googleapis.com/"
    if marker in url and "Signature=" not in url and "X-Goog-Signature" not in url \
       and "/storage/v1/b/" not in url and "/upload/storage/" not in url:
        rest = url.split(marker, 1)[1]
        if "/" in rest:
            bucket, path = rest.split("/", 1)
            return True, f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(path, safe='')}?alt=media"
    return False, url

def download_file(url, dest, desc="file"):
    """Reads unsigned GCS objects (the uploaded recording, beat images) with the
    service account; otherwise fetches the URL directly (signed/public)."""
    is_gcs, fetch_url = _gcs_object_media_url(url)
    if is_gcs:
        headers = {"Authorization": f"Bearer {gcs_token()}"}
        r = requests.get(fetch_url, headers=headers, stream=True, timeout=600)
    else:
        r = requests.get(url, stream=True, timeout=600)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest)//1024}KB", flush=True)
        return True
    print(f"   ❌ {desc} failed {r.status_code}", flush=True)
    return False

# ── GCS upload + V2 signed URL (mirrors the rest of the repo) ─
def gcs_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON),
        scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    creds.refresh(gr.Request())
    return creds.token

def upload_to_gcs(local_path, gcs_path, content_type="video/mp4", days=30):
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    token = gcs_token(); creds_info = json.loads(GCP_CREDS_JSON)
    print(f"   📤 Uploading {os.path.getsize(local_path)//(1024*1024)}MB to GCS...", flush=True)
    with open(local_path, "rb") as f:
        r = requests.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
            params={"uploadType": "media", "name": gcs_path},
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            data=f, timeout=600)
    if r.status_code not in (200, 201):
        print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}", flush=True)
        return None
    expiry_ts = int((datetime.utcnow() + timedelta(days=days)).timestamp())
    sts = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    pk  = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
    sig = pk.sign(sts.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    esig = requests.utils.quote(base64.b64encode(sig).decode("utf-8"), safe="")
    return (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
            f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={esig}")

# ── Fonts ─────────────────────────────────────────────────────
def font_paths():
    if LANGUAGE == "ta":
        cands = ["/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.ttf",
                 "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
                 "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf"]
    else:
        cands = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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

def wrap_text(draw, text, font, max_px):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_px or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines

def rounded(draw, box, r, fill, outline=None, width=0):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

# ── Per-beat overlay PNG (the graphics layer for one beat) ────
def build_overlay_png(beat, mode, font_path, logo_im, beat_img, out_path):
    """One 1920x1080 RGBA PNG: panel/image/headline/bullets/OPINION tag/logo."""
    img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    f_head = load_font(font_path, 46)
    f_panel = load_font(font_path, 40)
    f_bul  = load_font(font_path, 34)
    f_tag  = load_font(font_path, 28)

    headline = beat.get("headline", "") or ""
    bullets  = beat.get("bullets", []) or []

    if mode == "real_room":
        # Right-side graphics panel
        px0, py0, px1, py1 = 1170, 70, 1860, 840
        rounded(draw, [px0, py0, px1, py1], 22, C_PANEL, outline=C_PANEL_BD, width=2)
        inner_x = px0 + 28; inner_w = (px1 - 28) - inner_x; cy = py0 + 28
        if beat["mode"] == "image" and beat_img is not None:
            iw = inner_w; ih = int(iw * 9 / 16)
            thumb = beat_img.resize((iw, ih), Image.LANCZOS)
            img.paste(thumb, (inner_x, cy))
            draw.rounded_rectangle([inner_x, cy, inner_x + iw, cy + ih], radius=12,
                                   outline=(255, 255, 255, 60), width=1)
            cy += ih + 26
        # headline inside panel
        for ln in wrap_text(draw, headline, f_panel, inner_w):
            draw.text((inner_x, cy), ln, font=f_panel, fill=C_TEXT); cy += 50
        cy += 6
        for b in bullets:
            draw.ellipse([inner_x, cy + 14, inner_x + 10, cy + 24], fill=C_ACCENT)
            for ln in wrap_text(draw, b, f_bul, inner_w - 26):
                draw.text((inner_x + 26, cy), ln, font=f_bul, fill=C_SUB); cy += 42
    else:
        # green: image is full-frame behind (handled in bg). Here just kinetic text
        # for text beats, centred-left, large.
        if beat["mode"] == "text":
            cx, cy = 110, 360
            f_big = load_font(font_path, 64)
            for ln in wrap_text(draw, headline, f_big, 1000):
                draw.text((cx + 3, cy + 3), ln, font=f_big, fill=(0, 0, 0, 160))
                draw.text((cx, cy), ln, font=f_big, fill=C_TEXT); cy += 78
            cy += 12
            for b in bullets:
                draw.ellipse([cx, cy + 16, cx + 12, cy + 28], fill=C_ACCENT)
                for ln in wrap_text(draw, b, f_bul, 900):
                    draw.text((cx + 28, cy), ln, font=f_bul, fill=C_SUB); cy += 44

    # Lower-third strap (both modes) — the headline chyron in front of the host
    sx0, sy0, sx1, sy1 = 70, 900, 1130, 1010
    rounded(draw, [sx0, sy0, sx1, sy1], 16, C_STRAP, outline=C_STRAP_BD, width=2)
    draw.rectangle([sx0, sy0, sx0 + 8, sy1], fill=C_ACCENT)  # accent edge
    strap_lines = wrap_text(draw, headline, f_head, (sx1 - 40) - (sx0 + 28))
    ty = sy0 + (sy1 - sy0 - len(strap_lines) * 48) // 2
    for ln in strap_lines[:2]:
        draw.text((sx0 + 28, ty), ln, font=f_head, fill=C_TEXT); ty += 48

    # Persistent OPINION tag (top-left)
    tag_w = int(draw.textlength(OPINION_LABEL, font=f_tag)) + 40
    rounded(draw, [70, 70, 70 + tag_w, 120], 25, C_TAG)
    draw.text((90, 80), OPINION_LABEL, font=f_tag, fill=(255, 255, 255, 255))

    # Logo (top-right) if available
    if logo_im is not None:
        lw = 90; logo = logo_im.resize((lw, lw))
        img.paste(logo, (W - lw - 40, 60), logo if logo.mode == "RGBA" else None)

    img.save(out_path)
    return out_path

# ── Studio gradient still for green-mode text beats ──────────
def make_studio_bg(out_path):
    bg = Image.new("RGB", (W, H), (14, 16, 22))
    top = Image.new("RGB", (W, H), (24, 30, 44))
    mask = Image.linear_gradient("L").resize((W, H))
    bg = Image.composite(top, bg, mask)
    bg = bg.filter(ImageFilter.GaussianBlur(2))
    bg.save(out_path, "JPEG", quality=90)
    return out_path

def ffprobe_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0

# ── Build the background concat (green mode) ──────────────────
# (removed — green mode now composites image backgrounds with the same timed
#  overlay mechanism as real_room, which is frame-exact and PTS-drift-free.)

# ── Main render ───────────────────────────────────────────────
def render(row, tmp):
    studio_mode = (STUDIO_OVERRIDE or row.get("studio_mode") or "real_room").strip()
    studio_mode = "green" if studio_mode == "green" else "real_room"
    print(f"\n🎛️  Studio mode: {studio_mode}", flush=True)

    beats = row.get("beats") or []
    if isinstance(beats, str):
        beats = json.loads(beats)
    beats = sorted(beats, key=lambda b: b.get("order", 0))
    if not beats:
        print("❌ No beats — run Beats first."); return None

    # source
    src = os.path.join(tmp, "source.mp4")
    if not download_file(row["source_video_url"], src, "Recording"):
        return None
    src_dur = ffprobe_dur(src)
    print(f"   ⏱  Source duration: {src_dur:.1f}s", flush=True)
    # clamp beat ends to the real source duration
    for b in beats:
        b["end"] = min(b["end"], src_dur) if src_dur else b["end"]
    total = src_dur or beats[-1]["end"]

    font_path = font_paths()
    print(f"   🔤 Font: {font_path}", flush=True)

    # logo (optional)
    logo_im = None
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gr
        ci = json.loads(GCP_CREDS_JSON)
        lc = service_account.Credentials.from_service_account_info(
            ci, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        lc.refresh(gr.Request())
        r = requests.get(
            f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/ihaveacause_logo.png?alt=media",
            headers={"Authorization": f"Bearer {lc.token}"}, timeout=15)
        if r.status_code == 200:
            logo_im = Image.open(BytesIO(r.content)).convert("RGBA")
            print("   ✅ Logo loaded", flush=True)
    except Exception as e:
        print(f"   ℹ️  Logo skipped: {e}", flush=True)

    # download beat images + build overlay PNGs
    beat_images = {}     # order -> local jpg path (full image, for green bg / panel thumb)
    beat_pils   = {}     # order -> PIL image (for panel thumb)
    for b in beats:
        if b["mode"] == "image" and b.get("image_url"):
            p = os.path.join(tmp, f"img_{b['order']:02d}.jpg")
            if download_file(b["image_url"], p, f"Beat {b['order']} image"):
                beat_images[b["order"]] = p
                try:
                    beat_pils[b["order"]] = Image.open(p).convert("RGB")
                except Exception:
                    pass

    overlay_pngs = []
    for b in beats:
        op = os.path.join(tmp, f"ov_{b['order']:02d}.png")
        build_overlay_png(b, studio_mode, font_path, logo_im,
                          beat_pils.get(b["order"]), op)
        overlay_pngs.append((op, b["start"], b["end"]))

    out = os.path.join(tmp, "final.mp4")

    if studio_mode == "real_room":
        ok = render_real_room(src, overlay_pngs, total, out)
    else:
        studio_bg = make_studio_bg(os.path.join(tmp, "studio_bg.jpg"))
        # full-frame backgrounds for image beats only (text beats use the gradient)
        image_beats = [(beat_images[b["order"]], b["start"], b["end"])
                       for b in beats if b["mode"] == "image" and beat_images.get(b["order"])]
        ok = render_green(src, studio_bg, image_beats, overlay_pngs, total, out)
    return out if ok else None

def _overlay_chain(n_inputs_start, overlay_pngs):
    """Build the chained overlay filter for N overlay PNG inputs.
    Inputs are indexed starting at n_inputs_start. Returns (filter_str, last_label)."""
    cur = "[base]"
    parts = []
    for i, (_png, s, e) in enumerate(overlay_pngs):
        idx = n_inputs_start + i
        lbl = f"[v{i}]"
        parts.append(
            f"{cur}[{idx}:v]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{lbl}")
        cur = lbl
    return ";".join(parts), cur

def render_real_room(src, overlay_pngs, total, out):
    print("\n🎬 FFmpeg — real_room composite...", flush=True)
    inputs = ["-i", src]                                   # input 0 = source video
    for png, _s, _e in overlay_pngs:
        inputs += ["-loop", "1", "-i", png]               # inputs 1..N = overlay PNGs
    base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},fps={FPS},setsar=1[base];")
    chain, last = _overlay_chain(1, overlay_pngs)
    fc = base + chain + f";{last}format=yuv420p[vout]"
    cmd = (["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "0:a?",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-pix_fmt", "yuv420p",
        out])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ❌ FFmpeg failed:\n{r.stderr[-2500:]}", flush=True)
        return False
    print(f"   ✅ Video: {os.path.getsize(out)/1024/1024:.1f}MB", flush=True)
    return True

def render_green(src, studio_bg, image_beats, overlay_pngs, total, out):
    """Green-screen composite using the SAME timed-overlay mechanism as real_room
    (proven frame-exact), avoiding any concat-demuxer PTS drift.

    Layer order: studio gradient base
      -> per-image-beat full-frame image overlaid with enable=between(start,end)
      -> chroma-keyed speaker overlaid on top
      -> per-beat graphic PNGs (lower-third / text / tag) with enable windows.

    image_beats : list of (image_path, start, end) for mode=='image' beats only.
    """
    print("\n🎬 FFmpeg — green-screen composite...", flush=True)
    # input 0 = studio gradient (looped), input 1 = source (green)
    inputs = ["-loop", "1", "-i", studio_bg, "-i", src]
    next_idx = 2
    # image-beat backgrounds
    img_inputs = []
    for path, s, e in image_beats:
        inputs += ["-loop", "1", "-i", path]
        img_inputs.append((next_idx, s, e)); next_idx += 1
    # graphic overlay PNGs
    ov_start = next_idx
    for png, _s, _e in overlay_pngs:
        inputs += ["-loop", "1", "-i", png]; next_idx += 1

    fc = (f"[0:v]scale={W}:{H},fps={FPS},setsar=1[bg0];")
    cur = "[bg0]"
    # time the image backgrounds onto the gradient
    for k, (idx, s, e) in enumerate(img_inputs):
        lbl = f"[bgi{k}]"
        fc += (f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
               f"setsar=1[imb{k}];{cur}[imb{k}]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{lbl};")
        cur = lbl
    # key the speaker and overlay onto the timed background
    fc += (f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
           f"chromakey={GREEN_KEY}:0.30:0.10,setsar=1[key];"
           f"{cur}[key]overlay=0:0[base];")
    # graphic overlays (same helper as real_room)
    chain, last = _overlay_chain(ov_start, overlay_pngs)
    fc += chain + f";{last}format=yuv420p[vout]"

    cmd = (["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "1:a?",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-pix_fmt", "yuv420p",
        out])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ❌ FFmpeg failed:\n{r.stderr[-2500:]}", flush=True)
        return False
    print(f"   ✅ Video: {os.path.getsize(out)/1024/1024:.1f}MB", flush=True)
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print(f"🎥 Anchor Render — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
    print(f"   {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    print("=" * 60, flush=True)

    row = db_get_one(RECORD_ID)
    if not row:
        print("❌ Record not found"); return
    if not row.get("source_video_url"):
        print("❌ No source video"); return
    if not (row.get("beats")):
        print("❌ No beats — run Beats first."); return

    db_patch(RECORD_ID, {"status": "rendering"})
    with tempfile.TemporaryDirectory() as tmp:
        out = render(row, tmp)
        if not out:
            db_patch(RECORD_ID, {"status": "beats_ready"}); return
        print("\n☁️  Uploading studio render...", flush=True)
        url = upload_to_gcs(out, f"anchor/{RECORD_ID}/{LANGUAGE}/studio_final.mp4")
        if not url:
            db_patch(RECORD_ID, {"status": "beats_ready"}); return
        db_patch(RECORD_ID, {"video_url": url, "status": "rendered"})
    print(f"\n{'='*60}", flush=True)
    print(f"✅ Studio video rendered — review in dashboard, then Publish.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
