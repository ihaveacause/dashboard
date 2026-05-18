"""
I Have a Cause — Video Pipeline
================================
- Cloud Text-to-Speech API → Chirp3 HD voices
    Tamil  : ta-IN-Chirp3-HD-Callirrhoe (female, 0.85x)
    English: en-US-Chirp3-HD-Charon     (male,   0.85x)
- PIL composites text directly onto image frames (no FFmpeg overlay)
- Three fonts pre-loaded: Tamil (NotoSansTamil), Latin, Devanagari
- Word-level rendering — Tamil rendered as whole words not char-by-char
- 20 words per chunk — tight sync, no dropped words
- Line wrap: 60 chars for English, 40 for Tamil
- clean_script() strips markdown AND parenthetical stage directions
- Background music mixed at 8% volume, full duration, 3s fade in/out
- SVG infographic as second-to-last scene
- Saves video_url correctly to both tamil_episodes and english_episodes
"""

import os
import re
import json
import base64
import requests
import subprocess
import tempfile
import shutil
import unicodedata
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from google.oauth2 import service_account
import google.auth.transport.requests

# ── Config ──────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]

W, H, FPS  = 1280, 720, 24
FONT_SIZE  = 36
SPEAK_RATE = 0.85

# TTS
TAMIL_VOICE   = "ta-IN-Chirp3-HD-Callirrhoe"
ENGLISH_VOICE = "en-US-Chirp3-HD-Charon"
TTS_ENDPOINT  = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"
PROJECT_ID    = "gen-lang-client-0078128013"

# Background music
MUSIC_URL    = "https://alfuvzlmatfkgdrkeqgk.supabase.co/storage/v1/object/public/episode-music/background.mp3"
MUSIC_VOLUME = 0.08
MUSIC_FADE   = 3

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST     = f"{SUPABASE_URL}/rest/v1"
WORK_DIR = Path(tempfile.mkdtemp(prefix="ihac_video_"))

# ── GCP auth ────────────────────────────────────────────────
creds_info  = json.loads(GCP_CREDS_JSON)
credentials = service_account.Credentials.from_service_account_info(
    creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

def get_token():
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token

# ── Supabase helpers ────────────────────────────────────────
def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, n, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{n}",
        headers=SB_HEADERS, json=data, timeout=15
    )
    ok = r.status_code in (200, 204)
    if not ok:
        print(f"   ⚠️  db_patch failed on {table}: {r.status_code} {r.text[:200]}")
    return ok

def upload_video(filename, data_bytes):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/episode-videos/ep{EPISODE_NUMBER:03d}/{filename}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "video/mp4",
            "x-upsert":      "true"
        },
        data=data_bytes, timeout=600
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/episode-videos/ep{EPISODE_NUMBER:03d}/{filename}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

# ── Download background music ───────────────────────────────
def download_music():
    try:
        print(f"\n🎵 Downloading background music...")
        r = requests.get(MUSIC_URL, timeout=60)
        if r.status_code == 200:
            music_path = WORK_DIR / "background.mp3"
            music_path.write_bytes(r.content)
            print(f"   ✅ Music downloaded ({len(r.content)//1024} KB)")
            return str(music_path)
        print(f"   ❌ Music download failed: {r.status_code}")
        return None
    except Exception as e:
        print(f"   ❌ Music download error: {e}")
        return None

# ── Font loading ────────────────────────────────────────────
def load_fonts(size):
    """
    Load fonts. NotoSansTamil-Regular is confirmed present on runner.
    Returns dict of font objects.
    """
    tamil_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",   # confirmed present
        "/usr/share/fonts/truetype/noto/NotoSerifTamil-Regular.ttf",
    ]
    latin_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    devanagari_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    ]
    universal_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def try_load(candidates):
        for path in candidates:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size), path
                except Exception:
                    continue
        return None, None

    results = {}
    for name, candidates in [
        ("tamil",      tamil_candidates),
        ("latin",      latin_candidates),
        ("devanagari", devanagari_candidates),
        ("universal",  universal_candidates),
    ]:
        font, path = try_load(candidates)
        results[name] = font
        short = Path(path).name if path else "not found"
        print(f"   Font [{name}]: {'✅' if font else '❌'} {short}")

    results["default"] = ImageFont.load_default()
    return results

