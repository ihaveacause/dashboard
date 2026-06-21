"""
generate_thumbnail.py — New YouTube Pipeline (Studio thumbnail)
================================================================
Generates a catchy, episode-specific YouTube thumbnail:

  1. Claude reads the script and produces a short HOOK line + a one-line
     visual concept (both derived only from this episode's content).
  2. Gemini AI Studio "nano banana" generates the full thumbnail ILLUSTRATION
     with the title and hook rendered into it (no narrator photo).
  3. Pillow overlays ONE consistent element for series recognition — a small
     "EP NN" badge (top-left) — plus the channel logo (bottom-right).
  4. Normalized to 1280x720, stored in GCS as a 30-day signed URL,
     written to `thumbnail_url`.

This runs at the same stage as image generation. It does NOT touch the main
`status` column, so it can run alongside the image workflow without a race.

Hook editability: if `thumbnail_hook_text` is set on the row, that exact hook is
used instead of generating one (lets you tweak + regenerate from the dashboard).

Engines:
  - Hook/concept : Anthropic Claude            (ANTHROPIC_API_KEY)
  - Illustration : Gemini AI Studio nano banana (GEMINI_API_KEY) — NO Vertex
  - Storage      : Google Cloud Storage         (GOOGLE_APPLICATION_CREDENTIALS_JSON)

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  ANTHROPIC_API_KEY, GEMINI_API_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  IDEA_NUMBER, LANGUAGE   (ta | en)
"""

