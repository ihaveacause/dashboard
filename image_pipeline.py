"""
I Have a Cause — Image Pipeline
=================================
Triggered by GitHub Actions with EPISODE_NUMBER input.
Flow:
  1. Fetch episode + approved script from tamil_episodes
  2. Fetch active channel_preferences
  3. Gemini → generates 5 cosmic/surreal scene descriptions
  4. Imagen 3 → generates each scene image
  5. Gemini → generates SVG infographic (4 states concentric diagram)
  6. Upload all to Supabase Storage bucket 'episode-images'
  7. Save image_urls + infographic_svg to tamil_episodes
  8. status → images_ready
"""

import os
import json
import base64
import requests
from google import genai
from google.genai import types
from datetime import datetime

# ── Config ─────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])

client = genai.Client(api_key=GEMINI_API_KEY)

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST = f"{SUPABASE_URL}/rest/v1"

# ── Supabase helpers ────────────────────────────────────────
def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, episode_number, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{episode_number}",
        headers=SB_HEADERS,
        json=data, timeout=15
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
    image_prefs = [r["preference"] for r in rows if r["category"] == "image"]
    return "\n".join(f"- {p}" for p in image_prefs) if image_prefs else ""

# ── Step 1: Generate scene descriptions ─────────────────────
def generate_scene_descriptions(episode, prefs):
    print(f"\n🎨 Step 1: Generating scene descriptions...")

    pref_block = f"\n\nIMAGE PREFERENCES:\n{prefs}" if prefs else ""

    prompt = f"""You are a visual director for "I Have a Cause" — a Tamil philosophy YouTube channel.

Episode: {episode['episode_number']} — {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Module: {episode['module']}
Bridge: {episode['bridge']}
Script excerpt (first 800 chars): {str(episode.get('script_tamil',''))[:800]}{pref_block}

VISUAL STYLE MANDATE:
- Cosmic/surreal aesthetic — the human as a fragile, tiny being inside vast cosmic consciousness
- Abstract dream states, ethereal light, infinite space
- Deep blues, purples, indigo, gold light rays, nebulae, geometric sacred patterns
- NOT realistic photography — painterly, cinematic, otherworldly
- Each image must feel like a different dimension of consciousness

Generate EXACTLY 5 scene image prompts for Imagen 3. Each prompt should be 2-3 sentences, highly detailed and visual.

Return ONLY valid JSON, no markdown:
{{
  "scenes": [
    {{
      "id": 1,
      "label": "Hook — Opening Image",
      "prompt": "detailed Imagen 3 prompt here"
    }},
    {{
      "id": 2,
      "label": "Waking State",
      "prompt": "detailed Imagen 3 prompt here"
    }},
    {{
      "id": 3,
      "label": "Dream State",
      "prompt": "detailed Imagen 3 prompt here"
    }},
    {{
      "id": 4,
      "label": "Deep Sleep / Prajna",
      "prompt": "detailed Imagen 3 prompt here"
    }},
    {{
      "id": 5,
      "label": "Turiya — Pure Consciousness",
      "prompt": "detailed Imagen 3 prompt here"
    }}
  ]
}}"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    raw = response.text.strip().replace("```json","").replace("```","").strip()
    data = json.loads(raw)
    scenes = data["scenes"]
    print(f"   ✅ {len(scenes)} scene descriptions generated")
    return scenes

# ── Step 2: Generate images with Imagen 3 ──────────────────
def generate_images(scenes, episode_number):
    print(f"\n🖼  Step 2: Generating {len(scenes)} images with Imagen 3...")
    image_urls = []

    for scene in scenes:
        print(f"   Scene {scene['id']}: {scene['label']}...")
        try:
            # Add style suffix to every prompt
            full_prompt = (
                f"{scene['prompt']} "
                f"Cinematic 16:9, cosmic surreal, digital art, "
                f"deep space atmosphere, painterly, ethereal glow, "
                f"ultra detailed, award winning photography style."
            )

            response = client.models.generate_images(
                model="imagen-3.0-generate-002",
                prompt=full_prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="16:9",
                    output_mime_type="image/jpeg",
                    safety_filter_level="block_only_high",
                )
            )

            if not response.generated_images:
                print(f"      ⚠️  No image generated for scene {scene['id']}")
                continue

            image_bytes = response.generated_images[0].image.image_bytes
            storage_path = f"ep{str(episode_number).zfill(3)}/scene_{scene['id']}.jpg"
            url = upload_to_storage("episode-images", storage_path, image_bytes, "image/jpeg")

            if url:
                image_urls.append({
                    "id": scene["id"],
                    "label": scene["label"],
                    "url": url,
                    "prompt": scene["prompt"]
                })
                print(f"      ✅ Uploaded: {storage_path}")
            else:
                print(f"      ❌ Upload failed for scene {scene['id']}")

        except Exception as e:
            print(f"      ❌ Error on scene {scene['id']}: {e}")
            continue

    print(f"   ✅ {len(image_urls)}/{len(scenes)} images generated and uploaded")
    return image_urls

# ── Step 3: Generate SVG Infographic ───────────────────────
def generate_svg_infographic(episode):
    print(f"\n📊 Step 3: Generating SVG infographic...")

    prompt = f"""You are a data visualization expert for "I Have a Cause" — a Tamil philosophy YouTube channel.

