"""
anchor_beats.py — On Camera (Studio Desk) Pipeline · Step 2 of 3
=================================================================
Turns the transcript (+ your chosen title) into a timed beat sheet for the
studio render. Each beat is one moment on screen and carries:

  mode         : "image"  -> an AI studio image sits behind / beside you
                 "text"   -> kinetic text only (no image) for that moment
  headline     : the catchy lower-third line for this beat (in your language)
  bullets      : 0-3 very short summary points (optional, for text beats)
  scene        : what to illustrate (image beats only)
  image_prompt : the daylight-studio prompt actually sent to the image model
  trigger      : VERBATIM opening words of the beat, used to time it against
                 your speech (word_timings from step 1)
  start / end  : seconds, computed here from the trigger + word_timings
  image_url    : filled in for image beats after Vertex renders them

Claude proposes each beat's mode (names/comparisons/punchlines -> text;
places/events/concepts -> image). You can override any beat's mode or headline
in the dashboard before rendering.

Images run on Vertex AI nano-banana (credit-covered), anchor-conditioned so the
whole video shares ONE look, in the channel's bright daylight illustration style.

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  ANTHROPIC_API_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  RECORD_ID, LANGUAGE  (ta | en)
"""

import os
import re
import json
import time
import base64
from io import BytesIO
from datetime import datetime, timedelta

import requests
from PIL import Image
from google import genai
from google.oauth2 import service_account
import google.auth.transport.requests
import anthropic

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
RECORD_ID      = os.environ["RECORD_ID"]
LANGUAGE       = os.environ.get("LANGUAGE", "en")

GCS_BUCKET   = "ihaveacause-media"
IMAGE_MODEL  = "gemini-3.1-flash-image"          # nano-banana, same as series pipeline
CLAUDE_MODEL = "claude-sonnet-4-6"
MIN_BEATS, MAX_BEATS = 6, 14
TARGET_W, TARGET_H   = 1920, 1080
GEN_SLEEP            = 8
SIGNED_URL_DAYS      = 30
LANG_NAME = {"ta": "Tamil", "en": "English"}.get(LANGUAGE, "English")
TABLE     = "tamil_anchor" if LANGUAGE == "ta" else "english_anchor"

# Bright daylight illustration look (carried over from the Sprint 13 daylight work),
# used for the studio background images so they read as a clean, premium set.
DAYLIGHT_STYLE = (
    "Soft hand-painted storybook illustration, watercolour and gouache texture, "
    "gentle linework. Bright natural daylight, soft diffused sunlight, high-key airy "
    "lighting. Warm muted earth-tone palette — sage green, dusty blue, warm cream, "
    "soft ochre — on a light off-white background. Serene, calm, contemplative mood. "
    "No dark shadows, no neon, no photographic look. No text, words, letters or numbers."
)