import os
import time
import io
import re
import json
import base64
from datetime import datetime, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.oauth2 import service_account
import google.auth.transport.requests
import anthropic

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")   # optional now — Vertex-only, AI Studio disabled
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
IDEA_NUMBER = int(os.environ["IDEA_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

GCS_BUCKET   = "ihaveacause-media"
IMAGE_MODEL  = "gemini-3.1-flash-image"        # nano banana 2 (AI Studio)
CLAUDE_MODEL = "claude-sonnet-4-6"
W, H         = 1280, 720                        # YouTube thumbnail (16:9)
LOGO_SIZE    = 90
SIGNED_URL_DAYS = 30
THUMB_USE_ANCHOR = False   # keep thumbnails free/catchy; flip True to match video look

# Localized thumbnails for YouTube multi-language audio. Generated on the ENGLISH
# master run only (that video is the one YouTube dubs into the other languages).
# OFF by default until YouTube enables localized-thumbnail upload for this channel.
# Re-enable later with the env var MULTI_LANG_THUMBS=1 (no code change needed).
MULTI_LANG_THUMBS = os.environ.get("MULTI_LANG_THUMBS", "0") != "0"  # OFF until YouTube opens localized-thumbnail upload; set MULTI_LANG_THUMBS=1 to re-enable
TARGET_LANGS = {
    "ta": "Tamil",
    "hi": "Hindi", "te": "Telugu", "ml": "Malayalam", "bn": "Bengali",
    "es": "Spanish", "pt": "Portuguese", "id": "Indonesian", "pa": "Punjabi", "fr": "French",
}

LANG_NAME = {"ta": "Tamil", "en": "English"}.get(LANGUAGE, "Tamil")

# ── Thumbnail illustration on Vertex AI (credit-covered) with AI Studio fallback ──
_img_creds_info = json.loads(GCP_CREDS_JSON)
VERTEX_PROJECT  = _img_creds_info.get("project_id") or "gen-lang-client-0078128013"
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "global")
try:
    _vertex_creds = service_account.Credentials.from_service_account_info(
        _img_creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    image_client  = genai.Client(vertexai=True, project=VERTEX_PROJECT,
                                 location=VERTEX_LOCATION, credentials=_vertex_creds)
    IMAGE_BACKEND = f"Vertex AI · {VERTEX_PROJECT} · {VERTEX_LOCATION} (credit-covered)"
except Exception as _ve:
    raise RuntimeError(f"Vertex AI init failed (Vertex-only mode, no AI Studio fallback): {_ve}")

# AI Studio fallback removed — Vertex-only, no google/AI-Studio image API anywhere.
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

def log(m): print(m, flush=True)

# ── Supabase ──────────────────────────────────────────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json", "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(table, params):
    r = requests.get(f"{REST}/{table}",
                     headers={**SB_HEADERS, "Prefer": "return=representation"},
                     params=params, timeout=15)
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data):
    r = requests.patch(f"{REST}/{table}?episode_number=eq.{val}",
                       headers=SB_HEADERS, json=data, timeout=30)
    return r.status_code in (200, 204)

# ── GCS ───────────────────────────────────────────────────────
def gcs_token_and_info():
    creds_info  = json.loads(GCP_CREDS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token, creds_info

def gcs_download_path(gcs_path):
    token, _ = gcs_token_and_info()
    encoded  = requests.utils.quote(gcs_path, safe="")
    url      = f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}?alt=media"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return r.content if r.status_code == 200 else None

def gcs_upload_and_sign(data_bytes, gcs_path, content_type="image/jpeg", days=SIGNED_URL_DAYS):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    token, creds_info = gcs_token_and_info()
    r = requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
        params={"uploadType": "media", "name": gcs_path},
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        data=data_bytes, timeout=120)
    if r.status_code not in (200, 201):
        log(f"   ❌ Upload failed {r.status_code}: {r.text[:200]}"); return None
    expiry_ts      = int((datetime.utcnow() + timedelta(days=days)).timestamp())
    string_to_sign = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    private_key    = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
    signature   = private_key.sign(string_to_sign.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    encoded_sig = requests.utils.quote(base64.b64encode(signature).decode("utf-8"), safe="")
    return (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
            f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={encoded_sig}")

def download_url(url):
    r = requests.get(url, timeout=60)
    return r.content if r.status_code == 200 else None

# ── Claude: hook + visual concept (only from the script) ──────
def make_hook_and_concept(script, title):
    log("\n🧠 Claude drafting hook + visual concept from script...")
    prompt = f"""From this {LANG_NAME} philosophy episode script, produce a YouTube thumbnail plan.

Return ONLY JSON: {{"hook": "...", "visual": "..."}}

"hook"   : a PUNCHY, scroll-stopping line in {LANG_NAME} (max ~7 words) that opens a
           curiosity gap — a bold claim, a provocative question, or a "you won't believe"
           twist that makes someone NEED to click. Avoid flat descriptions. Distinct from
           the title. Derived only from the script's actual payoff.
"visual" : one sentence (English) describing a single catchy image that sums up THIS
           episode — concrete subject and setting, drawn only from the script. No style.

TITLE: {title}

SCRIPT:
{script[:6000]}"""
    msg = claude_client.messages.create(
        model=CLAUDE_MODEL, max_tokens=600,
        messages=[{"role": "user", "content": prompt}])
    raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw); data = json.loads(m.group()) if m else {}
    return data.get("hook", "").strip(), data.get("visual", "").strip()

# ── Claude: translate title + hook into each target language ──
def translate_title_hook(title, hook):
    """Translate the English title + hook into each TARGET_LANGS language (one call).
    Returns {code: {"title": "...", "hook": "..."}}."""
    langs = ", ".join(f"{c} ({n})" for c, n in TARGET_LANGS.items())
    prompt = (
        f"Translate this YouTube TITLE and HOOK into these languages: {langs}.\n"
        f"Make each natural and punchy for a native speaker (not literal). "
        f"Return ONLY JSON mapping each language code to "
        f'{{"title":"...","hook":"..."}}.\n\nTITLE: {title}\nHOOK: {hook}'
    )
    msg = claude_client.messages.create(
        model=CLAUDE_MODEL, max_tokens=2000,
        messages=[{"role": "user", "content": prompt}])
    raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw); return json.loads(m.group()) if m else {}

# ── Gemini: thumbnail illustration with title + hook baked in ─
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

def generate_thumbnail_illustration(title, hook, visual, anchor_img=None, lang_name=None):
    has_ref = anchor_img is not None
    lname = lang_name or LANG_NAME
    # No reference → default to the channel's bold illustration look.
    # Reference provided → impose NO medium/style bias of our own; the reference
    # dictates the whole look (illustration, photographic, clean, moody — anything).
    lead = ("A bold, eye-catching YouTube thumbnail that"
            if has_ref else
            "A bold, eye-catching YouTube thumbnail ILLUSTRATION (not a photograph) that")
    tail = ("Wide 16:9 composition, full-bleed."
            if has_ref else
            "Wide 16:9 composition, full-bleed, vibrant and attention-grabbing.")
    prompt = (
        f"{lead} visually summarizes this episode: {visual}. "
        f"Prominently render the title text «{title}» as the main headline, and below it "
        f"the smaller hook line «{hook}» as a subtitle — both in {lname}, spelled "
        f"exactly as written, large, bold and clearly legible even at small sizes. "
        f"Leave the BOTTOM-LEFT and BOTTOM-RIGHT corners relatively uncluttered. "
        f"{tail}"
    )
    if has_ref:
        prompt += (
            " Match the exact art style, rendering technique, colour palette and overall "
            "visual look of the provided reference image. Use the reference ONLY for visual "
            "style — ignore any text, words or specific subject matter inside it."
        )
    contents = [prompt] if (anchor_img is None) else [prompt, anchor_img]
    resp = None
    _delay = 12
    for _attempt in range(7):
        try:
            resp = image_client.models.generate_content(model=IMAGE_MODEL, contents=contents)
            break
        except Exception as _e:
            _m = str(_e)
            if ("429" in _m or "RESOURCE_EXHAUSTED" in _m) and _attempt < 6:
                log(f"   ⏳ Vertex busy (429) — waiting {_delay}s then retrying ({_attempt+1}/6)...")
                time.sleep(_delay); _delay = min(_delay * 2, 90)
                continue
            # Vertex-only: do NOT fall back to AI Studio (avoids Gemini-API billing)
            log(f"   ⚠️  Vertex image failed ({_m[:120]}) — skipping (Vertex-only, no fallback)")
            raise
    img = extract_image_bytes(resp)
    if not img:
        raise RuntimeError("Thumbnail model returned no image data")
    return img

