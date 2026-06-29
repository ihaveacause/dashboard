"""
idea_generate_script.py — Ideas Pipeline (YouTube Long)
=======================================================
Same proven engine as new_youtube/scripts/generate_script.py, adapted for
one-off IDEAS instead of series episodes.

Differences from the series version (and ONLY these):
  • Reads/writes tamil_ideas / english_ideas (never the episode tables).
  • Finds the row by its hidden internal number (IDEA_NUMBER) — same
    mechanism as episode_number, just a different column value the user
    never sees.
  • NO "previous episodes" continuity context (ideas stand alone).
  • NEW first step: Gemini turns the rough `working_title` + `description`
    into a catchy YouTube title + a short hook line, in the right language.
    The catchy title is stored in title_tamil/title_english, and the hook
    in thumbnail_hook_text (so the existing thumbnail step picks it up).
  • No "EP" anything — titles are clean.

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  IDEA_NUMBER          — the hidden internal number of the idea row
  LANGUAGE             — ta (Tamil) or en (English)
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
IDEA_NUMBER    = int(os.environ["IDEA_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")   # ta or en

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"

TABLE = "tamil_ideas" if LANGUAGE == "ta" else "english_ideas"

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

# ── Supabase helpers (identical to the series version) ────────
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

def db_patch(table, col, val, data, _retries=3):
    """Write to Supabase with VISIBLE failures + retries. Previously a non-2xx was
    logged once but never retried, so a transient/auth failure left the row stuck."""
    import time as _t
    last = ""
    for attempt in range(1, _retries + 1):
        try:
            r = requests.patch(
                f"{REST}/{table}?{col}=eq.{val}",
                headers=SB_HEADERS,
                json=data, timeout=30
            )
            if r.status_code in (200, 204):
                if attempt > 1:
                    print(f"  ✅ db_patch {table} {col}={val} succeeded on retry {attempt}", flush=True)
                return True
            last = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last = f"exception: {e}"
        print(f"  ⚠️  db_patch {table} {col}={val} failed (attempt {attempt}/{_retries}) — {last}", flush=True)
        if attempt < _retries:
            _t.sleep(2 * attempt)
    print(f"  ❌ db_patch GAVE UP on {table} {col}={val} after {_retries} attempts — {last} | columns: {list(data)}", flush=True)
    return False

# ── Gemini call ───────────────────────────────────────────────
def generate(prompt, model="gemini-2.5-pro"):
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    return response.text

# ── Fetch the idea row (self-contained per language) ──────────
def fetch_idea():
    rows = db_get(TABLE, {
        "episode_number": f"eq.{IDEA_NUMBER}",   # hidden internal number
        "select": "*"
    })
    return rows[0] if rows else None

def fetch_channel_preferences():
    rows = db_get("channel_preferences", {
        "is_active": "eq.true",
        "select": "category,preference",
    })
    script_prefs = [r["preference"] for r in rows if r["category"] == "script"]
    return "\n".join(f"- {p}" for p in script_prefs) if script_prefs else ""

# ── Step 0 (NEW): catchy title + hook from rough title + context ──
def generate_title_and_hook(idea):
    print("\n✨ Step 0: Catchy title + hook from your rough title...")
    working_title = (idea.get("working_title") or idea.get("title_english")
                     or idea.get("title_tamil") or "").strip()
    description   = (idea.get("description") or "").strip()
    module        = (idea.get("module") or idea.get("module_name") or "").strip()

    if LANGUAGE == "ta":
        lang_rule = ("Both the title and the hook MUST be in natural, modern spoken TAMIL "
                     "(no English words). Keep the title under ~60 characters.")
    else:
        lang_rule = ("Both the title and the hook MUST be in natural, compelling ENGLISH. "
                     "Keep the title under ~70 characters.")

    ctx = f"Rough working title: {working_title}"
    if description:
        ctx += f"\nExtra context from the creator: {description}"
    if module:
        ctx += f"\nThis belongs to the topic/series: {module}"

    prompt = f"""You are a YouTube title strategist for the channel "I Have a Cause"
(thoughtful Tamil philosophy, consciousness, and social-reform content).

Turn the creator's rough idea into ONE catchy, click-worthy YouTube title and
ONE short punchy hook line (max ~7 words) that stops a viewer from scrolling.

{ctx}

{lang_rule}
Do NOT add any episode number or "EP" prefix. No quotes around the text.

Return ONLY valid JSON, nothing else:
{{"title": "the catchy title", "hook": "the short hook line"}}"""

    raw = generate(prompt).strip()
    # strip code fences if Gemini added them
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip() if "```" in raw else raw
    try:
        data = json.loads(raw)
        title = (data.get("title") or "").strip()
        hook  = (data.get("hook")  or "").strip()
    except Exception as e:
        print(f"   ⚠️  Could not parse title/hook JSON ({e}); falling back to rough title")
        title, hook = working_title, ""
    if not title:
        title = working_title
    print(f"   ✅ Title: {title}")
    print(f"   ✅ Hook : {hook}")
    return title, hook

# ── Step 1: Deep research (from the idea, no series context) ──
def deep_research(idea, catchy_title):
    print("\n🔍 Step 1: Deep research...")
    working_title = (idea.get("working_title") or "").strip()
    description   = (idea.get("description") or "").strip()
    module        = (idea.get("module") or idea.get("module_name") or "").strip()

    ctx = f"Title: {catchy_title}"
    if working_title and working_title.lower() != catchy_title.lower():
        ctx += f"\nCreator's original angle: {working_title}"
    if description:
        ctx += f"\nCreator's notes / direction: {description}"
    if module:
        ctx += f"\nTopic group: {module}"

    prompt = f"""You are a deep research assistant for the Tamil philosophy YouTube
