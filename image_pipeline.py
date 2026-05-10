"""
I Have a Cause — Image Pipeline (Vertex AI)
============================================
Uses Vertex AI Imagen 3 for high quality cosmic/surreal images
and Gemini for SVG infographic generation.
"""

import os
import json
import base64
import requests
import tempfile
from google import genai
from google.genai import types
from google.oauth2 import service_account
import google.auth.transport.requests
from datetime import datetime
from pathlib import Path

# ── Config ─────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"

# ── Clients ─────────────────────────────────────────────────
# Gemini client (for scene descriptions + SVG)
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Vertex AI credentials (for Imagen 3)
creds_info = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

def get_vertex_token():
    """Get fresh access token for Vertex AI REST calls."""
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token

# ── Supabase helpers ────────────────────────────────────────
SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, n, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{n}",
        headers=SB_HEADERS, json=data, timeout=15
    )
    return r.status_code in (200, 204)

def upload_to_storage(bucket, path, data_bytes, content_type="image/jpeg"):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  content_type,
            "x-upsert":      "true"
        },
        data=data_bytes, timeout=120
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

# ── Fetch episode ───────────────────────────────────────────
def fetch_episode():
    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}",
        "select": "*"
    })
    return rows[0] if rows else None

def fetch_preferences():
    rows = db_get("channel_preferences", {
        "is_active": "eq.true",
        "select": "category,preference"
    })
    prefs = [r["preference"] for r in rows if r["category"] == "image"]
    return "\n".join(f"- {p}" for p in prefs) if prefs else ""

# ── Retry helper ────────────────────────────────────────────
def call_gemini_with_retry(fn, max_retries=4, wait=30):
    import time
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"   ⚠️  Attempt {attempt+1} failed: {str(e)[:80]}")
                print(f"   ⏳ Waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                raise

# ── Step 1: Scene descriptions ──────────────────────────────
def generate_scene_descriptions(episode, prefs):
    print(f"\n🎨 Step 1: Generating scene descriptions...")
    pref_block = f"\n\nIMAGE PREFERENCES:\n{prefs}" if prefs else ""

    prompt = f"""You are a visual director for "I Have a Cause" — a Tamil philosophy YouTube channel.

Episode: {episode['episode_number']} — {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Module: {episode['module']}
Bridge: {episode['bridge']}
Script (first 800 chars): {str(episode.get('script_tamil',''))[:800]}{pref_block}

VISUAL STYLE:
- Cosmic/surreal — human as fragile being inside vast cosmic consciousness
- Abstract dream states, ethereal light, infinite space
- Deep blues, purples, indigo, gold light rays, nebulae, sacred geometry
- Painterly and cinematic, NOT realistic photography

Generate EXACTLY 5 scene prompts for Imagen 3. Return ONLY valid JSON:
{{
  "scenes": [
    {{"id": 1, "label": "Hook — Opening Image", "prompt": "detailed prompt"}},
    {{"id": 2, "label": "Waking State — Vaishvanara", "prompt": "detailed prompt"}},
    {{"id": 3, "label": "Dream State — Taijasa", "prompt": "detailed prompt"}},
    {{"id": 4, "label": "Deep Sleep — Prajna", "prompt": "detailed prompt"}},
    {{"id": 5, "label": "Pure Consciousness — Turiya", "prompt": "detailed prompt"}}
  ]
}}"""

    def _call():
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        raw = response.text.strip().replace("```json","").replace("```","").strip()
        return json.loads(raw)

    data = call_gemini_with_retry(_call)
    print(f"   ✅ {len(data['scenes'])} scene descriptions generated")
    return data["scenes"]

# ── Step 2: Imagen 3 via Vertex AI REST API ─────────────────
def generate_image_vertex(prompt, scene_id):
    """Call Imagen 3 via Vertex AI REST API directly."""
    token = get_vertex_token()

    full_prompt = (
        f"{prompt} "
        f"Cinematic 16:9, cosmic surreal philosophy art, "
        f"deep space atmosphere, painterly, ethereal glow, "
        f"ultra detailed, award winning digital art."
    )

    url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{PROJECT_ID}/locations/{LOCATION}/"
        f"publishers/google/models/imagen-3.0-generate-001:predict"
    )

    payload = {
        "instances": [{"prompt": full_prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "16:9",
            "outputMimeType": "image/jpeg",
            "safetyFilterLevel": "block_few",
            "personGeneration": "allow_adult"
        }
    }

    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=120
    )

    if r.status_code == 200:
        data = r.json()
        b64 = data["predictions"][0]["bytesBase64Encoded"]
        return base64.b64decode(b64)
    else:
        print(f"      ❌ Vertex API error {r.status_code}: {r.text[:300]}")
        return None

