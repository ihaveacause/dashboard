"""
shorts_generate_images.py — Sprint 15 (Shorts track)
======================================================
Generates 9:16 vertical images for ONE short (by id), matching the visual
style already established for its parent episode — everything about the
short is "mimicked from the long version" except the script, so the images
here follow the exact same visual-direction approach as the episode/idea
image pipelines, just cropped for vertical.

Mirrors idea_image_pipeline.py exactly (same Imagen 3 model, same auth,
same Supabase storage bucket) — only the aspect ratio and source table differ.

Triggered by: shorts_generate_images.yml
Env vars: SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY,
          GOOGLE_APPLICATION_CREDENTIALS_JSON, SHORT_ID, LANGUAGE (ta or en)
"""

import os
import json
import base64
import requests
from google import genai
from google.oauth2 import service_account
import google.auth.transport.requests
from datetime import datetime

# ── Config ─────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SHORT_ID       = os.environ["SHORT_ID"]
LANGUAGE       = os.environ.get("LANGUAGE", "ta")
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

PROJECT_ID   = "gen-lang-client-0078128013"
LOCATION     = "us-central1"
IMAGEN_MODEL = "imagen-3.0-fast-generate-001"
IMAGEN_SLEEP = 20

SHORTS_TABLE  = "tamil_shorts"   if LANGUAGE == "ta" else "english_shorts"
EPISODE_TABLE = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"

# ── Clients ─────────────────────────────────────────────────
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

def get_vertex_token():
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

def db_get_one(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch_short(data):
    r = requests.patch(
        f"{REST}/{SHORTS_TABLE}?id=eq.{SHORT_ID}",
        headers=SB_HEADERS, json=data, timeout=30
    )
    if r.status_code not in (200, 204):
        print(f"  ❌ Supabase error {r.status_code}: {r.text[:300]}")
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

def call_with_retry(fn, max_retries=4, wait=30):
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

# ── Step 1: Scene descriptions (5 beats — faster cutting reads as more produced) ────
def generate_scene_descriptions(short, episode):
    print(f"\n🎨 Step 1: Generating scene descriptions...")
    title  = short.get("title", "")
    script = short.get("script", "")
    ep_title = episode.get("title_tamil") or episode.get("title_english") or "" if episode else ""

    prompt = f"""You are a visual director for "I Have a Cause" — a philosophy/social-reform
YouTube channel. This is a 45-60 second Short, cut to feel like it belongs to the same
visual world as its parent long-form episode.

Short title: {title}
Parent episode: {ep_title}

SHORT SCRIPT:
{script}

Design exactly 5 VERTICAL (9:16) scene images that follow this short's own tiny arc — 5
beats over ~50s means each holds the screen for ~8-10s, which cuts noticeably faster and
reads as more "produced" than a slow 3-image slideshow:
- Scene 1: the opening hook — striking, immediate, matches the first line's energy
- Scene 2: building the claim — the first piece of evidence or escalation
- Scene 3: the core argument at its sharpest point — the centre of the short
- Scene 4: the turn — where the tension tightens toward the close
- Scene 5: the closing beat — visually open-ended, leaves tension unresolved (matches the hook ending)

Each scene should feel like a distinct beat, not a repeat of the previous one — vary
composition, distance (wide/medium/close), and framing across the 5 so the cutting itself
carries energy.

Choose a visual style that fits the content (photorealistic/documentary for social topics,
painterly/symbolic for philosophical ones, bold/graphic for reform topics) — same
visual-direction approach the channel already uses for its long-form episodes, just
composed for a vertical 9:16 frame with the subject centered so it reads well on mobile.
Favor a single clear subject reasonably separated from its background in at least 3 of the
5 scenes (not flat/abstract patterns) — those scenes get a foreground/background depth
effect in the edit, which needs a distinguishable subject to work.

CRITICAL RULE: no text, letters, words, numbers, writing, or watermarks anywhere in the image.

Return ONLY valid JSON:
{{
  "scenes": [
    {{"id": 1, "label": "short scene label", "prompt": "detailed vertical visual prompt, no text"}},
    {{"id": 2, "label": "short scene label", "prompt": "detailed vertical visual prompt, no text"}},
    {{"id": 3, "label": "short scene label", "prompt": "detailed vertical visual prompt, no text"}},
    {{"id": 4, "label": "short scene label", "prompt": "detailed vertical visual prompt, no text"}},
    {{"id": 5, "label": "short scene label", "prompt": "detailed vertical visual prompt, no text"}}
  ]
}}"""

    def _call():
        response = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)

    data = call_with_retry(_call)
    print(f"   ✅ {len(data['scenes'])} scene descriptions generated")
    return data["scenes"]

