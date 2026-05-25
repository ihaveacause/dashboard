"""
generate_script.py — New YouTube Pipeline
==========================================
Generates ONE long script for ONE language for ONE episode.
Nothing else — no shorts, no X, no platform scripts.

Uses new google-genai SDK (safe past June 24 2026 deprecation).

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER
  LANGUAGE  — ta (Tamil) or en (English)
"""

import os
import json
import requests
from datetime import datetime
from google import genai
from google.oauth2 import service_account

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")   # ta or en

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"

YOUTUBE_URL = "https://www.youtube.com/@IHaveACause"

# ── New google-genai SDK auth (safe past June 24 2026) ────────
creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
    credentials=credentials,
)

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

def db_patch(table, col, val, data):
    r = requests.patch(
        f"{REST}/{table}?{col}=eq.{val}",
        headers=SB_HEADERS,
        json=data, timeout=30
    )
    if r.status_code not in (200, 204):
        print(f"  ❌ Supabase patch error {r.status_code}: {r.text[:300]}")
    return r.status_code in (200, 204)

# ── Gemini call ───────────────────────────────────────────────
def generate(prompt, model="gemini-2.5-pro"):
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    return response.text

# ── Fetch data ────────────────────────────────────────────────
def fetch_episode():
    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}",
        "select": "*"
    })
    return rows[0] if rows else None

def fetch_previous_summaries():
    rows = db_get("tamil_episodes", {
        "episode_number": f"lt.{EPISODE_NUMBER}",
        "select": "episode_number,title_english,bridge,script_summary",
        "order": "episode_number.asc",
    })
    if not rows:
        return ""
    lines = ["PREVIOUSLY COVERED — do NOT repeat these themes, examples, or analogies:"]
    for ep in rows:
        summary = ep.get("script_summary", "")
        lines.append(f"  EP {ep['episode_number']}: {ep['title_english']} | Bridge: {ep['bridge']}")
        if summary:
            lines.append(f"    Summary: {summary[:200]}")
    return "\n".join(lines)

def fetch_channel_preferences():
    rows = db_get("channel_preferences", {
        "is_active": "eq.true",
        "select": "category,preference",
    })
    script_prefs = [r["preference"] for r in rows if r["category"] == "script"]
    return "\n".join(f"- {p}" for p in script_prefs) if script_prefs else ""

# ── Step 1: Deep research ─────────────────────────────────────
def deep_research(episode, prev_context):
    print("\n🔍 Step 1: Deep research...")
    prev_block = f"\n\n{prev_context}" if prev_context else ""

    prompt = f"""You are a deep research assistant for a Tamil philosophy YouTube channel "I Have a Cause."

Research this episode thoroughly:

Episode {episode['episode_number']}: {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Module: {episode['module']}
Pillar: {episode['pillar']}
Bridge/Angle: {episode['bridge']}
Key Sources: {episode['research_source']}{prev_block}

Provide comprehensive research:
1. CORE PHILOSOPHY — the main concept explained deeply (3-4 paragraphs)
2. KEY SOURCES — specific quotes, verses, slokas from the sources listed
3. MODERN CONNECTIONS — how this ancient wisdom connects to modern science, psychology, daily life
4. TAMIL CONTEXT — Tamil poets, saints, Thiruvalluvar, Avvaiyar, Sangam literature
5. HOOK IDEAS — 3 powerful opening hooks that stop a Tamil viewer from scrolling
6. KEY INSIGHTS — 5-7 profound insights from this topic
7. STORY/ANALOGY — one compelling story or analogy that makes this concept tangible
8. SOCIAL CONNECTION — how this philosophy applies to social reform and compassion

IMPORTANT: Research must be FRESH. Do not cover ground already explored in previous episodes above."""

    research = generate(prompt)
    print(f"   ✅ Research complete ({len(research)} chars)")
    return research

# ── Step 2: Tamil script ──────────────────────────────────────
def generate_tamil_script(episode, research, prefs):
    print("\n✍️  Step 2: Tamil script...")
    target_min   = episode.get("target_duration_min", 12)
    target_words = target_min * 110
    pref_block   = f"\n\nCHANNEL PREFERENCES:\n{prefs}" if prefs else ""
    regen_note   = episode.get("regenerate_note", "") or ""
    regen_block  = f"\n\nSPECIAL INSTRUCTION: {regen_note}" if regen_note else ""

    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான expert Tamil script writer.{pref_block}{regen_block}

EPISODE: {episode['episode_number']} — {episode['title_tamil']}
Module: {episode['module']} | Bridge: {episode['bridge']}
Sources: {episode['research_source']}
Target: {target_min} நிமிடங்கள் (~{target_words} words)

RESEARCH:
{research}

