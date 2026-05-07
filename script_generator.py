"""
I Have a Cause — AI Script Generator (Final)
- Runs every 30 minutes via GitHub Actions
- Mode 1: Generate all 12 scripts for newly approved stories
- Mode 2: Regenerate all 11 formats from edited YT Long (when regenerate_requested = true)
- Saves each field individually (failproof)
- Updates status: approved → scripted
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

# ── Fetch stories needing fresh scripts ───────────────────────────────────────
def fetch_new_approvals():
    params = {
        "status":               "eq.approved",
        "script_youtube_tamil": "is.null",
        "select":               "*",
        "order":                "approved_at.desc",
        "limit":                "20"
    }
    return db_get("content_queue", params)

# ── Fetch stories needing regeneration ───────────────────────────────────────
def fetch_regeneration_requests():
    params = {
        "regenerate_requested": "eq.true",
        "select":               "*",
        "order":                "updated_at.desc",
        "limit":                "10"
    }
    return db_get("content_queue", params)

# ── Supabase helpers ──────────────────────────────────────────────────────────
def db_get(table, params):
    resp = requests.get(
        f"{REST_URL}/{table}",
        headers={**SUPA_HEADERS, "Prefer": "return=representation"},
        params=params,
        timeout=15
    )
    if resp.status_code == 200:
        return resp.json()
    print(f"  ❌ Fetch error {resp.status_code}: {resp.text[:200]}")
    return []

def save_field(story_id, field_name, content):
    """Save a single field — returns True on success"""
    if not content:
        return True  # nothing to save, skip
    resp = requests.patch(
        f"{REST_URL}/content_queue?id=eq.{story_id}",
        headers=SUPA_HEADERS,
        json={field_name: content},
        timeout=15
    )
    if resp.status_code in (200, 204):
        return True
    print(f"      ❌ [{field_name}] save failed — {resp.status_code}: {resp.text[:150]}")
    return False

def update_status(story_id, status, extra=None):
    data = {"status": status}
    if extra:
        data.update(extra)
    resp = requests.patch(
        f"{REST_URL}/content_queue?id=eq.{story_id}",
        headers=SUPA_HEADERS,
        json=data,
        timeout=15
    )
    return resp.status_code in (200, 204)

# ── Claude API call ───────────────────────────────────────────────────────────
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
        print(f"  ❌ Claude error {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()["content"][0]["text"].strip()

# ── Delimiter extractor ───────────────────────────────────────────────────────
def extract(raw, start, end):
    s = raw.find(start)
    e = raw.find(end)
    if s == -1 or e == -1 or e <= s:
        return None
    content = raw[s + len(start):e].strip()
    return content if content else None

# ── MODE 1: Generate Tamil scripts from news story ────────────────────────────
def generate_tamil_fresh(title, context, niche, is_idea):
    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான Tamil content creator.

தலைப்பு: {title}
சுருக்கம்: {context}
வகை: {niche}
{"இது creator சொந்த யோசனை" if is_idea else "இது செய்தி கதை"}

தெளிவான இயல்பான பேச்சு Tamil-ல் 6 formats எழுதுங்கள்.
ஒவ்வொரு scriptலும் context விளக்குங்கள் — பார்வையாளர்களுக்கு முன்பே தெரியாது என்று வைத்துக்கொள்ளுங்கள்.
கீழ்கண்ட delimiters அப்படியே பயன்படுத்துங்கள்:

<<<YT_LONG_TA>>>
HOOK: [கவனம் ஈர்க்கும் தொடக்கம்]
CONTEXT: [என்ன நடந்தது என்று தெளிவாக விளக்கு]
YOUR TAKE: [உங்கள் கருத்து மற்றும் பகுப்பாய்வு]
CTA: [subscribe மற்றும் share]
<<<YT_SHORT_TA>>>
[30-60 வினாடி YouTube Short — வேகமான கவர்ச்சியான]
<<<REEL_TA>>>
[30-60 வினாடி Instagram Reel — viral ஆன]
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

# ── MODE 1: Generate English scripts from news story ─────────────────────────
def generate_english_fresh(title, context, niche, is_idea):
    prompt = f"""You are a content creator for "I Have a Cause" — Tamil/Indian news commentary channel.

Title: {title}
Summary: {context}
Niche: {niche}
Type: {"Creator's original idea" if is_idea else "News story"}

Write in simple conversational English. Always explain context — assume viewers know nothing about this story.
Use these exact delimiters:

<<<YT_LONG_EN>>>
HOOK: [attention-grabbing opening]
CONTEXT: [clearly explain what happened]
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

# ── MODE 2: Regenerate Tamil formats from edited YT Long ──────────────────────
def regenerate_tamil_from_yt_long(yt_long_tamil, title, niche):
    prompt = f"""நீங்கள் "I Have a Cause" YouTube சேனலுக்கான Tamil content creator.

கீழே உள்ளது approved YouTube Long script. இதை மட்டும் base-ஆக வைத்து மற்ற 5 formats எழுதுங்கள்.
tone, message, core content எல்லாம் இந்த YT Long scriptஐ follow செய்யவேண்டும்.

APPROVED YOUTUBE LONG SCRIPT:
{yt_long_tamil}

தலைப்பு: {title} | வகை: {niche}

<<<YT_SHORT_TA>>>
[YT Long-ஐ base-ஆக வைத்து 30-60 வினாடி YouTube Short]
<<<REEL_TA>>>
[YT Long-ஐ base-ஆக வைத்து 30-60 வினாடி Reel]
<<<MEME_TA>>>
MAIN: [YT Long message-இன் punchline]
SUB: [விளக்கம்]
CAPTION: [post விளக்கம்]
HASHTAGS: [hashtags]
<<<X_THREAD_TA>>>
1/ [hook from YT Long]
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
        "script_youtube_short_tamil": extract(raw, "<<<YT_SHORT_TA>>>", "<<<REEL_TA>>>"),
        "script_reel_tamil":          extract(raw, "<<<REEL_TA>>>",      "<<<MEME_TA>>>"),
        "script_meme_tamil":          extract(raw, "<<<MEME_TA>>>",      "<<<X_THREAD_TA>>>"),
        "script_x_thread":            extract(raw, "<<<X_THREAD_TA>>>",  "<<<X_POST_TA>>>"),
        "script_x_post":              extract(raw, "<<<X_POST_TA>>>",    "<<<END>>>"),
    }

# ── MODE 2: Regenerate English formats from edited YT Long ────────────────────
def regenerate_english_from_yt_long(yt_long_english, title, niche):
    prompt = f"""You are a content creator for "I Have a Cause" — Tamil/Indian news channel.