def generate_images(scenes):
    print(f"\n🖼  Step 2: Generating {len(scenes)} images with Imagen 3 (Vertex AI)...")
    image_urls = []

    for scene in scenes:
        print(f"   Scene {scene['id']}: {scene['label']}...")
        try:
            image_bytes = call_gemini_with_retry(
                lambda p=scene["prompt"], i=scene["id"]: generate_image_vertex(p, i),
                max_retries=3, wait=20
            )
            if not image_bytes:
                continue

            storage_path = f"ep{EPISODE_NUMBER:03d}/scene_{scene['id']}.jpg"
            url = upload_to_storage("episode-images", storage_path, image_bytes)

            if url:
                image_urls.append({
                    "id": scene["id"],
                    "label": scene["label"],
                    "url": url,
                    "prompt": scene["prompt"]
                })
                print(f"      ✅ Uploaded: {storage_path}")
        except Exception as e:
            print(f"      ❌ Scene {scene['id']} error: {e}")

    print(f"   ✅ {len(image_urls)}/{len(scenes)} images generated")
    return image_urls

# ── Step 3: SVG Infographic ─────────────────────────────────
def generate_svg_infographic(episode):
    print(f"\n📊 Step 3: Generating SVG infographic...")

    prompt = f"""Create a stunning SVG infographic (1920x1080px) for this Tamil philosophy episode.

Episode: {episode['episode_number']} — {episode['title_english']}
Bridge: {episode['bridge']}

DESIGN:
- 4 concentric circles, same plane, superimposed, one inside the other
- Outermost to innermost: Vaishvanara (Waking), Taijasa (Dream), Prajna (Deep Sleep), Turiya (Pure Consciousness)
- Deep space black background with star field
- Rings in gold/amber/indigo/white glow with SVG gradients and filter blur
- Tamil + English labels for each ring
- Single pure white light point at center (Turiya)
- "I Have a Cause" watermark bottom right
- Feeling: looking at the universe from inside consciousness

Return ONLY the SVG code starting with <svg and ending with </svg>."""

    def _call_svg():
        return gemini_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )

    response = call_gemini_with_retry(_call_svg)
    svg = response.text.strip()
    if "```svg" in svg:
        svg = svg.split("```svg")[1].split("```")[0].strip()
    elif "```" in svg:
        svg = svg.split("```")[1].split("```")[0].strip()

    storage_path = f"ep{EPISODE_NUMBER:03d}/infographic.svg"
    url = upload_to_storage(
        "episode-images", storage_path,
        svg.encode("utf-8"), "image/svg+xml"
    )
    print(f"   {'✅' if url else '❌'} Infographic {'uploaded' if url else 'failed'}")
    return svg, url

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🖼  Image Pipeline (Vertex AI) — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    print(f"\n📖 {episode['title_english']}")
    print(f"   Bridge: {episode['bridge']}")

    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "generating_images"})
    prefs = fetch_preferences()

    try:
        scenes     = generate_scene_descriptions(episode, prefs)
        image_urls = generate_images(scenes)
        svg, svg_url = generate_svg_infographic(episode)

        if not image_urls:
            print("❌ No images generated — aborting")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "script_approved"})
            return

        ok = db_patch("tamil_episodes", EPISODE_NUMBER, {
            "image_urls":      json.dumps(image_urls),
            "infographic_svg": json.dumps({"svg": svg, "url": svg_url}),
            "status":          "images_ready",
        })

        if ok:
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — {len(image_urls)} images ready!")
            print(f"   Open dashboard to review and approve.")
            print(f"{'='*60}")
        else:
            print("❌ Failed to save to Supabase")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "script_approved"})

if __name__ == "__main__":
    main()