# ── Step 2: Imagen 3, vertical ──────────────────────────────
def generate_image_vertex(prompt):
    token = get_vertex_token()
    full_prompt = (
        f"{prompt} "
        f"Vertical 9:16 mobile frame, cinematic, professional photography or digital art, "
        f"ultra detailed, high quality, subject centered for a Shorts/Reels crop. "
        f"Absolutely no text, no letters, no words, no numbers, "
        f"no writing, no watermarks, no labels, no captions, "
        f"no signs, no symbols with meaning anywhere in the image."
    )
    url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{PROJECT_ID}/locations/{LOCATION}/"
        f"publishers/google/models/{IMAGEN_MODEL}:predict"
    )
    payload = {
        "instances": [{"prompt": full_prompt}],
        "parameters": {
            "sampleCount":      1,
            "aspectRatio":      "9:16",
            "outputMimeType":   "image/jpeg",
            "safetyFilterLevel":"block_few",
            "personGeneration": "allow_adult"
        }
    }
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=120
    )
    if r.status_code == 200:
        b64 = r.json()["predictions"][0]["bytesBase64Encoded"]
        return base64.b64decode(b64)
    print(f"      ❌ Vertex API error {r.status_code}: {r.text[:300]}")
    return None

def generate_images(scenes):
    import time
    print(f"\n🖼  Step 2: Generating vertical images with {IMAGEN_MODEL}...")
    image_urls = []
    short_slug = SHORT_ID[:8]

    for scene in scenes:
        print(f"   Scene {scene['id']}: {scene['label']}...")
        try:
            image_bytes = call_with_retry(
                lambda p=scene["prompt"]: generate_image_vertex(p),
                max_retries=3, wait=25
            )
            if not image_bytes:
                continue
            time.sleep(IMAGEN_SLEEP)
            storage_path = f"shorts/{short_slug}/scene_{scene['id']}.jpg"
            url = upload_to_storage("episode-images", storage_path, image_bytes)
            if url:
                image_urls.append({
                    "id": scene["id"], "label": scene["label"],
                    "url": url, "prompt": scene["prompt"]
                })
                print(f"      ✅ Uploaded: {storage_path}")
        except Exception as e:
            print(f"      ❌ Scene {scene['id']} error: {e}")

    print(f"   ✅ {len(image_urls)}/{len(scenes)} images generated")
    return image_urls

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🖼  Shorts Image Pipeline — {SHORT_ID}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    short = db_get_one(SHORTS_TABLE, {"id": f"eq.{SHORT_ID}", "select": "*"})
    if not short:
        print(f"❌ Short {SHORT_ID} not found")
        return
    episode = db_get_one(EPISODE_TABLE, {
        "episode_number": f"eq.{short['episode_number']}", "select": "title_tamil,title_english"
    })

    if not short.get("script"):
        print("❌ No script found — run the script step first")
        return

    db_patch_short({"status": "generating_images"})

    try:
        scenes     = generate_scene_descriptions(short, episode)
        image_urls = generate_images(scenes)

        if not image_urls:
            print("❌ No images generated — aborting")
            db_patch_short({"status": "script_approved"})
            return

        ok = db_patch_short({
            "image_urls_vertical": image_urls,
            "status":              "images_ready",
        })

        if ok:
            print(f"\n{'='*60}")
            print(f"✅ Short — {len(image_urls)} vertical images ready!")
            print(f"{'='*60}")
        else:
            print("❌ Failed to save to Supabase")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch_short({"status": "script_approved"})

if __name__ == "__main__":
    main()
