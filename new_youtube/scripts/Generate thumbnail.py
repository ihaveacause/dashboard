"""
generate_thumbnail.py — New YouTube Pipeline
=============================================
Generates a YouTube thumbnail (1280x720) for an episode.

- Tamil episode: Tamil title (large) + English title (smaller below)
- English episode: English title only (large)
- Background: channel-assets/thumbnail_bg_{language}.png from GCS
- Narrator photo: channel-assets/photo_{language}.jpg from GCS
- Logo: assets/ihaveacause_symbol.svg from repo
- Output: uploaded to GCS episodes/ep{N:03d}/{lang}/thumbnail.jpg
- Signed URL saved to Supabase thumbnail_url column

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER
  LANGUAGE — ta or en
"""

import os
import io
import json
import base64
import datetime
import requests
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

GCS_BUCKET     = "ihaveacause-media"

# ── Thumbnail settings ────────────────────────────────────────
W, H           = 1280, 720
PHOTO_SIZE     = 240
PHOTO_MARGIN   = 40
LOGO_SIZE      = 160

# Colours
BADGE_COLOUR   = (180, 0, 58, 220)
WHITE          = (255, 255, 255, 255)
SHADOW         = (0, 0, 0, 200)
SUBTITLE_COL   = (220, 200, 255, 220)

# Font sizes
EPISODE_FS     = 40
TITLE_FS       = 80
SUBTITLE_FS    = 44
CHANNEL_FS     = 28

# ── Supabase helpers ──────────────────────────────────────────
SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{val}",
        headers=SB_HEADERS, json=data, timeout=30
    )
    return r.status_code in (200, 204)

# ── GCS helpers ───────────────────────────────────────────────
def gcs_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as google_requests
    creds_info  = json.loads(GCP_CREDS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google_requests.Request())
    return credentials.token, creds_info

def gcs_download(gcs_path):
    """Download a file from GCS and return bytes."""
    token, _ = gcs_token()
    encoded  = requests.utils.quote(gcs_path, safe="")
    url      = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}?alt=media"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code == 200:
        print(f"   ✅ Downloaded {gcs_path} ({len(r.content)//1024}KB)")
        return r.content
    print(f"   ❌ GCS download failed {r.status_code}: {gcs_path}")
    return None

