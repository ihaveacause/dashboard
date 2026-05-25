"""
generate_images.py — New YouTube Pipeline
==========================================
Generates 5 high-quality, consistent landscape images for one episode.
Uses a 3-pass approach for maximum quality and consistency:
  Pass 1 — Visual Brief: Gemini reads the script and builds a visual world
  Pass 2 — Scene Prompts: Gemini writes 5 cinematographer-level prompts from the brief
  Pass 3 — Consistency Check: Gemini reviews all 5 prompts together for visual harmony
Then Imagen 3 generates from the refined prompts.

Uses new google-genai SDK (safe past June 24 2026 deprecation).
Imagen 3 uses Vertex AI REST API (unaffected by SDK deprecation).

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER
  LANGUAGE — ta or en
"""

import os
import json
import base64
import time
import requests
from datetime import datetime
from google import genai
from google.oauth2 import service_account
import google.auth.transport.requests

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

PROJECT_ID   = "gen-lang-client-0078128013"
LOCATION     = "us-central1"
IMAGEN_MODEL = "imagen-3.0-fast-generate-001"
IMAGEN_SLEEP = 20   # seconds between Imagen calls (rate limit)

# ── Auth ──────────────────────────────────────────────────────
creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
gemini_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
    credentials=credentials,
)

def get_vertex_token():
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token

# ── Supabase ──────────────────────────────────────────────────
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

def upload_to_storage(bucket, path, data_bytes, content_type="image/jpeg"):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  content_type,
            "x-upsert":      "true",
        },
        data=data_bytes, timeout=120
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

# ── Gemini helper ─────────────────────────────────────────────
def gemini(prompt, model="gemini-2.5-flash"):
    response = gemini_client.models.generate_content(model=model, contents=prompt)
    return response.text

def gemini_json(prompt, model="gemini-2.5-flash", retries=3):
    """Call Gemini and parse JSON. Retries on malformed JSON."""
    import re as _re
    for attempt in range(retries):
        try:
            raw = gemini(prompt, model).strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
            arr = _re.search(r'\[[\s\S]*\]', raw)
            if arr:
                return json.loads(arr.group())
            obj = _re.search(r'\{[\s\S]*\}', raw)
            if obj:
                return json.loads(obj.group())
            raise ValueError("No valid JSON in response")
        except Exception as e:
            if attempt < retries - 1:
                print(f"   ⚠️  JSON parse attempt {attempt+1} failed: {str(e)[:80]}. Retrying in 10s...")
                import time; time.sleep(10)
            else:
                raise

