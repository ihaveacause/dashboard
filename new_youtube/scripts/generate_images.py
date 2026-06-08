"""
generate_images.py — New YouTube Pipeline (Image Studio engine)
================================================================
Script-driven, per-episode-consistent illustration generator.

PIPELINE (nothing about content is hardcoded):
  1. Claude reads the approved script and splits it into 8-12 beats
     (model-decided in that range, no filler). Per beat it records:
       - trigger      : the VERBATIM opening words of that beat (for CTC)
       - display_text : the line(s) to render INTO the illustration
       - scene        : what to illustrate, drawn ONLY from that beat
  2. Gemini AI Studio "nano banana" (gemini-3.1-flash-image) generates images:
       - Image 1 is the episode ANCHOR (born purely from beat 1).
       - Images 2..N are conditioned on the anchor as a reference image, so the
         whole episode shares ONE look while each scene differs.
       - Text is baked INTO every image (image text + CTC karaoke + voice).
  3. Every image is hard-normalized to 1920x1080 (16:9) — no YouTube black bars.
  4. Images are stored in GCS:  ihaveacause-media/episodes/{NNN}/{lang}/
  5. Writes the `episode_images` column the CTC video engine + dashboard read:
       {order, url, trigger, filename, display_text, scene}

The ONLY fixed constants are the format rules the channel chose:
  illustrative (never photo) · 16:9 · text-baked-in · 8-12 beats.

Engines:
  - Segmentation : Anthropic Claude             (ANTHROPIC_API_KEY)
  - Images       : Gemini AI Studio nano banana (GEMINI_API_KEY) — NO Vertex
  - Storage      : Google Cloud Storage         (GOOGLE_APPLICATION_CREDENTIALS_JSON)
  - Metadata     : Supabase                     (SUPABASE_URL / SUPABASE_KEY)

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  ANTHROPIC_API_KEY
  GEMINI_API_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER, LANGUAGE   (ta | en)
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
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

GCS_BUCKET = "ihaveacause-media"

# Nano banana 2 via AI Studio. Swap to "gemini-3-pro-image-preview" (Nano Banana
# Pro) for the strongest multilingual text rendering at higher cost.
IMAGE_MODEL  = "gemini-3.1-flash-image"
# Claude reads the script and plans the beats. Adjust to whatever model string
# your Anthropic account serves if this one isn't available.
CLAUDE_MODEL = "claude-sonnet-4-6"

MIN_BEATS, MAX_BEATS = 8, 12
TARGET_W, TARGET_H   = 1920, 1080     # 16:9, enforced by normalize
GEN_SLEEP            = 8              # seconds between image calls (rate limit)
SIGNED_URL_DAYS      = 30             # GCS signed-URL validity

LANG_NAME = {"ta": "Tamil", "en": "English"}.get(LANGUAGE, "Tamil")

# ── Clients ───────────────────────────────────────────────────
# ── Image model on Vertex AI (credit-covered) with AI Studio fallback ─────────
# Nano Banana 2 (gemini-3.1-flash-image) runs on Vertex AI's GLOBAL endpoint
# (us-central1 returns "model not found" for Gemini-3 image models). Routing here
# instead of the AI Studio API key lets image spend draw on your Google Cloud
# credits, exactly like your script generation already does.
_img_creds_info = json.loads(GCP_CREDS_JSON)
VERTEX_PROJECT  = _img_creds_info.get("project_id") or "gen-lang-client-0078128013"
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")   # Gemini-3 image = global only
try:
    _vertex_creds = service_account.Credentials.from_service_account_info(
        _img_creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    image_client  = genai.Client(vertexai=True, project=VERTEX_PROJECT,
                                 location=VERTEX_LOCATION, credentials=_vertex_creds)
    IMAGE_BACKEND = f"Vertex AI · {VERTEX_PROJECT} · {VERTEX_LOCATION} (credit-covered)"
except Exception as _ve:
    image_client  = genai.Client(api_key=GEMINI_API_KEY)
    IMAGE_BACKEND = f"AI Studio (Vertex init failed: {_ve})"

# Lazy AI Studio client, used only if a Vertex call fails mid-run (keeps images flowing)
_ai_studio_client = None
def _ai_studio_fallback():
    global _ai_studio_client
    if _ai_studio_client is None:
        _ai_studio_client = genai.Client(api_key=GEMINI_API_KEY)
    return _ai_studio_client
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Supabase ──────────────────────────────────────────────────
SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15,
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{val}",
        headers=SB_HEADERS, json=data, timeout=30,
    )
    return r.status_code in (200, 204)

# ── GCS (matches your x_image / shorts pipelines) ─────────────
def gcs_token():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON),
        scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token

def upload_bytes_to_gcs(data_bytes, gcs_path, content_type="image/jpeg", days=SIGNED_URL_DAYS):
    """Upload bytes to GCS and return a signed URL valid for `days` days.
    Mirrors generate_thumbnail.py's gcs_upload_and_sign (V2 signing)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    token      = gcs_token()
    creds_info = json.loads(GCP_CREDS_JSON)
    r = requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
        params={"uploadType": "media", "name": gcs_path},
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        data=data_bytes, timeout=120,
    )
    if r.status_code not in (200, 201):
        print(f"  ❌ GCS upload failed {r.status_code}: {r.text[:200]}")
        return None
    expiry_ts      = int((datetime.utcnow() + timedelta(days=days)).timestamp())
    string_to_sign = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    private_key    = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
    signature   = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")
    return (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
            f"?GoogleAccessId={creds_info['client_email']}"
            f"&Expires={expiry_ts}&Signature={encoded_sig}")