channel "I Have a Cause." Research this ONE standalone video idea thoroughly.

{ctx}

Provide comprehensive research:
1. CORE IDEA — the main concept explained deeply (3-4 paragraphs)
2. KEY SOURCES — relevant quotes, verses, slokas, or facts that fit the idea
3. MODERN CONNECTIONS — how this connects to modern science, psychology, daily life
4. TAMIL CONTEXT — Tamil poets, saints, Thiruvalluvar, Avvaiyar, Sangam literature where relevant
5. HOOK IDEAS — 3 powerful opening hooks that stop a Tamil viewer from scrolling
6. KEY INSIGHTS — 5-7 profound insights
7. STORY/ANALOGY — one compelling story or analogy that makes it tangible
8. SOCIAL CONNECTION — how it applies to social reform and compassion

Keep the research tightly focused on THIS idea."""

    research = generate(prompt)
    print(f"   ✅ Research complete ({len(research)} chars)")
    return research

# ── Step 2: Tamil script ──────────────────────────────────────
def generate_tamil_script(idea, catchy_title, research, prefs):
    print("\n✍️  Step 2: Tamil script...")
    target_min   = idea.get("target_duration_min") or 12
    target_words = target_min * 110
    pref_block   = f"\n\nCHANNEL PREFERENCES:\n{prefs}" if prefs else ""
    regen_note   = idea.get("regenerate_note", "") or ""
    regen_block  = f"\n\nSPECIAL INSTRUCTION: {regen_note}" if regen_note else ""

    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான expert Tamil script writer.{pref_block}{regen_block}

VIDEO TITLE: {catchy_title}
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
def generate_english_script(idea, catchy_title, research, prefs):
    print("\n✍️  Step 3: English script...")
    target_min   = idea.get("target_duration_min") or 12
    target_words = target_min * 130
    pref_block   = f"\n\nCHANNEL PREFERENCES:\n{prefs}" if prefs else ""
    regen_note   = idea.get("regenerate_note", "") or ""
    regen_block  = f"\n\nSPECIAL INSTRUCTION: {regen_note}" if regen_note else ""

    prompt = f"""You are an expert English script writer for "I Have a Cause" — a Tamil philosophy YouTube channel.
Channel voice: Calm, intellectual, compassionate. Alan Watts meets Sadhguru in English.
Audience: Tamil diaspora (UK, USA, Canada, Singapore) + global seekers.{pref_block}{regen_block}

VIDEO TITLE: {catchy_title}
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

# ── Step 4: Script summary (kept; harmless, useful for follow-ups) ──
def generate_summary(catchy_title, script):
    print("\n📋 Step 4: Script summary...")
    try:
        prompt = f"""Summarise this video in exactly 6 bullet points capturing the key
concepts, analogies, stories, Tamil terms, and examples used.

Video: {catchy_title}
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
    print(f"💡 Idea Script Generator — Idea #{IDEA_NUMBER} | {LANGUAGE.upper()}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    idea = fetch_idea()
    if not idea:
        print(f"❌ Idea #{IDEA_NUMBER} not found in {TABLE}")
        return

    rough = idea.get("working_title") or idea.get("title_english") or idea.get("title_tamil") or "(untitled)"
    print(f"\n📖 Rough idea: {rough}")

    # status → generating
    db_patch(TABLE, "episode_number", IDEA_NUMBER, {"status": "generating"})

    prefs = fetch_channel_preferences()

    try:
        catchy_title, hook = generate_title_and_hook(idea)
        research = deep_research(idea, catchy_title)

        if LANGUAGE == "ta":
            script  = generate_tamil_script(idea, catchy_title, research, prefs)
            summary = generate_summary(catchy_title, script)
            print(f"\n💾 Saving Tamil idea script...")
            ok = db_patch(TABLE, "episode_number", IDEA_NUMBER, {
                "title_tamil":        catchy_title,
                "thumbnail_hook_text": hook,
                "script_tamil":       script,
                "research_brief":     research,
                "script_summary":     summary,
                "regenerate_note":    None,
                "status":             "script_ready",
            })
        else:
            script  = generate_english_script(idea, catchy_title, research, prefs)
            summary = generate_summary(catchy_title, script)
            print(f"\n💾 Saving English idea script...")
            ok = db_patch(TABLE, "episode_number", IDEA_NUMBER, {
                "title_english":      catchy_title,
                "thumbnail_hook_text": hook,
                "script_english":     script,
                "research_brief":     research,
                "script_summary":     summary,
                "regenerate_note":    None,
                "status":             "script_ready",
            })

        if ok:
            print(f"\n{'='*60}")
            print(f"✅ Idea #{IDEA_NUMBER} {LANGUAGE.upper()} — script ready!")
            print(f"{'='*60}")
        else:
            print("❌ Failed to save — check Supabase logs")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch(TABLE, "episode_number", IDEA_NUMBER, {"status": "pending"})

if __name__ == "__main__":
    main()