def get_font_for_word(word, fonts):
    """
    Detect the dominant script of a word and return appropriate font.
    Works at word level — Tamil script rendered as whole unit.
    """
    tamil_count = devanagari_count = latin_count = 0
    for ch in word:
        try:
            name = unicodedata.name(ch, "")
        except Exception:
            name = ""
        if "TAMIL" in name:
            tamil_count += 1
        elif "DEVANAGARI" in name:
            devanagari_count += 1
        elif ch.isascii() and ch.isalpha():
            latin_count += 1

    if tamil_count >= latin_count and tamil_count >= devanagari_count:
        return fonts["tamil"] or fonts["universal"] or fonts["default"]
    elif devanagari_count > latin_count:
        return fonts["devanagari"] or fonts["universal"] or fonts["default"]
    else:
        return fonts["latin"] or fonts["universal"] or fonts["default"]

# ── Script cleaning ─────────────────────────────────────────
def clean_script(text):
    """Strip all markdown, stage directions, and formatting before TTS."""
    text = re.sub(r'\(.*?\)', ' ', text)
    text = re.sub(r'\[.*?\]', ' ', text)
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#+\s*', '', text)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'_{2,}', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

# ── Script chunking ─────────────────────────────────────────
def chunk_script(script, words_per_chunk=20):
    """Split into ~20 word chunks for tight sync."""
    words = script.split()
    return [" ".join(words[i:i + words_per_chunk])
            for i in range(0, len(words), words_per_chunk)]

# ── Cloud TTS ───────────────────────────────────────────────
def generate_tts_chunk(text, chunk_idx, voice_name, language_code):
    """Call Cloud TTS API (Chirp3 HD). Returns wav path or None."""
    token = get_token()
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": language_code, "name": voice_name},
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "speakingRate":  SPEAK_RATE
        }
    }
    r = requests.post(
        TTS_ENDPOINT,
        headers={
            "Authorization":       f"Bearer {token}",
            "x-goog-user-project": PROJECT_ID,
            "Content-Type":        "application/json"
        },
        json=payload, timeout=120
    )
    if r.status_code != 200:
        print(f"      ❌ TTS error {r.status_code}: {r.text[:300]}")
        return None
    try:
        audio_b64 = r.json().get("audioContent", "")
        if not audio_b64:
            print(f"      ❌ No audioContent in TTS response")
            return None
        chunk_path = WORK_DIR / f"chunk_{chunk_idx:04d}.wav"
        chunk_path.write_bytes(base64.b64decode(audio_b64))
        return str(chunk_path)
    except Exception as e:
        print(f"      ❌ TTS parse error: {e}")
        return None

def get_audio_duration(path):
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return 8.0

# ── Image helpers ───────────────────────────────────────────
def is_dark_image(img):
    return np.array(img.convert("L")).mean() < 128

def download_image(url):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            tmp = WORK_DIR / f"img_{abs(hash(url))}.jpg"
            tmp.write_bytes(r.content)
            return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ Image download failed: {e}")
    return Image.new("RGB", (W, H), (10, 10, 30))

def download_svg_as_image(svg_text):
    try:
        import cairosvg
        png_bytes = cairosvg.svg2png(
            bytestring=svg_text.encode("utf-8"),
            output_width=W, output_height=H
        )
        tmp = WORK_DIR / "infographic.png"
        tmp.write_bytes(png_bytes)
        return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ SVG conversion failed: {e}")
        return Image.new("RGB", (W, H), (5, 5, 20))

