"""
I Have a Cause — Video Pipeline (Human Voice + Karaoke Sync)
=============================================================
Flow:
  1. Download recorded voice from Supabase Storage
     Tamil  : episode-voices/ep001_tamil.mp3  (wife's recording)
     English: episode-voices/ep001_english.mp3 (your recording)
  2. English voice: pitch shift down by PITCH_SHIFT_SEMITONES (configurable)
  3. faster-whisper: word-level forced alignment → exact timestamps per word
  4. Karaoke rendering:
     - Words appear one by one as voice speaks them
     - Max 3 lines visible at once
     - When 3 lines fill → ALL clear → fresh start from line 1
  5. Background music mixed at 8% volume, 3s fade in/out
  6. FFmpeg assembles final video with precise frame timing
  7. Upload to Supabase Storage, save video_url
"""

import os
import json
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

W, H = 1280, 720

# ── Karaoke settings ────────────────────────────────────────
WORDS_PER_LINE  = 7    # words per line
MAX_LINES       = 3    # lines before full clear
FONT_SIZE       = 30
FONT_SIZE_SMALL = 24

# ── Pitch shift ─────────────────────────────────────────────
PITCH_SHIFT_SEMITONES = -3   # configurable: 0 = no shift, -3 = deeper

# ── Music ───────────────────────────────────────────────────
MUSIC_URL    = "https://alfuvzlmatfkgdrkeqgk.supabase.co/storage/v1/object/public/episode-music/background.mp3"
MUSIC_VOLUME = 0.08
MUSIC_FADE   = 3

PROJECT_ID = "gen-lang-client-0078128013"

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

# ── Voice helpers ───────────────────────────────────────────
def voice_url(lang):
    return f"{SUPABASE_URL}/storage/v1/object/public/episode-voices/ep{EPISODE_NUMBER:03d}_{lang}.mp3"

def download_voice(lang):
    url = voice_url(lang)
    print(f"\n🎙  Downloading {lang} voice: {url}")
    try:
        r = requests.get(url, timeout=120)
        if r.status_code == 200:
            path = WORK_DIR / f"voice_{lang}.mp3"
            path.write_bytes(r.content)
            print(f"   ✅ Downloaded ({len(r.content)//1024} KB)")
            return str(path)
        print(f"   ❌ Not found ({r.status_code}) — skipping {lang} video")
        return None
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None

# ── Pitch shift ─────────────────────────────────────────────
def apply_pitch_shift(audio_path, semitones):
    if semitones == 0:
        return audio_path
    factor   = 2 ** (semitones / 12)
    atempo   = 1 / factor
    out_path = str(WORK_DIR / "voice_pitched.mp3")
    print(f"\n🎚  Pitch shift: {semitones} semitones (factor {factor:.4f})...")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", audio_path,
        "-af", f"asetrate=44100*{factor},aresample=44100,atempo={atempo}",
        out_path
    ], check=True, timeout=120)
    print(f"   ✅ Pitch shifted")
    return out_path

# ── Word alignment ──────────────────────────────────────────
def align_words(audio_path, language):
    """
    Use faster-whisper for word-level timestamps.
    Returns: [{"word": str, "start": float, "end": float}, ...]
    """
    print(f"\n🔤 Aligning words (faster-whisper, lang={language})...")
    from faster_whisper import WhisperModel
    model    = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, language=language, word_timestamps=True)
    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                word = w.word.strip()
                if word:
                    words.append({"word": word, "start": round(w.start, 3), "end": round(w.end, 3)})
    print(f"   ✅ {len(words)} words aligned")
    return words

