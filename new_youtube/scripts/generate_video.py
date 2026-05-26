"""
generate_video.py — New YouTube Pipeline
==========================================
Combines all assets into a final YouTube video:
  - Voice recording (wife for Tamil, you for English)
  - WhisperX word-level alignment for karaoke sync
  - English voice: -3 semitone pitch shift for deeper sound
  - 5 Imagen 3 script images (cycling behind voice)
  - Intro image (2s fade-in) — episode-specific or default
  - Outro image (3s fade-out) — episode-specific or default
  - Narrator circle photo — bottom-left corner, always visible
  - Channel logo — bottom-right corner, always visible
  - Background music at 12% volume
  - Karaoke text: rolling 3 lines, lower third, dark semi-transparent bar

Layout:
  ┌─────────────────────────────────┐
  │                                 │
  │   Imagen 3 images (top 70%)     │
  │                                 │
  ├─────────────────────────────────┤
  │[photo] Tamil/English script  [logo]│  ← lower third (30%)
  │        rolling 3-line karaoke       │
  └─────────────────────────────────┘

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  GOOGLE_APPLICATION_CREDENTIALS_JSON
  EPISODE_NUMBER
  LANGUAGE — ta or en
"""

import os
import sys
import json
import subprocess
import tempfile
import shutil
import requests
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

# ── Video settings ────────────────────────────────────────────
WIDTH         = 1920
HEIGHT        = 1080
FPS           = 24
LOWER_THIRD   = 0.30      # 30% of height for text bar
TEXT_Y_TOP    = HEIGHT - int(HEIGHT * LOWER_THIRD) + 20
LINE_HEIGHT   = 65
FONT_SIZE     = 42
WORDS_PER_LINE= 6
MAX_LINES     = 3
MUSIC_VOL     = 0.12
INTRO_DUR     = 2.0       # seconds
OUTRO_DUR     = 3.0       # seconds
FADE_DUR      = 0.5       # fade in/out duration
PITCH_SHIFT   = -3        # semitones for English voice deepening
PHOTO_SIZE    = 140       # narrator circle diameter
PHOTO_MARGIN  = 20
LOGO_HEIGHT   = 55
LOGO_MARGIN   = 20

# ── Supabase helpers ──────────────────────────────────────────
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
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{val}",
        headers=SB_HEADERS, json=data, timeout=30
    )
    return r.status_code in (200, 204)

def upload_to_storage(bucket, path, data_bytes, content_type="video/mp4"):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  content_type,
            "x-upsert":      "true",
        },
        data=data_bytes, timeout=600
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

def download_file(url, dest_path, desc="file"):
    """Download a file from Supabase storage URL."""
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    r = requests.get(url, headers=headers, stream=True, timeout=120)
    if r.status_code == 200:
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        size_kb = os.path.getsize(dest_path) // 1024
        print(f"   ✅ {desc}: {size_kb}KB")
        return True
    print(f"   ❌ {desc} download failed {r.status_code}: {url[:80]}")
    return False

def storage_url(bucket, path):
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"

# ── WhisperX alignment ────────────────────────────────────────
def run_whisperx(audio_path, language, tmpdir):
    """Run WhisperX to get word-level timestamps. Returns list of word dicts."""
    print(f"\n🎙️  Running WhisperX alignment ({language})...")
    model_size = "medium"

    result = subprocess.run(
        [
            "whisperx", audio_path,
            "--model", model_size,
            "--language", language,
            "--output_dir", tmpdir,
            "--output_format", "json",
            "--align_model", ("WAV2VEC2_ASR_LARGE_LV60K_400H" if language == "en" else "WAVE2VEC2_BASE_TH"),
            "--compute_type", "float32",
        ],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"   ⚠️  WhisperX stderr: {result.stderr[:300]}")
        # Fall back to simple duration-based sync
        return None

    # Find output JSON
    audio_stem = Path(audio_path).stem
    json_path  = os.path.join(tmpdir, f"{audio_stem}.json")
    if not os.path.exists(json_path):
        json_files = list(Path(tmpdir).glob("*.json"))
        if json_files:
            json_path = str(json_files[0])
        else:
            print("   ⚠️  WhisperX output JSON not found — falling back to duration sync")
            return None

    with open(json_path) as f:
        data = json.load(f)

    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            if "word" in w and "start" in w and "end" in w:
                words.append({
                    "word":  w["word"].strip(),
                    "start": w["start"],
                    "end":   w["end"],
                })

    print(f"   ✅ WhisperX: {len(words)} words aligned")
    return words if words else None

