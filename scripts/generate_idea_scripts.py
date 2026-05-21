"""
I Have a Cause — Idea Script Generator (Vertex AI)
===================================================
Triggered by GitHub Actions with IDEA_ID input.
Mirrors script_generator.py auth pattern exactly.
Flow:
  1. Fetch idea details from ideas table
  2. Vertex AI Gemini 2.5 Pro → deep research
  3. Gemini → Tamil long script
  4. Gemini → English long script
  5. Gemini → Tamil platform scripts (shorts, reels, x_post, x_thread)
  6. Gemini → English platform scripts
  7. Save everything to ideas table
  8. Update all statuses → script_ready
"""

import os
import json
import requests
from datetime import datetime
from google.oauth2 import service_account
import vertexai
from vertexai.generative_models import GenerativeModel

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
IDEA_ID        = os.environ["IDEA_ID"]

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"

YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@IHaveACause"

# ── Vertex AI auth (same as script_generator.py) ─────────────
creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)
model = GenerativeModel("gemini-2.5-pro")

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST = f"{SUPABASE_URL}/rest/v1"

# ── Supabase helpers ──────────────────────────────────────────
def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch_idea(data):
    r = requests.patch(
        f"{REST}/ideas?id=eq.{IDEA_ID}",
        headers=SB_HEADERS,
        json=data, timeout=30
    )
    if r.status_code not in (200, 204):
        print(f"   ❌ Supabase error {r.status_code}: {r.text[:500]}")
    return r.status_code in (200, 204)

# ── Gemini call ───────────────────────────────────────────────
def generate(prompt):
    response = model.generate_content(prompt)
    parts = []
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
    return "\n".join(parts)

# ── Fetch idea ────────────────────────────────────────────────
def fetch_idea():
    rows = db_get("ideas", {"id": f"eq.{IDEA_ID}", "select": "*"})
    return rows[0] if rows else None

# ── Step 1: Research ──────────────────────────────────────────
def deep_research(idea):
    print(f"\n🔍 Step 1: Deep Research")
    title          = idea.get("title", "")
    description    = idea.get("description", "")
    research_angle = idea.get("research_angle", "")
    angle_section  = f"\nResearch Angle: {research_angle}" if research_angle else ""

    prompt = f"""You are a deep research assistant for a Tamil philosophy YouTube channel called "I Have a Cause."

Research this idea thoroughly for a standalone video:

Title: {title}
Description: {description}{angle_section}

Provide comprehensive research covering:
1. CORE ARGUMENT: The main point explained deeply (3-4 paragraphs)
2. KEY SOURCES: Specific references — Thiruvalluvar, Tamil saints, Vedic texts, modern science as applicable
3. MODERN CONNECTIONS: How this connects to current events, science, psychology, daily life
4. TAMIL CONTEXT: Tamil poets, literature, history that strengthen this argument
5. HOOK IDEAS: 3 powerful opening hooks that stop a Tamil viewer from scrolling
6. KEY INSIGHTS: 5-7 profound insights from this topic
7. STORY/ANALOGY: A compelling story or analogy that makes this concept tangible
8. EMOTIONAL ARC: Where should the viewer feel moved, challenged, inspired?
9. COUNTER-ARGUMENTS: What would critics say and how to address them
10. PRACTICAL TAKEAWAYS: What can the viewer DO after watching?

Be thorough — this research will power Tamil + English scripts and social media content."""

    research = generate(prompt)
    print(f"   ✅ Research complete ({len(research)} chars)")
    return research

# ── Step 2: Tamil Long Script ─────────────────────────────────
def generate_tamil_script(idea, research):
    print(f"\n✍️  Step 2: Tamil Script")
    title       = idea.get("title", "")
    description = idea.get("description", "")
    target_words = 110 * 12  # 12 min × 110 words/min

    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான expert Tamil script writer.

சேனலின் குணாதிசயங்கள்:
- அமைதியான, அறிவார்ந்த, இரக்கமுள்ள குரல்
- தத்துவம் மற்றும் நவீன அறிவியலை இணைக்கும்
- சமூக சீர்திருத்தத்தை ஆதரிக்கும்
- Tamil diaspora மற்றும் Tamil Nadu பார்வையாளர்கள்

VIDEO விவரங்கள்:
தலைப்பு: {title}
கருத்து: {description}
Target Duration: 12 நிமிடங்கள் (~{target_words} words)

RESEARCH:
{research}

கட்டாய விதிகள்:
1. Script முழுவதும் 100% தமிழில் இருக்க வேண்டும் — ஒரு English word கூட வேண்டாம்
2. தொடர்ச்சியான பேச்சு வழக்கில் மட்டும் — headings, bullets, markdown வேண்டாம்
3. நேரடியாக hook-உடன் தொடங்கட்டும் — எந்த label-உம் வேண்டாம்
4. குறைந்தது {target_words} words எழுதவும்
5. ஒரு கருத்தை ஒரு முறை மட்டுமே சொல்லவும் — மீண்டும் சொல்லாதீர்கள்

FLOW: பார்வையாளரை உடனே கட்டிப் போடும் தொடக்கம் → கருத்து விரிவாக → examples, stories → சமூக மாற்றத்துடன் தொடர்பு → summary + குழுசேருங்கள் கோரிக்கை

