"""
anchor_shorts_render.py — On Camera Shorts Pipeline · Step 2 of 3
==================================================================
Composites your VERTICAL recording into the final Short:

  - Your real footage, scaled + center-cropped to 1080x1920 (9:16). If you
    filmed with the camera held vertically already, this is a clean fit with
    no distortion; if you filmed landscape by mistake, this will zoom in and
    crop the sides, so vertical source footage is what this track expects.
  - ONE persistent overlay, burned in for the entire clip: a semi-transparent
    bar across the bottom of the frame with the "I Have a Cause" logo + name,
    so your footage still shows through behind it.
  - No side panel / lower-third / beat graphics — that studio treatment is
    the landscape On Camera track's look (anchor_render.py). Shorts stay
    simple: your footage + the brand bar.
  - Auto-thumbnail: a branded frame grabbed from partway through the clip.
  - OPTIONAL: publicly-sourced clips (news footage etc.) you've attached via
    the `clips` column, shown as-is (no crop) in a small picture-in-picture
    box floating just above the bottom banner during their time window —
    your face and voice stay the through-line the whole time. If `clips` is
    empty (the default for every recording), this whole block is a no-op
    and the render is identical to before.

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
W, H, FPS  = 1080, 1920, 30

# PiP box for optional overlay clips — sits just above the bottom banner,
# right-aligned, small margin. Clips are shown AS-IS (letterboxed, never
# cropped) inside this box.
PIP_W, PIP_H   = 380, 214
PIP_MARGIN     = 24
BANNER_H       = 210
PIP_X          = W - PIP_W - PIP_MARGIN
PIP_Y          = (H - BANNER_H) - PIP_MARGIN - PIP_H

# Brand palette (matches the dashboard's dark editorial look)
C_BAR      = (10, 12, 18, 190)       # bottom banner fill (semi-transparent — footage shows through)
C_BAR_LINE = (232, 65, 42, 255)      # thin accent line, top edge of the banner (var(--accent))
C_TEXT     = (238, 241, 247, 255)

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


# ── GCS download / upload (mirrors anchor_render.py) ───────────
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
    if is_gcs:
        headers = {"Authorization": f"Bearer {gcs_token('https://www.googleapis.com/auth/devstorage.read_only')}"}
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


# ── Fonts (same lookup as the rest of the repo) ────────────────
def font_paths():
    # Used for the hook line, which IS in the recording's own language —
    # Tamil hook text needs the Tamil font here.
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
    # The brand wordmark ("I Have a Cause" / "@IHaveACause") is always
    # English text, regardless of LANGUAGE — use a dedicated Latin-coverage
    # font for it specifically, separate from font_paths() above (which is
    # language-aware, for the hook line).
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


# ── ffprobe ─────────────────────────────────────────────────────
def ffprobe_dur(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except Exception:
        return 0.0


# ── The persistent bottom banner (built once, overlaid for the whole clip) ──
def build_banner_png(font_path, logo_im, out_path, hook=""):
    """1080x1920 RGBA, transparent everywhere except a semi-opaque bar across
    the bottom ~11% of the frame — footage stays visible through it — carrying
    the logo + 'I Have a Cause' wordmark, plus an optional punchy hook line
    floating just above the bar."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bar_h = 210
    bar_top = H - bar_h
    draw.rectangle([0, bar_top, W, H], fill=C_BAR)
    draw.rectangle([0, bar_top, W, bar_top + 4], fill=C_BAR_LINE)  # thin accent line

    if hook.strip():
        f_hook = load_font(font_path, 44)
        hx, hy = 36, bar_top - 70
        # wrap to at most 2 lines within the frame width
        words, lines, cur = hook.strip().split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=f_hook) <= (W - 72):
                cur = trial
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)
        lines = lines[:2]
        hy = bar_top - 20 - len(lines) * 54
        for ln in lines:
            draw.text((hx + 2, hy + 2), ln, font=f_hook, fill=(0, 0, 0, 160))
            draw.text((hx, hy), ln, font=f_hook, fill=(255, 255, 255, 255))
            hy += 54

    pad = 36
    logo_size = 120
    if logo_im is not None:
        logo = logo_im.resize((logo_size, logo_size))
        logo_y = bar_top + (bar_h - logo_size) // 2
        img.paste(logo, (pad, logo_y), logo if logo.mode == "RGBA" else None)
        text_x = pad + logo_size + 24
    else:
        text_x = pad

    name_font = load_font(english_font_path(), 52)
    tag_font  = load_font(english_font_path(), 30)
    name_txt  = "I Have a Cause"
    tag_txt   = "@IHaveACause"

    # vertically center the two lines of text within the bar
    name_bbox = draw.textbbox((0, 0), name_txt, font=name_font)
    tag_bbox  = draw.textbbox((0, 0), tag_txt, font=tag_font)
    name_h = name_bbox[3] - name_bbox[1]
    tag_h  = tag_bbox[3] - tag_bbox[1]
    gap = 6
    block_h = name_h + gap + tag_h
    block_top = bar_top + (bar_h - block_h) // 2

    draw.text((text_x, block_top), name_txt, font=name_font, fill=C_TEXT)
    draw.text((text_x, block_top + name_h + gap), tag_txt, font=tag_font, fill=(180, 188, 206, 255))

    img.save(out_path)
    return out_path