def get_audio_duration(audio_path):
    """Get audio duration in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())

def find_trigger_timestamp(words, trigger_text):
    """
    Find exact timestamp when a trigger line is spoken.
    Searches for the trigger words in the WhisperX word sequence.
    Returns the start time of the first matching word, or None if not found.
    """
    import re as _re
    if not trigger_text or not words:
        return None

    def normalize(text):
        return _re.sub(r"[^\w\s]", "", text.lower()).split()

    trigger_words = normalize(trigger_text)
    if not trigger_words:
        return None

    n = len(trigger_words)
    word_list = [normalize(w["word"])[0] if normalize(w["word"]) else "" for w in words]

    for i in range(len(word_list) - n + 1):
        if word_list[i:i+n] == trigger_words:
            ts = words[i]["start"]
            print(f"   🎯 Trigger '{trigger_text[:40]}...' → {ts:.2f}s")
            return ts

    # Fuzzy fallback — match first 3 words only
    if n >= 3:
        short = trigger_words[:3]
        for i in range(len(word_list) - 3 + 1):
            if word_list[i:i+3] == short:
                ts = words[i]["start"]
                print(f"   🎯 Trigger (fuzzy) '{trigger_text[:40]}' → {ts:.2f}s")
                return ts

    print(f"   ⚠️  Trigger not found: '{trigger_text[:60]}'")
    return None

def build_image_timeline(episode_images, words, audio_duration):
    """
    Build image timeline: list of (image_path_placeholder, start_time, end_time).
    Uses trigger lines matched against WhisperX timestamps.
    Image 1 always starts at 0. Each image ends when the next one starts.
    Last image ends at audio_duration.
    Returns list of {url, start, end, trigger}
    """
    if not episode_images:
        return []

    timeline = []
    for i, img in enumerate(episode_images):
        trigger = img.get("trigger", "").strip()
        if i == 0:
            start = 0.0  # First image always starts immediately
        else:
            ts = find_trigger_timestamp(words, trigger) if trigger else None
            if ts is None:
                # Fallback: divide remaining time equally
                prev_end = timeline[-1]["end"] if timeline else 0
                remaining = audio_duration - prev_end
                remaining_imgs = len(episode_images) - i
                start = prev_end + (remaining / (remaining_imgs + 1))
                print(f"   ⚠️  Image {i+1} fallback timing: {start:.2f}s")
            else:
                start = ts

        timeline.append({
            "url":     img["url"],
            "start":   start,
            "end":     audio_duration,  # Will be updated
            "trigger": trigger,
            "order":   img.get("order", i+1),
        })

    # Set end times
    for i in range(len(timeline) - 1):
        timeline[i]["end"] = timeline[i+1]["start"]

    print(f"
   📋 Image timeline:")
    for t in timeline:
        print(f"      Image {t['order']}: {t['start']:.2f}s → {t['end']:.2f}s ({t['end']-t['start']:.1f}s)")

    return timeline

def build_karaoke_screens(words, script_text, audio_duration):
    """
    Build karaoke screens from word timestamps.
    Each screen: max 3 lines × 6 words.
    When 3 lines fill → all clear → fresh start.
    Returns list of {start, end, lines: [line1, line2, line3]}
    """
    if words:
        # Group words into lines
        lines = []
        current_line = []
        for w in words:
            current_line.append(w)
            if len(current_line) >= WORDS_PER_LINE:
                lines.append(current_line)
                current_line = []
        if current_line:
            lines.append(current_line)

        # Group lines into screens of MAX_LINES
        screens = []
        for i in range(0, len(lines), MAX_LINES):
            screen_lines = lines[i:i + MAX_LINES]
            start = screen_lines[0][0]["start"]
            end   = screen_lines[-1][-1]["end"]
            text_lines = [" ".join(w["word"] for w in line) for line in screen_lines]
            # Pad to MAX_LINES
            while len(text_lines) < MAX_LINES:
                text_lines.append("")
            screens.append({"start": start, "end": end, "lines": text_lines})

    else:
        # Fallback: simple duration-based sync from script text
        print("   ℹ️  Using duration-based sync (no WhisperX timestamps)")
        words_list = script_text.split()
        lines = []
        for i in range(0, len(words_list), WORDS_PER_LINE):
            lines.append(" ".join(words_list[i:i + WORDS_PER_LINE]))

        total_lines = len(lines)
        time_per_line = (audio_duration - INTRO_DUR - OUTRO_DUR) / max(total_lines, 1)

        screens = []
        for i in range(0, len(lines), MAX_LINES):
            screen_lines = lines[i:i + MAX_LINES]
            start = INTRO_DUR + (i * time_per_line)
            end   = start + (len(screen_lines) * time_per_line)
            while len(screen_lines) < MAX_LINES:
                screen_lines.append("")
            screens.append({"start": start, "end": end, "lines": screen_lines})

    print(f"   ✅ {len(screens)} karaoke screens built")
    return screens

# ── Circle mask for narrator photo ───────────────────────────
def make_circle_photo(input_path, output_path, size):
    """Create a circular cropped photo using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", (
            f"scale={size}:{size}:force_original_aspect_ratio=increase,"
            f"crop={size}:{size},"
            f"format=yuva420p,"
            f"geq=lum='p(X,Y)':a='if(gt(pow(X-{size//2},2)+pow(Y-{size//2},2),pow({size//2},2)),0,255)'"
        ),
        "-frames:v", "1",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(output_path)