கட்டாய விதிகள்:
1. Script முழுவதும் 100% தமிழில் — எந்த English word கூட வேண்டாம்
2. தொடர்ச்சியான பேச்சு வழக்கில் மட்டும் — headings, bullets, markdown வேண்டாம்
3. நேரடியாக hook-உடன் தொடங்கட்டும் — எந்த label-உம் வேண்டாம்
4. குறைந்தது {target_words} words எழுதவும்
5. ஒரு கருத்தை ஒரு முறை மட்டுமே சொல்லவும் — மீண்டும் சொல்லாதீர்கள்
6. ஒவ்வொரு வாக்கியமும் புதிய ஒன்றை சொல்ல வேண்டும்

FLOW: பார்வையாளரை கட்டிப் போடும் hook → core philosophy → examples, stories, analogies → சமூக மாற்றம் → summary → subscribe கோரிக்கை

Write the COMPLETE script now. Do not stop until done."""

    script = generate(prompt)
    print(f"   ✅ Tamil script ({len(script)} chars)")
    return script

# ── Step 3: English script ────────────────────────────────────
def generate_english_script(episode, research, prefs):
    print("\n✍️  Step 3: English script...")
    target_min   = episode.get("target_duration_min", 12)
    target_words = target_min * 130
    pref_block   = f"\n\nCHANNEL PREFERENCES:\n{prefs}" if prefs else ""
    regen_note   = episode.get("regenerate_note", "") or ""
    regen_block  = f"\n\nSPECIAL INSTRUCTION: {regen_note}" if regen_note else ""

    prompt = f"""You are an expert English script writer for "I Have a Cause" — a Tamil philosophy YouTube channel.
Channel voice: Calm, intellectual, compassionate. Alan Watts meets Sadhguru in English.
Audience: Tamil diaspora (UK, USA, Canada, Singapore) + global seekers.{pref_block}{regen_block}

EPISODE: {episode['episode_number']} — {episode['title_english']}
Module: {episode['module']} | Bridge: {episode['bridge']}
Sources: {episode['research_source']}
Target: {target_min} minutes (~{target_words} words)

RESEARCH:
{research}

CRITICAL RULES:
1. ENTIRE script in English only — not one word in Tamil or any other language
2. Continuous flowing spoken prose — no headings, bullets, markdown
3. Start directly with the hook — no labels, no preamble
4. Minimum {target_words} words
5. Every sentence must introduce something new — no repetition at all
6. Opening hook must be different from typical Tamil philosophy hooks — find a global angle
7. Closing: warm subscribe ask + mention Tamil version exists for Tamil speakers

Write the COMPLETE script now. Do not stop until done."""

    script = generate(prompt)
    print(f"   ✅ English script ({len(script)} chars)")
    return script

# ── Step 4: Script summary (for continuity) ───────────────────
def generate_summary(episode, script, language):
    print("\n📋 Step 4: Script summary for continuity...")
    try:
        prompt = f"""Summarise this episode in exactly 6 bullet points for use as continuity context in FUTURE episodes.
Focus on: key concepts explained, analogies used, stories told, Tamil terms introduced, examples given.
This will prevent future episodes from repeating these exact things.

Episode: {episode['title_english']}
Script excerpt: {script[:1500]}

Return ONLY 6 short bullet points starting with •
No preamble. No headings. Just the 6 bullets."""

        summary = generate(prompt, model="gemini-2.5-flash")
        print("   ✅ Summary generated")
        return summary
    except Exception as e:
        print(f"   ⚠️  Summary failed (non-critical): {e}")
        return ""

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"📝 Script Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found in tamil_episodes")
        return

    print(f"\n📖 {episode['title_english']}")
    print(f"   Module: {episode['module']} | Bridge: {episode['bridge']}")

    # Set status → generating
    if LANGUAGE == "ta":
        db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {"status": "generating"})
    else:
        db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {"status": "generating"})

    prefs    = fetch_channel_preferences()
    prev_ctx = fetch_previous_summaries()

    if prev_ctx:
        print(f"\n📚 Previous episode context loaded")

    try:
        research = deep_research(episode, prev_ctx)

        if LANGUAGE == "ta":
            script  = generate_tamil_script(episode, research, prefs)
            summary = generate_summary(episode, script, "ta")
            print(f"\n💾 Saving Tamil script...")
            ok = db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {
                "script_tamil":    script,
                "research_brief":  research,
                "script_summary":  summary,
                "regenerate_note": None,
                "status":          "script_ready",
            })
        else:
            script  = generate_english_script(episode, research, prefs)
            summary = generate_summary(episode, script, "en")
            print(f"\n💾 Saving English script...")
            ok = db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {
                "script_english":  script,
                "research_brief":  research,
                "script_summary":  summary,
                "regenerate_note": None,
                "status":          "script_ready",
            })

        if ok:
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — script ready!")
            print(f"{'='*60}")
        else:
            print("❌ Failed to save — check Supabase logs")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        table = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
        db_patch(table, "episode_number", EPISODE_NUMBER, {"status": "pending"})

if __name__ == "__main__":
    main()
