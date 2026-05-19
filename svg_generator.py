"""
I Have a Cause — SVG Infographic Generator (standalone)
========================================================
Only regenerates the SVG infographic — does NOT touch images.
Triggered separately from image pipeline via trigger-svg-gen edge function.
KEY FIX: User's regeneration direction placed at TOP of prompt for highest weight.
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

def validate_svg(svg_text):
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

    script_tamil   = str(episode.get("script_tamil",   "") or "")[:1500]
    script_english = str(episode.get("script_english", "") or "")[:1500]
    script_context = script_tamil if script_tamil else script_english

    # ── KEY FIX: user direction goes to the TOP ────────────
    # When REGEN_NOTE is set, it appears first so Gemini
    # weights it highest — not buried after long instructions.
    if REGEN_NOTE:
        direction_block = f"""CREATOR'S EXACT INSTRUCTION — FOLLOW THIS PRECISELY:
{REGEN_NOTE}

This is the most important instruction. Everything below supports it.
Do not default to generic philosophy diagrams. Follow the creator's direction exactly.

"""
    else:
        direction_block = ""

    prompt = f"""{direction_block}Create a stunning SVG infographic (1920x1080px) for this Tamil philosophy episode.

Episode: {episode['episode_number']} — {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Bridge: {episode['bridge']}
Module: {episode['module']}

EPISODE SCRIPT (for context):
{script_context}

DESIGN REQUIREMENTS:
- Represent the key philosophical concept of this episode visually
- Deep space black background with subtle star field (small white dots)
- Color scheme: gold, indigo, deep blue, white — glowing, ethereal
- Use SVG gradients and feGaussianBlur filters for glow effects
- "I Have a Cause" watermark bottom-right in small muted text
- Labels in both Tamil and English where appropriate
- Use SVG <defs> with <radialGradient> and <filter> elements

{"If no specific direction was given above, choose the diagram type that best fits this episode:" if not REGEN_NOTE else ""}
{"- Concentric circles for states of consciousness" if not REGEN_NOTE else ""}
{"- Tree diagram for cause and effect" if not REGEN_NOTE else ""}
{"- Spiral for cycles of existence" if not REGEN_NOTE else ""}
{"- Triangle for the three gunas" if not REGEN_NOTE else ""}
{"- Lotus for stages of awakening" if not REGEN_NOTE else ""}

CRITICAL: Return ONLY valid, well-formed SVG XML starting with <svg and ending with </svg>.
No markdown. No explanation. Every attribute properly quoted. No duplicate attributes."""

    max_svg_attempts = 3

    for attempt in range(1, max_svg_attempts + 1):
        print(f"  🔄 SVG attempt {attempt}/{max_svg_attempts}...")

        def _call():
            r = gemini_client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )
            return r.text.strip()

        svg = call_with_retry(_call)

        if "```svg" in svg:
            svg = svg.split("```svg")[1].split("```")[0].strip()
        elif "```" in svg:
            svg = svg.split("```")[1].split("```")[0].strip()

        if not svg.startswith("<svg"):
            print(f"  ❌ Attempt {attempt}: Does not start with <svg — retrying...")
            time.sleep(10)
            continue

        is_valid, error = validate_svg(svg)
        if not is_valid:
            print(f"  ❌ Attempt {attempt}: Invalid SVG XML — {error}")
            time.sleep(10)
            continue

        print(f"  ✅ SVG validated (attempt {attempt})")

        path = f"ep{EPISODE_NUMBER:03d}/infographic.svg"
        url  = upload_svg(path, svg.encode("utf-8"))
        if url:
            print(f"  ✅ SVG uploaded: {path}")
        return svg, url

    print(f"  ❌ SVG generation failed after {max_svg_attempts} attempts")
    return None, None

def main():
    print("=" * 60)
    print(f"📊 SVG Generator — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if REGEN_NOTE:
        print(f"   Direction: {REGEN_NOTE}")
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
