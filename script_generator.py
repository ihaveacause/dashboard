"""
I Have a Cause — AI Script Generator (Sprint 3)
Generates 12 scripts per story (Tamil + English for all 6 formats)
Processes one story at a time to avoid token limits
"""

import os
import requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}

CLAUDE_HEADERS = {
    "x-api-key":         ANTHROPIC_KEY,
    "anthropic-version": "2023-06-01",
    "content-type":      "application/json"
}

REST_URL = f"{SUPABASE_URL}/rest/v1"

# ── Fetch approved stories needing scripts ────────────────────────────────────
def fetch_pending_scripts():
    url = f"{REST_URL}/content_queue"
    params = {
        "status":               "eq.approved",
        "script_youtube_tamil": "is.null",
        "select":               "*",
        "order":                "approved_at.desc",
        "limit":                "20"
    }
    resp = requests.get(
        url,
        headers={**SUPA_HEADERS, "Prefer": "return=representation"},
        params=params,
        timeout=15
    )
    if resp.status_code == 200:
        return resp.json()
    print(f"  ❌ Failed to fetch: {resp.status_code} {resp.text}")
    return []

# ── Call Claude for one language block ───────────────────────────────────────
def call_claude(prompt):
    payload = {
        "model":      "claude-haiku-4-5-20251001",
        "max_tokens": 3000,
        "messages":   [{"role": "user", "content": prompt}]
    }
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=CLAUDE_HEADERS,
        json=payload,
        timeout=90
    )
    if resp.status_code != 200:
        print(f"  ❌ Claude error: {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json()["content"][0]["text"].strip()

# ── Parse delimiter response ──────────────────────────────────────────────────
def extract(raw, start_marker, end_marker):
    start = raw.find(start_marker)
    end   = raw.find(end_marker)
    if start == -1 or end == -1:
        return None
    return raw[start + len(start_marker):end].strip() or None

# ── Generate Tamil scripts ────────────────────────────────────────────────────
def generate_tamil(title, context, niche, is_my_idea):
    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான Tamil content creator.

தலைப்பு: {title}
சுருக்கம்: {context}
வகை: {niche}
{"இது creator-இன் சொந்த யோசனை" if is_my_idea else "இது செய்தி கதை"}

கீழ்கண்ட 6 formats-க்கு Tamil-ல் scripts எழுதுங்கள்.
தெளிவான, இயல்பான பேச்சு Tamil பயன்படுத்துங்கள்.
ஒவ்வொரு script-லும் context விளக்குங்கள் — பார்வையாளர்களுக்கு முன்பே தெரியாது என்று வைத்துக்கொள்ளுங்கள்.

<<<YT_LONG_TA>>>
[5-8 நிமிட YouTube script]
HOOK: கவனம் ஈர்க்கும் தொடக்கம்
CONTEXT: என்ன நடந்தது என்று தெளிவாக விளக்குங்கள்
YOUR TAKE: உங்கள் கருத்து மற்றும் பகுப்பாய்வு
CTA: subscribe மற்றும் share செய்யுங்கள்
<<<YT_SHORT_TA>>>
[30-60 வினாடி YouTube Short script — கவர்ச்சியான, வேகமான]
<<<REEL_TA>>>
[30-60 வினாடி Instagram Reel script — viral-ஆன]
<<<MEME_TA>>>
MAIN: [முக்கிய வரி]
SUB: [விளக்கம்]
CAPTION: [post விளக்கம்]
HASHTAGS: [hashtags]
<<<X_THREAD_TA>>>
1/ [hook tweet]
2/ [context]
3/ [key point]
4/ [your take]
5/ [conclusion + YouTube link]
<<<X_POST_TA>>>
[280 எழுத்துகளுக்கு குறைவான ஒரு tweet]
<<<END>>>"""

    raw = call_claude(prompt)
    if not raw:
        return {}

    return {
        "script_youtube_tamil":       extract(raw, "<<<YT_LONG_TA>>>",   "<<<YT_SHORT_TA>>>"),
        "script_youtube_short_tamil": extract(raw, "<<<YT_SHORT_TA>>>",  "<<<REEL_TA>>>"),
        "script_reel_tamil":          extract(raw, "<<<REEL_TA>>>",       "<<<MEME_TA>>>"),
        "script_meme_tamil":          extract(raw, "<<<MEME_TA>>>",       "<<<X_THREAD_TA>>>"),
        "script_x_thread":            extract(raw, "<<<X_THREAD_TA>>>",   "<<<X_POST_TA>>>"),
        "script_x_post":              extract(raw, "<<<X_POST_TA>>>",     "<<<END>>>"),
    }

# ── Generate English scripts ──────────────────────────────────────────────────
def generate_english(title, context, niche, is_my_idea):
    prompt = f"""You are a content creator for "I Have a Cause" YouTube channel — Tamil/Indian news commentary.

Title: {title}
Summary: {context}
Niche: {niche}
Type: {"Creator's original idea" if is_my_idea else "News story"}

Write scripts in simple conversational English for all 6 formats below.
Always explain context fully — assume viewers know nothing about this story.

<<<YT_LONG_EN>>>
[Full 5-8 minute YouTube script]
HOOK: attention-grabbing opening line
CONTEXT: clearly explain what happened and background
YOUR TAKE: your opinion and analysis
CTA: subscribe and share
<<<YT_SHORT_EN>>>
[30-60 second YouTube Short — punchy and fast]
<<<REEL_EN>>>
[30-60 second Instagram Reel script — viral and engaging]
<<<MEME_EN>>>
MAIN: [bold punchline]
SUB: [short explanation]
CAPTION: [post description]
HASHTAGS: [relevant hashtags]
<<<X_THREAD_EN>>>
1/ [hook tweet]
2/ [context tweet]
3/ [key point]
4/ [your take]
5/ [conclusion + YouTube link]
<<<X_POST_EN>>>
[single punchy tweet under 280 characters with hashtags]
<<<END>>>"""

    raw = call_claude(prompt)
    if not raw:
        return {}

    return {
        "script_youtube_english":       extract(raw, "<<<YT_LONG_EN>>>",   "<<<YT_SHORT_EN>>>"),
        "script_youtube_short_english": extract(raw, "<<<YT_SHORT_EN>>>",  "<<<REEL_EN>>>"),
        "script_reel_english":          extract(raw, "<<<REEL_EN>>>",       "<<<MEME_EN>>>"),
        "script_meme_english":          extract(raw, "<<<MEME_EN>>>",       "<<<X_THREAD_EN>>>"),
        "script_x_thread_english":      extract(raw, "<<<X_THREAD_EN>>>",   "<<<X_POST_EN>>>"),
        "script_x_post_english":        extract(raw, "<<<X_POST_EN>>>",     "<<<END>>>"),
    }

# ── Save scripts to Supabase ──────────────────────────────────────────────────
def save_scripts(story_id, scripts):
    url  = f"{REST_URL}/content_queue?id=eq.{story_id}"
    resp = requests.patch(url, headers=SUPA_HEADERS, json=scripts, timeout=15)
    return resp.status_code in (200, 204)

# ── Main ──────────────────────────────────────────────────────────────────────
def run_generator():
    print("=" * 60)
    print(f"✍️  Script Generator — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    stories = fetch_pending_scripts()
    print(f"  📋 Found {len(stories)} stories needing scripts")

    if not stories:
        print("  ✅ All caught up — no pending scripts!")
        return

    success = failed = 0

    for story in stories:
        sid       = story["id"]
        title     = story["title"]
        niche     = story.get("niche", "general")
        summary   = story.get("summary", "")
        source    = story.get("source_name", "")
        context   = summary or title
        is_idea   = source == "💡 My Idea"

        print(f"\n  ✍️  [{niche}] {title[:55]}")

        # Generate Tamil and English separately to avoid token limits
        print(f"      → Generating Tamil scripts...")
        tamil_scripts = generate_tamil(title, context, niche, is_idea)

        print(f"      → Generating English scripts...")
        english_scripts = generate_english(title, context, niche, is_idea)

        all_scripts = {**tamil_scripts, **english_scripts}

        if any(v for v in all_scripts.values()):
            if save_scripts(sid, all_scripts):
                success += 1
                tamil_count   = sum(1 for v in tamil_scripts.values() if v)
                english_count = sum(1 for v in english_scripts.values() if v)
                print(f"      ✅ Saved! Tamil: {tamil_count}/6  English: {english_count}/6")
            else:
                failed += 1
                print(f"      ❌ Failed to save to Supabase")
        else:
            failed += 1
            print(f"      ❌ No scripts generated")

    print("\n" + "=" * 60)
    print(f"✅ Done — Generated: {success}  |  Failed: {failed}")
    print("=" * 60)

if __name__ == "__main__":
    run_generator()
