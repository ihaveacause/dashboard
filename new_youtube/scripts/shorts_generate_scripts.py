"""
shorts_generate_scripts.py — Sprint 15 (Shorts track)
=======================================================
Reads the APPROVED long-form episode script and asks Gemini to find
1 to 3 moments strong enough to stand alone as a YouTube Short —
each one a single, self-sufficient, provocative point that a viewer
gets full value from in 45-60 seconds, WITHOUT having watched the long
video, but that still ends on a hook pulling them toward it.

Gemini decides the count (1-3) based on how many genuinely standalone,
provocative moments actually exist — never padded to a quota.

Mirrors script_generator.py's Vertex AI auth pattern exactly.

Triggered by: shorts_generate_scripts.yml
Env vars: SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON,
          EPISODE_NUMBER, LANGUAGE (ta or en)
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
LANGUAGE       = os.environ.get("LANGUAGE", "ta")   # ta or en

PROJECT_ID = "gen-lang-client-0078128013"
LOCATION   = "us-central1"

# ── Vertex AI auth ────────────────────────────────────────────
creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)
model = GenerativeModel("gemini-2.5-pro")

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST = f"{SUPABASE_URL}/rest/v1"

EPISODE_TABLE = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
SHORTS_TABLE  = "tamil_shorts"   if LANGUAGE == "ta" else "english_shorts"
SCRIPT_COL    = "script_tamil"   if LANGUAGE == "ta" else "script_english"
TITLE_COL     = "title_tamil"    if LANGUAGE == "ta" else "title_english"

# ── Supabase helpers ──────────────────────────────────────────
def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_delete(table, params):
    r = requests.delete(f"{REST}/{table}", headers=SB_HEADERS, params=params, timeout=15)
    return r.status_code in (200, 204)

def db_insert(table, rows):
    r = requests.post(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        json=rows, timeout=30
    )
    if r.status_code not in (200, 201):
        print(f"   ❌ Supabase insert error {r.status_code}: {r.text[:500]}")
        return []
    return r.json()

def generate(prompt):
    response = model.generate_content(prompt)
    parts = []
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
    return "\n".join(parts)

# ── Fetch the approved episode ──────────────────────────────────
def fetch_episode():
    rows = db_get(EPISODE_TABLE, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    return rows[0] if rows else None

# ── Step 1: Gemini finds + writes 1-3 standalone provocative shorts ────────────
def generate_shorts(episode):
    lang_note   = "Tamil" if LANGUAGE == "ta" else "English"
    title       = episode.get(TITLE_COL) or ""
    long_script = episode.get(SCRIPT_COL) or ""
    module      = episode.get("module") or ""

    prompt = f"""You are the Shorts strategist for "I Have a Cause" — a {lang_note} philosophy/social-reform
YouTube channel. You are given the FULL, APPROVED long-form script for one episode.

EPISODE TITLE: {title}
MODULE: {module}

FULL LONG SCRIPT:
{long_script}

YOUR JOB:
Find between 1 and 3 moments in this script that are strong enough to become standalone
YouTube Shorts. Do NOT force 3 — if only 1 or 2 moments are genuinely strong, return fewer.
Never pad with a weak or repetitive short just to hit a number.

Each Short you propose MUST satisfy ALL of these:
1. SELF-SUFFICIENT: a viewer who has NEVER seen the long video gets one complete,
   satisfying point from it alone — a full thought, not a fragment.
2. PROVOCATIVE: opens with a claim, question, or statement designed to stop the scroll
   in the first 2 seconds — bold, specific, a little uncomfortable or surprising.
3. ONE MESSAGE ONLY: a single idea, argued tightly. No sub-points, no meandering.
4. ENDS ON A HOOK: the final line creates a gap the viewer wants closed — a
   cliffhanger, an implied "but here's the part that changes everything," or a direct
   provocation — that can ONLY be resolved by watching the full episode. Never spell
   out the resolution. Never explicitly say "watch the full video" as a flat CTA —
   the hook itself should pull them, not ask them.
5. LENGTH: 100-150 words of spoken script (45-60 seconds at natural pace).
6. VOICE: matches the channel's existing voice in the long script exactly — same tone,
   same register, same style of argument. This must feel like it was cut FROM this
   episode, not written separately.