# ── Text frame rendering ────────────────────────────────────
def render_text_frame(base_img, text, fonts, is_tamil=True):
    """
    Composite text onto image.
    KEY FIX: Renders each word as a whole unit using the correct font.
    Tamil is an abugida — characters must not be split; render word-by-word.
    Line wrap: 40 chars for Tamil, 60 for English.
    """
    img     = base_img.copy().convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    dark     = is_dark_image(base_img)
    text_col = (255, 255, 255, 240) if dark else (20, 20, 20, 240)
    bg_col   = (0,   0,   0,   110) if dark else (255, 255, 255, 110)

    # ── Build lines (word-level wrap) ──────────────────────
    max_chars = 40 if is_tamil else 60
    words     = text.split()
    lines, cur, cur_len = [], [], 0
    for word in words:
        if cur_len + len(word) + 1 > max_chars and cur:
            lines.append(cur)
            cur, cur_len = [word], len(word)
        else:
            cur.append(word)
            cur_len += len(word) + 1
    if cur:
        lines.append(cur)
    lines = lines[:4]  # max 4 lines

    line_h  = FONT_SIZE + 12
    total_h = len(lines) * line_h + 24
    box_y   = H - total_h - 60
    box_x   = 60

    # Background box
    draw.rectangle(
        [box_x - 12, box_y - 12, W - box_x + 12, box_y + total_h + 12],
        fill=bg_col
    )

    # ── Render word by word (not char by char) ─────────────
    for li, line_words in enumerate(lines):
        y = box_y + li * line_h
        x = box_x

        for wi, word in enumerate(line_words):
            font = get_font_for_word(word, fonts)
            space = " " if wi < len(line_words) - 1 else ""
            token = word + space

            # Draw whole word at once — preserves Tamil ligatures
            draw.text((x, y), token, font=font, fill=text_col)

            # Advance x by word width
            try:
                bbox = font.getbbox(token)
                x += bbox[2] - bbox[0]
            except Exception:
                x += len(token) * (FONT_SIZE // 2)

    return Image.alpha_composite(img, overlay).convert("RGB")

# ── Build one video clip ────────────────────────────────────
def frames_to_clip(frame_path, audio_path, clip_idx, lang_prefix):
    clip_path = WORK_DIR / f"clip_{lang_prefix}_{clip_idx:04d}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(frame_path),
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(clip_path)
    ], check=True, timeout=300)
    return str(clip_path)

# ── Mix background music ────────────────────────────────────
def mix_music(video_path, music_path, output_path):
    """Mix background music at 8% volume with 3s fade in/out."""
    try:
        duration = get_audio_duration(video_path)
        print(f"   🎵 Mixing music (video: {duration:.1f}s, vol: {int(MUSIC_VOLUME*100)}%)...")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", video_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            (
                f"[1:a]volume={MUSIC_VOLUME},"
                f"afade=t=in:st=0:d={MUSIC_FADE},"
                f"afade=t=out:st={max(0, duration - MUSIC_FADE)}:d={MUSIC_FADE},"
                f"atrim=0:{duration}[music];"
                f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            ),
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            output_path
        ], check=True, timeout=600)
        print(f"   ✅ Music mixed successfully")
        return output_path
    except Exception as e:
        print(f"   ⚠️  Music mixing failed: {e} — using video without music")
        return video_path