# ── Clients ───────────────────────────────────────────────────
_creds_info     = json.loads(GCP_CREDS_JSON)
VERTEX_PROJECT  = _creds_info.get("project_id") or "gen-lang-client-0078128013"
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")   # Gemini-3 image = global only
_vertex_creds   = service_account.Credentials.from_service_account_info(
    _creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
image_client    = genai.Client(vertexai=True, project=VERTEX_PROJECT,
                               location=VERTEX_LOCATION, credentials=_vertex_creds)
IMAGE_BACKEND   = f"Vertex AI · {VERTEX_PROJECT} · {VERTEX_LOCATION} (credit-covered)"
claude_client   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Supabase ──────────────────────────────────────────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json", "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get_one(rid):
    r = requests.get(f"{REST}/{TABLE}",
                     headers={**SB_HEADERS, "Prefer": "return=representation"},
                     params={"id": f"eq.{rid}", "select": "*"}, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch(rid, data):
    r = requests.patch(f"{REST}/{TABLE}?id=eq.{rid}", headers=SB_HEADERS, json=data, timeout=30)
    if r.status_code not in (200, 204):
        print(f"   ❌ patch {r.status_code}: {r.text[:200]}", flush=True)
    return r.status_code in (200, 204)

# ── GCS upload + V2 signed URL (mirrors generate_images.py) ───
def gcs_token():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON),
        scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token

def upload_bytes_to_gcs(data_bytes, gcs_path, content_type="image/jpeg", days=SIGNED_URL_DAYS):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    token      = gcs_token()
    creds_info = json.loads(GCP_CREDS_JSON)
    r = requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
        params={"uploadType": "media", "name": gcs_path},
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        data=data_bytes, timeout=120)
    if r.status_code not in (200, 201):
        print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}", flush=True)
        return None
    expiry_ts      = int((datetime.utcnow() + timedelta(days=days)).timestamp())
    string_to_sign = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    private_key    = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
    signature   = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")
    return (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
            f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={encoded_sig}")

# ── JSON parse + verbatim trigger (mirrors generate_images.py) ─
def parse_json(raw):
    raw = raw.strip().replace("```json", "").replace("```", "").strip()
    m = re.search(r"\[[\s\S]*\]", raw)
    candidate = m.group() if m else raw
    for attempt in (candidate, raw):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            pass
    repaired = re.sub(r",\s*([\]}])", r"\1", candidate)
    repaired = re.sub(r"[\x00-\x1f]+", " ", repaired)
    return json.loads(repaired)

def _norm(w):
    return re.sub(r"[^\w]", "", w.lower())

def trigger_start_time(trigger, word_timings):
    """Find the verbatim trigger phrase in the spoken word_timings → its start time."""
    if not trigger or not word_timings:
        return None
    spoken = [_norm(w["word"]) for w in word_timings]
    tw = [_norm(x) for x in trigger.split() if _norm(x)]
    if not tw:
        return None
    for i in range(len(spoken) - len(tw) + 1):
        if spoken[i:i + len(tw)] == tw:
            return word_timings[i]["start"]
    if len(tw) >= 3:                      # looser 3-word match
        for i in range(len(spoken) - 3 + 1):
            if spoken[i:i + 3] == tw[:3]:
                return word_timings[i]["start"]
    return None

# ── Claude: transcript → tagged beat sheet ────────────────────
def plan_beats(row):
    transcript = row["transcript"]
    title      = row.get("title") or ""
    print(f"\n🧠 Claude planning beats ({LANG_NAME})...", flush=True)
    prompt = f"""You are directing the on-screen graphics for ONE {LANG_NAME} opinion /
commentary video. The host is on camera the whole time. Below is the transcript
of what they said. Break it into a sequence of on-screen BEATS — one per distinct
point or moment — in the order they are spoken.

Number of beats is your call but MUST be between {MIN_BEATS} and {MAX_BEATS}. No filler.

For each beat return an object with EXACTLY these keys:
  "order"        : 1-based integer, in spoken order
  "trigger"      : the FIRST 5-8 words of this beat, COPIED VERBATIM from the
                   transcript (same words/spelling/order) so it can be located in
                   the audio. For order 1 use "".
  "mode"         : "image" or "text".
                   Use "text" for names, head-to-head comparisons, punchlines,
                   sharp one-liners (kinetic typography carries these best).
                   Use "image" for places, events, scenes, concepts that benefit
                   from a visual behind the host.
  "headline"     : ONE short, catchy {LANG_NAME} lower-third line for this beat
                   (a pull-quote / chyron, under ~8 words). Drawn from what was said.
  "bullets"      : 0-3 very short {LANG_NAME} summary points for this beat
                   (each under ~6 words). Use [] if none. Most useful on "text" beats.
  "scene"        : (image beats only; "" for text beats) a concrete description of
                   WHAT TO ILLUSTRATE — subject, setting, action. No style or colour words.

CONTEXT (do not illustrate the title literally): {title}

TRANSCRIPT:
{transcript}

Return ONLY a JSON array of beat objects. No prose, no markdown."""

    beats = None; last_err = None
    for attempt in range(3):
        extra = "" if attempt == 0 else (
            "\n\nIMPORTANT: your previous reply was NOT valid JSON. Return ONLY a valid "
            "JSON array. Escape every double-quote inside a string value as \\\", and put "
            "no raw line breaks inside any string value.")
        msg = claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": prompt + extra}])
        try:
            beats = parse_json(msg.content[0].text)
            if not isinstance(beats, list) or not beats:
                raise ValueError("no beats")
            break
        except Exception as e:
            last_err = e; beats = None
            print(f"   ⚠️  Beat JSON invalid (attempt {attempt+1}/3): {e} — retrying...", flush=True)
    if not beats:
        raise ValueError(f"Beat planning failed after 3 attempts: {last_err}")

    cleaned = []
    for i, b in enumerate(beats[:MAX_BEATS]):
        order = i + 1
        mode  = "text" if str(b.get("mode", "")).strip().lower() == "text" else "image"
        bl    = b.get("bullets", []) or []
        if not isinstance(bl, list):
            bl = [str(bl)]
        cleaned.append({
            "order":    order,
            "trigger":  "" if order == 1 else str(b.get("trigger", "")).strip(),
            "mode":     mode,
            "headline": str(b.get("headline", "")).strip(),
            "bullets":  [str(x).strip() for x in bl[:3] if str(x).strip()],
            "scene":    str(b.get("scene", "")).strip() if mode == "image" else "",
        })
    print(f"   ✅ {len(cleaned)} beats planned "
          f"({sum(1 for b in cleaned if b['mode']=='image')} image / "
          f"{sum(1 for b in cleaned if b['mode']=='text')} text)", flush=True)
    return cleaned

# ── Vertex image generation (mirrors generate_images.py) ──────
def extract_image_bytes(response):
    cand = getattr(response, "candidates", None)
    parts = cand[0].content.parts if cand else (getattr(response, "parts", []) or [])
    for p in parts:
        inline = getattr(p, "inline_data", None)
        if inline is not None and getattr(inline, "data", None):
            data = inline.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            return data
    return None

def generate_one_image(prompt_text, ref_image=None):
    contents = [prompt_text] if ref_image is None else [prompt_text, ref_image]
    delay = 12
    for attempt in range(7):
        try:
            resp = image_client.models.generate_content(model=IMAGE_MODEL, contents=contents)
            break
        except Exception as e:
            m = str(e)
            if ("429" in m or "RESOURCE_EXHAUSTED" in m) and attempt < 6:
                print(f"   ⏳ Vertex busy (429) — waiting {delay}s ({attempt+1}/6)...", flush=True)
                time.sleep(delay); delay = min(delay * 2, 90); continue
            print(f"   ⚠️  Vertex image FAILED ({m[:120]}) — re-run.", flush=True)
            raise
    img = extract_image_bytes(resp)
    if not img:
        raise RuntimeError("Model returned no image data")
    return img

def normalize_16x9(img_bytes):
    im = Image.open(BytesIO(img_bytes)).convert("RGB")
    sw, sh = im.size
    target = TARGET_W / TARGET_H; src = sw / sh
    if abs(src - target) < 0.01:
        im = im.resize((TARGET_W, TARGET_H), Image.LANCZOS)
    elif src > target:
        new_w = int(round(sh * target)); left = (sw - new_w) // 2
        im = im.crop((left, 0, left + new_w, sh)).resize((TARGET_W, TARGET_H), Image.LANCZOS)
    else:
        new_h = int(round(sw / target)); top = (sh - new_h) // 2
        im = im.crop((0, top, sw, top + new_h)).resize((TARGET_W, TARGET_H), Image.LANCZOS)
    out = BytesIO(); im.save(out, "JPEG", quality=92)
    return out.getvalue()

def build_image_prompt(scene, is_anchor):
    base = (f"An original ILLUSTRATION (not a photograph) for a news/commentary studio "
            f"background depicting: {scene}. "
            f"Composition: wide 16:9, full-bleed, fills the frame edge to edge, no borders. "
            f"{DAYLIGHT_STYLE}")
    if not is_anchor:
        base += (" Match the exact art style, technique, colour palette and overall look of "
                 "the provided reference image, but depict the new scene above. Use the "
                 "reference ONLY for style — ignore its specific subject matter and any text.")
    return base

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print(f"🎬 Anchor Beats — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
    print(f"   Segment: Claude ({CLAUDE_MODEL}) | Images: {IMAGE_MODEL} via {IMAGE_BACKEND}", flush=True)
    print("=" * 60, flush=True)

    row = db_get_one(RECORD_ID)
    if not row:
        print(f"❌ Record not found"); return
    if not (row.get("title") or "").strip():
        print("❌ No title chosen yet — pick a title in the dashboard first."); return
    if not row.get("transcript"):
        print("❌ No transcript — run transcribe first."); return
    word_timings = row.get("word_timings") or []
    if isinstance(word_timings, str):
        try: word_timings = json.loads(word_timings)
        except Exception: word_timings = []

    db_patch(RECORD_ID, {"status": "generating_beats"})

    # 1) Plan beats
    beats = plan_beats(row)

    # 2) Time each beat from the trigger against the spoken word_timings
    total = word_timings[-1]["end"] if word_timings else 0.0
    n = len(beats)
    for i, b in enumerate(beats):
        if i == 0:
            b["start"] = 0.0
        else:
            ts = trigger_start_time(b["trigger"], word_timings)
            if ts is None:
                ts = round((total / n) * i, 3) if total else 0.0
                print(f"   ℹ️  Beat {b['order']} trigger not found — equal-spacing → {ts:.1f}s", flush=True)
            b["start"] = ts
    for i in range(n - 1):
        beats[i]["end"] = beats[i + 1]["start"]
    beats[-1]["end"] = total if total else (beats[-1]["start"] + 5.0)
    # guard inversions
    for i in range(1, n):
        if beats[i]["start"] <= beats[i - 1]["start"]:
            beats[i]["start"] = beats[i - 1]["start"] + 1.0
        beats[i - 1]["end"] = beats[i]["start"]

    # 3) Render the image beats on Vertex (anchor-conditioned)
    img_beats = [b for b in beats if b["mode"] == "image"]
    print(f"\n🖼️  Rendering {len(img_beats)} image beats on Vertex...", flush=True)
    anchor_img = None
    for bi, b in enumerate(img_beats):
        is_anchor = anchor_img is None
        prompt = build_image_prompt(b["scene"] or b["headline"], is_anchor)
        try:
            raw = generate_one_image(prompt, ref_image=None if is_anchor else anchor_img)
            norm = normalize_16x9(raw)
            if is_anchor:
                anchor_img = Image.open(BytesIO(norm)).convert("RGB")
            url = upload_bytes_to_gcs(
                norm, f"anchor/{RECORD_ID}/{LANGUAGE}/beat_{b['order']:02d}.jpg")
            b["image_url"] = url or ""
            print(f"   ✅ Beat {b['order']} image {'(ANCHOR)' if is_anchor else ''} → "
                  f"{'ok' if url else 'upload failed'}", flush=True)
        except Exception as e:
            b["image_url"] = ""
            print(f"   ⚠️  Beat {b['order']} image failed: {e}", flush=True)
        if bi < len(img_beats) - 1:
            time.sleep(GEN_SLEEP)

    for b in beats:
        b.setdefault("image_url", "")

    db_patch(RECORD_ID, {"beats": beats, "status": "beats_ready"})
    print(f"\n{'='*60}", flush=True)
    print(f"✅ Beats ready — review/override in the dashboard, then Render.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
