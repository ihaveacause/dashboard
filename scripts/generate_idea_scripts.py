"""
generate_idea_scripts.py — Sprint 7
Triggered by generate_idea_scripts.yml via trigger-idea-gen edge function.
Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON (same as generate_script.yml)
Library: google.genai via google-cloud-aiplatform (already installed)
"""

import json
import os
import signal
import sys
import tempfile
import time

from supabase import create_client, Client

# ── Credentials (mirrors generate_script.yml pattern) ────────
_creds_json = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
_tmp.write(_creds_json)
_tmp.close()
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _tmp.name

# ── Gemini via google.genai with ADC (no API key needed) ──────
from google import genai

client = genai.Client()  # Uses GOOGLE_APPLICATION_CREDENTIALS automatically
MODEL  = "gemini-2.5-pro-preview-05-06"

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
IDEA_ID      = os.environ["IDEA_ID"]

YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@IHaveACause"
MIN_WORDS_LONG      = 1200
MIN_WORDS_SHORT     = 80
TIMEOUT_SECONDS     = 300

# ── Supabase ──────────────────────────────────────────────────

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_idea(idea_id: str) -> dict:
    res = get_supabase().table("ideas").select("*").eq("id", idea_id).single().execute()
    if not res.data:
        raise ValueError(f"Idea not found: {idea_id}")
    return res.data

def update_idea(idea_id: str, updates: dict):
    get_supabase().table("ideas").update(updates).eq("id", idea_id).execute()

def set_all_statuses(idea_id: str, status: str):
    update_idea(idea_id, {
        "status":        status,
        "status_shorts": status,
        "status_reels":  status,
        "status_x":      status,
    })

# ── Generation helpers ────────────────────────────────────────

def _timeout_handler(signum, frame):
    raise TimeoutError("Gemini call exceeded 5 minutes")

def generate(prompt: str) -> str:
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
        )
        return response.text.strip()
    finally:
        signal.alarm(0)

def validate_word_count(text: str, minimum: int, label: str) -> bool:
    count = len(text.split())
    if count < minimum:
        print(f"  ⚠️  {label}: only {count} words (minimum {minimum}) — will retry")
        return False
    print(f"  ✅ {label}: {count} words")
    return True

def generate_with_retry(prompt: str, minimum_words: int, label: str, max_retries: int = 2) -> str:
    for attempt in range(max_retries + 1):
        try:
            text = generate(prompt)
            if validate_word_count(text, minimum_words, label):
                return text
            if attempt < max_retries:
                print(f"  🔄 Retrying {label} (attempt {attempt + 2})...")
                prompt += "\n\nIMPORTANT: Previous response was too short. Write COMPLETE content."
        except TimeoutError:
            print(f"  ⏰ Timeout on {label} attempt {attempt + 1}")
            if attempt == max_retries:
                raise
    return text

# ── Research ──────────────────────────────────────────────────

def generate_research(idea: dict) -> str:
    title          = idea.get("title", "")
    description    = idea.get("description", "")
    research_angle = idea.get("research_angle", "")
    angle_section  = f"\nResearch angle: {research_angle}" if research_angle else ""

    prompt = f"""You are a Tamil philosophy and social reform researcher writing for "I Have a Cause" — a YouTube channel for the Tamil diaspora.

Research this idea thoroughly for a standalone video:

Title: {title}
Description: {description}{angle_section}

Provide:
1. Core concepts and arguments to make (with Tamil cultural references where relevant)
2. Historical or philosophical grounding (Vedic, Thirukkural, Tamil literature as applicable)
3. 3-4 relatable modern analogies that Tamil diaspora would connect with
4. Data, evidence, or real-world examples that strengthen the argument
5. Counter-arguments and how to address them
6. Emotional arc — where should the viewer feel moved, challenged, inspired?
7. Practical takeaways for the audience

Be thorough — this research will power Tamil + English scripts and social media content."""

    print("  📚 Generating research...")
    return generate(prompt)

# ── Long scripts ──────────────────────────────────────────────

def generate_tamil_script(idea: dict, research: str) -> str:
    prompt = f"""You are writing a Tamil YouTube script for "I Have a Cause" — a philosophy and social reform channel for the Tamil diaspora.

Video title: {idea.get("title", "")}
Concept: {idea.get("description", "")}

RESEARCH TO USE:
{research}

SCRIPT REQUIREMENTS:
- Write entirely in Tamil (Unicode — no transliteration)
- Target: 12 minutes spoken aloud (~1560 Tamil words)
- Opening hook: a powerful question, story, or fact in first 20 seconds
- Natural conversational tone — like an intelligent friend explaining
- Add [PAUSE] markers for effect, [EMPHASIS] on key Tamil terms
- Closing: meaningful summary with a call to think, act, or share

Write the COMPLETE script now. Do not truncate."""

    print("  📝 Generating Tamil long script...")
    return generate_with_retry(prompt, MIN_WORDS_LONG, "Tamil script")