# ── Single language render ──────────────────────────────────
def render_video(script, all_scenes, fonts, voice_name, language_code,
                 label, lang_prefix, work_subdir, music_path):
    """Render full video for one language. Returns path to final mp4 or None."""
    print(f"\n{'─'*50}")
    print(f"🎬 Rendering {label} video...")
    print(f"   Voice: {voice_name} | Rate: {SPEAK_RATE}x")

    work_subdir.mkdir(exist_ok=True)
    is_tamil    = language_code.startswith("ta")
    chunks      = chunk_script(script, words_per_chunk=20)
    clip_paths  = []
    scene_count = len(all_scenes)

    print(f"   Chunks: {len(chunks)} | Scenes: {scene_count}")

    for i, chunk in enumerate(chunks):
        scene_idx  = min(int(i / len(chunks) * scene_count), scene_count - 1)
        base_img   = all_scenes[scene_idx]

        audio_path = generate_tts_chunk(chunk, i, voice_name, language_code)
        if not audio_path:
            print(f"   ⚠️  Chunk {i+1} TTS failed — skipping")
            continue

        composited = render_text_frame(base_img, chunk, fonts, is_tamil=is_tamil)
        frame_path = work_subdir / f"frame_{i:04d}.jpg"
        composited.save(str(frame_path), "JPEG", quality=95)

        clip_path  = frames_to_clip(frame_path, audio_path, i, lang_prefix)
        clip_paths.append(clip_path)

        if (i + 1) % 10 == 0:
            print(f"   ✅ {i+1}/{len(chunks)} chunks done...")

    if not clip_paths:
        print(f"   ❌ No clips generated for {label}")
        return None

    # Concatenate clips
    concat_file = work_subdir / "concat.txt"
    with open(concat_file, "w") as f:
        for cp in clip_paths:
            f.write(f"file '{cp}'\n")

    raw_video = work_subdir / f"raw_{lang_prefix}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", str(raw_video)
    ], check=True, timeout=600)

    print(f"   ✅ {label} assembled — {len(clip_paths)}/{len(chunks)} clips")

    # Mix music
    final_video = work_subdir / f"final_{lang_prefix}.mp4"
    if music_path:
        return mix_music(str(raw_video), music_path, str(final_video))
    return str(raw_video)

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Pipeline — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    tamil_rows   = db_get("tamil_episodes",   {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    english_rows = db_get("english_episodes", {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})

    if not tamil_rows:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    tamil_ep   = tamil_rows[0]
    english_ep = english_rows[0] if english_rows else None
    print(f"\n📖 {tamil_ep['title_english']}")

    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "rendering_video"})

    try:
        # Load fonts once — shared by both videos
        print(f"\n🔤 Loading fonts...")
        fonts = load_fonts(FONT_SIZE)

        # Download background music once — shared by both videos
        music_path = download_music()

        # Download scene images from tamil_episodes (shared)
        image_urls_raw = tamil_ep.get("image_urls", "[]")
        image_urls     = json.loads(image_urls_raw) if isinstance(image_urls_raw, str) else image_urls_raw
        image_urls     = sorted(image_urls, key=lambda x: x["id"])
        scene_images   = [download_image(s["url"]) for s in image_urls]
        print(f"\n🖼  Downloaded {len(scene_images)} scene images")

        # SVG infographic
        svg_img = None
        svg_raw = tamil_ep.get("infographic_svg")
        if svg_raw:
            try:
                parsed  = json.loads(svg_raw) if isinstance(svg_raw, str) else svg_raw
                svg_txt = parsed.get("svg", "")
                if svg_txt:
                    print(f"\n📊 Converting SVG infographic...")
                    svg_img = download_svg_as_image(svg_txt)
                    print(f"   ✅ SVG ready")
            except Exception as e:
                print(f"   ⚠️  SVG load failed: {e}")

        # Scene list — SVG second-to-last
        all_scenes = scene_images.copy()
        if svg_img and len(all_scenes) > 1:
            all_scenes.insert(-1, svg_img)
        elif svg_img:
            all_scenes.append(svg_img)
        print(f"   Total scenes: {len(all_scenes)}")

        # ── Tamil video ────────────────────────────────────
        tamil_url    = None
        tamil_script = clean_script(tamil_ep.get("script_tamil", "") or "")

        if tamil_script:
            print(f"\n📝 Tamil script: {len(tamil_script.split())} words")
            tamil_path = render_video(
                script=tamil_script, all_scenes=all_scenes, fonts=fonts,
                voice_name=TAMIL_VOICE, language_code="ta-IN",
                label="Tamil", lang_prefix="ta",
                work_subdir=WORK_DIR / "tamil", music_path=music_path
            )
            if tamil_path:
                print(f"\n☁️  Uploading Tamil video...")
                tamil_url = upload_video(
                    f"ep{EPISODE_NUMBER:03d}_tamil.mp4",
                    Path(tamil_path).read_bytes()
                )
                print(f"   {'✅' if tamil_url else '❌'} Tamil upload")
                ok = db_patch("tamil_episodes", EPISODE_NUMBER, {
                    "video_url": tamil_url,
                    "status":    "video_ready"
                })
                print(f"   {'✅' if ok else '❌'} Tamil status saved")
        else:
            print("⚠️  No Tamil script — skipping")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})

        # ── English video ──────────────────────────────────
        english_url    = None
        english_script = clean_script(english_ep.get("script_english", "") or "") if english_ep else ""

        if english_script:
            print(f"\n📝 English script: {len(english_script.split())} words")
            english_path = render_video(
                script=english_script, all_scenes=all_scenes, fonts=fonts,
                voice_name=ENGLISH_VOICE, language_code="en-US",
                label="English", lang_prefix="en",
                work_subdir=WORK_DIR / "english", music_path=music_path
            )
            if english_path:
                print(f"\n☁️  Uploading English video...")
                english_url = upload_video(
                    f"ep{EPISODE_NUMBER:03d}_english.mp4",
                    Path(english_path).read_bytes()
                )
                print(f"   {'✅' if english_url else '❌'} English upload")
                ok = db_patch("english_episodes", EPISODE_NUMBER, {
                    "video_url": english_url,
                    "status":    "video_ready"
                })
                print(f"   {'✅' if ok else '❌'} English status saved")
        else:
            print("⚠️  No English script — skipping")

        # ── Summary ────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"✅ Episode {EPISODE_NUMBER} — Complete!")
        if tamil_url:
            print(f"   🇮🇳 Tamil  : {tamil_url}")
        if english_url:
            print(f"   🇬🇧 English: {english_url}")
        print(f"{'='*60}")

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
    finally:
        shutil.rmtree(str(WORK_DIR), ignore_errors=True)

if __name__ == "__main__":
    main()