# ── Build display states ────────────────────────────────────
def build_display_states(words):
    """
    Build karaoke display states.
    Each state = current visible lines at a point in time.
    When block fills (WORDS_PER_LINE * MAX_LINES) → all clear → fresh start.
    """
    BLOCK_SIZE = WORDS_PER_LINE * MAX_LINES
    states     = []
    block      = []

    for i, word_data in enumerate(words):
        block.append(word_data)

        end_time = words[i + 1]["start"] if i + 1 < len(words) else word_data["end"] + 0.5

        # Build lines from current block
        lines = []
        for line_idx in range(MAX_LINES):
            s = line_idx * WORDS_PER_LINE
            e = s + WORDS_PER_LINE
            chunk = [w["word"] for w in block[s:e]]
            if chunk:
                lines.append(chunk)

        states.append({
            "lines": lines,
            "start": word_data["start"],
            "end":   end_time
        })

        # Block full → clear
        if len(block) >= BLOCK_SIZE:
            block = []

    return states

# ── Font loading ────────────────────────────────────────────
def load_fonts(size, small_size):
    def try_load(candidates, sz):
        for path in candidates:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, sz), path
                except Exception:
                    continue
        return None, None

    tamil_c      = ["/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf",
                    "/usr/share/fonts/truetype/noto/NotoSerifTamil-Regular.ttf"]
    latin_c      = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"]
    devanagari_c = ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"]
    universal_c  = ["/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]

    fonts = {}; fonts_small = {}
    for name, cands in [("tamil", tamil_c), ("latin", latin_c),
                         ("devanagari", devanagari_c), ("universal", universal_c)]:
        f, path  = try_load(cands, size)
        fs, _    = try_load(cands, small_size)
        fonts[name]       = f
        fonts_small[name] = fs
        print(f"   Font [{name}]: {'✅' if f else '❌'} {Path(path).name if path else 'not found'}")

    fonts["default"]       = ImageFont.load_default()
    fonts_small["default"] = ImageFont.load_default()
    return fonts, fonts_small

def get_font_for_word(word, fonts):
    tamil_c = dev_c = latin_c = 0
    for ch in word:
        try:
            n = unicodedata.name(ch, "")
        except Exception:
            n = ""
        if "TAMIL" in n:      tamil_c += 1
        elif "DEVANAGARI" in n: dev_c  += 1
        elif ch.isascii() and ch.isalpha(): latin_c += 1
    if tamil_c >= latin_c and tamil_c >= dev_c:
        return fonts["tamil"] or fonts["universal"] or fonts["default"]
    elif dev_c > latin_c:
        return fonts["devanagari"] or fonts["universal"] or fonts["default"]
    return fonts["latin"] or fonts["universal"] or fonts["default"]

# ── Image helpers ───────────────────────────────────────────
def is_dark(img):
    return np.array(img.convert("L")).mean() < 128

def download_image(url):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            tmp = WORK_DIR / f"img_{abs(hash(url))}.jpg"
            tmp.write_bytes(r.content)
            return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ Image download: {e}")
    return Image.new("RGB", (W, H), (10, 10, 30))

def download_svg(svg_text):
    try:
        import cairosvg
        png = cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), output_width=W, output_height=H)
        tmp = WORK_DIR / "infographic.png"
        tmp.write_bytes(png)
        return Image.open(tmp).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        print(f"   ❌ SVG convert: {e}")
    return Image.new("RGB", (W, H), (5, 5, 20))

