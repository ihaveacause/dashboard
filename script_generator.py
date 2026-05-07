"""
I Have a Cause — AI Script Generator (Sprint 3) — Failproof Version
- Generates Tamil + English scripts separately (avoids token limits)
- Saves each script field INDIVIDUALLY (one failure never loses the others)
- Full error logging from Supabase
- Processes all approved stories with missing scripts
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

# ── All script fields we handle ───────────────────────────────────────────────
ALL_SCRIPT_FIELDS = [
    "script_youtube_tamil",
    "script_youtube_short_tamil",
    "script_reel_tamil",
    "script_meme_tamil",
    "script_x_thread",
    "script_x_post",
    "script_youtube_english",
    "script_youtube_short_english",
    "script_reel_english",
    "script_meme_english",
    "script_x_thread_english",
    "script_x_post_english",
]

# ── Fetch approved stories missing Tamil script ───────────────────────────────
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
    print(f"  ❌ Fetch error {resp.status_code}: {resp.text[:300]}")
    return []

# ── Call Claude AI ────────────────────────────────────────────────────────────
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
        print(f"  ❌ Claude error {resp.status_code}: {resp.text[:300]}")
        return None
    return resp.json()["content"][0]["text"].strip()

# ── Extract section between two delimiters ────────────────────────────────────
def extract(raw, start_marker, end_marker):
    s = raw.find(start_marker)
    e = raw.find(end_marker)
    if s == -1 or e == -1 or e <= s:
        return None
    content = raw[s + len(start_marker):e].strip()
    return content if content else None

# ── Save ONE field at a time ──────────────────────────────────────────────────
def save_field(story_id, field_name, content):
    if not content:
        return True  # nothing to save, skip silently
    url  = f"{REST_URL}/content_queue?id=eq.{story_id}"
    resp = requests.patch(
        url,
        headers=SUPA_HEADERS,
        json={field_name: content},
        timeout=15
    )
    if resp.status_code in (200, 204):
        return True
    print(f"      ❌ Save failed [{field_name}] — {resp.status_code}: {resp.text[:200]}")
    return False

# ── Generate Tamil scripts ────────────────────────────────────────────────────
def generate_tamil(title, context, niche, is_my_idea):
    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான Tamil content creator.

தலைப்பு: {title}
சுருக்கம்: {context}
வகை: {niche}
{"இது creator சொந்த யோசனை" if is_my_idea else "இது செய்தி கதை"}

தெளிவான, இயல்பான பேச்சு Tamil-ல் scripts எழுதுங்கள்.
ஒவ்வொரு script-லும் context விளக்குங்கள் — பார்வையாளர்களுக்கு முன்பே தெரியாது என்று வைத்துக்கொள்ளுங்கள்.

கீழ்கண்ட delimiters-ஐ அப்படியே பயன்படுத்துங்கள்:

<<<YT_LONG_TA>>>
HOOK: [கவனம் ஈர்க்கும் தொடக்கம்]
CONTEXT: [என்ன நடந்தது என்று தெளிவாக விளக்கு — 5-8 நிமிட script]
YOUR TAKE: [உங்கள் கருத்து மற்றும் பகுப்பாய்வு]
CTA: [subscribe மற்றும் share]
<<<YT_SHORT_TA>>>
[30-60 வினாடி YouTube Short — வேகமான, கவர்ச்சியான]
<<<REEL_TA>>>
[30-60 வினாடி Instagram Reel — viral-ஆன]
<<<MEME_TA>>>
MAIN: [முக்கிய வரி]
SUB: [விளக்கம்]
CAPTION: [post விளக்கம்]
HASHTAGS: [hashtags]
<<<X_THREAD_TA>>>
1/ [hook]
2/ [context]
3/ [key point]
4/ [your take]
5/ [YouTube link]
<<<X_POST_TA>>>
[280 எழுத்துக்கு குறைவான tweet]
<<<END>>>"""

    raw = call_claude(prompt)
    if not raw:
        return {}

    return {
        "script_youtube_tamil":       extract(raw, "<<<YT_LONG_TA>>>",  "<<<YT_SHORT_TA>>>"),
        "script_youtube_short_tamil": extract(raw, "<<<YT_SHORT_TA>>>", "<<<REEL_TA>>>"),
        "script_reel_tamil":          extract(raw, "<<<REEL_TA>>>",      "<<<MEME_TA>>>"),
        "script_meme_tamil":          extract(raw, "<<<MEME_TA>>>",      "<<<X_THREAD_TA>>>"),
        "script_x_thread":            extract(raw, "<<<X_THREAD_TA>>>",  "<<<X_POST_TA>>>"),
        "script_x_post":              extract(raw, "<<<X_POST_TA>>>",    "<<<END>>>"),
    }

