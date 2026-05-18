"""
I Have a Cause — Script Generator (Vertex AI)
=============================================
Triggered by GitHub Actions with EPISODE_NUMBER input.
Flow:
  1. Fetch episode details from tamil_episodes
  2. Fetch active channel_preferences
  3. Vertex AI Gemini → deep research
  4. Vertex AI Gemini → clean spoken Tamil script (no English, no markdown)
  5. Vertex AI Gemini → clean spoken English script
  6. Save both to tamil_episodes + english_episodes
  7. Update status → script_ready
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

# ── Vertex AI auth ────────────────────────────────────────────
creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)
model = GenerativeModel("gemini-2.5-flash")

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
    """Call Gemini via Vertex AI and safely extract text from any response shape."""
    response = model.generate_content(prompt)
    parts = []
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
    return "\n".join(parts)

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

Provide comprehensive research covering:
1. CORE PHILOSOPHY: The main philosophical concept explained deeply (3-4 paragraphs)
2. KEY SOURCES: Specific quotes, verses, slokas from the sources listed above
3. MODERN CONNECTIONS: How this ancient wisdom connects to modern science, psychology, daily life
4. TAMIL CONTEXT: Tamil poets, saints, literature (Thiruvalluvar, Vallalar, Avvaiyar, Sangam etc.)
5. HOOK IDEAS: 3 powerful opening hooks that stop a Tamil viewer from scrolling
6. KEY INSIGHTS: 5-7 profound insights from this topic
7. STORY/ANALOGY: A compelling story or analogy that makes this concept tangible
8. SOCIAL CONNECTION: How this philosophy applies to social reform and compassion

Research deeply. This will power a {episode['target_duration_min']}-minute YouTube video script."""

    research = generate(prompt)
    print(f"   ✅ Research complete ({len(research)} chars)")
    return research

# ── Step 2: Tamil Script ──────────────────────────────────────
def generate_tamil_script(episode, research, preferences):
    print(f"\n✍️  Step 2: Tamil Script")
    pref_block = f"\n\nCHANNEL PREFERENCES (always apply):\n{preferences}" if preferences else ""

    target_min   = episode['target_duration_min']
    target_words = target_min * 110  # ~110 Tamil words/min at 0.89x speed

    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான expert Tamil script writer.

சேனலின் குணாதிசயங்கள்:
- அமைதியான, அறிவார்ந்த, இரக்கமுள்ள குரல்
- தத்துவம் மற்றும் நவீன அறிவியலை இணைக்கும்
- சமூக சீர்திருத்தத்தை ஆதரிக்கும்
- Tamil diaspora மற்றும் Tamil Nadu பார்வையாளர்கள்
- எளிய Tamil பேச்சு வழக்கு{pref_block}

EPISODE விவரங்கள்:
எண்: {episode['episode_number']}
தலைப்பு: {episode['title_tamil']}
Module: {episode['module']}
Bridge/Angle: {episode['bridge']}
Sources: {episode['research_source']}
Target Duration: {target_min} நிமிடங்கள் (~{target_words} words)

RESEARCH:
{research}

கட்டாய விதிகள் — இவற்றை கண்டிப்பாக பின்பற்றவும்:

1. மொழி: Script முழுவதும் 100% தமிழில் இருக்க வேண்டும்.
   எந்த English words-உம் கூடாது — ஒரு வார்த்தை கூட வேண்டாம்.
   English words-க்கு தமிழ் மாற்றங்கள்:
   - YouTube → யூடியூப்
   - Subscribe → சந்தா செய்யுங்கள் / குழுசேருங்கள்
   - Like → விரும்பல் குறி போடுங்கள்
   - Comment → கருத்து பகிருங்கள்
   - Share → பகிருங்கள்
   - Module → தொகுதி
   - Episode → அத்தியாயம்
   - Channel → சேவை
   - Notification → அறிவிப்பு
   - Bell → மணி
   - Science → அறிவியல்
   - Psychology → உளவியல்
   - Research → ஆராய்ச்சி
   - Modern → நவீன
   - Video → காணொளி

