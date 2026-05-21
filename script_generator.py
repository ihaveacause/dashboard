"""
script_generator.py  —  Sprint 7 edition
Generates for each episode:
  - Research brief
  - Tamil long script + English long script
  - Script summary (continuity for future episodes)
  - Shorts teaser  Tamil + English (60 sec, drives to YouTube)
  - Reels script   Tamil + English (30-45 sec vertical, drives to YouTube)
  - X post + thread Tamil + English (caption + 5-7 tweet thread)

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON via ADC (same as generate_script.yml)
Library: google.genai (included in google-cloud-aiplatform, not deprecated)
"""

import json
import os
import signal
import sys
import tempfile
import time

from supabase import create_client, Client

# ── Credentials ───────────────────────────────────────────────
_creds_json = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
_tmp.write(_creds_json)
_tmp.close()
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _tmp.name

from google import genai

client = genai.Client()   # Uses GOOGLE_APPLICATION_CREDENTIALS automatically
MODEL  = "gemini-2.5-pro-preview-05-06"

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]

YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@IHaveACause"
MIN_WORDS_LONG      = 1200
MIN_WORDS_SHORT     = 80
MIN_WORDS_THREAD    = 50
TIMEOUT_SECONDS     = 300

# ── Supabase ──────────────────────────────────────────────────

def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_pending_episodes(table: str) -> list[dict]:
    sb = get_supabase()
    res = (sb.table(table)
             .select("*")
             .eq("status", "pending")
             .order("episode_number")
             .limit(5)
             .execute())
    return res.data or []

def fetch_previous_episodes(table: str, current_episode_number: int) -> list[dict]:
    sb = get_supabase()
    res = (sb.table(table)
             .select("episode_number, title_tamil, title_english, bridge_angle, script_summary")
             .lt("episode_number", current_episode_number)
             .not_.is_("script_summary", "null")
             .order("episode_number", desc=True)
             .limit(5)
             .execute())
    return res.data or []

def update_episode(table: str, episode_id: str, updates: dict):
    get_supabase().table(table).update(updates).eq("id", episode_id).execute()

def set_status(table: str, episode_id: str, status: str):
    update_episode(table, episode_id, {"status": status})

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
                prompt += "\n\nIMPORTANT: The previous response was too short. Please write a FULL, COMPLETE script."
        except TimeoutError:
            print(f"  ⏰ Timeout on {label} attempt {attempt + 1}")
            if attempt == max_retries:
                raise
    return text

# ── Context builder ───────────────────────────────────────────

def build_continuity_context(previous_episodes: list[dict]) -> str:
    if not previous_episodes:
        return "This is the first episode — no previous episodes to reference."
    lines = ["PREVIOUSLY COVERED EPISODES — avoid repeating these specific examples, analogies, or stories:"]
    for ep in reversed(previous_episodes):
        title   = ep.get("title_english") or ep.get("title_tamil", "")
        bridge  = ep.get("bridge_angle", "")
        summary = ep.get("script_summary", "")
        lines.append(f"\nEP {ep['episode_number']}: {title}")
        if bridge:
            lines.append(f"  Bridge angle: {bridge}")
        if summary:
            lines.append(f"  What was covered: {summary}")
    return "\n".join(lines)

# ── Research ──────────────────────────────────────────────────

def generate_research(episode: dict) -> str:
    title_en        = episode.get("title_english", "")
    title_ta        = episode.get("title_tamil", "")
    pillar          = episode.get("content_pillar", "Consciousness")
    module          = episode.get("module_name", "")
    research_source = episode.get("research_source") or "Mandukya Upanishad, Mandukya Karika by Gaudapada, Shankaracharya's commentary"
    target_min      = episode.get("target_duration_min") or 12

    prompt = f"""You are a Tamil philosophy researcher specialising in Advaita Vedanta and consciousness studies.

Research this episode thoroughly for a Tamil YouTube channel "I Have a Cause":

Episode: {title_en} (Tamil: {title_ta})
Module: {module}
Pillar: {pillar}
Primary source: {research_source}
Target duration: {target_min} minutes

Provide:
1. Core philosophical concepts to explain (with Sanskrit + Tamil terms)
2. Key verses or sutras to reference (with transliteration)
3. 3-4 relatable modern analogies that Tamil diaspora audiences would connect with
4. Historical context and relevance today
5. Common misconceptions to address
6. Practical takeaways for daily life
7. Suggested bridge to next episode

Be thorough — this research will power Tamil + English scripts and social media content."""

    print("  📚 Generating research...")
    return generate(prompt)

# ── Long scripts ──────────────────────────────────────────────

