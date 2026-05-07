"""
I Have a Cause — AI Script Generator (Sprint 3)
Finds approved stories with no scripts and generates
Tamil + English scripts for all formats using Claude AI
Uses delimiter-based parsing (reliable for Tamil text)
"""

import os
import re
import requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]

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

DELIMITERS = [
    "<<<YOUTUBE_TAMIL>>>",
    "<<<YOUTUBE_ENGLISH>>>",
    "<<<REEL_TAMIL>>>",
    "<<<REEL_ENGLISH>>>",
    "<<<MEME>>>",
    "<<<X_THREAD>>>",
    "<<<X_POST>>>",
    "<<<END>>>"
]

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
    print(f"  ❌ Failed to fetch stories: {resp.status_code} {resp.text}")
    return []

# ── Call Claude AI ────────────────────────────────────────────────────────────
def generate_scripts(title, summary, niche, source_name):
    is_my_idea = source_name == "💡 My Idea"
    context    = summary or title

    prompt = f"""You are a Tamil YouTube content creator writing scripts for the channel "I Have a Cause" — a Tamil/English news commentary and opinion channel.

Story details:
Title: {title}
Summary: {context}
Niche: {niche}
Type: {"Creator original idea" if is_my_idea else "News story"}

Write scripts for all 6 formats. Always explain context so viewers who know nothing about this story understand fully.
For Tamil scripts write in Tamil language. For English write conversational English.

OUTPUT FORMAT — use these exact delimiters, put each script between its markers:

<<<YOUTUBE_TAMIL>>>
[Full 5-8 minute Tamil YouTube script]
HOOK: attention-grabbing opening in Tamil
CONTEXT: explain what happened clearly in Tamil
YOUR TAKE: your opinion and analysis in Tamil
CTA: subscribe and share call to action in Tamil
<<<YOUTUBE_ENGLISH>>>
[Full 5-8 minute English YouTube script]
HOOK: attention-grabbing opening
CONTEXT: explain what happened clearly
YOUR TAKE: your opinion and analysis
CTA: subscribe and share call to action
<<<REEL_TAMIL>>>
[30-60 second punchy Tamil reel script - short and viral]
<<<REEL_ENGLISH>>>
[30-60 second punchy English reel script - short and viral]
<<<MEME>>>
MAIN TEXT: [bold punchline]
SUB TEXT: [short explanation]
CAPTION: [post description for Instagram/X]
HASHTAGS: [relevant hashtags]
<<<X_THREAD>>>
1/ [first tweet - hook]
2/ [context tweet]
3/ [key point]
4/ [your take]
5/ [conclusion + YouTube link]
<<<X_POST>>>
[single punchy tweet under 280 characters with hashtags]
<<<END>>>"""

    payload = {
        "model":      "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "messages":   [{"role": "user", "content": prompt}]
    }

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=CLAUDE_HEADERS,
        json=payload,
        timeout=90
    )

    if resp.status_code != 200:
        print(f"  ❌ Claude API error: {resp.status_code} {resp.text[:200]}")
        return None

    raw = resp.json()["content"][0]["text"].strip()
    return parse_scripts(raw)

# ── Parse delimiter-based response ───────────────────────────────────────────
def parse_scripts(raw):
    keys = [
        "script_youtube_tamil",
        "script_youtube_english",
        "script_reel_tamil",
        "script_reel_english",
        "meme_caption",
        "script_x_thread",
        "script_x_post",
    ]
    end_markers = DELIMITERS[1:] + ["<<<END>>>"]
    result = {}

    for i, start_marker in enumerate(DELIMITERS[:-1]):
        end_marker = end_markers[i]
        start_idx = raw.find(start_marker)
        end_idx   = raw.find(end_marker)

        if start_idx == -1 or end_idx == -1:
            print(f"  ⚠️  Missing section: {start_marker}")
            result[keys[i]] = None
            continue

        content = raw[start_idx + len(start_marker):end_idx].strip()
        result[keys[i]] = content if content else None

    return result

# ── Save scripts to Supabase ──────────────────────────────────────────────────
def save_scripts(story_id, scripts):
    url  = f"{REST_URL}/content_queue?id=eq.{story_id}"
    resp = requests.patch(url, headers=SUPA_HEADERS, json=scripts, timeout=15)
    return resp.status_code in (200, 204)

# ── Main ──────────────────────────────────────────────────────────────────────
def run_generator():
    print("=" * 60)
    print(f"✍️  Script Generator started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    stories = fetch_pending_scripts()
    print(f"  📋 Found {len(stories)} approved stories needing scripts")

    if not stories:
        print("  ✅ Nothing to do — all approved stories have scripts!")
        return

    success = failed = 0

    for story in stories:
        sid     = story["id"]
        title   = story["title"]
        niche   = story.get("niche", "general")
        summary = story.get("summary", "")
        source  = story.get("source_name", "")

        print(f"\n  ✍️  Generating: {title[:60]}")

        scripts = generate_scripts(title, summary, niche, source)

        if scripts:
            if save_scripts(sid, scripts):
                success += 1
                print(f"  ✅ Scripts saved!")
            else:
                failed += 1
                print(f"  ❌ Failed to save to Supabase")
        else:
            failed += 1
            print(f"  ❌ Script generation failed")

    print("\n" + "=" * 60)
    print(f"✅ Done — Generated: {success}  |  Failed: {failed}")
    print("=" * 60)

if __name__ == "__main__":
    run_generator()