# ── Retry helper ──────────────────────────────────────────────
def with_retry(fn, retries=3, wait=20):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt < retries - 1:
                print(f"   ⚠️  Attempt {attempt+1} failed: {str(e)[:80]}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

# ── PASS 1: Visual Brief ──────────────────────────────────────
def build_visual_brief(episode, script):
    print("\n🎨 Pass 1: Building visual brief from script...")

    prompt = f"""You are the visual director for "I Have a Cause" — a Tamil philosophy YouTube channel.

Read this episode script and build a VISUAL BRIEF — a document that defines the visual world of this episode.

Episode: {episode['episode_number']} — {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Bridge: {episode['bridge']}
Module: {episode['module']}

SCRIPT (first 3000 chars):
{script[:3000]}

Build a visual brief covering:

1. EMOTIONAL ARC — what should the viewer FEEL in each of the 5 sections?
   (Opening, Concept Intro, Core Story/Analogy, Deep Insight, Resolution)

2. VISUAL WORLD — what kind of world does this episode live in?
   Is it: urban Chennai streets? Ancient temple settings? Vast natural landscapes?
   Rural Tamil village? Cosmic/abstract space? Choose based on the script content.

3. COLOUR PALETTE — what colours dominate? Give specific descriptions:
   e.g. "golden hour amber, deep forest green, monsoon grey-blue"
   This palette must be consistent across ALL 5 images.

4. LIGHTING MOOD — what lighting defines this episode?
   e.g. "soft diffused morning light", "harsh midday shadow", "warm lamp glow at dusk"

5. CULTURAL ANCHORS — what specific Tamil/South Indian elements appear?
   e.g. temple gopurams, banana trees, red soil, jasmine flowers, oil lamps, kolam patterns

6. CAMERA LANGUAGE — what camera approach fits?
   e.g. "intimate close-ups for emotional moments", "wide establishing shots for cosmic scale"

7. STYLE — what is the overall visual style?
   e.g. "photorealistic documentary", "painterly oil painting", "National Geographic photography"

Return as a structured visual brief document. Be specific. No generic philosophy aesthetics."""

    brief = gemini(prompt, model="gemini-2.5-pro")
    print(f"   ✅ Visual brief ({len(brief)} chars)")
    return brief

# ── PASS 2: Scene Prompts ─────────────────────────────────────
def build_scene_prompts(episode, script, brief):
    print("\n🎬 Pass 2: Writing 5 cinematographer-level scene prompts...")

    prompt = f"""You are a cinematographer writing Imagen 3 image prompts.

EPISODE: {episode['episode_number']} — {episode['title_english']}
SCRIPT EXCERPT: {script[:2000]}

VISUAL BRIEF (follow this exactly):
{brief}

Write 5 scene prompts — one for each section of the episode:
- Scene 1: Opening hook — the image that stops the viewer from scrolling
- Scene 2: Core concept introduction — visual metaphor for the philosophy
- Scene 3: Key story or analogy used in the script — the most tangible image
- Scene 4: Deep insight or emotional turning point
- Scene 5: Resolution and wisdom — the image they remember

RULES for each prompt:
- Start with the SUBJECT and ACTION: "An elderly Tamil woman sitting..."
- Include SPECIFIC LOCATION: real place, not abstract
- Include TIME OF DAY and LIGHT SOURCE: "golden hour backlight through coconut palms"
- Include CAMERA ANGLE: "low angle", "bird's eye", "intimate close-up at 50mm"
- Include VISUAL STYLE from the brief: match it exactly
- Include TAMIL CULTURAL DETAILS from the brief anchor list
- MINIMUM 50 words per prompt — more detail = better image
- NEVER mention: text, writing, letters, words, numbers, signs, watermarks
- NEVER use: floating, cosmic, universe, infinite void, surreal, abstract, ethereal space
- Every image must be PHYSICALLY GROUNDED — a real place, a real person, a real object
- If the topic is philosophical/cosmic, show it through HUMAN MOMENTS and REAL ENVIRONMENTS
  e.g. NOT "cosmic consciousness floating in space" → YES "an old man meditating at dawn by a temple tank"

Return ONLY valid JSON:
[
  {{"id": 1, "label": "short label", "prompt": "full detailed prompt"}},
  {{"id": 2, "label": "short label", "prompt": "full detailed prompt"}},
  {{"id": 3, "label": "short label", "prompt": "full detailed prompt"}},
  {{"id": 4, "label": "short label", "prompt": "full detailed prompt"}},
  {{"id": 5, "label": "short label", "prompt": "full detailed prompt"}}
]"""

    scenes = gemini_json(prompt)
    print(f"   ✅ {len(scenes)} scene prompts written")
    for s in scenes:
        print(f"      Scene {s['id']}: {s['label']}")
        print(f"      → {s['prompt'][:80]}...")
    return scenes

# ── PASS 3: Consistency Check ─────────────────────────────────
def check_consistency(scenes, brief):
    print("\n🔍 Pass 3: Consistency check across all 5 scenes...")

    prompts_text = "\n\n".join([f"Scene {s['id']} ({s['label']}):\n{s['prompt']}" for s in scenes])

    prompt = f"""You are a visual director reviewing 5 image prompts for a YouTube video.

These 5 images must feel like they come from the SAME VISUAL WORLD.
They are for one video and the viewer will see them in sequence.

VISUAL BRIEF (the intended world):
{brief[:1000]}

THE 5 PROMPTS:
{prompts_text}

Review for:
1. COLOUR CONSISTENCY — do all prompts match the brief's palette?
2. STYLE CONSISTENCY — are all in the same visual style (photorealistic/painterly/etc)?
3. ERA/SETTING CONSISTENCY — no jarring switches (e.g. ancient → modern → ancient)?
4. LIGHTING CONSISTENCY — does the overall lighting mood feel unified?
5. VARIETY — are the 5 scenes visually DIFFERENT from each other (angle, distance, subject)?

For each scene that needs adjustment, rewrite its prompt to fix the issue.
For scenes that are already consistent, return them unchanged.

Return ONLY valid JSON with the same structure:
[
  {{"id": 1, "label": "...", "prompt": "..."}},
  {{"id": 2, "label": "...", "prompt": "..."}},
  {{"id": 3, "label": "...", "prompt": "..."}},
  {{"id": 4, "label": "...", "prompt": "..."}},
  {{"id": 5, "label": "...", "prompt": "..."}}
]"""

    refined = gemini_json(prompt)
    print(f"   ✅ Consistency check done — {len(refined)} scenes refined")
    return refined

# ── Imagen 3 via Vertex AI REST ───────────────────────────────
def generate_image(prompt_text, scene_id):
    token = get_vertex_token()

    full_prompt = (
        f"{prompt_text}. "
        f"Ultra high resolution, 8K quality, professional photography or digital art. "
        f"Absolutely no text, no letters, no words, no numbers, no writing, "
        f"no watermarks, no signs, no labels anywhere in the image."
    )

    url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{PROJECT_ID}/locations/{LOCATION}/"
        f"publishers/google/models/{IMAGEN_MODEL}:predict"
    )
    payload = {
        "instances": [{"prompt": full_prompt}],
        "parameters": {
            "sampleCount":       1,
            "aspectRatio":       "16:9",
            "outputMimeType":    "image/jpeg",
            "safetyFilterLevel": "block_few",
            "personGeneration":  "allow_adult",
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
    print(f"   ❌ Imagen error {r.status_code}: {r.text[:300]}")
    return None

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🖼  Image Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    print(f"   Model: {IMAGEN_MODEL}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. Fetch episode
    episode = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"
    })
    episode = episode[0] if episode else None
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    print(f"\n📖 {episode['title_english']}")

    # 2. Get the correct row and script for this language
    table = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    if LANGUAGE == "en":
        ep_lang = db_get("english_episodes", {
            "episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"
        })
        ep_lang = ep_lang[0] if ep_lang else {}
        script  = ep_lang.get("script_english", "")
    else:
        ep_lang = episode   # tamil_episodes row already fetched
        script  = episode.get("script_tamil", "")

    if not script:
        print(f"❌ No script found for language '{LANGUAGE}' — generate script first")
        return

    # 3. Check for existing images from the CORRECT language table
    existing_raw = ep_lang.get("image_urls") or []
    if isinstance(existing_raw, str):
        try:
            existing_raw = json.loads(existing_raw)
        except:
            existing_raw = []

    regen_note = ep_lang.get("regenerate_note", "") or ""
    regen_scene_id  = None
    regen_direction = None

    if regen_note:
        import re
        m = re.search(r'[Rr]egenerate\s+scene\s+(\d+)[:\-]?\s*(.*)', regen_note, re.IGNORECASE)
        if m:
            regen_scene_id  = int(m.group(1))
            regen_direction = m.group(2).strip()
            print(f"\n🔄 Regenerating Scene {regen_scene_id}: {regen_direction}")
        db_patch(table, EPISODE_NUMBER, {"regenerate_note": None})

    # 4. Set status
    db_patch(table, EPISODE_NUMBER, {"status": "generating_images"})

    try:
        # ── 3-pass image prompt generation ──────────────────
        brief  = build_visual_brief(episode, script)
        scenes = build_scene_prompts(episode, script, brief)
        scenes = check_consistency(scenes, brief)

        # ── Generate images ──────────────────────────────────
        print(f"\n🖼  Generating {len(scenes)} images with Imagen 3...")
        existing_ids = {img["id"] for img in existing_raw}
        image_urls   = list(existing_raw)

        if regen_scene_id and regen_scene_id in existing_ids:
            image_urls   = [img for img in image_urls if img["id"] != regen_scene_id]
            existing_ids = {img["id"] for img in image_urls}

        missing = [s for s in scenes if s["id"] not in existing_ids]
        print(f"   Generating: {[s['id'] for s in missing]}")

        for scene in missing:
            scene_prompt = (
                regen_direction if (regen_scene_id and scene["id"] == regen_scene_id and regen_direction)
                else scene["prompt"]
            )

            print(f"\n   Scene {scene['id']}: {scene['label']}")
            print(f"   Prompt: {scene_prompt[:100]}...")

            def _gen(p=scene_prompt, sid=scene["id"]):
                return generate_image(p, sid)

            img_bytes = with_retry(_gen, retries=3, wait=25)
            if not img_bytes:
                print(f"   ❌ Scene {scene['id']} failed — skipping")
                continue

            time.sleep(IMAGEN_SLEEP)

            lang_folder = "ta" if LANGUAGE == "ta" else "en"
            storage_path = f"ep{EPISODE_NUMBER:03d}/{lang_folder}/scene_{scene['id']}.jpg"
            url = upload_to_storage("episode-images", storage_path, img_bytes)

            if url:
                image_urls.append({
                    "id":     scene["id"],
                    "label":  scene["label"],
                    "url":    url,
                    "prompt": scene_prompt[:150],
                })
                print(f"   ✅ Uploaded: {storage_path}")

        if not image_urls:
            print("❌ No images generated")
            db_patch(table, EPISODE_NUMBER, {"status": "script_approved"})
            return

        # Save images + update status
        ok = db_patch(table, EPISODE_NUMBER, {
            "image_urls":  json.dumps(image_urls),
            "status":      "images_ready",
        })
        print(f"\n{'='*60}")
        print(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — {len(image_urls)} images ready!")
        print(f"{'='*60}")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch(table, EPISODE_NUMBER, {"status": "script_approved"})

if __name__ == "__main__":
    main()