# ── Generate English scripts ──────────────────────────────────────────────────
def generate_english(title, context, niche, is_my_idea):
    prompt = f"""You are a content creator for "I Have a Cause" — a Tamil/Indian news commentary YouTube channel.

Title: {title}
Summary: {context}
Niche: {niche}
Type: {"Creator's original idea" if is_my_idea else "News story"}

Write in simple conversational English. Always explain context fully — assume viewers know nothing about this story.
Use these exact delimiters:

<<<YT_LONG_EN>>>
HOOK: [attention-grabbing opening]
CONTEXT: [clearly explain what happened — 5-8 minute script]
YOUR TAKE: [your opinion and analysis]
CTA: [subscribe and share]
<<<YT_SHORT_EN>>>
[30-60 second YouTube Short — punchy and fast]
<<<REEL_EN>>>
[30-60 second Instagram Reel — viral and engaging]
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
        "script_youtube_english":       extract(raw, "<<<YT_LONG_EN>>>",  "<<<YT_SHORT_EN>>>"),
        "script_youtube_short_english": extract(raw, "<<<YT_SHORT_EN>>>", "<<<REEL_EN>>>"),
        "script_reel_english":          extract(raw, "<<<REEL_EN>>>",      "<<<MEME_EN>>>"),
        "script_meme_english":          extract(raw, "<<<MEME_EN>>>",      "<<<X_THREAD_EN>>>"),
        "script_x_thread_english":      extract(raw, "<<<X_THREAD_EN>>>",  "<<<X_POST_EN>>>"),
        "script_x_post_english":        extract(raw, "<<<X_POST_EN>>>",    "<<<END>>>"),
    }

# ── Main ──────────────────────────────────────────────────────────────────────
def run_generator():
    print("=" * 60)
    print(f"✍️  Script Generator — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    stories = fetch_pending_scripts()
    print(f"  📋 Found {len(stories)} stories needing scripts\n")

    if not stories:
        print("  ✅ All caught up — no pending scripts!")
        return

    total_saved = total_failed = 0

    for story in stories:
        sid      = story["id"]
        title    = story["title"]
        niche    = story.get("niche", "general")
        summary  = story.get("summary", "")
        source   = story.get("source_name", "")
        context  = summary or title
        is_idea  = source == "💡 My Idea"

        print(f"  ✍️  [{niche}] {title[:55]}")

        # ── Tamil ──
        print(f"      → Tamil scripts...")
        tamil = generate_tamil(title, context, niche, is_idea)
        saved_ta = failed_ta = 0
        for field, content in tamil.items():
            if save_field(sid, field, content):
                saved_ta += 1
            else:
                failed_ta += 1
        print(f"      ✅ Tamil: {saved_ta} saved, {failed_ta} failed")

        # ── English ──
        print(f"      → English scripts...")
        english = generate_english(title, context, niche, is_idea)
        saved_en = failed_en = 0
        for field, content in english.items():
            if save_field(sid, field, content):
                saved_en += 1
            else:
                failed_en += 1
        print(f"      ✅ English: {saved_en} saved, {failed_en} failed")

        story_saved  = saved_ta + saved_en
        story_failed = failed_ta + failed_en
        total_saved  += story_saved
        total_failed += story_failed
        print(f"      📊 Story total: {story_saved} scripts saved, {story_failed} failed\n")

    print("=" * 60)
    print(f"✅ Done — Total saved: {total_saved}  |  Total failed: {total_failed}")
    print("=" * 60)

if __name__ == "__main__":
    run_generator()