def generate_english_script(idea: dict, research: str, tamil_script: str) -> str:
    prompt = f"""You are writing an English YouTube script for "I Have a Cause" — a Tamil philosophy channel for the global diaspora.

Video title: {idea.get("title", "")}
Concept: {idea.get("description", "")}

RESEARCH TO USE:
{research}

TAMIL SCRIPT THEMES (align but do NOT copy):
{tamil_script[:800]}...

SCRIPT REQUIREMENTS:
- Write entirely in English
- Target: 12 minutes (~1680 English words)
- Opening hook must be DIFFERENT from Tamil version
- Use Tamil terms with brief English explanations in brackets
- Add [PAUSE] and [EMPHASIS] markers
- Closing: drive viewers to subscribe, mention Tamil version exists

Write the COMPLETE script now. Do not truncate."""

    print("  📝 Generating English long script...")
    return generate_with_retry(prompt, MIN_WORDS_LONG, "English script")

# ── Platform scripts ──────────────────────────────────────────

def generate_platform_scripts(idea: dict, long_script: str, language: str) -> dict:
    title     = idea.get("title", "")
    lang_note = "Tamil" if language == "tamil" else "English"
    yt_url    = YOUTUBE_CHANNEL_URL

    prompt = f"""You are a social media content writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Video title: {title}
Language: {lang_note}
YouTube channel: {yt_url}

EPISODE SUMMARY (base all content on this):
{long_script[:1500]}

Generate ALL FOUR in {lang_note}. Return as valid JSON only — no markdown, no preamble.

{{
  "shorts": "<60-second TEASER script — hook in 5 seconds, reveal just enough, end with 'Full video on YouTube: {yt_url}'. 80-120 words.>",
  "reels": "<30-45 second VERTICAL REEL — punchy opening, one core insight, CTA 'Watch the full video — link in bio'. 60-90 words.>",
  "x_post": "<Single X post — one powerful insight, max 240 chars. Include #IHaveACause #TamilPhilosophy. Include YouTube link.>",
  "x_thread": "<5-tweet thread. Format: TWEET_1: ... | TWEET_2: ... | TWEET_3: ... | TWEET_4: ... | TWEET_5: (CTA with YouTube link)>"
}}

Return ONLY the JSON object."""

    print(f"  📱 Generating {lang_note} platform scripts...")
    try:
        raw = generate(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return {
            "shorts":   data.get("shorts", ""),
            "reels":    data.get("reels", ""),
            "x_post":   data.get("x_post", ""),
            "x_thread": data.get("x_thread", ""),
        }
    except Exception as e:
        print(f"  ⚠️  Platform scripts parse error: {e}")
        return {"shorts": "", "reels": "", "x_post": "", "x_thread": ""}

# ── Main ──────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"💡 Processing Idea: {IDEA_ID}")
    print(f"{'='*60}")

    idea = fetch_idea(IDEA_ID)
    print(f"  Title: {idea.get('title', 'Unknown')}")

    try:
        set_all_statuses(IDEA_ID, "generating")

        # 1. Research
        research = generate_research(idea)
        print(f"  ✅ Research: {len(research.split())} words")
        time.sleep(2)

        # 2. Tamil long script
        tamil_script = generate_tamil_script(idea, research)
        time.sleep(2)

        # 3. English long script
        english_script = generate_english_script(idea, research, tamil_script)
        time.sleep(2)

        # 4. Tamil platform scripts
        tamil_p = generate_platform_scripts(idea, tamil_script, "tamil")
        time.sleep(2)

        # 5. English platform scripts
        english_p = generate_platform_scripts(idea, english_script, "english")
        time.sleep(1)

        # 6. Save everything
        update_idea(IDEA_ID, {
            "script_tamil":            tamil_script,
            "script_english":          english_script,
            "research_brief":          research,
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

        print(f"\n  🎉 Done — all scripts saved, all platforms → script_ready")
        print(f"{'='*60}")

    except TimeoutError as e:
        print(f"  ⏰ Timeout: {e}")
        set_all_statuses(IDEA_ID, "pending")
        sys.exit(1)

    except Exception as e:
        print(f"  ❌ Error: {e}")
        set_all_statuses(IDEA_ID, "pending")
        raise

if __name__ == "__main__":
    main()
