"""
I Have a Cause — Idea Image Pipeline (Vertex AI)
=================================================
Mirrors image_pipeline.py exactly — same auth, same Imagen 3.
Differences: reads from `ideas` table by IDEA_ID (UUID).
             stores images at ideas/{idea_id}/scene_{n}.jpg
             updates image_urls_landscape, infographic_svg, status
"""

import os
import re
import json
import base64
import requests
import xml.etree.ElementTree as ET
from google import genai
from google.oauth2 import service_account
import google.auth.transport.requests
from datetime import datetime
from pathlib import Path

# ── Config ─────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
IDEA_ID        = os.environ["IDEA_ID"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

PROJECT_ID   = "gen-lang-client-0078128013"
LOCATION     = "us-central1"
IMAGEN_MODEL = "imagen-3.0-fast-generate-001"
IMAGEN_SLEEP = 20

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

def db_get_idea():
    r = requests.get(
        f"{REST}/ideas",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params={"id": f"eq.{IDEA_ID}", "select": "*"}, timeout=15
    )
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch_idea(data):
    r = requests.patch(
        f"{REST}/ideas?id=eq.{IDEA_ID}",
        headers=SB_HEADERS, json=data, timeout=15
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

def validate_svg(svg_text):
    try:
        ET.fromstring(svg_text)
        return True, None
    except ET.ParseError as e:
        return False, str(e)

# ── Retry helper ────────────────────────────────────────────
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

# ── Step 1: Scene descriptions ──────────────────────────────
def generate_scene_descriptions(idea):
    print(f"\n🎨 Step 1: Generating scene descriptions...")
    title       = idea.get("title", "")
    description = idea.get("description", "")
    tamil_script   = str(idea.get("script_tamil",   "") or "")[:2000]
    english_script = str(idea.get("script_english", "") or "")[:2000]
    script_context = tamil_script if tamil_script else english_script

    prompt = f"""You are a visual director for "I Have a Cause" — a Tamil philosophy YouTube channel.

Read this idea carefully and design 5 unique scene images that VISUALLY REPRESENT
the specific concepts, emotions, and arguments in this video.

Title: {title}
Description: {description}

SCRIPT (first 2000 chars):
{script_context}

YOUR TASK:
Design 5 scene images that follow the NARRATIVE ARC of this specific idea:
- Scene 1: A striking visual representing the opening hook/question
- Scene 2: A visual representing the core concept or problem being raised
- Scene 3: A visual representing the key story, metaphor or analogy
- Scene 4: A visual representing the deeper insight or turning point
- Scene 5: A visual representing the resolution, wisdom or call to action

YOUR VISUAL APPROACH:
First, understand what this idea is actually about — is it social reform? Animal welfare?
Philosophy? Science? Current events? Emotion?

Then choose the RIGHT visual style for THAT content:
- For social/emotional topics: photorealistic, documentary-style, human-centred, warm tones
- For philosophical/spiritual topics: painterly, symbolic, ethereal, deep colours
- For scientific topics: clean, technical, data-inspired, structured
- For nature/animal topics: naturalistic, vivid, real environments
- For political/reform topics: bold, graphic, high contrast, powerful composition

Each scene must feel like it was designed specifically for THIS idea — not recycled from
a generic philosophy channel. The style must serve the story.

CRITICAL RULE: Prompts must NOT include any text, letters, words, signs,
writing or language of any kind in the image. Pure visual only.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "scenes": [
    {{"id": 1, "label": "short scene label", "prompt": "detailed visual prompt, no text"}},
    {{"id": 2, "label": "short scene label", "prompt": "detailed visual prompt, no text"}},
    {{"id": 3, "label": "short scene label", "prompt": "detailed visual prompt, no text"}},
    {{"id": 4, "label": "short scene label", "prompt": "detailed visual prompt, no text"}},
    {{"id": 5, "label": "short scene label", "prompt": "detailed visual prompt, no text"}}
  ]
}}"""

    def _call():
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)

    data = call_with_retry(_call)
    print(f"   ✅ {len(data['scenes'])} scene descriptions generated")
    for s in data["scenes"]:
        print(f"      Scene {s['id']}: {s['label']}")
    return data["scenes"]

# ── Step 2: Imagen 3 ────────────────────────────────────────
def generate_image_vertex(prompt, scene_id):
    token = get_vertex_token()
    full_prompt = (
        f"{prompt} "
        f"Cinematic 16:9, professional photography or digital art, "
        f"ultra detailed, high quality, award winning composition. "
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
            "aspectRatio":      "16:9",
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
    print(f"\n🖼  Step 2: Generating images with {IMAGEN_MODEL}...")
    image_urls = []
    idea_slug  = IDEA_ID[:8]  # first 8 chars of UUID for path

    for scene in scenes:
        print(f"   Scene {scene['id']}: {scene['label']}...")
        try:
            image_bytes = call_with_retry(
                lambda p=scene["prompt"], i=scene["id"]: generate_image_vertex(p, i),
                max_retries=3, wait=25
            )
            if not image_bytes:
                continue
            time.sleep(IMAGEN_SLEEP)
            storage_path = f"ideas/{idea_slug}/scene_{scene['id']}.jpg"
            url = upload_to_storage("episode-images", storage_path, image_bytes)
            if url:
                image_urls.append({
                    "id":     scene["id"],
                    "label":  scene["label"],
                    "url":    url,
                    "prompt": scene["prompt"]
                })
                print(f"      ✅ Uploaded: {storage_path}")
        except Exception as e:
            print(f"      ❌ Scene {scene['id']} error: {e}")

    print(f"   ✅ {len(image_urls)}/{len(scenes)} images generated")
    return image_urls

# ── Step 3: SVG Infographic ─────────────────────────────────
def generate_svg_infographic(idea):
    print(f"\n📊 Step 3: Generating SVG infographic...")
    title       = idea.get("title", "")
    description = idea.get("description", "")
    tamil_script   = str(idea.get("script_tamil",   "") or "")[:1500]
    english_script = str(idea.get("script_english", "") or "")[:1500]
    script_context = tamil_script if tamil_script else english_script

    prompt = f"""Create a stunning SVG infographic (1920x1080px) for this Tamil philosophy video.

Title: {title}
Description: {description}

SCRIPT (for context):
{script_context}

Design an SVG diagram that VISUALLY EXPLAINS the core concept of this specific idea.
- Represent the key philosophical model, argument or framework
- Use geometric shapes, circles, arrows, or flow diagrams as appropriate
- Deep space black background with subtle star field
- Color scheme: gold, indigo, deep blue, white — glowing, ethereal
- Use SVG gradients and feGaussianBlur filters for glow effects
- "I Have a Cause" watermark bottom-right in small muted text
- Labels in both Tamil and English where appropriate

CRITICAL: Return ONLY valid, well-formed SVG XML starting with <svg and ending with </svg>.
No markdown. No explanation. Every attribute properly quoted."""

    import time
    for attempt in range(1, 4):
        print(f"   🔄 SVG attempt {attempt}/3...")
        def _call_svg():
            return gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        response = call_with_retry(_call_svg)
        svg = response.text.strip()
        if "```svg" in svg:
            svg = svg.split("```svg")[1].split("```")[0].strip()
        elif "```" in svg:
            svg = svg.split("```")[1].split("```")[0].strip()
        if not svg.startswith("<svg"):
            print(f"   ❌ Attempt {attempt}: Does not start with <svg")
            time.sleep(10)
            continue
        is_valid, error = validate_svg(svg)
        if not is_valid:
            print(f"   ❌ Attempt {attempt}: Invalid SVG — {error}")
            time.sleep(10)
            continue
        print(f"   ✅ SVG validated")
        idea_slug    = IDEA_ID[:8]
        storage_path = f"ideas/{idea_slug}/infographic.svg"
        url = upload_to_storage("episode-images", storage_path, svg.encode("utf-8"), "image/svg+xml")
        print(f"   {'✅' if url else '❌'} Infographic {'uploaded' if url else 'failed'}")
        return svg, url
    print(f"   ❌ SVG generation failed after 3 attempts")
    return None, None

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🖼  Idea Image Pipeline — {IDEA_ID}")
    print(f"   Model: {IMAGEN_MODEL}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    idea = db_get_idea()
    if not idea:
        print(f"❌ Idea {IDEA_ID} not found")
        return

    print(f"\n📖 {idea.get('title', '')}")

    if not idea.get("script_tamil") and not idea.get("script_english"):
        print("❌ No script found — run idea script generator first")
        return

    db_patch_idea({"status": "generating_images"})

    try:
        scenes     = generate_scene_descriptions(idea)
        image_urls = generate_images(scenes)
        svg, svg_url = generate_svg_infographic(idea)

        if not image_urls:
            print("❌ No images generated — aborting")
            db_patch_idea({"status": "script_approved"})
            return

        ok = db_patch_idea({
            "image_urls_landscape": json.dumps(image_urls),
            "infographic_svg":      json.dumps({"svg": svg, "url": svg_url}) if svg else None,
            "status":               "images_ready",
        })

        if ok:
            print(f"\n{'='*60}")
            print(f"✅ Idea — {len(image_urls)} images ready!")
            print(f"{'='*60}")
        else:
            print("❌ Failed to save to Supabase")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch_idea({"status": "script_approved"})

if __name__ == "__main__":
    main()
