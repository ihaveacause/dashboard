"""
x_image_pipeline.py — Sprint 8
================================
Generates one image per tweet in the X thread (6 images × Tamil + English).
- Gemini determines appropriate visual style per tweet content
- Imagen 3 generates 1200×675 (X optimal) images
- Logo composited bottom-right corner via Pillow
- Images saved to GCS
- Saves x_images_tamil / x_images_english JSONB to Supabase
- Sets status_x → x_images_ready

Triggered by: generate_x_images.yml
Env vars: SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY,
          GOOGLE_APPLICATION_CREDENTIALS_JSON,
          EPISODE_NUMBER (or IDEA_ID)
"""

import os
import json
import base64
import tempfile
import io
import requests
import time

# ── Auth ──────────────────────────────────────────────────────
CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
creds_path = "/tmp/gcp_creds.json"
with open(creds_path, "w") as f:
    f.write(CREDS_JSON)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials as SACreds
from google import genai
from google.genai import types as genai_types

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
EPISODE_NUMBER = os.environ.get("EPISODE_NUMBER")
IDEA_ID        = os.environ.get("IDEA_ID")

GCS_BUCKET = "ihaveacause-media"
LOGO_GCS   = "assets/ihaveacause_logo.png"   # Pre-uploaded logo PNG

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# ── Gemini client ──────────────────────────────────────────────
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ── Helpers ────────────────────────────────────────────────────
def sb_get(table, filters):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{filters}&limit=1", headers=SB_HEADERS)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No row in {table} with {filters}")
    return data[0]

def sb_patch(table, match_col, match_val, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{match_col}=eq.{match_val}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json=data,
    )
    r.raise_for_status()

def get_gcs_token():
    creds = SACreds.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/devstorage.read_write"]
    )
    creds.refresh(Request())
    return creds.token

def upload_bytes_to_gcs(data_bytes, gcs_path, content_type="image/png"):
    token = get_gcs_token()
    r = requests.put(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o"
        f"?uploadType=media&name={gcs_path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        data=data_bytes,
    )
    r.raise_for_status()
    return f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"