# ── Normalize to exactly 1280x720 (cover-crop) ────────────────
def normalize(img_bytes):
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    sw, sh = im.size; target = W / H; src = sw / sh
    if abs(src - target) < 0.01:
        im = im.resize((W, H), Image.LANCZOS)
    elif src > target:
        nw = int(round(sh * target)); left = (sw - nw) // 2
        im = im.crop((left, 0, left + nw, sh)).resize((W, H), Image.LANCZOS)
    else:
        nh = int(round(sw / target)); top = (sh - nh) // 2
        im = im.crop((0, top, sw, top + nh)).resize((W, H), Image.LANCZOS)
    return im.convert("RGBA")

# ── Consistent EP badge (series recognition) ──────────────────
def load_font(size):
    for path in (
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def draw_ep_badge(canvas, episode_number):
    """Small consistent badge, top-left: 'EP NN'. Same style every episode."""
    draw = ImageDraw.Draw(canvas)
    label = f"EP {episode_number:02d}"
    font  = load_font(38)
    pad_x, pad_y = 22, 12
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    x, y = 28, H - bh - 28        # bottom-left: clear of the title (top) and logo (bottom-right)
    # drop shadow + dark rounded plate + gold text
    draw.rounded_rectangle([x+3, y+3, x+bw+3, y+bh+3], radius=14, fill=(0, 0, 0, 110))
    draw.rounded_rectangle([x, y, x+bw, y+bh], radius=14,
                           fill=(15, 18, 24, 235), outline=(245, 200, 70, 255), width=2)
    draw.text((x + pad_x, y + pad_y - bbox[1]), label, font=font, fill=(245, 200, 70, 255))
    return canvas

# ── Main ──────────────────────────────────────────────────────
def main():
    log("=" * 60)
    log(f"🖼️  Studio Thumbnail — Episode {IDEA_NUMBER} | {LANGUAGE.upper()}")
    log(f"   Illustration: {IMAGE_MODEL} via {IMAGE_BACKEND} | Hook: Claude")
    log("=" * 60)

    table   = "tamil_ideas" if LANGUAGE == "ta" else "english_ideas"
    meta    = db_get(table, {"episode_number": f"eq.{IDEA_NUMBER}", "select": "*"})
    meta    = meta[0] if meta else None
    if not meta:
        log(f"❌ Episode {IDEA_NUMBER} not found"); return

    if LANGUAGE == "en":
        row = db_get("english_ideas", {"episode_number": f"eq.{IDEA_NUMBER}", "select": "*"})
        row = row[0] if row else {}
        script = row.get("script_english", "") or ""
        title  = row.get("title_english") or meta.get("title_english") or ""
    else:
        row = meta
        script = meta.get("script_tamil", "") or ""
        title  = meta.get("title_tamil") or meta.get("title_english") or ""

    if not script.strip():
        log(f"❌ No {LANG_NAME} script — generate the script first"); return

    log(f"   ✅ {title}")

    # hook: use override if provided, else derive from script
    override_hook = (row.get("thumbnail_hook_text") or "").strip()
    _, visual = make_hook_and_concept(script, title)
    if override_hook:
        hook = override_hook
        log(f"   ℹ️  Using your hook override: {hook}")
    else:
        hook, visual2 = make_hook_and_concept(script, title)
        visual = visual or visual2
    log(f"   🎣 Hook: {hook}")
    log(f"   🎨 Visual: {visual[:80]}")

    # optional: condition on the episode's first image for stylistic match
    anchor_img = None
    if THUMB_USE_ANCHOR:
        imgs = row.get("episode_images") or []
        if isinstance(imgs, str):
            try: imgs = json.loads(imgs)
            except: imgs = []
        first = next((i for i in sorted(imgs, key=lambda x: x.get("order", 0))), None)
        if first and first.get("url"):
            data = download_url(first["url"])
            if data:
                anchor_img = Image.open(io.BytesIO(data)).convert("RGB")

    # your uploaded style reference (from the dashboard) takes priority
    thumb_ref_url = (row.get("thumbnail_ref_url") or "").strip()
    if thumb_ref_url:
        log("   🖼  Using your uploaded reference image for the thumbnail style")
        _ref = download_url(thumb_ref_url)
        if _ref:
            anchor_img = Image.open(io.BytesIO(_ref)).convert("RGB")
        else:
            log("   ⚠️  Could not download the thumbnail reference image")
        db_patch(table, IDEA_NUMBER, {"thumbnail_ref_url": None})

    try:
        log("\n🖼  Generating thumbnail illustration...")
        raw    = generate_thumbnail_illustration(title, hook, visual, anchor_img)
        canvas = normalize(raw)

        # No EP badge for ideas — keep the thumbnail clean (title + hook + logo only)
        logo_data = gcs_download_path("ihaveacause_logo.png")
        if logo_data:
            logo = Image.open(io.BytesIO(logo_data)).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
            canvas.paste(logo, (W - LOGO_SIZE - 20, H - LOGO_SIZE - 20), logo)
            log("   ✅ Logo added")

        out = io.BytesIO()
        canvas.convert("RGB").save(out, "JPEG", quality=92)
        out = out.getvalue()
        log(f"   ✅ Thumbnail composed ({len(out)//1024}KB)")

        lang_code = "ta" if LANGUAGE == "ta" else "en"
        gcs_path  = f"ideas/{IDEA_NUMBER:03d}/{lang_code}/thumbnail.jpg"
        signed_url = gcs_upload_and_sign(out, gcs_path)
        if not signed_url:
            log("❌ Upload failed"); return

        # write thumbnail_url ONLY — do not touch the main status (image flow owns it)
        db_patch(table, IDEA_NUMBER, {"thumbnail_url": signed_url})

        # ── Localized thumbnails (English master only) ──────────────────
        # The English idea is the video YouTube dubs into the other languages,
        # so we generate one localized thumbnail per target language here and
        # store the set in `localized_thumbnails` for manual attach in Studio.
        if MULTI_LANG_THUMBS and LANGUAGE == "en":
            log("\n🌐 Generating localized thumbnails (one per language)...")
            try:
                trans = translate_title_hook(title, hook)
            except Exception as te:
                log(f"   ⚠️  Translation failed: {te}"); trans = {}
            loc_thumbs, loc_titles = {}, {"en": title}
            for code, name in TARGET_LANGS.items():
                t = trans.get(code) or {}
                l_title = (t.get("title") or title).strip()
                l_hook  = (t.get("hook")  or hook ).strip()
                loc_titles[code] = l_title
                try:
                    lraw    = generate_thumbnail_illustration(l_title, l_hook, visual, anchor_img, lang_name=name)
                    lcanvas = normalize(lraw)   # no EP badge for ideas
                    if logo_data:
                        llogo = Image.open(io.BytesIO(logo_data)).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
                        lcanvas.paste(llogo, (W - LOGO_SIZE - 20, H - LOGO_SIZE - 20), llogo)
                    lbuf = io.BytesIO(); lcanvas.convert("RGB").save(lbuf, "JPEG", quality=92)
                    lurl = gcs_upload_and_sign(lbuf.getvalue(), f"ideas/{IDEA_NUMBER:03d}/{code}/thumbnail.jpg")
                    if lurl:
                        loc_thumbs[code] = lurl
                        log(f"   ✅ {name}")
                except Exception as le:
                    log(f"   ⚠️  {name} thumbnail failed: {le}")
            db_patch(table, IDEA_NUMBER, {"localized_thumbnails": loc_thumbs, "localized_titles": loc_titles})
            log(f"   ✅ {len(loc_thumbs)} localized thumbnails stored (attach them in Studio)")

        log(f"\n{'='*60}")
        log(f"✅ Episode {IDEA_NUMBER} {LANGUAGE.upper()} — thumbnail ready")
        log(f"{'='*60}")

    except Exception as e:
        import traceback
        log(f"\n❌ Error: {e}"); traceback.print_exc()

if __name__ == "__main__":
    main()