def generate_tamil_script(episode: dict, research: str, continuity: str) -> str:
    title_ta   = episode.get("title_tamil", "")
    title_en   = episode.get("title_english", "")
    target_min = episode.get("target_duration_min") or 12
    regen_note = episode.get("regenerate_note", "")
    module     = episode.get("module_name", "")
    regen_section = f"\nSPECIAL INSTRUCTION: {regen_note}\n" if regen_note else ""

    prompt = f"""You are writing a Tamil YouTube script for "I Have a Cause" — a philosophy channel for the Tamil diaspora.
{regen_section}
Episode: {title_ta} ({title_en})
Module: {module}
Target: {target_min} minutes spoken aloud (approx {target_min * 130} Tamil words)

RESEARCH TO USE:
{research}

{continuity}

SCRIPT REQUIREMENTS:
- Write entirely in Tamil (Unicode — no transliteration)
- Opening hook: a powerful question or story that grabs attention in first 20 seconds
- Natural conversational tone — like an intelligent friend explaining, not a lecture
- Use the analogies from research but make them feel fresh
- Include moments of wonder, humour where appropriate
- Each concept must build on the previous one
- Closing: meaningful summary + tease for next episode
- Add [PAUSE] markers where the speaker should pause for effect
- Add [EMPHASIS] markers on key Tamil philosophical terms

CRITICAL: Do not repeat examples, stories or analogies used in previous episodes.

Write the COMPLETE script now. Do not truncate."""

    print("  📝 Generating Tamil script...")
    return generate_with_retry(prompt, MIN_WORDS_LONG, "Tamil script")

def generate_english_script(episode: dict, research: str, continuity: str, tamil_script: str) -> str:
    title_en   = episode.get("title_english", "")
    title_ta   = episode.get("title_tamil", "")
    target_min = episode.get("target_duration_min") or 12
    regen_note = episode.get("regenerate_note", "")
    module     = episode.get("module_name", "")
    regen_section = f"\nSPECIAL INSTRUCTION: {regen_note}\n" if regen_note else ""

    prompt = f"""You are writing an English YouTube script for "I Have a Cause" — a Tamil philosophy channel for the global diaspora.
{regen_section}
Episode: {title_en} (Tamil: {title_ta})
Module: {module}
Target: {target_min} minutes spoken aloud (approx {target_min * 140} English words)

RESEARCH TO USE:
{research}

{continuity}

TAMIL SCRIPT THEMES (already written — align but do NOT copy):
{tamil_script[:800]}...

SCRIPT REQUIREMENTS:
- Write entirely in English
- Adapt for a global Tamil diaspora — use Tamil terms with brief English explanations in brackets
- Same conversational intelligence as Tamil version but natural English rhythm
- Same philosophical depth — different cultural entry points where relevant
- Include [PAUSE] and [EMPHASIS] markers same as Tamil
- Opening hook must be different from Tamil version — find a fresh angle
- Closing: drive viewers to subscribe and mention the Tamil version exists

CRITICAL: Do not repeat examples from previous episodes.

Write the COMPLETE script now. Do not truncate."""

    print("  📝 Generating English script...")
    return generate_with_retry(prompt, MIN_WORDS_LONG, "English script")

# ── Script summary ────────────────────────────────────────────

def generate_script_summary(episode: dict, research: str, tamil_script: str) -> str:
    title = episode.get("title_english") or episode.get("title_tamil", "")
    prompt = f"""Summarise this episode in exactly 6 bullet points for use as continuity context in FUTURE episodes.
Focus on: key concepts explained, analogies used, stories told, Tamil terms introduced, examples given.
This will be read by AI when writing future episodes to AVOID repetition.

Episode: {title}
Research themes: {research[:1500]}
Script excerpt: {tamil_script[:1000]}

Return ONLY 6 short bullet points starting with •
No preamble. No headings. Just the 6 bullets."""

    print("  📋 Generating script summary...")
    try:
        return generate(prompt)
    except Exception as e:
        print(f"  ⚠️  Summary generation failed: {e} — skipping")
        return ""

# ── Platform scripts ──────────────────────────────────────────