# ── Image cycle list ──────────────────────────────────────────
def build_image_cycle(image_paths, audio_duration):
    """Return list of (image_path, start, end) for cycling images."""
    n       = len(image_paths)
    main_dur = audio_duration  # intro/outro handled separately
    per_img  = main_dur / n
    cycles   = []
    for i, path in enumerate(image_paths):
        cycles.append((path, i * per_img, (i + 1) * per_img))
    return cycles

# ── FFmpeg: pitch shift ───────────────────────────────────────
def pitch_shift_audio(input_path, output_path, semitones):
    """Shift pitch by N semitones. Negative = deeper."""
    factor = 2 ** (semitones / 12.0)
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"asetrate=44100*{factor:.6f},aresample=44100",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(f"   ⚠️  Pitch shift failed, using original audio")
        shutil.copy(input_path, output_path)

# ── Main FFmpeg render ────────────────────────────────────────
def render_video(
    image_timeline, audio_path, music_path,
    intro_path, outro_path,
    photo_path, logo_path,
    screens, audio_duration, output_path
):
    print(f"\n🎬 Rendering video with FFmpeg...")

    n_images = len(image_timeline)

    # Build input list — each image runs for its exact duration from timeline
    inputs = []
    for item in image_timeline:
        dur = item["end"] - item["start"]
        inputs += ["-loop", "1", "-t", str(dur + 0.1), "-i", item["local_path"]]

    inputs += ["-i", audio_path]
    inputs += ["-i", music_path]

    audio_idx = n_images
    music_idx = n_images + 1

    # ── Scale + set duration + concat images ────────────────
    # Each image scaled to fill frame (handles both 1:1 and 16:9 inputs)
    scale_filters = ""
    for i in range(n_images):
        dur = image_timeline[i]["end"] - image_timeline[i]["start"]
        scale_filters += (
            f"[{i}:v]scale={WIDTH}:{HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},"
            f"trim=duration={dur},setpts=PTS-STARTPTS[sv{i}];"
        )

    # Concat all images into one stream
    concat_in  = "".join(f"[sv{i}]" for i in range(n_images))
    concat_filter = f"{concat_in}concat=n={n_images}:v=1:a=0[bg_raw];"

    # ── Dark lower third bar (semi-transparent) ──────────────
    lower_top = HEIGHT - int(HEIGHT * LOWER_THIRD)
    bar_filter = (
        f"[bg_raw]drawbox="
        f"x=0:y={lower_top}:w={WIDTH}:h={int(HEIGHT * LOWER_THIRD)}:"
        f"color=black@0.72:t=fill[bg_bar];"
    )

    # ── Karaoke drawtext filters ─────────────────────────────
    font_path = "/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.ttf"
    if LANGUAGE == "en":
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not os.path.exists(font_path):
            font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"

    def esc(s):
        return s.replace("'", "\\'").replace(":", "\\:").replace("\\", "\\\\")

    drawtext_chain = "[bg_bar]"
    for idx, screen in enumerate(screens):
        out_label = f"[kt{idx+1}]" if idx < len(screens) - 1 else "[bg_text]"
        in_label  = drawtext_chain
        # Draw 3 lines
        filter_parts = []
        for li, line_text in enumerate(screen["lines"]):
            if not line_text.strip():
                continue
            y = lower_top + 30 + (li * LINE_HEIGHT)
            t_start = screen["start"]
            t_end   = screen["end"]
            filter_parts.append(
                f"drawtext=text='{esc(line_text)}':"
                f"fontsize={FONT_SIZE}:fontcolor=white:"
                f"x=(w-text_w)/2:y={y}:"
                f"font='{font_path}':"
                f"enable='between(t,{t_start:.3f},{t_end:.3f})'"
            )
        if filter_parts:
            combined = ",".join(filter_parts)
            drawtext_chain = f"{in_label}{combined}{out_label};"
        else:
            drawtext_chain = f"{in_label}null{out_label};"

    if "[bg_text]" not in drawtext_chain:
        drawtext_chain = "[bg_bar]null[bg_text];"

    # ── Narrator photo circle overlay ─────────────────────────
    photo_filter = ""
    photo_input_idx = None
    if photo_path and os.path.exists(photo_path):
        photo_input_idx = n_images + 2
        inputs += ["-i", photo_path]
        px = PHOTO_MARGIN
        py = HEIGHT - PHOTO_SIZE - PHOTO_MARGIN
        photo_filter = (
            f"[{photo_input_idx}:v]scale={PHOTO_SIZE}:{PHOTO_SIZE},"
            f"format=yuva420p[photo_scaled];"
            f"[bg_text][photo_scaled]overlay={px}:{py}:format=auto[bg_photo];"
        )
        video_out = "[bg_photo]"
    else:
        video_out = "[bg_text]"

    # ── Logo overlay ──────────────────────────────────────────
    logo_filter = ""
    if logo_path and os.path.exists(logo_path):
        logo_input_idx = n_images + 2 + (1 if photo_input_idx else 0)
        inputs += ["-i", logo_path]
        logo_w  = int(LOGO_HEIGHT * 2.0)  # approximate
        lx      = WIDTH - logo_w - LOGO_MARGIN
        ly      = HEIGHT - LOGO_HEIGHT - LOGO_MARGIN
        logo_filter = (
            f"[{logo_input_idx}:v]scale={logo_w}:{LOGO_HEIGHT},"
            f"format=yuva420p[logo_scaled];"
            f"[{video_out[1:-1]}][logo_scaled]overlay={lx}:{ly}:format=auto[bg_logo];"
        )
        video_out = "[bg_logo]"

    # ── Intro / Outro ─────────────────────────────────────────
    intro_input_idx = n_images + 2 + (1 if photo_input_idx else 0) + (1 if logo_filter else 0)
    outro_input_idx = intro_input_idx + 1
    inputs += [
        "-loop", "1", "-t", str(INTRO_DUR + FADE_DUR), "-i", intro_path,
        "-loop", "1", "-t", str(OUTRO_DUR + FADE_DUR), "-i", outro_path,
    ]

    intro_filter = (
        f"[{intro_input_idx}:v]scale={WIDTH}:{HEIGHT}:"
        f"force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"fade=t=out:st={INTRO_DUR - FADE_DUR}:d={FADE_DUR},setsar=1[intro_v];"
    )
    outro_filter = (
        f"[{outro_input_idx}:v]scale={WIDTH}:{HEIGHT}:"
        f"force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"fade=t=in:st=0:d={FADE_DUR},setsar=1[outro_v];"
    )

    # Final concat: intro + main + outro
    final_concat = (
        f"[intro_v]{video_out[1:-1]}[outro_v]"
        f"concat=n=3:v=1:a=0[video_out];"
    )

    # ── Audio: voice + music mix ──────────────────────────────
    total_dur = INTRO_DUR + audio_duration + OUTRO_DUR
    audio_filter = (
        f"[{audio_idx}:a]adelay={int(INTRO_DUR * 1000)}|{int(INTRO_DUR * 1000)}[voice_delayed];"
        f"[{music_idx}:a]aloop=loop=-1:size=2e+09,volume={MUSIC_VOL},"
        f"atrim=duration={total_dur}[music_loop];"
        f"[voice_delayed][music_loop]amix=inputs=2:duration=first[audio_out];"
    )

    # ── Build complete filtergraph ────────────────────────────
    filtergraph = (
        scale_filters +
        concat_filter +
        bar_filter +
        drawtext_chain +
        photo_filter +
        logo_filter +
        intro_filter +
        outro_filter +
        final_concat +
        audio_filter
    )

    # ── Final FFmpeg command ──────────────────────────────────
    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filtergraph,
            "-map", "[video_out]",
            "-map", "[audio_out]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
    )

    print("   Running FFmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ❌ FFmpeg failed:")
        print(result.stderr[-3000:])
        return False

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"   ✅ Video rendered: {size_mb:.1f}MB")
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Generator — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. Fetch episode
    table = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    ep_rows = db_get(table, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    episode = ep_rows[0] if ep_rows else None
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found in {table}")
        return

    print(f"\n📖 {episode.get('title_english') or episode.get('title_tamil')}")

    db_patch(table, EPISODE_NUMBER, {"status": "generating_video"})

    with tempfile.TemporaryDirectory() as tmpdir:
        lang_code = "ta" if LANGUAGE == "ta" else "en"

        # 2. Download voice recording
        voice_url = episode.get("voice_url")
        if not voice_url:
            print("❌ No voice recording found — upload voice first")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        voice_raw = os.path.join(tmpdir, f"voice_raw.mp3")
        if not download_file(voice_url, voice_raw, "Voice recording"):
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 3. Pitch shift for English
        if LANGUAGE == "en":
            print(f"\n🎚️  Applying {PITCH_SHIFT} semitone pitch shift...")
            voice_path = os.path.join(tmpdir, "voice.mp3")
            pitch_shift_audio(voice_raw, voice_path, PITCH_SHIFT)
        else:
            voice_path = voice_raw

        audio_duration = get_audio_duration(voice_path)
        print(f"\n   Audio duration: {audio_duration:.1f}s")

        # 4. Get script for WhisperX
        script_col = "script_tamil" if LANGUAGE == "ta" else "script_english"
        script_text = episode.get(script_col, "") or ""

        # 5. WhisperX alignment
        whisper_lang = "ta" if LANGUAGE == "ta" else "en"
        words = run_whisperx(voice_path, whisper_lang, tmpdir)
        screens = build_karaoke_screens(words, script_text, audio_duration)

        # 6. Load episode images (user-uploaded with trigger lines)
        raw_ep_images = episode.get("episode_images") or []
        if isinstance(raw_ep_images, str):
            try:
                raw_ep_images = json.loads(raw_ep_images)
            except:
                raw_ep_images = []

        if not raw_ep_images:
            print("❌ No episode images found — upload images first")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        print(f"\n📸 Downloading {len(raw_ep_images)} episode images...")
        # Download each image and build local path map
        image_paths = []
        for img in sorted(raw_ep_images, key=lambda x: x.get("order", 0)):
            dest = os.path.join(tmpdir, f"ep_img_{img.get('order',len(image_paths)+1)}.jpg")
            if download_file(img["url"], dest, f"Image {img.get('order','')} — trigger: '{img.get('trigger','(start)')[:30]}'"):
                image_paths.append({**img, "local_path": dest})

        if not image_paths:
            print("❌ Could not download any images")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 7. Download intro image (episode-specific or default)
        print(f"\n🖼️  Downloading intro/outro images...")
        intro_path = os.path.join(tmpdir, "intro.png")
        outro_path = os.path.join(tmpdir, "outro.png")

        intro_url = episode.get("intro_image_url") or storage_url("channel-assets", "default_intro.png")
        outro_url = episode.get("outro_image_url") or storage_url("channel-assets", "default_outro.png")

        download_file(intro_url, intro_path, "Intro image")
        download_file(outro_url, outro_path, "Outro image")

        # 8. Download narrator photo
        print(f"\n👤 Downloading narrator photo...")
        photo_file = "photo_tamil.jpg" if LANGUAGE == "ta" else "photo_english.jpg"
        photo_path = os.path.join(tmpdir, "narrator.jpg")
        photo_circle = os.path.join(tmpdir, "narrator_circle.png")
        photo_url  = storage_url("channel-assets", photo_file)

        if download_file(photo_url, photo_path, f"Narrator photo ({photo_file})"):
            make_circle_photo(photo_path, photo_circle, PHOTO_SIZE)
            photo_final = photo_circle if os.path.exists(photo_circle) else photo_path
        else:
            photo_final = None

        # 9. Download channel logo
        print(f"\n🔱 Downloading channel logo...")
        logo_path = os.path.join(tmpdir, "logo.png")
        logo_url  = "https://storage.googleapis.com/ihaveacause-media/assets/ihaveacause_logo.png"
        logo_final = None
        try:
            r = requests.get(logo_url, timeout=15)
            if r.status_code == 200:
                with open(logo_path, "wb") as f:
                    f.write(r.content)
                logo_final = logo_path
                print(f"   ✅ Logo downloaded")
        except Exception as e:
            print(f"   ⚠️  Logo download failed: {e} — continuing without logo")

        # 10. Download background music
        print(f"\n🎵 Downloading background music...")
        music_path = os.path.join(tmpdir, "music.mp3")
        music_url  = storage_url("episode-music", "background.mp3")
        if not download_file(music_url, music_path, "Background music"):
            print("   ⚠️  Music not found — rendering without music")
            # Create 1 second of silence as fallback
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "1", music_path
            ], capture_output=True)

        # 11. Render video
        output_path = os.path.join(tmpdir, f"ep{EPISODE_NUMBER:03d}_{lang_code}.mp4")

        # Build image timeline using WhisperX trigger matching
        image_timeline = build_image_timeline(image_paths, words or [], audio_duration)

        success = render_video(
            image_timeline = image_timeline,
            audio_path   = voice_path,
            music_path   = music_path,
            intro_path   = intro_path,
            outro_path   = outro_path,
            photo_path   = photo_final,
            logo_path    = logo_final,
            screens      = screens,
            audio_duration = audio_duration,
            output_path  = output_path,
        )

        if not success:
            print("❌ Video rendering failed")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 12. Upload to Supabase
        print(f"\n☁️  Uploading video to Supabase...")
        storage_path = f"ep{EPISODE_NUMBER:03d}/{lang_code}/final.mp4"
        with open(output_path, "rb") as f:
            video_data = f.read()

        video_url = upload_to_storage("episode-videos", storage_path, video_data, "video/mp4")

        if not video_url:
            print("❌ Video upload failed")
            db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"})
            return

        # 13. Save to database
        db_patch(table, EPISODE_NUMBER, {
            "video_url": video_url,
            "status":    "video_ready",
        })

        print(f"\n{'='*60}")
        print(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — video ready!")
        print(f"   URL: {video_url}")
        print(f"{'='*60}")

if __name__ == "__main__":
    main()