def download_image(url):
    r = requests.get(url, timeout=60)
    if r.status_code == 200:
        return Image.open(BytesIO(r.content)).convert("RGB")
    return None

# ── Claude segmentation: script → 8-12 beats (content only from script) ──
def parse_json(raw):
    raw = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            return json.loads(m.group())
        raise ValueError("No valid JSON array in Claude response")

def segment_script(script, episode):
    print(f"\n📝 Claude segmenting script into {MIN_BEATS}-{MAX_BEATS} beats ({LANG_NAME})...")
    prompt = f"""You are planning the on-screen images for ONE episode of a {LANG_NAME} philosophy video.

Split the narration script below into a sequence of visual beats — one image per
distinct idea or moment.

ABSOLUTE RULES:
- The number of beats is YOUR decision based on the content, but MUST be between
  {MIN_BEATS} and {MAX_BEATS}. No filler. No two consecutive beats that would look
  near-identical.
- Each beat's content comes ONLY from that part of the script. Invent nothing.
  Do not impose any fixed style, motif, palette, or recurring symbol.
- Beats must be in the order they appear in the script.

For each beat return an object with EXACTLY these keys:
  "order"        : 1-based position (integer)
  "trigger"      : the FIRST 5-8 words of this beat, COPIED VERBATIM and EXACTLY
                   from the script (same words, spelling, order). It must be a
                   literal substring of the script so it can be located in the
                   audio. For order 1, use "".
  "display_text" : the key {LANG_NAME} line(s) from this beat to render as text in
                   the image (1-3 short lines, taken from the script).
  "scene"        : a vivid, concrete description of WHAT TO ILLUSTRATE for this
                   beat (subject, setting, action), drawn only from this beat.
                   Do NOT mention art style or colours.

EPISODE (context only — do not illustrate the title): {episode.get('title_english','')}

SCRIPT:
{script}

Return ONLY a JSON array of beat objects. No prose, no markdown."""

    msg = claude_client.messages.create(
        model=CLAUDE_MODEL, max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    beats = parse_json(msg.content[0].text)
    if not isinstance(beats, list) or not beats:
        raise ValueError("Segmentation returned no beats")

    beats = beats[:MAX_BEATS]
    cleaned = []
    for i, b in enumerate(beats):
        order = i + 1
        trig  = "" if order == 1 else verify_trigger_in_script(str(b.get("trigger", "")).strip(), script)
        cleaned.append({
            "order":        order,
            "trigger":      trig,
            "display_text": str(b.get("display_text", "")).strip(),
            "scene":        str(b.get("scene", "")).strip(),
        })
    print(f"   ✅ {len(cleaned)} beats planned")
    for b in cleaned:
        print(f"      Beat {b['order']:>2}: trig='{b['trigger'][:30]}' | {b['scene'][:55]}")
    return cleaned

# ── Trigger verification (must be verbatim in the script) ─────
def _norm(w):
    return re.sub(r"[^\w]", "", w.lower())

def verify_trigger_in_script(trigger, script_text):
    if not trigger:
        return ""
    sw = [_norm(w) for w in script_text.split() if _norm(w)]
    tw = [_norm(w) for w in trigger.split() if _norm(w)]
    if not tw:
        return ""
    for i in range(len(sw) - len(tw) + 1):
        if sw[i:i + len(tw)] == tw:
            return trigger
    if len(tw) >= 4:
        for i in range(len(sw) - 4 + 1):
            if sw[i:i + 4] == tw[:4]:
                return trigger
    print(f"   ⚠️  Trigger not verbatim (will equal-space): '{trigger[:45]}'")
    return ""

# ── Image prompt (only fixed rules: illustrative + 16:9 + text) ──
def build_image_prompt(beat, is_anchor):
    parts = [
        f"An original ILLUSTRATION (not a photograph, no photographic realism) depicting: {beat['scene']}.",
        "Composition: wide 16:9 horizontal, full-bleed, fills the entire frame edge to edge, no borders, no letterboxing.",
    ]
    if beat["display_text"]:
        parts.append(
            f"Render this {LANG_NAME} text clearly and legibly within the artwork, "
            f"spelled exactly as written, and no other text: «{beat['display_text']}»."
        )
    else:
        parts.append("Do not add any text.")
    if not is_anchor:
        parts.append(
            "Match the exact art style, rendering technique, colour palette and overall "
            "visual look of the provided reference image, but depict the new scene above."
        )
    return " ".join(parts)

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
    resp = None
    _delay = 12
    for _attempt in range(5):
        try:
            resp = image_client.models.generate_content(model=IMAGE_MODEL, contents=contents)
            break
        except Exception as _e:
            _m = str(_e)
            if ("429" in _m or "RESOURCE_EXHAUSTED" in _m) and _attempt < 4:
                print(f"   ⏳ Vertex busy (429) — waiting {_delay}s then retrying ({_attempt+1}/4)...")
                time.sleep(_delay); _delay = min(_delay * 2, 90)
                continue
            print(f"   ⚠️  Vertex image failed ({_m[:120]}); trying AI Studio once...")
            resp = _ai_studio_fallback().models.generate_content(model=IMAGE_MODEL, contents=contents)
            break
    img = extract_image_bytes(resp)
    if not img:
        raise RuntimeError("Model returned no image data")
    return img

# ── 16:9 guarantee: cover-crop/resize to exactly 1920x1080 ────
def normalize_16x9(img_bytes):
    im = Image.open(BytesIO(img_bytes)).convert("RGB")
    sw, sh = im.size
    target = TARGET_W / TARGET_H
    src    = sw / sh
    if abs(src - target) < 0.01:
        im = im.resize((TARGET_W, TARGET_H), Image.LANCZOS)
    elif src > target:                       # too wide → crop sides
        new_w = int(round(sh * target)); left = (sw - new_w) // 2
        im = im.crop((left, 0, left + new_w, sh)).resize((TARGET_W, TARGET_H), Image.LANCZOS)
    else:                                    # too tall → crop top/bottom
        new_h = int(round(sw / target)); top = (sh - new_h) // 2
        im = im.crop((0, top, sw, top + new_h)).resize((TARGET_W, TARGET_H), Image.LANCZOS)
    out = BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()

def slug(text, n=40):
    # ASCII-only: non-Latin (e.g. Tamil) chars in a GCS object name break signed
    # URLs (the browser percent-encodes the path, so the signature won't match).
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip())
    return s[:n].strip("_") or "scene"

