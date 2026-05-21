"""
I Have a Cause — Script Generator (Vertex AI) — Sprint 7
=========================================================
Triggered by GitHub Actions with EPISODE_NUMBER input.
Auth: Same as original — Vertex AI service account credentials.
Sprint 7 additions: platform scripts (shorts, reels, x_post, x_thread)
                    script_summary for continuity
Flow:
  1. Fetch episode + preferences + previous episodes
  2. Vertex AI Gemini 2.5 Pro → deep research
  3. Gemini → Tamil long script
  4. Gemini → English long script
  5. Gemini → Script summary (continuity)
  6. Gemini → Tamil platform scripts (shorts, reels, x)
  7. Gemini → English platform scripts
  8. Save everything → status script_ready on all platforms
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
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"

YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@IHaveACause"

# ── Vertex AI auth ────────────────────────────────────────────
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

def db_patch(table, match_col, match_val, data):
    r = requests.patch(
        f"{REST}/{table}?{match_col}=eq.{match_val}",
        headers=SB_HEADERS,
        json=data, timeout=15
    )
    return r.status_code in (200, 204)

def generate(prompt):
    response = model.generate_content(prompt)
    parts = []
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
    return "\n".join(parts)

# ── Fetch ─────────────────────────────────────────────────────
def fetch_episode():
    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}",
        "select": "*"
    })
    return rows[0] if rows else None

def fetch_previous_episodes():
    rows = db_get("tamil_episodes", {
        "episode_number": f"lt.{EPISODE_NUMBER}",
        "status":         "in.(script_ready,images_ready,images_approved,video_ready,published)",
        "select":         "episode_number,title_english,title_tamil,bridge,module,script_summary",
        "order":          "episode_number.asc"
    })
    if not rows:
        return ""
    lines = ["PREVIOUSLY COVERED EPISODES — do not repeat themes, angles, or examples:"]
    for ep in rows:
        summary = ep.get("script_summary", "")
        lines.append(f"  Episode {ep['episode_number']}: {ep['title_english']} (Bridge: {ep['bridge']})")
        if summary:
            lines.append(f"  Summary: {summary}")
    lines.append("")
    lines.append("Each new episode must go DEEPER or cover a DIFFERENT angle.")
    return "\n".join(lines)

def fetch_preferences():
    rows = db_get("channel_preferences", {
        "is_active": "eq.true",
        "select":    "category,preference",
        "order":     "created_at.asc"
    })
    if not rows:
        return ""
    prefs_by_cat = {}
    for row in rows:
        cat = row["category"]
        if cat not in prefs_by_cat:
            prefs_by_cat[cat] = []
        prefs_by_cat[cat].append(row["preference"])
    lines = []
    for cat, prefs in prefs_by_cat.items():
        lines.append(f"{cat.upper()} PREFERENCES:")
        for p in prefs:
            lines.append(f"  - {p}")
    return "\n".join(lines)

# ── Step 1: Research ──────────────────────────────────────────
def deep_research(episode, previous_episodes):
    print(f"\n🔍 Step 1: Deep Research for Episode {EPISODE_NUMBER}")
    prev_block = f"\n\n{previous_episodes}" if previous_episodes else ""

    prompt = f"""You are a deep research assistant for a Tamil philosophy YouTube channel called "I Have a Cause."

Research this episode topic thoroughly:

Episode: {episode['episode_number']} — {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Module: {episode['module']}
Pillar: {episode['pillar']}
Bridge/Angle: {episode['bridge']}
Key Sources: {episode['research_source']}
Target Duration: {episode['target_duration_min']} minutes{prev_block}

Provide comprehensive research covering:
1. CORE PHILOSOPHY: The main philosophical concept explained deeply (3-4 paragraphs)
2. KEY SOURCES: Specific quotes, verses, slokas from the sources listed above
3. MODERN CONNECTIONS: How this ancient wisdom connects to modern science, psychology, daily life
4. TAMIL CONTEXT: Tamil poets, saints, literature (Thiruvalluvar, Vallalar, Avvaiyar, Sangam etc.)
5. HOOK IDEAS: 3 powerful opening hooks that stop a Tamil viewer from scrolling
6. KEY INSIGHTS: 5-7 profound insights from this topic
7. STORY/ANALOGY: A compelling story or analogy that makes this concept tangible
8. SOCIAL CONNECTION: How this philosophy applies to social reform and compassion

IMPORTANT: Research must be FRESH — do not cover ground already explored in previous episodes above."""

    research = generate(prompt)
    print(f"   ✅ Research complete ({len(research)} chars)")
    return research

# ── Step 2: Tamil Script ──────────────────────────────────────
def generate_tamil_script(episode, research, preferences, previous_episodes):
    print(f"\n✍️  Step 2: Tamil Script")
    pref_block   = f"\n\nCHANNEL PREFERENCES (always apply):\n{preferences}" if preferences else ""
    prev_block   = f"\n\n{previous_episodes}" if previous_episodes else ""
    target_min   = episode['target_duration_min']
    target_words = target_min * 110
    regen_note   = episode.get("regenerate_note", "")
    regen_block  = f"\n\nSPECIAL INSTRUCTION: {regen_note}" if regen_note else ""

    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான expert Tamil script writer.{pref_block}{prev_block}{regen_block}

EPISODE விவரங்கள்:
எண்: {episode['episode_number']}
தலைப்பு: {episode['title_tamil']}
Module: {episode['module']}
Bridge/Angle: {episode['bridge']}
Sources: {episode['research_source']}
Target Duration: {target_min} நிமிடங்கள் (~{target_words} words)

RESEARCH:
{research}

கட்டாய விதிகள்:
1. Script முழுவதும் 100% தமிழில் — எந்த English word கூட வேண்டாம்
2. தொடர்ச்சியான பேச்சு வழக்கில் மட்டும் — headings, bullets, markdown வேண்டாம்
3. நேரடியாக hook-உடன் தொடங்கட்டும் — எந்த label-உம் வேண்டாம்
4. குறைந்தது {target_words} words எழுதவும்
5. ஒரு கருத்தை ஒரு முறை மட்டுமே சொல்லவும்

FLOW: பார்வையாளரை கட்டிப் போடும் தொடக்கம் → core philosophy விரிவாக → examples, stories → சமூக மாற்றம் → summary + குழுசேருங்கள் + அடுத்த அத்தியாயம் preview

Write the COMPLETE script now."""

    script = generate(prompt)
    print(f"   ✅ Tamil script ({len(script)} chars)")
    return script

# ── Step 3: English Script ────────────────────────────────────
def generate_english_script(episode, research, preferences, previous_episodes):
    print(f"\n✍️  Step 3: English Script")
    eng_prefs    = f"\n\nCHANNEL PREFERENCES:\n{preferences}" if preferences else ""
    prev_block   = f"\n\n{previous_episodes}" if previous_episodes else ""
    target_min   = episode['target_duration_min']
    target_words = target_min * 130
    regen_note   = episode.get("regenerate_note", "")
    regen_block  = f"\n\nSPECIAL INSTRUCTION: {regen_note}" if regen_note else ""

    prompt = f"""You are an expert English script writer for "I Have a Cause" — a Tamil philosophy YouTube channel.
Channel voice: Calm, intellectual, compassionate. Think Alan Watts meets Sadhguru in English.{eng_prefs}{prev_block}{regen_block}

EPISODE DETAILS:
Number: {episode['episode_number']}
Title: {episode['title_english']}
Module: {episode['module']}
Bridge/Angle: {episode['bridge']}
Sources: {episode['research_source']}
Target Duration: {target_min} minutes (~{target_words} words)

RESEARCH:
{research}

CRITICAL RULES:
1. ENTIRE script in English only — not one word in Tamil or any other language
2. Continuous flowing spoken prose — no headings, bullets, markdown
3. Start directly with the hook — no labels
4. Minimum {target_words} words
5. NO REPETITION — every sentence must introduce something new
6. Closing: subscribe ask + mention Tamil version exists

Write the COMPLETE script now."""

    script = generate(prompt)
    print(f"   ✅ English script ({len(script)} chars)")
    return script

# ── Step 4: Script Summary ────────────────────────────────────
def generate_script_summary(episode, research, tamil_script):
    print(f"\n📋 Step 4: Script Summary")
    title = episode.get("title_english") or episode.get("title_tamil", "")
    prompt = f"""Summarise this episode in exactly 6 bullet points for use as continuity context in FUTURE episodes.
Focus on: key concepts explained, analogies used, stories told, Tamil terms introduced, examples given.
This will be read by AI when writing future episodes to AVOID repetition.

Episode: {title}
Research themes: {research[:1500]}
Script excerpt: {tamil_script[:1000]}

Return ONLY 6 short bullet points starting with •
No preamble. No headings. Just the 6 bullets."""

    try:
        summary = generate(prompt)
        print(f"   ✅ Summary saved")
        return summary
    except Exception as e:
        print(f"   ⚠️  Summary failed: {e}")
        return ""

# ── Step 5: Platform Scripts ──────────────────────────────────
def generate_platform_scripts(episode, tamil_script, english_script, language):
    print(f"\n📱 Step 5: {language.title()} platform scripts")
    title     = episode.get(f"title_{language}") or episode.get("title_english", "")
    ep_num    = episode.get("episode_number", 1)
    lang_note = "Tamil" if language == "tamil" else "English"
    script_ref = tamil_script if language == "tamil" else english_script
    yt_url    = YOUTUBE_CHANNEL_URL

    prompt = f"""You are a social media content writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Episode: EP {ep_num:02d} — {title}
Language: {lang_note}
YouTube: {yt_url}

EPISODE SUMMARY:
{script_ref[:1500]}

Generate ALL FOUR in {lang_note}. Return as valid JSON only — no markdown, no preamble.

{{
  "shorts": "<60-second TEASER — hook in 5 seconds, just enough to make them want the full video, end with 'Full episode on YouTube: {yt_url}'. 80-120 words.>",
  "reels": "<30-45 second VERTICAL REEL — punchy opening, one core insight, CTA 'Watch the full episode — link in bio'. 60-90 words.>",
  "x_post": "<Single X post — one powerful insight, max 240 chars, #IHaveACause #TamilPhilosophy #Consciousness, YouTube link.>",
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

# ── Save preference ───────────────────────────────────────────
def save_preference_if_noted(episode):
    note = episode.get("regenerate_note", "")
    if not note:
        return
    note_lower = note.lower()
    if any(w in note_lower for w in ["image", "visual", "photo", "scene", "colour", "color"]):
        category = "image"
    elif any(w in note_lower for w in ["voice", "audio", "tone", "speed", "pace"]):
        category = "voice"
    elif any(w in note_lower for w in ["infographic", "svg", "diagram", "chart"]):
        category = "infographic"
    else:
        category = "script"
    requests.post(
        f"{REST}/channel_preferences",
        headers=SB_HEADERS,
        json={"category": category, "preference": note,
              "episode_number": EPISODE_NUMBER, "is_active": True},
        timeout=10
    )
    print(f"   💡 Preference saved: [{category}] {note[:60]}")

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Script Generator (Vertex AI) — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    print(f"\n📖 {episode['title_english']}")
    print(f"   Module: {episode['module']}")
    print(f"   Bridge: {episode['bridge']}")

    if episode.get("regenerate_note"):
        print(f"   🔄 Regen: {episode['regenerate_note']}")
        save_preference_if_noted(episode)

    db_patch("tamil_episodes",   "episode_number", EPISODE_NUMBER, {"status": "generating"})
    db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {"status": "generating"})

    preferences       = fetch_preferences()
    previous_episodes = fetch_previous_episodes()

    if preferences:
        print(f"\n📋 Channel preferences loaded")
    if previous_episodes:
        print(f"📚 Previous episodes loaded as context")

    try:
        research       = deep_research(episode, previous_episodes)
        tamil_script   = generate_tamil_script(episode, research, preferences, previous_episodes)
        english_script = generate_english_script(episode, research, preferences, previous_episodes)
        summary        = generate_script_summary(episode, research, tamil_script)
        tamil_p        = generate_platform_scripts(episode, tamil_script, english_script, "tamil")
        english_p      = generate_platform_scripts(episode, tamil_script, english_script, "english")

        print(f"\n💾 Saving to Supabase...")
        ok_ta = db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {
            "script_tamil":          tamil_script,
            "research_brief":        research,
            "script_summary":        summary,
            "script_shorts_tamil":   tamil_p["shorts"],
            "script_reels_tamil":    tamil_p["reels"],
            "script_x_post_tamil":   tamil_p["x_post"],
            "script_x_thread_tamil": tamil_p["x_thread"],
            "status":                "script_ready",
            "status_shorts":         "script_ready",
            "status_reels":          "script_ready",
            "status_x":              "script_ready",
            "regenerate_note":       None,
        })
        ok_en = db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {
            "script_english":          english_script,
            "script_shorts_english":   english_p["shorts"],
            "script_reels_english":    english_p["reels"],
            "script_x_post_english":   english_p["x_post"],
            "script_x_thread_english": english_p["x_thread"],
            "status":                  "script_ready",
            "status_shorts":           "script_ready",
            "status_reels":            "script_ready",
            "status_x":                "script_ready",
        })

        print(f"   Tamil:   {'✅' if ok_ta else '❌'}")
        print(f"   English: {'✅' if ok_en else '❌'}")

        if ok_ta and ok_en:
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — All scripts ready!")
            print(f"{'='*60}")
        else:
            print("❌ Save failed")
            db_patch("tamil_episodes",   "episode_number", EPISODE_NUMBER, {"status": "pending"})
            db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {"status": "pending"})

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch("tamil_episodes",   "episode_number", EPISODE_NUMBER, {"status": "pending"})
        db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {"status": "pending"})

if __name__ == "__main__":
    main()