# ── Render karaoke frame ────────────────────────────────────
def render_frame(base_img, lines, fonts, fonts_small):
    img     = base_img.copy().convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    if not lines:
        return Image.alpha_composite(img, overlay).convert("RGB")

    dark     = is_dark(base_img)
    text_col = (255, 255, 255, 240) if dark else (20, 20, 20, 240)
    bg_col   = (0, 0, 0, 110) if dark else (255, 255, 255, 110)

    max_line_len = max(sum(len(w) for w in line) for line in lines)
    af = fonts_small if max_line_len > 35 else fonts

    line_h  = FONT_SIZE + 10
    total_h = len(lines) * line_h + 24
    box_y   = H - total_h - 60
    box_x   = 60

    draw.rectangle([box_x - 12, box_y - 12, W - box_x + 12, box_y + total_h + 12], fill=bg_col)

    for li, words in enumerate(lines):
        y, x = box_y + li * line_h, box_x
        for wi, word in enumerate(words):
            font  = get_font_for_word(word, af)
            token = word + (" " if wi < len(words) - 1 else "")
            draw.text((x, y), token, font=font, fill=text_col)
            try:
                bbox = font.getbbox(token)
                x += bbox[2] - bbox[0]
            except Exception:
                x += len(token) * (FONT_SIZE // 2)

    return Image.alpha_composite(img, overlay).convert("RGB")

# ── Audio duration ──────────────────────────────────────────
def get_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0

# ── Download music ──────────────────────────────────────────
def download_music():
    try:
        r = requests.get(MUSIC_URL, timeout=60)
        if r.status_code == 200:
            p = WORK_DIR / "background.mp3"
            p.write_bytes(r.content)
            print(f"   ✅ Music downloaded ({len(r.content)//1024} KB)")
            return str(p)
    except Exception as e:
        print(f"   ⚠️  Music: {e}")
    return None

# ── Mix music ───────────────────────────────────────────────
def mix_music(video_path, music_path, output_path):
    try:
        dur = get_duration(video_path)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", video_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex",
            (f"[1:a]volume={MUSIC_VOLUME},"
             f"afade=t=in:st=0:d={MUSIC_FADE},"
             f"afade=t=out:st={max(0,dur-MUSIC_FADE)}:d={MUSIC_FADE},"
             f"atrim=0:{dur}[music];"
             f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            output_path
        ], check=True, timeout=600)
        print(f"   ✅ Music mixed")
        return output_path
    except Exception as e:
        print(f"   ⚠️  Music mix failed: {e}")
        return video_path