Episode: {episode['episode_number']} — {episode['title_english']}
Module: {episode['module']}
Bridge: {episode['bridge']}

Create a stunning SVG infographic (1920x1080px) that visualizes the core concept of this episode.

DESIGN MANDATE:
- 4 concentric circles all in the same plane, superimposed, one inside the other
- All circles share the same center point — like rings of a ripple or tree rings
- From outermost to innermost: Vaishvanara (Waking), Taijasa (Dream), Prajna (Deep Sleep), Turiya (Pure Consciousness)
- Cosmic color palette: deep space black background, rings in gold/amber/indigo/white glow
- Each ring has a label inside or beside it in both Tamil and English
- A single point of pure white light at the very center (Turiya)
- Subtle star field in background
- Channel watermark "I Have a Cause" in bottom right
- The visual feeling: you are looking at the universe from inside consciousness
- Use SVG gradients, glows (filter blur), and transparency for depth

Return ONLY the complete SVG code starting with <svg and ending with </svg>.
No markdown, no explanation, just the SVG."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    svg = response.text.strip()
    # Clean up if wrapped in markdown
    if "```svg" in svg:
        svg = svg.split("```svg")[1].split("```")[0].strip()
    elif "```" in svg:
        svg = svg.split("```")[1].split("```")[0].strip()

    # Upload SVG to storage
    svg_bytes = svg.encode("utf-8")
    storage_path = f"ep{str(episode['episode_number']).zfill(3)}/infographic.svg"
    url = upload_to_storage("episode-images", storage_path, svg_bytes, "image/svg+xml")

    if url:
        print(f"   ✅ Infographic uploaded: {storage_path}")
    else:
        print(f"   ❌ Infographic upload failed")

    return svg, url

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🖼  Image Pipeline — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    if episode.get("status") != "script_approved":
        print(f"⚠️  Episode status is '{episode.get('status')}' — expected 'script_approved'")
        print("   Proceeding anyway...")

    print(f"\n📖 {episode['title_english']}")
    print(f"   Bridge: {episode['bridge']}")

    # Mark as generating
    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "generating_images"})

    prefs = fetch_preferences()
    if prefs:
        print(f"\n📋 Image preferences loaded")

    try:
        # Step 1: Scene descriptions
        scenes = generate_scene_descriptions(episode, prefs)

        # Step 2: Imagen 3 images
        image_urls = generate_images(scenes, EPISODE_NUMBER)

        # Step 3: SVG infographic
        svg_content, svg_url = generate_svg_infographic(episode)

        if not image_urls:
            print("❌ No images generated — aborting")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "script_approved"})
            return

        # Save to Supabase
        ok = db_patch("tamil_episodes", EPISODE_NUMBER, {
            "image_urls":      json.dumps(image_urls),
            "infographic_svg": json.dumps({"svg": svg_content, "url": svg_url}),
            "status":          "images_ready",
        })

        if ok:
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — Images ready for review!")
            print(f"   {len(image_urls)} images + infographic uploaded")
            print(f"   Open dashboard to review and approve images.")
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