def download_logo(tmpdir):
    """Download logo from GCS and return as PIL Image."""
    from PIL import Image
    token = get_gcs_token()
    r = requests.get(
        f"https://storage.googleapis.com/{GCS_BUCKET}/{LOGO_GCS}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code == 200:
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    print(f"  Warning: Logo not found at {LOGO_GCS}, skipping logo composite")
    return None

def parse_thread(thread_text):
    """Split thread into individual tweets (separated by blank lines)."""
    tweets = [t.strip() for t in thread_text.split("\n\n") if t.strip()]
    return tweets[:6]  # Max 6 tweets

# ── Gemini: generate image prompt per tweet ────────────────────
PROMPT_SYSTEM = """You generate Imagen 3 image prompts for X (Twitter) post images.
Each image must:
- Be 1200×675px landscape format
- Have NO text, letters, numbers, or writing anywhere
- Match the emotional and conceptual content of the specific tweet
- Choose the right visual style for the content (photorealistic for social topics,
  painterly for philosophical, documentary for political, etc.)
- Be striking and shareable on X

Return ONLY a JSON array of objects with "prompt" and "style_rationale".
No markdown, no explanation.
"""

def generate_image_prompts(tweets, topic_context):
    tweet_list = "\n".join([f"Tweet {i+1}: {t[:200]}" for i, t in enumerate(tweets)])
    user_msg = f"""Topic context: {topic_context}

Tweets:
{tweet_list}

Generate one Imagen 3 image prompt for each tweet.
Return JSON array: [{{"tweet": 1, "prompt": "...", "style_rationale": "..."}}]"""

    response = gemini_client.models.generate_content(
        model="gemini-2.0-flash",
        config=genai_types.GenerateContentConfig(system_instruction=PROMPT_SYSTEM),
        contents=user_msg,
    )
    raw = response.text.strip().replace("```json", "").replace("```", "")
    return json.loads(raw)

# ── Imagen 3 ───────────────────────────────────────────────────
def generate_image(prompt, index):
    """Generate one 1200×675 image via Imagen 3."""
    full_prompt = (
        f"{prompt} "
        f"Landscape 16:9, professional quality, high resolution, cinematic composition. "
        f"Absolutely no text, no letters, no words, no numbers, no watermarks, "
        f"no writing anywhere in the image."
    )

    creds = SACreds.from_service_account_file(
        creds_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(Request())
    project = json.load(open(creds_path))["project_id"]

    payload = {
        "instances": [{"prompt": full_prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "16:9",
            "safetyFilterLevel": "block_some",
            "personGeneration": "allow_all",
        },
    }
    r = requests.post(
        f"https://us-central1-aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/us-central1/publishers/google/models/imagen-3.0-generate-001:predict",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    b64 = r.json()["predictions"][0]["bytesBase64Encoded"]
    return base64.b64decode(b64)

# ── Logo composite ─────────────────────────────────────────────
def composite_logo(image_bytes, logo_img):
    """Composite the logo at bottom-right of the image."""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    img = img.resize((1200, 675), Image.LANCZOS)

    if logo_img:
        # Scale logo to 80px height, preserve aspect
        lh = 64
        lw = int(logo_img.width * (lh / logo_img.height))
        logo_resized = logo_img.resize((lw, lh), Image.LANCZOS)

        # Paste at bottom-right with 16px margin
        margin = 16
        x = img.width - lw - margin
        y = img.height - lh - margin
        img.paste(logo_resized, (x, y), logo_resized)

    output = io.BytesIO()
    img.convert("RGB").save(output, format="JPEG", quality=90)
    return output.getvalue()

# ── Main ───────────────────────────────────────────────────────
def main():
    is_idea = bool(IDEA_ID)
    print(f"Sprint 8 | X Image Pipeline")
    print(f"  Source: {'idea ' + IDEA_ID if is_idea else 'episode ' + EPISODE_NUMBER}")

    # 1. Fetch row
    if is_idea:
        row = sb_get("ideas", f"id=eq.{IDEA_ID}")
        table = "ideas"
        match_col, match_val = "id", IDEA_ID
        topic_context = f"{row.get('title','')} — {row.get('description','')}"
    else:
        row = sb_get("tamil_episodes", f"episode_number=eq.{EPISODE_NUMBER}")
        table = "tamil_episodes"
        match_col, match_val = "episode_number", EPISODE_NUMBER
        topic_context = f"{row.get('title_tamil','')} | {row.get('title_english','')} — {row.get('bridge','')}"

    # 2. Process both languages
    results = {}
    logo_img = None

    try:
        from PIL import Image
        logo_img = download_logo("/tmp")
    except ImportError:
        print("  Warning: Pillow not installed, skipping logo composite")

    for lang in ["tamil", "english"]:
        lang_code = "ta" if lang == "tamil" else "en"
        thread_col = f"script_x_thread_{lang}"
        thread_text = row.get(thread_col, "")

        if not thread_text:
            print(f"  Skipping {lang} — no X thread script found in '{thread_col}'")
            continue

        tweets = parse_thread(thread_text)
        print(f"\n  {lang.title()} thread: {len(tweets)} tweets")

        # Generate image prompts for all tweets at once
        print("  Generating image prompts via Gemini...")
        prompt_data = generate_image_prompts(tweets, topic_context)

        lang_images = []
        for item in prompt_data:
            tweet_idx = item["tweet"] - 1
            prompt    = item["prompt"]
            style     = item.get("style_rationale", "")
            tweet_txt = tweets[tweet_idx] if tweet_idx < len(tweets) else ""

            print(f"  Generating image for tweet {tweet_idx+1}: {style[:60]}...")
            print(f"    Prompt: {prompt[:100]}...")

            for attempt in range(3):
                try:
                    img_bytes = generate_image(prompt, tweet_idx)
                    img_bytes = composite_logo(img_bytes, logo_img)

                    # Upload to GCS
                    if is_idea:
                        gcs_path = f"ideas/{IDEA_ID}/x_{lang}_tweet_{tweet_idx+1:02d}.jpg"
                    else:
                        gcs_path = f"episodes/{EPISODE_NUMBER}/x_{lang}_tweet_{tweet_idx+1:02d}.jpg"

                    url = upload_bytes_to_gcs(img_bytes, gcs_path, "image/jpeg")
                    lang_images.append({
                        "tweet_index": tweet_idx + 1,
                        "url": url,
                        "prompt": prompt[:120],
                        "style": style,
                    })
                    print(f"    ✓ Uploaded: {url}")
                    break
                except Exception as e:
                    print(f"    Attempt {attempt+1} failed: {e}")
                    if attempt < 2:
                        time.sleep(5)
                    else:
                        print(f"    ✗ Skipping tweet {tweet_idx+1} image after 3 failures")

        results[f"x_images_{lang}"] = lang_images

    # 3. Save to Supabase
    patch_data = {
        "status_x": "x_images_ready",
        **{k: v for k, v in results.items() if v},
    }
    sb_patch(table, match_col, match_val, patch_data)
    print(f"\n✅ Saved {sum(len(v) for v in results.values())} images → status_x = x_images_ready")

if __name__ == "__main__":
    main()