def gcs_upload_and_sign(local_path, gcs_path, content_type="image/jpeg", days=30):
    """Upload file to GCS and return 30-day signed URL."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    token, creds_info = gcs_token()

    # Upload
    upload_url = f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o"
    with open(local_path, "rb") as f:
        r = requests.post(
            upload_url,
            params={"uploadType": "media", "name": gcs_path},
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            data=f,
            timeout=120
        )
    if r.status_code not in (200, 201):
        print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}")
        return None
    print(f"   ✅ GCS upload complete: {gcs_path}")

    # Sign
    expiry_ts = int((datetime.datetime.utcnow() + datetime.timedelta(days=days)).timestamp())
    string_to_sign = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    private_key = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend()
    )
    signature   = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")
    signed_url  = (
        f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
        f"?GoogleAccessId={creds_info['client_email']}"
        f"&Expires={expiry_ts}&Signature={encoded_sig}"
    )
    print(f"   ✅ Signed URL generated (valid {days} days)")
    return signed_url

# ── Font helpers ──────────────────────────────────────────────
def get_font(size, bold=False, tamil=False):
    """Load best available font — Noto Sans Tamil for Tamil text."""
    if tamil:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansTamil-Bold.otf",
            "/usr/share/fonts/truetype/noto/NotoSansTamil-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
        ]
    elif bold:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            print(f"   ✅ Font loaded: {Path(path).name} ({size}px)")
            return ImageFont.truetype(path, size)
    print(f"   ⚠️  No font found — using default")
    return ImageFont.load_default()

def wrap_text(text, font, max_width):
    """Word-wrap text to fit within max_width pixels."""
    dummy = Image.new("RGBA", (1, 1))
    draw  = ImageDraw.Draw(dummy)
    words, lines, current = text.split(), [], ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def draw_text_shadow(draw, xy, text, font, fill=WHITE, offset=3):
    x, y = xy
    draw.text((x + offset, y + offset), text, font=font, fill=SHADOW)
    draw.text((x, y), text, font=font, fill=fill)

def draw_centered(draw, y, text, font, fill=WHITE, max_w=None, shadow=True):
    """Draw text centered horizontally."""
    dummy = Image.new("RGBA", (1, 1))
    d     = ImageDraw.Draw(dummy)
    bbox  = d.textbbox((0, 0), text, font=font)
    tw    = bbox[2] - bbox[0]
    x     = (W - tw) // 2
    if shadow:
        draw.text((x + 3, y + 3), text, font=font, fill=SHADOW)
    draw.text((x, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]  # return line height

# ── Circle crop ───────────────────────────────────────────────
def circle_crop(img, size):
    img  = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result

# ── Main thumbnail generator ──────────────────────────────────
def generate_thumbnail(episode, lang_code, output_path):
    import tempfile

    title_tamil   = episode.get("title_tamil", "") or ""
    title_english = episode.get("title_english", "") or ""

    print(f"\n🎨 Generating thumbnail...")
    print(f"   Tamil title:   {title_tamil}")
    print(f"   English title: {title_english}")

    # 1. Download background from GCS
    print(f"\n📥 Downloading assets from GCS...")
    bg_key    = f"channel-assets/thumbnail_bg_{'tamil' if lang_code == 'ta' else 'english'}.png"
    photo_key = f"channel-assets/photo_{'tamil' if lang_code == 'ta' else 'english'}.jpg"

    bg_data    = gcs_download(bg_key)
    photo_data = gcs_download(photo_key)

    if not bg_data:
        print("❌ Background image download failed")
        return False

    # 2. Build base image
    bg    = Image.open(io.BytesIO(bg_data)).convert("RGBA").resize((W, H), Image.LANCZOS)
    thumb = Image.new("RGBA", (W, H))
    thumb.paste(bg, (0, 0))

    # 3. Dark gradient overlay (bottom 65%) for text readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    for i in range(H):
        alpha = int(180 * max(0, (i - H * 0.30) / (H * 0.70)))
        ov_draw.line([(0, i), (W, i)], fill=(0, 0, 0, alpha))
    thumb = Image.alpha_composite(thumb, overlay)

    draw = ImageDraw.Draw(thumb)

    # 4. Episode badge (top-left)
    badge_font = get_font(EPISODE_FS, bold=True)
    badge_text = f"EP {EPISODE_NUMBER:02d}"
    bbox       = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw = bbox[2] - bbox[0] + 40
    bh = bbox[3] - bbox[1] + 20
    draw.rounded_rectangle([40, 35, 40 + bw, 35 + bh], radius=10, fill=BADGE_COLOUR)
    draw.text((60, 45), badge_text, font=badge_font, fill=WHITE)

    # 5. Channel name (top, after badge)
    ch_font = get_font(CHANNEL_FS)
    draw.text((50 + bw + 20, 50), "I Have a Cause", font=ch_font, fill=(220, 180, 255, 200))

    # 6. Logo (top-right) — from SVG in repo
    logo_final = None
    logo_svg   = "assets/ihaveacause_symbol.svg"
    if os.path.exists(logo_svg):
        try:
            import cairosvg
            png_data   = cairosvg.svg2png(url=logo_svg, output_width=LOGO_SIZE, output_height=LOGO_SIZE)
            logo_img   = Image.open(io.BytesIO(png_data)).convert("RGBA")
            logo_final = logo_img
            print(f"   ✅ Logo loaded from SVG")
        except Exception as e:
            print(f"   ⚠️  cairosvg failed: {e} — skipping logo")
    else:
        print(f"   ⚠️  Logo SVG not found at {logo_svg} — skipping")

    if logo_final:
        lx = W - LOGO_SIZE - 30
        ly = 20
        thumb.paste(logo_final, (lx, ly), logo_final)

    # 7. Title text
    # Text area: left side, avoid narrator photo on right
    text_max_w = W - PHOTO_SIZE - PHOTO_MARGIN * 3 - 60

    if lang_code == "ta":
        # Tamil title large + English subtitle below
        ta_font = get_font(TITLE_FS, tamil=True)
        en_font = get_font(SUBTITLE_FS, bold=True)

        ta_lines = wrap_text(title_tamil, ta_font, text_max_w)
        en_lines = wrap_text(title_english, en_font, text_max_w) if title_english else []

        line_h_ta = TITLE_FS + 16
        line_h_en = SUBTITLE_FS + 12
        total_h   = len(ta_lines) * line_h_ta + (len(en_lines) * line_h_en + 12 if en_lines else 0)
        text_y    = H - total_h - 80

        # Decorative line above title
        draw.line([(60, text_y - 16), (min(600, text_max_w + 60), text_y - 16)],
                  fill=(220, 50, 120, 200), width=3)

        for line in ta_lines:
            draw_text_shadow(draw, (60, text_y), line, ta_font)
            text_y += line_h_ta

        if en_lines:
            text_y += 8
            for line in en_lines:
                draw_text_shadow(draw, (60, text_y), line, en_font, fill=SUBTITLE_COL)
                text_y += line_h_en

    else:
        # English only — large bold
        en_font = get_font(TITLE_FS, bold=True)
        lines   = wrap_text(title_english or title_tamil, en_font, text_max_w)
        line_h  = TITLE_FS + 16
        total_h = len(lines) * line_h
        text_y  = H - total_h - 80

        draw.line([(60, text_y - 16), (min(600, text_max_w + 60), text_y - 16)],
                  fill=(220, 50, 120, 200), width=3)

        for line in lines:
            draw_text_shadow(draw, (60, text_y), line, en_font)
            text_y += line_h

        # Channel name below title
        ch_font2 = get_font(CHANNEL_FS)
        draw_text_shadow(draw, (60, text_y + 8), "I Have a Cause", ch_font2,
                         fill=(220, 180, 255, 200))

    # 8. Narrator photo (bottom-right, circular)
    if photo_data:
        photo = Image.open(io.BytesIO(photo_data)).convert("RGBA")
        photo_circle = circle_crop(photo, PHOTO_SIZE)

        # White ring border
        ring_size = PHOTO_SIZE + 8
        ring      = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((0, 0, ring_size, ring_size), fill=(255, 255, 255, 200))
        px = W - ring_size - PHOTO_MARGIN
        py = H - ring_size - PHOTO_MARGIN
        thumb.paste(ring, (px, py), ring)
        thumb.paste(photo_circle, (px + 4, py + 4), photo_circle)
        print(f"   ✅ Narrator photo placed")
    else:
        print(f"   ⚠️  No narrator photo — skipping")

    # 9. Save as JPEG
    final = thumb.convert("RGB")
    final.save(output_path, "JPEG", quality=92, optimize=True)
    size_kb = os.path.getsize(output_path) // 1024
    print(f"   ✅ Thumbnail saved: {size_kb}KB")
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    import sys
    def log(msg): print(msg, flush=True)

    log("=" * 60)
    log(f"🖼️  Thumbnail Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    log("=" * 60)

    table   = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    log(f"\n📡 Fetching episode {EPISODE_NUMBER} from Supabase...")
    ep_rows = db_get(table, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    episode = ep_rows[0] if ep_rows else None
    if not episode:
        log(f"❌ Episode {EPISODE_NUMBER} not found in {table}")
        return

    log(f"   ✅ Episode found: {episode.get('title_english') or episode.get('title_tamil')}")
    db_patch(table, EPISODE_NUMBER, {"status": "generating_thumbnail"})

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        lang_code   = "ta" if LANGUAGE == "ta" else "en"
        output_path = os.path.join(tmpdir, f"ep{EPISODE_NUMBER:03d}_{lang_code}_thumbnail.jpg")

        success = generate_thumbnail(episode, lang_code, output_path)
        if not success:
            log("❌ Thumbnail generation failed")
            db_patch(table, EPISODE_NUMBER, {"status": "video_approved"})
            return

        log(f"\n☁️  Uploading thumbnail to GCS...")
        gcs_path   = f"episodes/ep{EPISODE_NUMBER:03d}/{lang_code}/thumbnail.jpg"
        signed_url = gcs_upload_and_sign(output_path, gcs_path, content_type="image/jpeg")

        if not signed_url:
            log("❌ Thumbnail upload failed")
            db_patch(table, EPISODE_NUMBER, {"status": "video_approved"})
            return

        db_patch(table, EPISODE_NUMBER, {
            "thumbnail_url": signed_url,
            "status":        "thumbnail_ready",
        })

        log(f"\n{'='*60}")
        log(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — thumbnail ready!")
        log(f"{'='*60}")

if __name__ == "__main__":
    main()
