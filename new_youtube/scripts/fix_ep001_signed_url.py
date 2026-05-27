"""
fix_ep001_signed_url.py — One-time fix for Episode 1
=====================================================
Generates a 30-day signed URL for the already-uploaded
episodes/ep001/en/final.mp4 and updates Supabase.

Run once via GitHub Actions or locally.

Env vars needed:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
"""

import os
import json
import base64
import datetime
import requests

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

GCS_BUCKET = "ihaveacause-media"
GCS_PATH   = "episodes/ep001/en/final.mp4"
TABLE      = "english_episodes"
EPISODE    = 1

def generate_signed_url(gcs_path, days=30):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    creds_info   = json.loads(GCP_CREDS_JSON)
    expiry_ts    = int((datetime.datetime.utcnow() + datetime.timedelta(days=days)).timestamp())

    string_to_sign = "\n".join([
        "GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"
    ])

    private_key = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"),
        password=None,
        backend=default_backend()
    )
    signature    = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    encoded_sig  = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")

    return (
        f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
        f"?GoogleAccessId={creds_info['client_email']}"
        f"&Expires={expiry_ts}"
        f"&Signature={encoded_sig}"
    )

def main():
    print("🔗 Generating signed URL for ep001/en...")
    signed_url = generate_signed_url(GCS_PATH, days=30)
    print(f"   ✅ Signed URL generated (valid 30 days)")

    print("💾 Updating Supabase...")
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{TABLE}?episode_number=eq.{EPISODE}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        },
        json={"video_url": signed_url},
        timeout=15
    )

    if r.status_code in (200, 204):
        print(f"   ✅ Supabase updated")
        print(f"\n✅ Done! Open your dashboard to preview Episode 1.")
        print(f"\nURL: {signed_url[:80]}...")
    else:
        print(f"   ❌ Supabase update failed {r.status_code}: {r.text}")

if __name__ == "__main__":
    main()
