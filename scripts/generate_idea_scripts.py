"""
generate_idea_scripts.py — Sprint 7
Triggered by generate_idea_scripts.yml via trigger-idea-gen edge function.
Reads one idea by IDEA_ID env var, generates all scripts, saves to ideas table.

Generates:
  - Research brief (from title + description + research_angle)
  - Tamil long script + English long script
  - Shorts teaser Tamil + English  (60 sec)
  - Reels script  Tamil + English  (30-45 sec)
  - X post + thread Tamil + English
Sets status, status_shorts, status_reels, status_x → script_ready on success.
"""

import json
import os
import signal
import sys
import tempfile
import time

from supabase import create_client, Client
import vertexai
from vertexai.generative_models import GenerativeModel

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
IDEA_ID      = os.environ["IDEA_ID"]

# Write credentials to disk and init Vertex AI
_creds_json = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
_tmp        = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
_tmp.write(_creds_json)
_tmp.close()
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _tmp.name

_project_id = json.loads(_creds_json).get("project_id", "")
vertexai.init(project=_project_id, location="us-central1")
model = GenerativeModel("gemini-2.5-pro-preview-05-06")

YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@IHaveACause"
MIN_WORDS_LONG      = 1200
MIN_WORDS_SHORT     = 80
TIMEOUT_SECONDS     = 300

# ── Supabase ──────────────────────────────────────────────────

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_idea(idea_id: str) -> dict:
    sb  = get_supabase()
    res = sb.table("ideas").select("*").eq("id", idea_id).single().execute()
    if not res.data:
        raise ValueError(f"Idea not found: {idea_id}")
    return res.data

def update_idea(idea_id: str, updates: dict):
    get_supabase().table("ideas").update(updates).eq("id", idea_id).execute()

def set_generating(idea_id: str):
    update_idea(idea_id, {
        "status":        "generating",
        "status_shorts": "generating",
        "status_reels":  "generating",
        "status_x":      "generating",
    })

def set_failed(idea_id: str):
    update_idea(idea_id, {
        "status":        "pending",
        "status_shorts": "pending",
        "status_reels":  "pending",
        "status_x":      "pending",
    })

# ── Generation helpers ────────────────────────────────────────

def _timeout_handler(signum, frame):
    raise TimeoutError("Gemini call exceeded 5 minutes")

def generate(prompt: str) -> str:
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        response = model.generate_content(prompt)
        # Handle both vertexai and google.generativeai response formats
        try:
            text_parts = []
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
            return "\n".join(text_parts).strip()
        except Exception:
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

    angle_section = f"\nResearch angle: {research_angle}" if research_angle else ""

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
    title          = idea.get("title", "")
    description    = idea.get("description", "")

    prompt = f"""You are writing a Tamil YouTube script for "I Have a Cause" — a philosophy and social reform channel for the Tamil diaspora.

Video title: {title}
Concept: {description}

RESEARCH TO USE:
{research}

SCRIPT REQUIREMENTS:
- Write entirely in Tamil (Unicode — no transliteration)
- Target: 12 minutes spoken aloud (~1560 Tamil words)
- Opening hook: a powerful question, story, or fact that grabs attention in first 20 seconds
- Natural conversational tone — like an intelligent friend explaining, not a lecture
- Use analogies from the research but make them feel fresh and organic
- Include moments of emotion, clarity, and practical wisdom
- Each idea must build naturally on the previous one
- Add [PAUSE] markers where the speaker should pause for effect
- Add [EMPHASIS] markers on key Tamil terms
- Closing: meaningful summary with a call to think, act, or share

Write the COMPLETE script now. Do not truncate."""

    print("  📝 Generating Tamil long script...")
    return generate_with_retry(prompt, MIN_WORDS_LONG, "Tamil script")


