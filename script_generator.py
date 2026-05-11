"""
I Have a Cause — Script Generator (New Architecture)
=====================================================
Triggered by GitHub Actions with EPISODE_NUMBER input.
Flow:
  1. Fetch episode details from tamil_episodes
  2. Fetch active channel_preferences
  3. Gemini Deep Research → rich knowledge base
  4. Gemini → clean spoken Tamil script (no markdown)
  5. Gemini → clean spoken English script (same research)
  6. Save both to tamil_episodes + english_episodes
  7. Update status → script_ready in both tables
"""

import os
import requests
from google import genai
from datetime import datetime

# ── Config ────────────────────────────────────────────────────
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

# ── Fetch episode ─────────────────────────────────────────────
def fetch_episode():
    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}",
        "select": "*"
    })
    return rows[0] if rows else None

# ── Fetch channel preferences ─────────────────────────────────
def fetch_preferences():
    rows = db_get("channel_preferences", {
        "is_active": "eq.true",
        "select": "category,preference",
        "order": "created_at.asc"
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

# ── Step 1: Deep Research ─────────────────────────────────────
def deep_research(episode):
    print(f"\n🔍 Step 1: Deep Research for Episode {EPISODE_NUMBER}")
    prompt = f"""You are a deep research assistant for a Tamil philosophy YouTube channel called "I Have a Cause."

Research this episode topic thoroughly:

Episode: {episode['episode_number']} — {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Module: {episode['module']}
Pillar: {episode['pillar']}
Bridge/Angle: {episode['bridge']}
Key Sources: {episode['research_source']}
Target Duration: {episode['target_duration_min']} minutes

Perform comprehensive research and provide:

1. CORE PHILOSOPHY: The main philosophical concept explained deeply (3-4 paragraphs)
2. KEY SOURCES: Specific quotes, verses, slokas or passages from the research sources listed above
3. MODERN CONNECTIONS: How this ancient wisdom connects to modern science, psychology, or daily life
4. TAMIL CONTEXT: Specific Tamil poets, saints, or literature that relate (Thiruvalluvar, Vallalar, Avvaiyar, Sangam literature etc.)
5. HOOK IDEAS: 3 powerful opening hooks that would stop a Tamil viewer from scrolling
6. KEY INSIGHTS: 5-7 profound insights from this episode topic
7. STORY/ANALOGY: A compelling story or analogy that makes this concept tangible
8. SOCIAL CONNECTION: How this philosophy applies to social reform and compassion (the channel's mission)

Research deeply and thoroughly. This will be used to write a {episode['target_duration_min']}-minute YouTube video script."""

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    research = response.text
    print(f"   ✅ Research complete ({len(research)} chars)")
    return research

# ── Step 2: Tamil Script ──────────────────────────────────────
def generate_tamil_script(episode, research, preferences):
    print(f"\n✍️  Step 2: Tamil Script")
    pref_block = f"\n\nCHANNEL PREFERENCES (apply these always):\n{preferences}" if preferences else ""

    target_min  = episode['target_duration_min']
    # Tamil speech: ~120-130 words/min at 0.92 speed → ~110 words/min effective
    target_words = target_min * 110

    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான expert Tamil script writer.

இந்த சேனலின் குணாதிசயங்கள்:
- அமைதியான, அறிவார்ந்த, இரக்கமுள்ள குரல்
- தத்துவம் மற்றும் நவீன அறிவியலை இணைக்கும்
- சமூக சீர்திருத்தத்தை ஆதரிக்கும்
- Tamil diaspora மற்றும் Tamil Nadu பார்வையாளர்கள்
- எளிய Tamil பேச்சு வழக்கு — கடினமான சொற்கள் தவிர்க்கவும்{pref_block}

EPISODE விவரங்கள்:
எண்: {episode['episode_number']}
தலைப்பு: {episode['title_tamil']}
English Title: {episode['title_english']}
Module: {episode['module']}
Bridge/Angle: {episode['bridge']}
Sources: {episode['research_source']}
Target Duration: {target_min} நிமிடங்கள் (~{target_words} words)

RESEARCH (இதை base-ஆக வைத்து script எழுதுங்கள்):
{research}

CRITICAL FORMATTING RULES — இவற்றை கண்டிப்பாக பின்பற்றவும்:
- Script முழுவதும் தொடர்ச்சியான பேச்சு வடிவில் இருக்க வேண்டும்
- எந்த section headings போடாதீர்கள் (HOOK:, INTRODUCTION: போன்றவை வேண்டாம்)
- எந்த timestamps போடாதீர்கள் (0:00-2:00 போன்றவை வேண்டாம்)
- எந்த markdown formatting வேண்டாம் (**bold**, *italic*, ## headers, bullet points, numbered lists)
- Script ஒரு YouTuber camera-வில் நேரடியாக பேசுவது போல் இருக்க வேண்டும்
- ஒவ்வொரு paragraph-உம் இயல்பாக அடுத்ததில் தொடர வேண்டும்

FLOW (headings இல்லாமல் இந்த வரிசையில் எழுதுங்கள்):
பார்வையாளரை உடனே கட்டிப் போடும் தொடக்கம். பிறகு episode-ஐ introduce செய்யுங்கள். பிறகு core philosophy, examples, stories, modern connections விரிவாக விளக்குங்கள். பிறகு இந்த தத்துவம் சமூக மாற்றத்துடன் எப்படி தொடர்புடையது என்று சொல்லுங்கள். இறுதியில் summary, subscribe கோரிக்கை, next episode preview.

குறைந்தது {target_words} words எழுதுங்கள். Script நேரடியாக தொடங்கட்டும் — எந்த label-உம் வேண்டாம்."""

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    script = response.text
    print(f"   ✅ Tamil script complete ({len(script)} chars)")
    return script

# ── Step 3: English Script ────────────────────────────────────
def generate_english_script(episode, research, preferences):
    print(f"\n✍️  Step 3: English Script")
    eng_prefs = f"\n\nCHANNEL PREFERENCES (apply these always):\n{preferences}" if preferences else ""

    target_min  = episode['target_duration_min']
    # English speech: ~140-150 words/min at 0.92 speed → ~130 words/min effective
    target_words = target_min * 130

    prompt = f"""You are an expert English script writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Channel voice characteristics:
- Calm, intellectual, compassionate tone
- Bridges ancient Vedic wisdom with modern science
- Champions social reform and animal consciousness
- Audience: Tamil diaspora (UK, USA, Canada, Singapore, Malaysia) + global seekers
- Language: Clear, eloquent English — not academic jargon, not dumbed down
- Think: Alan Watts meets Sadhguru in English{eng_prefs}

EPISODE DETAILS:
Number: {episode['episode_number']}
Title: {episode['title_english']}
Tamil Title: {episode['title_tamil']}
Module: {episode['module']}
Bridge/Angle: {episode['bridge']}
Sources: {episode['research_source']}
Target Duration: {target_min} minutes (~{target_words} words)

RESEARCH (use this as your foundation — same content, fresh English voice):
{research}

CRITICAL FORMATTING RULES — follow these exactly:
- Write the entire script as continuous flowing spoken prose
- NO section headings (no HOOK:, INTRODUCTION:, MAIN CONTENT:, CONCLUSION: etc.)
- NO timestamps (no 0:00-2:00 markers)
- NO markdown formatting (no **bold**, no *italic*, no ## headers, no bullet points, no numbered lists)
- Write exactly as you would speak to a camera — natural, warm, intelligent
- Each paragraph flows naturally into the next with no labels or breaks

FLOW (write without any headings, in this order):
Open with a powerful hook that stops the viewer immediately. Then ease into the episode topic. Then unpack the core philosophy with examples, analogies, science, and stories in rich detail. Then connect this philosophy to social reform and compassion. Close with a meaningful summary, subscribe ask, and next episode teaser.

Write at least {target_words} words. Start the script directly — no labels, no preamble."""

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    script = response.text
    print(f"   ✅ English script complete ({len(script)} chars)")
    return script

# ── Save scripts ──────────────────────────────────────────────
def save_scripts(tamil_script, english_script):
    print(f"\n💾 Saving to Supabase...")

    ok_ta = db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {
        "script_tamil": tamil_script,
        "status": "script_ready",
    })
    print(f"   Tamil episodes: {'✅' if ok_ta else '❌'}")

    ok_en = db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {
        "script_english": english_script,
        "status": "script_ready",
    })
    print(f"   English episodes: {'✅' if ok_en else '❌'}")

    return ok_ta and ok_en

# ── Save preferences from regenerate note ────────────────────
def save_preference_if_noted(episode):
    note = episode.get("regenerate_note", "")
    if not note:
        return
    note_lower = note.lower()
    if any(w in note_lower for w in ["image", "visual", "photo", "picture", "colour", "color"]):
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
        json={
            "category": category,
            "preference": note,
            "episode_number": EPISODE_NUMBER,
            "is_active": True
        },
        timeout=10
    )
    print(f"   💡 Preference saved: [{category}] {note[:60]}")

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Script Generator — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found in tamil_episodes")
        return

    print(f"\n📖 Episode: {episode['title_english']}")
    print(f"   Module: {episode['module']}")
    print(f"   Bridge: {episode['bridge']}")

    if episode.get("regenerate_note"):
        print(f"   🔄 Regeneration requested: {episode['regenerate_note']}")
        save_preference_if_noted(episode)

    db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {"status": "generating"})
    db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {"status": "generating"})

    preferences = fetch_preferences()
    if preferences:
        print(f"\n📋 Channel preferences loaded")

    try:
        research      = deep_research(episode)
        tamil_script  = generate_tamil_script(episode, research, preferences)
        english_script = generate_english_script(episode, research, preferences)
        success       = save_scripts(tamil_script, english_script)

        if success:
            print(f"\n{'=' * 60}")
            print(f"✅ Episode {EPISODE_NUMBER} — Both scripts ready!")
            print(f"   Open dashboard to review and approve.")
            print(f"{'=' * 60}")
        else:
            print(f"\n❌ Save failed — check Supabase connection")
            db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {"status": "pending"})
            db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {"status": "pending"})

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {"status": "pending"})
        db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {"status": "pending"})

if __name__ == "__main__":
    main()
