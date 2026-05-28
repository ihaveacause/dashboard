"""
generate_thumbnail.py — New YouTube Pipeline
=============================================
Takes your uploaded thumbnail image and composites:
  - Your circular narrator photo (optional — controlled by thumbnail_add_photo)
  - Channel logo (always added, bottom-right)

Inputs from Supabase:
  - thumbnail_hook_image_url  — your thumbnail image uploaded from dashboard
  - thumbnail_add_photo       — true/false

Assets from GCS:
  - channel-assets/photo_{language}.jpg
  - ihaveacause_logo.png

Output: 1280x720 JPEG uploaded to GCS, signed URL saved to Supabase.
Status → thumbnail_ready

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER, LANGUAGE
"""

import os
import io
import json
import base64
import datetime
import requests
from PIL import Image, ImageDraw

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

GCS_BUCKET = "ihaveacause-media"
W, H       = 1280, 720
PHOTO_SIZE = 200
LOGO_SIZE  = 90

# ── Supabase ──────────────────────────────────────────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(table, params):
    r = requests.get(f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15)
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data):
    r = requests.patch(f"{REST}/{table}?episode_number=eq.{val}",
        headers=SB_HEADERS, json=data, timeout=30)
    return r.status_code in (200, 204)

# ── GCS ───────────────────────────────────────────────────────
def gcs_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as google_requests
    creds_info  = json.loads(GCP_CREDS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google_requests.Request())
    return credentials.token, creds_info

def gcs_download_path(gcs_path):
    token, _ = gcs_token()
    encoded  = requests.utils.quote(gcs_path, safe="")
    url      = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}?alt=media"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code == 200:
        print(f"   ✅ Downloaded {gcs_path} ({len(r.content)//1024}KB)", flush=True)
        return r.content
    print(f"   ❌ Failed {r.status_code}: {gcs_path}", flush=True)
    return None

def download_url(url):
    """Download from any URL — Supabase or public."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 200:
        print(f"   ✅ Downloaded {url[:60]} ({len(r.content)//1024}KB)", flush=True)
        return r.content
    print(f"   ❌ Failed {r.status_code}: {url[:60]}", flush=True)
    return None

def gcs_upload_and_sign(local_path, gcs_path, content_type="image/jpeg", days=30):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    token, creds_info = gcs_token()
    with open(local_path, "rb") as f:
        r = requests.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
            params={"uploadType": "media", "name": gcs_path},
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            data=f, timeout=120)
    if r.status_code not in (200, 201):
        print(f"   ❌ Upload failed {r.status_code}: {r.text[:200]}", flush=True)
        return None
    expiry_ts      = int((datetime.datetime.utcnow() + datetime.timedelta(days=days)).timestamp())
    string_to_sign = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    private_key    = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None,
        backend=default_backend())
    signature   = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")
    signed_url  = (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
                   f"?GoogleAccessId={creds_info['client_email']}"
                   f"&Expires={expiry_ts}&Signature={encoded_sig}")
    print(f"   ✅ Uploaded + signed: {gcs_path}", flush=True)
    return signed_url

def circle_crop(img, size):
    img  = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result

# ── Main ──────────────────────────────────────────────────────
def main():
    def log(msg): print(msg, flush=True)

    log("=" * 60)
    log(f"🖼️  Thumbnail Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    log("=" * 60)

    table   = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    ep_rows = db_get(table, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    episode = ep_rows[0] if ep_rows else None
    if not episode:
        log(f"❌ Episode {EPISODE_NUMBER} not found"); return

    hook_image_url = episode.get("thumbnail_hook_image_url", "")
    add_photo      = episode.get("thumbnail_add_photo", True)

    if not hook_image_url:
        log("❌ No thumbnail image uploaded — upload image first"); return

    log(f"   ✅ Episode: {episode.get('title_english') or episode.get('title_tamil')}")
    log(f"   ℹ️  Add photo: {add_photo}")
    db_patch(table, EPISODE_NUMBER, {"status": "generating_thumbnail"})

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        lang_code = "ta" if LANGUAGE == "ta" else "en"

        # 1. Download thumbnail image
        log(f"\n📥 Downloading thumbnail image...")
        img_data = download_url(hook_image_url)
        if not img_data:
            log("❌ Thumbnail image download failed")
            db_patch(table, EPISODE_NUMBER, {"status": "video_approved"}); return
        canvas = Image.open(io.BytesIO(img_data)).convert("RGBA").resize((W, H), Image.LANCZOS)
        log(f"   ✅ Image loaded: {canvas.size}")

        # 2. Narrator photo (optional)
        if add_photo:
            log(f"\n👤 Downloading narrator photo...")
            photo_key  = f"channel-assets/photo_{'tamil' if lang_code=='ta' else 'english'}.jpg"
            photo_data = gcs_download_path(photo_key)
            if photo_data:
                photo    = Image.open(io.BytesIO(photo_data)).convert("RGBA")
                ph_circle = circle_crop(photo, PHOTO_SIZE)
                # White ring border
                ring = Image.new("RGBA", (PHOTO_SIZE+8, PHOTO_SIZE+8), (0,0,0,0))
                ImageDraw.Draw(ring).ellipse((0,0,PHOTO_SIZE+8,PHOTO_SIZE+8), fill=(255,255,255,200))
                px = 20
                py = H - PHOTO_SIZE - 28
                canvas.paste(ring, (px, py), ring)
                canvas.paste(ph_circle, (px+4, py+4), ph_circle)
                log(f"   ✅ Narrator photo added")
            else:
                log(f"   ⚠️  Photo download failed — continuing without photo")

        # 3. Logo (always added)
        log(f"\n🔱 Downloading logo...")
        logo_data = gcs_download_path("ihaveacause_logo.png")
        if logo_data:
            logo = Image.open(io.BytesIO(logo_data)).convert("RGBA")
            logo = logo.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
            lx   = W - LOGO_SIZE - 20
            ly   = H - LOGO_SIZE - 20
            canvas.paste(logo, (lx, ly), logo)
            log(f"   ✅ Logo added")
        else:
            log(f"   ⚠️  Logo download failed — continuing without logo")

        # 4. Save + upload
        output_path = os.path.join(tmpdir, "thumbnail.jpg")
        canvas.convert("RGB").save(output_path, "JPEG", quality=92)
        size_kb = os.path.getsize(output_path) // 1024
        log(f"   ✅ Thumbnail saved: {size_kb}KB")

        log(f"\n☁️  Uploading to GCS...")
        gcs_path  = f"episodes/ep{EPISODE_NUMBER:03d}/{lang_code}/thumbnail.jpg"
        signed_url = gcs_upload_and_sign(output_path, gcs_path)
        if not signed_url:
            log("❌ Upload failed")
            db_patch(table, EPISODE_NUMBER, {"status": "video_approved"}); return

        db_patch(table, EPISODE_NUMBER, {
            "thumbnail_url": signed_url,
            "status":        "thumbnail_ready",
        })

        log(f"\n{'='*60}")
        log(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — thumbnail ready!")
        log(f"{'='*60}")

if __name__ == "__main__":
    main()