def make_thumbnail(src_path, src_dur, title, font_path, logo_im, out_path, hook=""):
    """Grab a frame ~35% into the clip and brand it the same way as the video."""
    grab_t = max(0.3, (src_dur or 3) * 0.35)
    frame_path = out_path.replace(".jpg", "_raw.jpg")
    subprocess.run(["ffmpeg", "-y", "-nostdin", "-ss", f"{grab_t:.2f}", "-i", src_path,
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


# ── FFmpeg render ────────────────────────────────────────────────
def render_vertical(src, banner_png, out, clips=None):
    """
    clips: list of {local_path, start, duration, mute_original, kind} —
    already downloaded to local_path by render(). `kind` is 'video' or
    'image' (render() infers it from the file if not already set). Each one
    is:
      - scaled to fit inside PIP_W x PIP_H with NO crop (letterboxed with
        plain black bars if its aspect ratio isn't exactly 16:9)
      - video clips are timeline-shifted with setpts so they start playing
        at `start`; images are just a static frame, so no shift is needed —
        they're looped the same way the banner already is
      - overlaid into the PiP box, visible only during [start, start+duration]
    Clips render in order, on top of your footage, underneath the banner
    (banner is always the last, topmost layer). If `clips` is empty this
    collapses back to exactly the original single-overlay graph.
    """
    clips = clips or []

    IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

    def is_image(c):
        if c.get("kind"):
            return c["kind"] == "image"
        return os.path.splitext(c["local_path"])[1].lower() in IMAGE_EXT

    inputs = ["-i", src]
    for c in clips:
        if is_image(c):
            inputs += ["-loop", "1", "-i", c["local_path"]]
        else:
            inputs += ["-i", c["local_path"]]
    inputs += ["-loop", "1", "-i", banner_png]
    banner_idx = 1 + len(clips)

    fc_parts = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={FPS},setsar=1[base]"
    ]

    # audio for any VIDEO clip explicitly asking to keep its own sound
    # (images never have audio, so they're skipped here regardless of
    # mute_original)
    unmuted_audio_labels = []

    stage = "base"
    for i, c in enumerate(clips):
        idx = i + 1
        start = float(c.get("start", 0) or 0)
        dur = float(c.get("duration", 5) or 5)
        end = start + dur
        clip_label = f"clip{i}"
        next_stage = f"s{i}"
        img = is_image(c)
        shift = "" if img else f",setpts=PTS+{start}/TB"
        fc_parts.append(
            f"[{idx}:v]scale={PIP_W}:{PIP_H}:force_original_aspect_ratio=decrease,"
            f"pad={PIP_W}:{PIP_H}:(ow-iw)/2:(oh-ih)/2:black{shift}[{clip_label}]"
        )
        fc_parts.append(
            f"[{stage}][{clip_label}]overlay={PIP_X}:{PIP_Y}:"
            f"enable='between(t,{start},{end})'[{next_stage}]"
        )
        stage = next_stage

        if not img and not c.get("mute_original", True):
            a_label = f"a{i}"
            fc_parts.append(
                f"[{idx}:a]adelay={int(start*1000)}|{int(start*1000)},"
                f"atrim=0:{end},volume=1[{a_label}]"
            )
            unmuted_audio_labels.append(a_label)

    fc_parts.append(f"[{stage}][{banner_idx}:v]overlay=0:0[vout]")

    audio_map = ["-map", "0:a?"]
    if unmuted_audio_labels:
        mix_inputs = "".join(f"[{lbl}]" for lbl in unmuted_audio_labels)
        fc_parts.append(
            f"[0:a]{mix_inputs}amix=inputs={1+len(unmuted_audio_labels)}:"
            f"duration=first:dropout_transition=0[aout]"
        )
        audio_map = ["-map", "[aout]"]

    fc = ";".join(fc_parts)
    cmd = ["ffmpeg", "-y", "-nostdin", *inputs,
           "-filter_complex", fc,
           "-map", "[vout]", *audio_map,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-shortest",
           "-movflags", "+faststart", "-pix_fmt", "yuv420p", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FFmpeg stderr:", r.stderr[-2000:], flush=True)
        return False
    print(f"   ✅ Rendered: {out} ({os.path.getsize(out)//1024}KB, "
          f"{len(clips)} PiP clip(s))", flush=True)
    return True


def render(row, tmp):
    src = os.path.join(tmp, "source.mp4")
    if not download_file(row["source_video_url"], src, "Recording"):
        return None, None
    src_dur = ffprobe_dur(src)
    print(f"   ⏱  Source duration: {src_dur:.1f}s", flush=True)
    if src_dur > 180:
        print(f"   ⚠️  Source is {src_dur:.0f}s — over YouTube's 3-minute Shorts "
              f"ceiling. It will still render, but YouTube will publish it as a "
              f"regular video, not a Short.", flush=True)

    font_path = font_paths()

    logo_im = None
    try:
        r = requests.get(
            f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/ihaveacause_logo.png?alt=media",
            headers={"Authorization": f"Bearer {gcs_token('https://www.googleapis.com/auth/devstorage.read_only')}"},
            timeout=15)
        if r.status_code == 200:
            logo_im = Image.open(BytesIO(r.content)).convert("RGBA")
            print("   ✅ Logo loaded", flush=True)
    except Exception as e:
        print(f"   ℹ️  Logo skipped: {e}", flush=True)

    title = (row.get("title") or row.get("working_title") or "").strip()
    hook  = (row.get("hook_text") or "").strip()
    thumb_path = make_thumbnail(src, src_dur, title, font_path, logo_im,
                                os.path.join(tmp, "thumbnail.jpg"), hook=hook)

    banner_png = os.path.join(tmp, "banner.png")
    build_banner_png(font_path, logo_im, banner_png, hook=hook)

    # Optional PiP clips — empty by default, so this is a no-op for every
    # recording unless clips were explicitly attached in the dashboard.
    clips_meta = row.get("clips") or []
    clips = []
    for i, c in enumerate(clips_meta):
        if not c.get("clip_url"):
            continue
        local_path = os.path.join(tmp, f"clip_{i}.mp4")
        if download_file(c["clip_url"], local_path, f"Clip {i+1}"):
            clips.append({**c, "local_path": local_path})
        else:
            print(f"   ⚠️  Skipping clip {i+1} — download failed", flush=True)

    out = os.path.join(tmp, "final.mp4")
    ok = render_vertical(src, banner_png, out, clips=clips)
    return (out, thumb_path) if ok else (None, None)


def main():
    print("=" * 60, flush=True)
    print(f"🤳 Anchor Shorts Render — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
    print(f"   {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    print("=" * 60, flush=True)

    row = db_get_one(RECORD_ID)
    if not row:
        print(f"❌ Record {RECORD_ID} not found in {TABLE}"); return
    if not row.get("source_video_url"):
        print("❌ No source_video_url on this record"); return

    db_patch(RECORD_ID, {"status": "rendering"})

    with tempfile.TemporaryDirectory() as tmp:
        video_path, thumb_path = render(row, tmp)
        if not video_path:
            db_patch(RECORD_ID, {"status": "transcribed"}); return

        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        video_url = upload_to_gcs(video_path, f"anchor_shorts/{RECORD_ID}_{stamp}.mp4", "video/mp4")
        if not video_url:
            db_patch(RECORD_ID, {"status": "transcribed"}); return

        thumb_url = None
        if thumb_path and os.path.exists(thumb_path):
            thumb_url = upload_to_gcs(thumb_path, f"anchor_shorts/{RECORD_ID}_{stamp}_thumb.jpg", "image/jpeg")

        db_patch(RECORD_ID, {
            "video_url":     video_url,
            "thumbnail_url": thumb_url,
            "status":        "rendered",
        })

    print(f"\n{'='*60}", flush=True)
    print("✅ Rendered — preview it, then Approve & Publish.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