def generate_english_script(idea: dict, research: str, tamil_script: str) -> str:
    title       = idea.get("title", "")
    description = idea.get("description", "")

    prompt = f"""You are writing an English YouTube script for "I Have a Cause" — a Tamil philosophy and social reform channel for the global diaspora.

Video title: {title}
Concept: {description}

RESEARCH TO USE:
{research}

TAMIL SCRIPT THEMES (already written — align but do NOT copy):
{tamil_script[:800]}...

SCRIPT REQUIREMENTS:
- Write entirely in English
- Target: 12 minutes spoken aloud (~1680 English words)
- Adapt for global Tamil diaspora — use Tamil terms with brief English explanations in brackets
- Same philosophical and emotional depth — different cultural entry points where relevant
- Opening hook must be DIFFERENT from Tamil version — find a fresh entry angle
- Add [PAUSE] and [EMPHASIS] markers
- Closing: drive viewers to subscribe, mention Tamil version exists

Write the COMPLETE script now. Do not truncate."""

    print("  📝 Generating English long script...")
    return generate_with_retry(prompt, MIN_WORDS_LONG, "English script")

# ── Platform scripts ──────────────────────────────────────────

def generate_platform_scripts(idea: dict, research: str, long_script: str, language: str) -> dict:
    """
    Generate Shorts + Reels + X post + X thread in one Gemini call.
    Mirrors the same function in script_generator.py.
    """
    title     = idea.get("title", "")
    yt_url    = YOUTUBE_CHANNEL_URL
    lang_note = "Tamil" if language == "tamil" else "English"

    prompt = f"""You are a social media content writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Video title: {title}
Language: {lang_note}
YouTube channel: {yt_url}

EPISODE SUMMARY (base all content on this):
{long_script[:1500]}

Generate ALL FOUR of the following in {lang_note}. Return as valid JSON only — no markdown, no preamble.

{{
  "shorts": "<60-second TEASER script — hook in first 5 seconds, reveal just enough to make them want the full video, end with 'Full video on YouTube: {yt_url}'. Natural spoken {lang_note}. 80-120 words.>",

  "reels": "<30-45 second VERTICAL REEL script — punchy opening question or shocking fact, one core insight, strong CTA: 'Watch the full video — link in bio'. Written for vertical short-form. 60-90 words.>",

  "x_post": "<Single X/Twitter post — one powerful insight as a thought-provoking statement or question. Max 240 characters. Include hashtags: #IHaveACause #TamilPhilosophy #Consciousness. Include YouTube link.>",

  "x_thread": "<5-tweet thread. Tweet 1: hook question. Tweets 2-4: one insight per tweet (each under 240 chars). Tweet 5: CTA with YouTube link. Format: TWEET_1: ... | TWEET_2: ... | TWEET_3: ... | TWEET_4: ... | TWEET_5: ...>"
}}

IMPORTANT: Return ONLY the JSON object. No text before or after."""

    print(f"  📱 Generating {lang_note} platform scripts (Shorts + Reels + X)...")

    try:
        raw = generate(prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        data = json.loads(raw)
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
        set_generating(IDEA_ID)

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
        tamil_platforms = generate_platform_scripts(idea, research, tamil_script, "tamil")
        time.sleep(2)

        # 5. English platform scripts
        english_platforms = generate_platform_scripts(idea, research, english_script, "english")
        time.sleep(1)

        # 6. Save everything
        updates = {
            # Long scripts
            "script_tamil":            tamil_script,
            "script_english":          english_script,
            "research_brief":          research,

            # Tamil platform scripts
            "script_shorts_tamil":     tamil_platforms["shorts"],
            "script_reels_tamil":      tamil_platforms["reels"],
            "script_x_post_tamil":     tamil_platforms["x_post"],
            "script_x_thread_tamil":   tamil_platforms["x_thread"],

            # English platform scripts
            "script_shorts_english":   english_platforms["shorts"],
            "script_reels_english":    english_platforms["reels"],
            "script_x_post_english":   english_platforms["x_post"],
            "script_x_thread_english": english_platforms["x_thread"],

            # All platform statuses → script_ready
            "status":        "script_ready",
            "status_shorts": "script_ready",
            "status_reels":  "script_ready",
            "status_x":      "script_ready",
        }

        update_idea(IDEA_ID, updates)

        print(f"\n  🎉 Idea complete — all scripts saved, all platforms → script_ready")
        print(f"{'='*60}")

    except TimeoutError as e:
        print(f"  ⏰ Timeout: {e}")
        set_failed(IDEA_ID)
        sys.exit(1)

    except Exception as e:
        print(f"  ❌ Error: {e}")
        set_failed(IDEA_ID)
        raise


if __name__ == "__main__":
    main()