# ── Render one language ─────────────────────────────────────
def render_video(audio_path, all_scenes, fonts, fonts_small,
                 language, label, lang_prefix, work_subdir, music_path):
    print(f"\n{'─'*50}")
    print(f"🎬 Rendering {label} video...")
    work_subdir.mkdir(exist_ok=True)

    words = align_words(audio_path, language)
    if not words:
        print(f"   ❌ No words — cannot render")
        return None

    total_dur = get_duration(audio_path)
    print(f"   Audio: {total_dur:.1f}s | Words: {len(words)}")

    states      = build_display_states(words)
    frames_dir  = work_subdir / "frames"
    frames_dir.mkdir()
    concat_file = work_subdir / "concat.txt"
    scene_count = len(all_scenes)

    print(f"   Rendering {len(states)} frames...")
    with open(concat_file, "w") as cf:
        for i, state in enumerate(states):
            scene_idx = min(int((state["start"] / max(total_dur, 1)) * scene_count), scene_count - 1)
            frame     = render_frame(all_scenes[scene_idx], state["lines"], fonts, fonts_small)
            fp        = frames_dir / f"frame_{i:05d}.jpg"
            frame.save(str(fp), "JPEG", quality=95)
            dur = max(0.033, state["end"] - state["start"])
            cf.write(f"file '{fp}'\n")
            cf.write(f"duration {dur:.3f}\n")
        # hold last frame briefly
        if states:
            cf.write(f"file '{frames_dir}/frame_{len(states)-1:05d}.jpg'\n")
            cf.write(f"duration 0.5\n")

    print(f"   ✅ Frames rendered")

    raw = work_subdir / f"raw_{lang_prefix}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", audio_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(raw)
    ], check=True, timeout=600)
    print(f"   ✅ {label} assembled ({total_dur:.0f}s)")

    final = work_subdir / f"final_{lang_prefix}.mp4"
    if music_path:
        return mix_music(str(raw), music_path, str(final))
    return str(raw)

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Pipeline (Human Voice + Karaoke) — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Pitch shift (English): {PITCH_SHIFT_SEMITONES} semitones")
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
        print(f"\n🔤 Loading fonts...")
        fonts, fonts_small = load_fonts(FONT_SIZE, FONT_SIZE_SMALL)

        print(f"\n🎵 Downloading music...")
        music_path = download_music()

        # Scene images
        image_urls_raw = tamil_ep.get("image_urls", "[]")
        image_urls     = json.loads(image_urls_raw) if isinstance(image_urls_raw, str) else image_urls_raw
        image_urls     = sorted(image_urls, key=lambda x: x["id"])
        scene_images   = [download_image(s["url"]) for s in image_urls]
        print(f"\n🖼  {len(scene_images)} scene images downloaded")

        # SVG
        svg_img = None
        svg_raw = tamil_ep.get("infographic_svg")
        if svg_raw:
            try:
                parsed  = json.loads(svg_raw) if isinstance(svg_raw, str) else svg_raw
                svg_txt = parsed.get("svg", "")
                if svg_txt:
                    print(f"\n📊 Converting SVG...")
                    svg_img = download_svg(svg_txt)
                    print(f"   ✅ SVG ready")
            except Exception as e:
                print(f"   ⚠️  SVG: {e}")

        all_scenes = scene_images.copy()
        if svg_img and len(all_scenes) > 1:
            all_scenes.insert(-1, svg_img)
        elif svg_img:
            all_scenes.append(svg_img)
        print(f"   Total scenes: {len(all_scenes)}")

        # ── Tamil ──────────────────────────────────────────
        tamil_url   = None
        tamil_voice = download_voice("tamil")
        if tamil_voice:
            tamil_final = render_video(
                audio_path  = tamil_voice,
                all_scenes  = all_scenes,
                fonts       = fonts,
                fonts_small = fonts_small,
                language    = "ta",
                label       = "Tamil",
                lang_prefix = "ta",
                work_subdir = WORK_DIR / "tamil",
                music_path  = music_path
            )
            if tamil_final:
                print(f"\n☁️  Uploading Tamil video...")
                tamil_url = upload_video(f"ep{EPISODE_NUMBER:03d}_tamil.mp4", Path(tamil_final).read_bytes())
                print(f"   {'✅' if tamil_url else '❌'} Tamil upload")
                db_patch("tamil_episodes", EPISODE_NUMBER, {
                    "video_url": tamil_url,
                    "voice_url": voice_url("tamil"),
                    "status":    "video_ready"
                })
        else:
            print(f"\n⚠️  Upload Tamil voice to: episode-voices/ep{EPISODE_NUMBER:03d}_tamil.mp3")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})

        # ── English ────────────────────────────────────────
        english_url   = None
        english_voice = download_voice("english")
        if english_voice and english_ep:
            english_voice = apply_pitch_shift(english_voice, PITCH_SHIFT_SEMITONES)
            english_final = render_video(
                audio_path  = english_voice,
                all_scenes  = all_scenes,
                fonts       = fonts,
                fonts_small = fonts_small,
                language    = "en",
                label       = "English",
                lang_prefix = "en",
                work_subdir = WORK_DIR / "english",
                music_path  = music_path
            )
            if english_final:
                print(f"\n☁️  Uploading English video...")
                english_url = upload_video(f"ep{EPISODE_NUMBER:03d}_english.mp4", Path(english_final).read_bytes())
                print(f"   {'✅' if english_url else '❌'} English upload")
                db_patch("english_episodes", EPISODE_NUMBER, {
                    "video_url": english_url,
                    "voice_url": voice_url("english"),
                    "status":    "video_ready"
                })
        else:
            if not english_voice:
                print(f"\n⚠️  Upload English voice to: episode-voices/ep{EPISODE_NUMBER:03d}_english.mp3")

        print(f"\n{'='*60}")
        print(f"✅ Episode {EPISODE_NUMBER} — Complete!")
        if tamil_url:   print(f"   🇮🇳 Tamil  : {tamil_url}")
        if english_url: print(f"   🇬🇧 English: {english_url}")
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