Write the COMPLETE script now."""

    script = generate(prompt)
    print(f"   ✅ Tamil script ({len(script)} chars)")
    return script

# ── Step 3: English Long Script ───────────────────────────────
def generate_english_script(idea, research, tamil_script):
    print(f"\n✍️  Step 3: English Script")
    title        = idea.get("title", "")
    description  = idea.get("description", "")
    target_words = 130 * 12  # 12 min × 130 words/min

    prompt = f"""You are an expert English script writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Channel voice: Calm, intellectual, compassionate. Think Alan Watts meets Sadhguru in English.
Audience: Tamil diaspora (UK, USA, Canada, Singapore) + global seekers.

VIDEO DETAILS:
Title: {title}
Concept: {description}
Target: 12 minutes (~{target_words} words)

RESEARCH:
{research}

TAMIL SCRIPT THEMES (align but do NOT copy):
{tamil_script[:800]}...

CRITICAL RULES:
1. Write ENTIRE script in English only — not one word in Tamil or any other language
2. Continuous flowing spoken prose — no headings, bullets, markdown, timestamps
3. Start directly with the hook — no labels or preamble
4. Minimum {target_words} words
5. Opening hook must be DIFFERENT from Tamil version — find a fresh angle
6. NO REPETITION — every sentence must introduce something new
7. Closing: subscribe ask + mention Tamil version exists

Write the COMPLETE script now."""

    script = generate(prompt)
    print(f"   ✅ English script ({len(script)} chars)")
    return script

# ── Step 4: Platform Scripts ──────────────────────────────────
def generate_platform_scripts(idea, long_script, language):
    print(f"\n📱 Step 4: {language.title()} platform scripts")
    title     = idea.get("title", "")
    lang_note = "Tamil" if language == "tamil" else "English"
    yt_url    = YOUTUBE_CHANNEL_URL

    prompt = f"""You are a social media content writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Video title: {title}
Language: {lang_note}
YouTube: {yt_url}

EPISODE SUMMARY:
{long_script[:1500]}

Generate ALL FOUR in {lang_note}. Return as valid JSON only — no markdown, no preamble.

{{
  "shorts": "<60-second TEASER — hook in 5 seconds, just enough to make them want the full video, end with 'Full video on YouTube: {yt_url}'. 80-120 words.>",
  "reels": "<30-45 second VERTICAL REEL — punchy opening, one core insight, CTA 'Watch full video — link in bio'. 60-90 words.>",
  "x_post": "<Single X post — one powerful insight, max 240 chars, hashtags #IHaveACause #TamilPhilosophy, YouTube link.>",
  "x_thread": "<5-tweet thread. Format: TWEET_1: ... | TWEET_2: ... | TWEET_3: ... | TWEET_4: ... | TWEET_5: (CTA + YouTube link)>"
}}

Return ONLY the JSON object."""

    try:
        raw = generate(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        print(f"   ✅ {lang_note} platform scripts parsed")
        return {
            "shorts":   data.get("shorts", ""),
            "reels":    data.get("reels", ""),
            "x_post":   data.get("x_post", ""),
            "x_thread": data.get("x_thread", ""),
        }
    except Exception as e:
        print(f"   ⚠️  Parse error: {e}")
        return {"shorts": "", "reels": "", "x_post": "", "x_thread": ""}

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"💡 Idea Script Generator (Vertex AI) — {IDEA_ID}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    idea = fetch_idea()
    if not idea:
        print(f"❌ Idea {IDEA_ID} not found")
        return

    print(f"\n📖 {idea.get('title', '')}")

    db_patch_idea({
        "status":        "generating",
        "status_shorts": "generating",
        "status_reels":  "generating",
        "status_x":      "generating",
    })

    try:
        research       = deep_research(idea)
        tamil_script   = generate_tamil_script(idea, research)
        english_script = generate_english_script(idea, research, tamil_script)
        tamil_p        = generate_platform_scripts(idea, tamil_script, "tamil")
        english_p      = generate_platform_scripts(idea, english_script, "english")

        print(f"\n💾 Saving to Supabase...")
        # Save in two calls to avoid payload size issues
        ok1 = db_patch_idea({
            "script_tamil":   tamil_script,
            "script_english": english_script,
            "research_brief": research,
        })
        print(f"   Long scripts: {'✅' if ok1 else '❌'}")

        ok2 = db_patch_idea({
            "script_shorts_tamil":     tamil_p["shorts"],
            "script_reels_tamil":      tamil_p["reels"],
            "script_x_post_tamil":     tamil_p["x_post"],
            "script_x_thread_tamil":   tamil_p["x_thread"],
            "script_shorts_english":   english_p["shorts"],
            "script_reels_english":    english_p["reels"],
            "script_x_post_english":   english_p["x_post"],
            "script_x_thread_english": english_p["x_thread"],
            "status":        "script_ready",
            "status_shorts": "script_ready",
            "status_reels":  "script_ready",
            "status_x":      "script_ready",
        })
        print(f"   Platform scripts + statuses: {'✅' if ok2 else '❌'}")
        ok = ok1 and ok2

        if ok:
            print(f"\n{'='*60}")
            print(f"✅ Idea complete — all scripts ready!")
            print(f"{'='*60}")
        else:
            print("❌ Save failed")
            db_patch_idea({
                "status": "pending", "status_shorts": "pending",
                "status_reels": "pending", "status_x": "pending",
            })

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch_idea({
            "status": "pending", "status_shorts": "pending",
            "status_reels": "pending", "status_x": "pending",
        })

if __name__ == "__main__":
    main()