def gcs_path_for(order, trigger):
    return f"episodes/{EPISODE_NUMBER:03d}/{LANGUAGE}/img_{order:02d}_{slug(trigger)}.jpg"

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎨 Image Studio — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    print(f"   Segment: Claude ({CLAUDE_MODEL})  |  Images: {IMAGE_MODEL} via {IMAGE_BACKEND}")
    print(f"   Storage: gs://{GCS_BUCKET}/episodes/{EPISODE_NUMBER:03d}/{LANGUAGE}/")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    table = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"

    meta = db_get("tamil_episodes", {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    meta = meta[0] if meta else {}
    if not meta:
        print(f"❌ Episode {EPISODE_NUMBER} not found"); return

    if LANGUAGE == "en":
        row = db_get("english_episodes", {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
        row = row[0] if row else {}
        script = row.get("script_english", "") or ""
    else:
        row = meta
        script = meta.get("script_tamil", "") or ""

    if not script.strip():
        print(f"❌ No {LANG_NAME} script — generate the script first"); return

    print(f"\n📖 {meta.get('title_english','')}  ({len(script.split())} words)")

    existing = row.get("episode_images") or []
    if isinstance(existing, str):
        try: existing = json.loads(existing)
        except: existing = []

    # optional single-image regenerate via regenerate_note
    regen_note  = (row.get("regenerate_note") or "").strip()
    regen_order = regen_dir = None
    if regen_note:
        m = re.search(r"(?:scene|image)\s+(\d+)[:\-]?\s*(.*)", regen_note, re.IGNORECASE)
        if m:
            regen_order = int(m.group(1)); regen_dir = m.group(2).strip()
            print(f"\n🔄 Regenerate image {regen_order}: {regen_dir}")
        db_patch(table, EPISODE_NUMBER, {"regenerate_note": None})

    db_patch(table, EPISODE_NUMBER, {"status": "generating_images"})

    try:
        # ── REGENERATE ──────────────────────────────────────────
        # Regen image 1 (the anchor) => re-roll EVERY image in the new look.
        # Regen any other image => just that one, matched to the current anchor.
        if regen_order and existing:
            existing = sorted(existing, key=lambda x: x.get("order", 0))

            def augmented_scene(entry, direction):
                base = entry.get("scene", "")
                return (base + (". Additional art direction: " + direction if direction else "")).strip(". ")

            # ── Anchor cascade: regen image 1 → restyle the whole episode ──
            if regen_order == 1:
                a = next((e for e in existing if e.get("order") == 1), existing[0])
                anchor_beat = {"order": 1, "scene": augmented_scene(a, regen_dir),
                               "display_text": a.get("display_text", ""), "trigger": ""}
                print("\n🎨 Re-anchoring image 1 — the whole episode will be redone in this look")
                raw = generate_one_image(build_image_prompt(anchor_beat, is_anchor=True), None)
                img = normalize_16x9(raw)
                new_anchor = Image.open(BytesIO(img)).convert("RGB")
                path = gcs_path_for(1, a.get("trigger", ""))
                url  = upload_bytes_to_gcs(img, path)
                if url:
                    a.update({"url": url, "filename": path.split("/")[-1]})
                    print("   ✅ New anchor set")
                # re-roll every other image on the new anchor (content unchanged)
                for e in existing:
                    if e.get("order") == 1:
                        continue
                    beat = {"order": e.get("order"), "scene": e.get("scene", ""),
                            "display_text": e.get("display_text", ""), "trigger": e.get("trigger", "")}
                    print(f"   ↻ Image {beat['order']} → matching new anchor")
                    raw = generate_one_image(build_image_prompt(beat, is_anchor=False), new_anchor)
                    p2  = gcs_path_for(beat["order"], beat["trigger"])
                    u2  = upload_bytes_to_gcs(normalize_16x9(raw), p2)
                    if u2:
                        e.update({"url": u2, "filename": p2.split("/")[-1]})
                    time.sleep(GEN_SLEEP)
                db_patch(table, EPISODE_NUMBER, {"episode_images": existing, "status": "images_ready"})
                print(f"\n✅ Whole episode re-styled from the new anchor")
                return

            # ── Single non-anchor image ──
            anchor_entry = next((e for e in existing if e.get("order") == 1), existing[0])
            anchor_img = download_image(anchor_entry["url"])
            target = next((e for e in existing if e.get("order") == regen_order), None)
            if not target:
                print(f"❌ Image {regen_order} not in existing set")
                db_patch(table, EPISODE_NUMBER, {"status": "images_ready"}); return
            beat = {
                "order":        regen_order,
                "scene":        augmented_scene(target, regen_dir),
                "display_text": target.get("display_text", ""),
                "trigger":      target.get("trigger", ""),
            }
            prompt = build_image_prompt(beat, is_anchor=False)
            raw = generate_one_image(prompt, anchor_img)
            path = gcs_path_for(regen_order, beat["trigger"])
            url = upload_bytes_to_gcs(normalize_16x9(raw), path)
            if url:
                target.update({"url": url, "filename": path.split("/")[-1]})
                db_patch(table, EPISODE_NUMBER, {"episode_images": existing, "status": "images_ready"})
                print(f"\n✅ Image {regen_order} regenerated")
            else:
                db_patch(table, EPISODE_NUMBER, {"status": "images_ready"})
            return

        # ── FULL GENERATION ────────────────────────────────────
        beats = segment_script(script, meta)
        print(f"\n🖼  Generating {len(beats)} images (anchor + reference-conditioned)...")
        episode_images = []
        anchor_img = None

        for beat in beats:
            is_anchor = (beat["order"] == 1)
            prompt = build_image_prompt(beat, is_anchor)
            print(f"\n   Image {beat['order']}/{len(beats)} {'(ANCHOR)' if is_anchor else ''}")
            print(f"   → {beat['scene'][:90]}")

            raw = generate_one_image(prompt, anchor_img)
            img = normalize_16x9(raw)
            if is_anchor:
                anchor_img = Image.open(BytesIO(img)).convert("RGB")  # defines the look

            path = gcs_path_for(beat["order"], beat["trigger"])
            url  = upload_bytes_to_gcs(img, path)
            if not url:
                print(f"   ❌ Image {beat['order']} upload failed — skipping"); continue

            episode_images.append({
                "order":        beat["order"],
                "url":          url,
                "trigger":      beat["trigger"],
                "filename":     path.split("/")[-1],
                "display_text": beat["display_text"],
                "scene":        beat["scene"][:200],
            })
            print(f"   ✅ {url}")
            time.sleep(GEN_SLEEP)

        if not episode_images:
            print("❌ No images generated")
            db_patch(table, EPISODE_NUMBER, {"status": "script_approved"}); return

        episode_images.sort(key=lambda x: x["order"])
        db_patch(table, EPISODE_NUMBER, {
            "episode_images": episode_images,
            "status":         "images_ready",
        })
        print(f"\n{'='*60}")
        print(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — {len(episode_images)} images ready")
        print(f"{'='*60}")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}"); traceback.print_exc()
        db_patch(table, EPISODE_NUMBER, {"status": "script_approved"})

if __name__ == "__main__":
    main()
