"""
I Have a Cause — SVG Infographic Generator (standalone)
========================================================
Only regenerates the SVG infographic — does NOT touch images.
Triggered separately from image pipeline.
"""

import os
import json
import requests
import xml.etree.ElementTree as ET
from google import genai
from datetime import datetime

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
REGEN_NOTE     = os.environ.get("REGEN_NOTE", "")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

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

def upload_svg(path, data_bytes):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/episode-images/{path}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "image/svg+xml",
            "x-upsert":      "true"
        },
        data=data_bytes, timeout=60
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/episode-images/{path}"
    print(f"  ❌ Upload failed: {r.text[:200]}")
    return None

# ── SVG Validation ──────────────────────────────────────────
def validate_svg(svg_text):
    """
    Validate SVG is well-formed XML.
    Returns (is_valid, error_message)
    """
    try:
        ET.fromstring(svg_text)
        return True, None
    except ET.ParseError as e:
        return False, str(e)

def call_with_retry(fn, max_retries=4, wait=30):
    import time
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  ⚠️  Attempt {attempt+1} failed: {str(e)[:80]}")
                print(f"  ⏳ Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

def generate_svg(episode):
    import time
    print(f"\n📊 Generating SVG infographic...")
    pref_note = f"\n\nSPECIFIC REQUEST: {REGEN_NOTE}" if REGEN_NOTE else ""

    prompt = f"""Create a stunning SVG infographic (1920x1080px) for this Tamil philosophy episode.

Episode: {episode['episode_number']} — {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Bridge: {episode['bridge']}{pref_note}

DESIGN MANDATE:
- 4 concentric circles, same plane, superimposed — like ripples from one point
- Outermost → innermost: Vaishvanara (விழிப்பு), Taijasa (கனவு), Prajna (உறக்கம்), Turiya (துரியம்)
- Deep space black background with subtle star field (small white dots)
- Each ring: gradient stroke, glow filter (feGaussianBlur), semi-transparent fill
- Color scheme: Vaishvanara=amber/gold, Taijasa=indigo, Prajna=deep blue, Turiya=pure white
- Tamil label INSIDE each ring + English label outside
- Single bright white point at exact center (Turiya — pure consciousness)
- "I Have a Cause" watermark bottom-right in small muted text
- All text readable, good contrast
- Use SVG <defs> with <radialGradient> and <filter> for glows

CRITICAL: Return ONLY valid, well-formed SVG XML code starting with <svg and ending with </svg>.
No markdown fences. No explanation. The SVG must be 100% valid XML — every attribute must be
properly quoted, no duplicate attributes, no typos in attribute values."""

    max_svg_attempts = 3

    for attempt in range(1, max_svg_attempts + 1):
        print(f"  🔄 SVG attempt {attempt}/{max_svg_attempts}...")

        def _call():
            r = gemini_client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
            return r.text.strip()

        svg = call_with_retry(_call)

        # Clean markdown fences if present
        if "```svg" in svg:
            svg = svg.split("```svg")[1].split("```")[0].strip()
        elif "```" in svg:
            svg = svg.split("```")[1].split("```")[0].strip()

        if not svg.startswith("<svg"):
            print(f"  ❌ Attempt {attempt}: Response does not start with <svg — retrying...")
            time.sleep(10)
            continue

        # ── Validate SVG XML ──────────────────────────────
        is_valid, error = validate_svg(svg)
        if not is_valid:
            print(f"  ❌ Attempt {attempt}: Invalid SVG XML — {error}")
            print(f"  🔄 Retrying SVG generation...")
            time.sleep(10)
            continue

        print(f"  ✅ SVG validated (attempt {attempt})")

        # Upload
        path = f"ep{EPISODE_NUMBER:03d}/infographic.svg"
        url  = upload_svg(path, svg.encode("utf-8"))

        if url:
            print(f"  ✅ SVG uploaded: {path}")
        return svg, url

    print(f"  ❌ SVG generation failed after {max_svg_attempts} attempts — all invalid XML")
    return None, None

def main():
    print("=" * 60)
    print(f"📊 SVG Generator — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if REGEN_NOTE:
        print(f"   Note: {REGEN_NOTE}")
    print("=" * 60)

    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"
    })
    if not rows:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    episode = rows[0]
    print(f"\n📖 {episode['title_english']}")

    try:
        svg, url = generate_svg(episode)

        if svg and url:
            ok = db_patch("tamil_episodes", EPISODE_NUMBER, {
                "infographic_svg": json.dumps({"svg": svg, "url": url}),
                "regenerate_note": None
            })
            if ok:
                print(f"\n✅ Infographic updated — images untouched")
            else:
                print("❌ Failed to save to Supabase")
        else:
            print("❌ SVG generation failed")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