Below is the approved YouTube Long script. Use ONLY this as the base for the other 5 formats.
Keep the same tone, message and core content as the YT Long.

APPROVED YOUTUBE LONG SCRIPT:
{yt_long_english}

Title: {title} | Niche: {niche}

<<<YT_SHORT_EN>>>
[30-60 second Short based on the YT Long above]
<<<REEL_EN>>>
[30-60 second Reel based on the YT Long above]
<<<MEME_EN>>>
MAIN: [punchline from YT Long message]
SUB: [short explanation]
CAPTION: [post description]
HASHTAGS: [relevant hashtags]
<<<X_THREAD_EN>>>
1/ [hook from YT Long]
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
        "script_youtube_short_english": extract(raw, "<<<YT_SHORT_EN>>>", "<<<REEL_EN>>>"),
        "script_reel_english":          extract(raw, "<<<REEL_EN>>>",      "<<<MEME_EN>>>"),
        "script_meme_english":          extract(raw, "<<<MEME_EN>>>",      "<<<X_THREAD_EN>>>"),
        "script_x_thread_english":      extract(raw, "<<<X_THREAD_EN>>>",  "<<<X_POST_EN>>>"),
        "script_x_post_english":        extract(raw, "<<<X_POST_EN>>>",    "<<<END>>>"),
    }

# ── Save all fields from a dict ───────────────────────────────────────────────
def save_all_fields(story_id, scripts):
    saved = failed = 0
    for field, content in scripts.items():
        if save_field(story_id, field, content):
            saved += 1
        else:
            failed += 1
    return saved, failed

# ── Process fresh approvals ───────────────────────────────────────────────────
def process_new_approvals():
    stories = fetch_new_approvals()
    if not stories:
        return 0

    print(f"\n  📋 MODE 1 — {len(stories)} new approvals to process")
    processed = 0

    for story in stories:
        sid     = story["id"]
        title   = story["title"]
        niche   = story.get("niche", "general")
        summary = story.get("summary", "")
        source  = story.get("source_name", "")
        context = summary or title
        is_idea = source == "💡 My Idea"

        print(f"\n  ✍️  [{niche}] {title[:55]}")

        print(f"      → Tamil (6 formats)...")
        tamil = generate_tamil_fresh(title, context, niche, is_idea)
        s1, f1 = save_all_fields(sid, tamil)
        print(f"         Saved: {s1}  Failed: {f1}")

        print(f"      → English (6 formats)...")
        english = generate_english_fresh(title, context, niche, is_idea)
        s2, f2 = save_all_fields(sid, english)
        print(f"         Saved: {s2}  Failed: {f2}")

        total_saved = s1 + s2
        if total_saved > 0:
            update_status(sid, "scripted")
            print(f"      ✅ Status → scripted ({total_saved}/12 scripts saved)")
            processed += 1
        else:
            print(f"      ❌ No scripts saved — status unchanged")

    return processed

# ── Process regeneration requests ────────────────────────────────────────────
def process_regenerations():
    stories = fetch_regeneration_requests()
    if not stories:
        return 0

    print(f"\n  📋 MODE 2 — {len(stories)} regeneration requests")
    processed = 0

    for story in stories:
        sid         = story["id"]
        title       = story["title"]
        niche       = story.get("niche", "general")
        yt_long_ta  = story.get("script_youtube_tamil", "")
        yt_long_en  = story.get("script_youtube_english", "")

        print(f"\n  🔄  Regenerating from YT Long: {title[:55]}")

        if yt_long_ta:
            print(f"      → Tamil (5 formats from YT Long Tamil)...")
            tamil = regenerate_tamil_from_yt_long(yt_long_ta, title, niche)
            s1, f1 = save_all_fields(sid, tamil)
            print(f"         Saved: {s1}  Failed: {f1}")

        if yt_long_en:
            print(f"      → English (5 formats from YT Long English)...")
            english = regenerate_english_from_yt_long(yt_long_en, title, niche)
            s2, f2 = save_all_fields(sid, english)
            print(f"         Saved: {s2}  Failed: {f2}")

        # Clear regeneration flag + set back to scripted
        update_status(sid, "scripted", {"regenerate_requested": False})
        print(f"      ✅ Regeneration complete")
        processed += 1

    return processed

# ── Main ──────────────────────────────────────────────────────────────────────
def run_generator():
    print("=" * 60)
    print(f"✍️  Script Generator — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    p1 = process_new_approvals()
    p2 = process_regenerations()

    if p1 == 0 and p2 == 0:
        print("\n  ✅ Nothing to do — all caught up!")

    print("\n" + "=" * 60)
    print(f"✅ Done — New: {p1}  |  Regenerated: {p2}")
    print("=" * 60)

if __name__ == "__main__":
    run_generator()
