"""
I Have a Cause — AI Script Generator (Sprint 3)
Finds approved stories with no scripts and generates
Tamil + English scripts for all formats using Claude AI
"""

import os
import json
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

# ── Fetch approved stories that need scripts ──────────────────────────────────
def fetch_pending_scripts():
    url = f"{REST_URL}/content_queue"
    params = {
        "status":                  "eq.approved",
        "script_youtube_tamil":    "is.null",
        "select":                  "*",
        "order":                   "approved_at.desc",
        "limit":                   "20"
    }
    resp = requests.get(url, headers={**SUPA_HEADERS, "Prefer": "return=representation"}, params=params, timeout=15)
    if resp.status_code == 200:
        return resp.json()
    print(f"  ❌ Failed to fetch stories: {resp.status_code} {resp.text}")
    return []

# ── Call Claude AI to generate scripts ───────────────────────────────────────
def generate_scripts(title, summary, niche, source_name):
    is_my_idea = source_name == "💡 My Idea"
    context = summary or title

    prompt = f"""You are a Tamil YouTube content creator writing scripts for the channel "I Have a Cause" — a Tamil/English news commentary and opinion channel.

Story details:
- Title: {title}
- Summary: {context}
- Niche: {niche}
- Type: {"Creator's original idea" if is_my_idea else "News story"}

Generate scripts for ALL 6 formats below. Write naturally, conversationally, and engagingly.
For Tamil scripts use Tamil language (தமிழ்). For English scripts use simple conversational English.
Always explain the context so viewers who haven't seen the original story understand everything.

Return ONLY a valid JSON object with these exact keys (no extra text, no markdown):

{{
  "script_youtube_tamil": "Full 5-8 minute Tamil YouTube script with HOOK, CONTEXT, YOUR TAKE, CTA sections",
  "script_youtube_english": "Full 5-8 minute English YouTube script with HOOK, CONTEXT, YOUR TAKE, CTA sections",
  "script_reel_tamil": "30-60 second punchy Tamil reel script",
  "script_reel_english": "30-60 second punchy English reel script",
  "meme_caption": "Main text (bold punchline), Sub text (explanation), Caption (post description), Hashtags",
  "script_x_thread": "5-8 tweet thread. Each tweet on a new line starting with 1/, 2/ etc. Last tweet links to YouTube.",
  "script_x_post": "Single punchy tweet under 280 characters with hashtags"
}}"""

    payload = {
        "model":      "claude-sonnet-4-20250514",
        "max_tokens": 4000,
        "messages":   [{"role": "user", "content": prompt}]
    }

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=CLAUDE_HEADERS,
        json=payload,
        timeout=60
    )

    if resp.status_code != 200:
        print(f"  ❌ Claude API error: {resp.status_code} {resp.text}")
        return None

    raw = resp.json()["content"][0]["text"].strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON parse error: {e}")
        print(f"  Raw response: {raw[:200]}")
        return None

# ── Save scripts back to Supabase ─────────────────────────────────────────────
def save_scripts(story_id, scripts):
    url = f"{REST_URL}/content_queue?id=eq.{story_id}"
    payload = {
        "script_youtube_tamil":   scripts.get("script_youtube_tamil"),
        "script_youtube_english": scripts.get("script_youtube_english"),
        "script_reel_tamil":      scripts.get("script_reel_tamil"),
        "script_reel_english":    scripts.get("script_reel_english"),
        "meme_caption":           scripts.get("meme_caption"),
        "script_x_thread":        scripts.get("script_x_thread"),
        "script_x_post":          scripts.get("script_x_post"),
    }
    resp = requests.patch(url, headers=SUPA_HEADERS, json=payload, timeout=15)
    return resp.status_code in (200, 204)

# ── Main ──────────────────────────────────────────────────────────────────────
def run_generator():
    print("=" * 60)
    print(f"✍️  Script Generator started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    stories = fetch_pending_scripts()
    print(f"  📋 Found {len(stories)} approved stories needing scripts")

    if not stories:
        print("  ✅ Nothing to do — all approved stories already have scripts!")
        return

    success = failed = 0

    for story in stories:
        sid    = story["id"]
        title  = story["title"]
        niche  = story.get("niche", "general")
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
                print(f"  ❌ Failed to save scripts")
        else:
            failed += 1
            print(f"  ❌ Script generation failed")

    print("\n" + "=" * 60)
    print(f"✅ Done — Generated: {success}  |  Failed: {failed}")
    print("=" * 60)

if __name__ == "__main__":
    run_generator()