7. Written entirely in {lang_note} — not one word of another language.

You ALSO write a series of on-screen hook texts — these are NOT spoken lines from the
script. They're big bold text punched onto the screen at intervals through the WHOLE
short (not just the opening) — the visual thread that keeps a scrolling thumb engaged
from start to finish, not just at second one. Write 3 to 4 of them, one for each rough
beat of the short's arc (opening hook → escalation → the sharpest point → the closing
tension), so a new punchy phrase appears roughly every 10-15 seconds throughout.
Rules for EACH phrase:
- MAX 6 words. Shorter is almost always better (3-5 words is ideal) — these need to be
  readable in under a second, not read like a sentence.
- Phrase it as a direct QUESTION or a blunt, provocative CLAIM — never a description.
  Bad: "The truth about sleep". Good: "Sleep is lying to you." or "Why can't you sleep?"
- Each one should escalate or shift the tension from the last — not repeat the same
  point reworded. Think of them as a mini trail of breadcrumbs, not 4 copies of a title.
- They do NOT need to be verbatim lines from the script — write them fresh, purely for
  maximum scroll-stopping punch, as long as they're true to the short's actual content.
- No hashtags, no emoji, no punctuation-as-decoration — just the bare, punchy phrase.

Return ONLY valid JSON, no markdown, no explanation:
{{
  "shorts": [
    {{
      "title": "<short, punchy on-screen title, under 60 chars>",
      "on_screen_texts": ["<max 6 words, beat 1: the opening hook>", "<beat 2: escalation>", "<beat 3: sharpest point>", "<optional beat 4: closing tension>"],
      "hook_line": "<the exact opening line of the SPOKEN script — the scroll-stopper>",
      "script": "<the complete self-contained short script, 100-150 words, spoken prose only>",
      "cta_line": "<the exact closing line — the hook toward the full episode>"
    }}
  ]
}}"""

    def _parse(raw):
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())

    raw = generate(prompt)
    try:
        data = _parse(raw)
    except Exception as e:
        print(f"   ⚠️  Parse error: {e} — raw head: {raw[:200]}")
        return []

    shorts = data.get("shorts", [])[:3]   # hard cap at 3 regardless
    print(f"   ✅ Gemini proposed {len(shorts)} short(s)")
    for s in shorts:
        print(f"      • {s.get('title','')}")
    return shorts

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Shorts Script Generator — Episode {EPISODE_NUMBER} ({LANGUAGE})")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found in {EPISODE_TABLE}")
        return

    script_col_val = episode.get(SCRIPT_COL)
    if not script_col_val:
        print(f"❌ No approved long script in '{SCRIPT_COL}' — approve the episode script first")
        return

    try:
        existing = db_get(SHORTS_TABLE, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "id,status,short_index"})
        published = [r for r in existing if r.get("status") == "published"]
        if published:
            indices = ", ".join(str(r["short_index"]) for r in published)
            print(f"❌ Refusing to regenerate — short #{indices} for episode {EPISODE_NUMBER} is already "
                  f"published to YouTube. Regenerating would delete its row here while leaving the live "
                  f"video up, orphaning it from the dashboard. Delete/unpublish it manually first if you "
                  f"really want to replace it.")
            return

        shorts = generate_shorts(episode)
        if not shorts:
            print("❌ No usable shorts found for this episode — nothing written")
            return

        # Replace any existing (non-published) shorts for this episode — safe to
        # re-run/regenerate now that we've confirmed none are published above.
        db_delete(SHORTS_TABLE, {"episode_number": f"eq.{EPISODE_NUMBER}"})

        rows = []
        for i, s in enumerate(shorts, start=1):
            rows.append({
                "episode_number": EPISODE_NUMBER,
                "short_index":    i,
                "title":          s.get("title", ""),
                "on_screen_texts": s.get("on_screen_texts", []),
                "hook_line":      s.get("hook_line", ""),
                "script":         s.get("script", ""),
                "cta_line":       s.get("cta_line", ""),
                "status":         "script_ready",
            })

        saved = db_insert(SHORTS_TABLE, rows)
        if saved:
            print(f"\n{'='*60}")
            print(f"✅ {len(saved)} short(s) saved — script_ready")
            print(f"{'='*60}")
        else:
            print("❌ Failed to save shorts to Supabase")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