def generate_platform_scripts(episode: dict, research: str, tamil_script: str, english_script: str, language: str) -> dict:
    title     = episode.get(f"title_{language}") or episode.get("title_english", "")
    ep_num    = episode.get("episode_number", 1)
    module    = episode.get("module_name", "")
    lang_note = "Tamil" if language == "tamil" else "English"
    script_ref = tamil_script if language == "tamil" else english_script
    yt_url    = YOUTUBE_CHANNEL_URL

    prompt = f"""You are a social media content writer for "I Have a Cause" — a Tamil philosophy YouTube channel.

Episode: EP {ep_num:02d} — {title}
Module: {module}
Language: {lang_note}
YouTube channel: {yt_url}

EPISODE SUMMARY (to base all content on):
{script_ref[:1500]}

Generate ALL FOUR of the following in {lang_note}. Return as valid JSON only — no markdown, no preamble.

{{
  "shorts": "<60-second TEASER script — hook the viewer in first 5 seconds, reveal just enough to make them want the full video, end with 'Full episode on YouTube: {yt_url}'. Natural spoken {lang_note}. 80-120 words.>",
  "reels": "<30-45 second VERTICAL REEL script — punchy opening question or shocking philosophical fact, one core insight from the episode, strong call to action 'Watch the full episode — link in bio'. Written for vertical short-form video. 60-90 words.>",
  "x_post": "<Single X/Twitter post — one powerful insight from the episode as a thought-provoking statement or question. Max 240 characters. Include hashtags: #IHaveACause #TamilPhilosophy #Consciousness. Include YouTube link.>",
  "x_thread": "<5-tweet thread. Tweet 1: hook question. Tweets 2-4: one insight per tweet (each under 240 chars). Tweet 5: CTA with YouTube link. Format as: TWEET_1: ... | TWEET_2: ... | TWEET_3: ... | TWEET_4: ... | TWEET_5: ...>"
}}

IMPORTANT: Return ONLY the JSON object. No text before or after."""

    print(f"  📱 Generating {lang_note} platform scripts (Shorts + Reels + X)...")
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

# ── Main episode processor ────────────────────────────────────

def process_episode(episode: dict, table: str):
    ep_id  = episode["id"]
    ep_num = episode.get("episode_number", "?")
    title  = episode.get("title_english") or episode.get("title_tamil", "Unknown")

    print(f"\n{'='*60}")
    print(f"🎬 Processing EP {ep_num}: {title}")
    print(f"{'='*60}")

    try:
        set_status(table, ep_id, "generating")

        previous   = fetch_previous_episodes(table, ep_num)
        continuity = build_continuity_context(previous)
        print(f"  📖 Continuity context: {len(previous)} previous episodes")

        research       = generate_research(episode)
        print(f"  ✅ Research: {len(research.split())} words")
        time.sleep(2)

        tamil_script   = generate_tamil_script(episode, research, continuity)
        time.sleep(2)

        english_script = generate_english_script(episode, research, continuity, tamil_script)
        time.sleep(2)

        summary        = generate_script_summary(episode, research, tamil_script)
        time.sleep(1)

        tamil_platforms   = generate_platform_scripts(episode, research, tamil_script, english_script, "tamil")
        time.sleep(2)

        english_platforms = generate_platform_scripts(episode, research, tamil_script, english_script, "english")
        time.sleep(1)

        update_episode(table, ep_id, {
            "script_tamil":             tamil_script,
            "script_english":           english_script,
            "research_brief":           research,
            "script_summary":           summary,
            "script_shorts_tamil":      tamil_platforms["shorts"],
            "script_reels_tamil":       tamil_platforms["reels"],
            "script_x_post_tamil":      tamil_platforms["x_post"],
            "script_x_thread_tamil":    tamil_platforms["x_thread"],
            "script_shorts_english":    english_platforms["shorts"],
            "script_reels_english":     english_platforms["reels"],
            "script_x_post_english":    english_platforms["x_post"],
            "script_x_thread_english":  english_platforms["x_thread"],
            "status":                   "script_ready",
            "status_shorts":            "script_ready",
            "status_reels":             "script_ready",
            "status_x":                 "script_ready",
            "regenerate_note":          None,
        })

        print(f"\n  🎉 EP {ep_num} complete — all scripts saved")

    except TimeoutError as e:
        print(f"  ⏰ Timeout on EP {ep_num}: {e}")
        set_status(table, ep_id, "pending")

    except Exception as e:
        print(f"  ❌ Error on EP {ep_num}: {e}")
        set_status(table, ep_id, "pending")
        raise

# ── Entry point ───────────────────────────────────────────────

def main():
    tables = [
        ("tamil_episodes",   "Tamil"),
        ("english_episodes", "English"),
    ]
    total_processed = 0

    for table, lang_label in tables:
        print(f"\n{'#'*60}")
        print(f"# {lang_label} episodes")
        print(f"{'#'*60}")

        episodes = fetch_pending_episodes(table)
        if not episodes:
            print(f"  No pending {lang_label} episodes.")
            continue

        print(f"  Found {len(episodes)} pending episodes.")
        for episode in episodes:
            process_episode(episode, table)
            total_processed += 1
            time.sleep(3)

    print(f"\n{'='*60}")
    print(f"✅ Done — processed {total_processed} episodes total")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