2. வடிவம்: தொடர்ச்சியான பேச்சு வழக்கில் மட்டுமே எழுதவும்.
   - எந்த section headings வேண்டாம்
   - எந்த timestamps வேண்டாம்
   - எந்த markdown வேண்டாம் (**bold**, *italic*, bullets, numbers)
   - [stage directions] வேண்டாம்
   - Camera முன்பு பேசுவது போல் இயல்பான உரையாடல் வடிவம்

3. தொடக்கம்: Script நேரடியாக hook-உடன் தொடங்கட்டும்.
   எந்த label-உம், header-உம் வேண்டாம்.

4. நீளம்: குறைந்தது {target_words} words எழுதவும்.

FLOW (labels இல்லாமல்):
பார்வையாளரை உடனே கட்டிப் போடும் தொடக்கம் → episode அறிமுகம் → core philosophy விரிவாக → examples, stories, modern connections → சமூக மாற்றத்துடன் தொடர்பு → summary, குழுசேருங்கள் கோரிக்கை, அடுத்த அத்தியாயம் preview."""

    script = generate(prompt)
    print(f"   ✅ Tamil script complete ({len(script)} chars)")
    return script

# ── Step 3: English Script ────────────────────────────────────
def generate_english_script(episode, research, preferences):
    print(f"\n✍️  Step 3: English Script")
    eng_prefs = f"\n\nCHANNEL PREFERENCES (always apply):\n{preferences}" if preferences else ""

    target_min   = episode['target_duration_min']
    target_words = target_min * 130  # ~130 English words/min at 0.89x speed

    prompt = f"""You are an expert English script writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Channel voice:
- Calm, intellectual, compassionate tone
- Bridges ancient Vedic wisdom with modern science
- Champions social reform and animal consciousness
- Audience: Tamil diaspora (UK, USA, Canada, Singapore) + global seekers
- Think: Alan Watts meets Sadhguru in English{eng_prefs}

EPISODE DETAILS:
Number: {episode['episode_number']}
Title: {episode['title_english']}
Module: {episode['module']}
Bridge/Angle: {episode['bridge']}
Sources: {episode['research_source']}
Target Duration: {target_min} minutes (~{target_words} words)

RESEARCH:
{research}

CRITICAL RULES — follow exactly:
1. Write the entire script as continuous flowing spoken prose
2. NO section headings, NO timestamps, NO markdown formatting
3. NO bullet points, NO numbered lists, NO bold/italic
4. Write exactly as you would speak to a camera — natural, warm, intelligent
5. Start directly with the hook — no labels, no preamble
6. Minimum {target_words} words — rich, layered, profound

FLOW (no headings):
Powerful hook → ease into episode → unpack core philosophy with examples, analogies, science, stories → connect to social reform and compassion → meaningful summary + subscribe ask + next episode teaser."""

    script = generate(prompt)
    print(f"   ✅ English script complete ({len(script)} chars)")
    return script

# ── Save scripts ──────────────────────────────────────────────
def save_scripts(tamil_script, english_script):
    print(f"\n💾 Saving to Supabase...")
    ok_ta = db_patch("tamil_episodes", "episode_number", EPISODE_NUMBER, {
        "script_tamil": tamil_script,
        "status":       "script_ready",
    })
    print(f"   Tamil:   {'✅' if ok_ta else '❌'}")

    ok_en = db_patch("english_episodes", "episode_number", EPISODE_NUMBER, {
        "script_english": english_script,
        "status":         "script_ready",
    })
    print(f"   English: {'✅' if ok_en else '❌'}")
    return ok_ta and ok_en

# ── Save preference from regenerate note ──────────────────────
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

    preferences = fetch_preferences()
    if preferences:
        print(f"\n📋 Channel preferences loaded")

    try:
        research       = deep_research(episode)
        tamil_script   = generate_tamil_script(episode, research, preferences)
        english_script = generate_english_script(episode, research, preferences)
        success        = save_scripts(tamil_script, english_script)

        if success:
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — Both scripts ready!")
            print(f"{'='*60}")
        else:
            print(f"\n❌ Save failed")
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
