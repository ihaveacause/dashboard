"""
generate_video.py — New YouTube Pipeline (CTC Forced Alignment)
================================================================
TIMING ENGINE: CTC forced alignment (NOT transcribe-then-match).

The core fix for the weeks-long sync problem:
  Previous approaches transcribed the audio (WhisperX/Whisper) then tried to
  MAP the script onto the transcription — by ratio or content matching. That
  is always an approximation and drifts (the 7:40 break).

  CTC forced alignment is fundamentally different: it takes your EXACT script
  text + the audio together, and finds precisely WHEN each script word is
  spoken. No transcription, no matching, no drift. Output is one timestamp
  per script word, in script order — exact 1:1 correspondence.

  Because every script word has an exact timestamp:
    • Karaoke screen N → look up timestamp of its first word. Exact.
    • Image trigger phrase → find it in the script → look up timestamp. Exact.
  Both Tamil and English use the same multilingual model (MMS, 1100+ langs).

FRAME COMPOSITING (unchanged, working):
  Pillow burns text + image into single frames → FFmpeg stitches one stream.
  Seeking is frame-accurate because there is only one video stream.

Env vars: SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON,
          EPISODE_NUMBER, LANGUAGE (ta or en)
"""

import os
import json
import subprocess
import tempfile
import requests
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])
LANGUAGE       = os.environ.get("LANGUAGE", "ta")
GCS_BUCKET     = "ihaveacause-media"
# Burned-in karaoke captions are OFF by default now: the English master must stay
# text-free so YouTube's per-language subtitles (from the uploaded caption file) carry
# the words. Set BURN_CAPTIONS=1 to restore the old on-screen karaoke.
BURN_CAPTIONS  = os.environ.get("BURN_CAPTIONS", "0") != "0"
# AI voice pace. 1.0 = normal; 0.8 = 20% slower. Chirp 3 HD has no speaking-rate
# param, so we adjust with ffmpeg atempo, which changes speed but preserves pitch.
VOICE_SPEED    = float(os.environ.get("VOICE_SPEED", "0.9"))

# ── Video settings ────────────────────────────────────────────
WIDTH, HEIGHT  = 1920, 1080
FPS            = 24
BAR_HEIGHT     = 230
LOWER_TOP      = HEIGHT - BAR_HEIGHT   # 850
LINE_HEIGHT    = 65
FONT_SIZE      = 42
WORDS_PER_LINE = 11
MAX_LINES      = 3
MUSIC_VOL      = 0.05
INTRO_DUR      = 2.0
OUTRO_DUR      = 3.0
FADE_DUR       = 0.5
# Photo: bottom-left of IMAGE AREA (above text bar)
PHOTO_SIZE     = 110
PHOTO_X        = 18
PHOTO_Y        = LOWER_TOP - PHOTO_SIZE - 12
# Logo: top-right of IMAGE AREA
LOGO_SIZE      = 90
LOGO_X         = WIDTH - LOGO_SIZE - 18
LOGO_Y         = 18

# ── Supabase helpers ──────────────────────────────────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json", "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get(table, params):
    r = requests.get(f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"}, params=params, timeout=15)
    return r.json() if r.status_code == 200 else []

def db_patch(table, val, data, _retries=3):
    """Write to Supabase with VISIBLE failures + retries. Previously a non-2xx was
    swallowed silently, leaving the row stuck at its old status."""
    import time as _t
    last = ""
    for attempt in range(1, _retries + 1):
        try:
            r = requests.patch(f"{REST}/{table}?episode_number=eq.{val}",
                headers=SB_HEADERS, json=data, timeout=30)
            if r.status_code in (200, 204):
                if attempt > 1:
                    print(f"   ✅ db_patch {table} #{val} succeeded on retry {attempt}", flush=True)
                return True
            last = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last = f"exception: {e}"
        print(f"   ⚠️  db_patch {table} #{val} failed (attempt {attempt}/{_retries}) — {last}", flush=True)
        if attempt < _retries:
            _t.sleep(2 * attempt)
    print(f"   ❌ db_patch GAVE UP on {table} #{val} after {_retries} attempts — {last} | columns: {list(data)}", flush=True)
    return False

def download_file(url, dest_path, desc="file"):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    r = requests.get(url, headers=headers, stream=True, timeout=120)
    if r.status_code == 200:
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest_path)//1024}KB", flush=True)
        return True
    print(f"   ❌ {desc} failed {r.status_code}: {url[:80]}", flush=True)
    return False

def storage_url(bucket, path):
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{path}"

# ── GCS upload ────────────────────────────────────────────────
def upload_to_gcs(local_path, gcs_path, content_type="video/mp4"):
    try:
        import base64, datetime as dt
        from google.oauth2 import service_account
        import google.auth.transport.requests as google_requests
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        creds_info  = json.loads(GCP_CREDS_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(google_requests.Request())

        print(f"   📤 Uploading {os.path.getsize(local_path)//(1024*1024)}MB to GCS...", flush=True)
        with open(local_path, "rb") as f:
            r = requests.post(
                f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o",
                params={"uploadType": "media", "name": gcs_path},
                headers={"Authorization": f"Bearer {credentials.token}", "Content-Type": content_type},
                data=f, timeout=600)
        if r.status_code not in (200, 201):
            print(f"   ❌ GCS upload failed {r.status_code}: {r.text[:200]}", flush=True)
            return None
        print(f"   ✅ GCS upload complete", flush=True)

        expiry_ts = int((dt.datetime.utcnow() + dt.timedelta(days=30)).timestamp())
        sts = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
        pk  = serialization.load_pem_private_key(
            creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
        sig = pk.sign(sts.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        esig = requests.utils.quote(base64.b64encode(sig).decode("utf-8"), safe="")
        signed = (f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
                  f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={esig}")
        print(f"   ✅ Signed URL generated (30 days)", flush=True)
        return signed
    except Exception as e:
        print(f"   ❌ GCS error: {e}", flush=True)
        return None

# ── Chirp 3: HD text-to-speech (AI master voice) ──────────────
def _google_access_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON), scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(gr.Request())
    return creds.token

def _clean_for_tts(text):
    import re
    t = re.sub(r"[*_#>`]", " ", text)        # strip markdown
    t = re.sub(r"\[[^\]]*\]", " ", t)        # strip [stage directions]
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _chunk_text(text, limit=4500):
    """Split into chunks under `limit` UTF-8 BYTES (not characters).
    Google TTS caps input at 5000 bytes. Tamil/Indic glyphs are ~3 bytes each,
    so a character-based limit silently busts the cap. We split on sentence
    boundaries, then on spaces, then hard-split at codepoint boundaries as a
    last resort (never mid-glyph). 4500 leaves headroom under the 5000 cap."""
    import re
    blen = lambda s: len(s.encode("utf-8"))

    def hard_split(s):
        out, cur = [], ""
        for ch in s:                       # codepoint-by-codepoint, never mid-glyph
            if blen(cur) + blen(ch) > limit and cur:
                out.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    sents = re.split(r"(?<=[.!?।])\s+", text)
    chunks, cur = [], ""
    for s in sents:
        if blen(s) > limit:                # a single sentence bigger than the cap
            if cur:
                chunks.append(cur.strip()); cur = ""
            piece = ""
            for w in s.split(" "):         # try splitting on spaces first
                if blen(piece) + blen(w) + 1 > limit and piece:
                    chunks.append(piece.strip()); piece = w
                else:
                    piece = f"{piece} {w}".strip()
            if piece:
                for hs in hard_split(piece):   # still too big → codepoint hard-split
                    chunks.append(hs.strip())
            continue
        if blen(cur) + blen(s) + 1 > limit and cur:
            chunks.append(cur.strip()); cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur.strip())
    return chunks or hard_split(text)

def synthesize_chirp3(script_text, voice_name, out_path, tmpdir):
    """Synthesize the master voiceover with a Google Chirp 3: HD voice.
    voice_name e.g. 'en-GB-Chirp3-HD-Charon'. Chirp 3 HD takes plain text only
    (no SSML) and no pitch/rate params. Long scripts are chunked + concatenated."""
    import base64
    lang_code = "-".join(voice_name.split("-")[:2])   # 'en-GB'
    token  = _google_access_token()
    chunks = _chunk_text(_clean_for_tts(script_text))
    print(f"   🎙  Chirp 3 HD voice: {voice_name} ({lang_code}) — {len(chunks)} chunk(s)", flush=True)
    part_files = []
    for i, chunk in enumerate(chunks):
        r = requests.post(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "input": {"text": chunk},
                "voice": {"languageCode": lang_code, "name": voice_name},
                "audioConfig": {"audioEncoding": "MP3"},
            }, timeout=120)
        if r.status_code != 200:
            print(f"   ❌ TTS chunk {i} failed {r.status_code}: {r.text[:200]}", flush=True)
            return False
        pf = os.path.join(tmpdir, f"tts_{i:03d}.mp3")
        with open(pf, "wb") as f:
            f.write(base64.b64decode(r.json()["audioContent"]))
        part_files.append(pf)
    if len(part_files) == 1:
        os.replace(part_files[0], out_path)
    else:
        listf = os.path.join(tmpdir, "tts_list.txt")
        with open(listf, "w") as f:
            for pf in part_files:
                f.write(f"file '{pf}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                        "-c:a", "libmp3lame", "-q:a", "2", out_path],
                       check=True, capture_output=True)
    if abs(VOICE_SPEED - 1.0) > 0.001:
        slowed = out_path + ".paced.mp3"
        subprocess.run(["ffmpeg", "-y", "-i", out_path, "-filter:a", f"atempo={VOICE_SPEED}",
                        "-c:a", "libmp3lame", "-q:a", "2", slowed], check=True, capture_output=True)
        os.replace(slowed, out_path)
        print(f"   🐢 Voice pace adjusted to {int(VOICE_SPEED*100)}% speed (pitch preserved)", flush=True)
    print(f"   ✅ Voice synthesized: {os.path.getsize(out_path)//1024}KB", flush=True)
    return True

def get_audio_duration(audio_path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path], capture_output=True, text=True)
    return float(r.stdout.strip())

# ══════════════════════════════════════════════════════════════
# CTC FORCED ALIGNMENT — the timing engine
# ══════════════════════════════════════════════════════════════
def run_ctc_alignment(audio_path, script_text, language, tmpdir):
    """
    Forced-align the EXACT script to the audio.
    Returns list of {word, start, end} — one per script word, in order.
    word_timestamps[i] is the real spoken time of script word i.
    Returns None if alignment fails (caller falls back to duration sync).
    """
    print(f"\n🎯 CTC forced alignment ({language})...", flush=True)
    script_words = script_text.split()
    if not script_words:
        return None

    # ── ONNX multilingual MMS aligner (HuggingFace) + uroman ─────
    # uroman romanizes ANY script (Tamil + English) for the multilingual
    # MMS acoustic model. This is true forced alignment of the exact script.
    try:
        import numpy as np
        import ctc_forced_aligner as cfa
        import onnxruntime as ort

        # 1. Download / cache the ONNX model
        cache_dir  = os.path.expanduser("~/.cache/ctc_model")
        os.makedirs(cache_dir, exist_ok=True)
        model_path = os.path.join(cache_dir, "ctc_aligner.onnx")
        cfa.ensure_onnx_model(model_path, cfa.MODEL_URL)
        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        print(f"   ✅ CTC model ready", flush=True)

        # 2. Load audio (librosa → 16kHz mono numpy; no torchcodec needed)
        audio = cfa.load_audio(audio_path, ret_type="np")
        print(f"   ✅ Audio loaded ({len(audio)/cfa.SAMPLING_FREQ:.0f}s)", flush=True)

        # 3. Emissions — returns (emissions[frames,vocab], stride_ms)
        emissions, stride = cfa.generate_emissions(session, audio, batch_size=4)
        print(f"   ✅ Emissions {emissions.shape}, stride {stride}ms", flush=True)

        # 4. Romanise each script word (one uroman token per word, count preserved)
        iso = "tam" if language == "ta" else "eng"
        uroman_toks = cfa.get_uroman_tokens(script_words, iso=iso)

        # 5. Forced align
        tokenizer = cfa.Tokenizer()
        segments, scores, blank = cfa.get_alignments(emissions, uroman_toks, tokenizer)
        spans = cfa.get_spans(uroman_toks, segments, blank)
        print(f"   ✅ Aligned {len(spans)} word spans", flush=True)

        # 6. Convert frame spans → seconds using stride (ms per frame)
        sec_per_frame = stride / 1000.0
        words = []
        for i, span in enumerate(spans):
            if not span or i >= len(script_words):
                prev = words[-1]["end"] if words else 0.0
                words.append({"word": script_words[i] if i < len(script_words) else "",
                              "start": prev, "end": prev})
                continue
            start = span[0].start * sec_per_frame
            end   = span[-1].end   * sec_per_frame
            words.append({"word": script_words[i], "start": float(start), "end": float(end)})

        # Ensure monotonic non-decreasing starts
        for i in range(1, len(words)):
            if words[i]["start"] < words[i-1]["start"]:
                words[i]["start"] = words[i-1]["start"]

        if words and len(words) >= len(script_words) * 0.8:
            print(f"   ✅ CTC aligned {len(words)} / {len(script_words)} words", flush=True)
            return words
        print(f"   ⚠️  Alignment thin ({len(words)}/{len(script_words)}) — duration sync", flush=True)
        return None

    except Exception as e:
        print(f"   ⚠️  CTC alignment failed: {e}", flush=True)
        import traceback; traceback.print_exc()
        print(f"   ⚠️  Falling back to duration sync", flush=True)
        return None

# ── Karaoke screens — exact word-timestamp lookup ────────────
def build_karaoke_screens(words, script_text, audio_duration, font_path=None):
    """
    Text always from the script. Lines are wrapped to FIT THE FRAME WIDTH
    (measured in pixels with the real caption font) so long Tamil words never
    run off the right edge. With CTC, each screen starts at the exact spoken
    time of its first word. Falls back to duration sync if CTC unavailable.
    """
    script_words = script_text.split()
    if not script_words:
        print("   ⚠️  No script text", flush=True)
        return []

    # measure with the actual caption font
    try:
        mfont = ImageFont.truetype(font_path, FONT_SIZE) if font_path else ImageFont.load_default()
    except Exception:
        mfont = ImageFont.load_default()
    measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    MAX_LINE_PX = WIDTH - 180        # ~90px margin each side

    def line_width(s):
        try:
            return measure.textlength(s, font=mfont)
        except Exception:
            b = measure.textbbox((0, 0), s, font=mfont); return b[2] - b[0]

    # wrap words into lines by pixel width; remember each line's first word index
    lines, cur, cur_idx = [], [], 0
    for i, w in enumerate(script_words):
        if cur and line_width(" ".join(cur + [w])) > MAX_LINE_PX:
            lines.append({"text": " ".join(cur), "idx": cur_idx})
            cur, cur_idx = [w], i
        else:
            if not cur:
                cur_idx = i
            cur.append(w)
    if cur:
        lines.append({"text": " ".join(cur), "idx": cur_idx})

    # group lines into MAX_LINES-line screens; screen starts at its first word
    script_screens = []
    for i in range(0, len(lines), MAX_LINES):
        block = lines[i:i+MAX_LINES]
        texts = [b["text"] for b in block]
        while len(texts) < MAX_LINES:
            texts.append("")
        script_screens.append({"texts": texts, "idx": block[0]["idx"]})

    if words and len(words) > 0:
        # CTC: each screen's start = spoken time of its first word
        n = len(words)
        screens = []
        for sc in script_screens:
            wi = min(sc["idx"], n - 1)
            screens.append({"start": words[wi]["start"], "end": audio_duration, "lines": sc["texts"]})
        for i in range(len(screens)-1):
            screens[i]["end"] = screens[i+1]["start"]
        for i in range(1, len(screens)):
            if screens[i]["start"] <= screens[i-1]["start"]:
                screens[i]["start"] = screens[i-1]["start"] + 1.0/FPS
            screens[i-1]["end"] = screens[i]["start"]
        print(f"   ✅ {len(screens)} screens — CTC exact word timing (width-wrapped)", flush=True)
        return screens

    # Fallback: duration sync
    print(f"   ℹ️  Duration-based sync (no CTC)", flush=True)
    usable = audio_duration - INTRO_DUR - OUTRO_DUR
    tps    = usable / max(len(script_screens), 1)
    screens = []
    for idx, sc in enumerate(script_screens):
        start = INTRO_DUR + idx * tps
        screens.append({"start": start, "end": start + tps, "lines": sc["texts"]})
    print(f"   ✅ {len(screens)} screens — duration sync", flush=True)
    return screens

# ── Image timeline — exact trigger lookup via script position ─
def build_image_timeline(episode_images, words, audio_duration, script_text=""):
    if not episode_images:
        return []
    import re as _re
    n = len(episode_images)
    script_words = script_text.split() if script_text else []

    def norm_word(w):
        return _re.sub(r"[^\w]", "", w.lower())
    norm_script = [norm_word(w) for w in script_words]

    def trigger_timestamp(trigger):
        """Find trigger phrase in script → exact word timestamp via CTC."""
        if not trigger or not words or not script_words:
            return None
        tw = [norm_word(w) for w in trigger.split() if norm_word(w)]
        if not tw:
            return None
        # Find the trigger word sequence in the script
        for i in range(len(norm_script) - len(tw) + 1):
            if norm_script[i:i+len(tw)] == tw:
                wi = min(i, len(words) - 1)
                ts = words[wi]["start"]
                print(f"   🎯 Image trigger '{trigger[:35]}' → {ts:.1f}s (exact)", flush=True)
                return ts
        # Try first 3 trigger words
        if len(tw) >= 3:
            for i in range(len(norm_script) - 3 + 1):
                if norm_script[i:i+3] == tw[:3]:
                    wi = min(i, len(words) - 1)
                    ts = words[wi]["start"]
                    print(f"   🎯 Image trigger (partial) '{trigger[:35]}' → {ts:.1f}s", flush=True)
                    return ts
        print(f"   ⚠️  Trigger not found in script: '{trigger[:45]}'", flush=True)
        return None

    timeline = []
    for i, img in enumerate(episode_images):
        trigger = img.get("trigger", "").strip()
        if i == 0:
            start = 0.0
        else:
            ts = trigger_timestamp(trigger)
            if ts is None:
                start = round((audio_duration / n) * i, 3)
                print(f"   Info: Image {i+1} equal-spacing fallback {start:.2f}s", flush=True)
            else:
                start = ts
        timeline.append({"url": img.get("url",""), "local_path": img.get("local_path",""),
                         "start": start, "end": audio_duration, "trigger": trigger,
                         "order": img.get("order", i+1)})
    for i in range(len(timeline)-1):
        timeline[i]["end"] = timeline[i+1]["start"]
    # guard inversions (triggers out of script order)
    for i in range(1, len(timeline)):
        if timeline[i]["start"] <= timeline[i-1]["start"]:
            timeline[i]["start"] = timeline[i-1]["start"] + 1.0
        timeline[i-1]["end"] = timeline[i]["start"]
    print(f"\n   Image timeline:", flush=True)
    for t in timeline:
        print(f"      Image {t['order']}: {t['start']:.1f}s → {t['end']:.1f}s ({t['end']-t['start']:.1f}s)", flush=True)
    return timeline

# ── Pillow: preprocess image (blurred bg + sharp + dark bar) ──
def preprocess_base_image(src_path):
    img = Image.open(src_path).convert("RGB")
    iw, ih = img.size
    if not BURN_CAPTIONS:
        # Text-free master: image fills the whole frame, no caption bar.
        canvas = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
        bg = img.copy().resize((WIDTH, HEIGHT), Image.LANCZOS).filter(ImageFilter.GaussianBlur(radius=25))
        canvas.paste(bg, (0, 0))
        scale = min(WIDTH / iw, HEIGHT / ih)
        nw, nh = int(iw*scale), int(ih*scale)
        canvas.paste(img.resize((nw, nh), Image.LANCZOS), ((WIDTH-nw)//2, (HEIGHT-nh)//2))
        return canvas
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    bg = img.copy().resize((WIDTH, LOWER_TOP), Image.LANCZOS).filter(ImageFilter.GaussianBlur(radius=25))
    canvas.paste(bg, (0, 0))
    scale = min(WIDTH / iw, LOWER_TOP / ih)
    nw, nh = int(iw*scale), int(ih*scale)
    canvas.paste(img.resize((nw, nh), Image.LANCZOS), ((WIDTH-nw)//2, (LOWER_TOP-nh)//2))
    ImageDraw.Draw(canvas).rectangle([(0, LOWER_TOP), (WIDTH, HEIGHT)], fill=(15, 15, 15))
    return canvas

def build_frames_no_caption(image_timeline, tmpdir):
    """Text-free frames: one full-frame image per timeline segment, exact image
    switches at the trigger timestamps, no on-screen words."""
    print(f"\n🖼️  Building text-free frames ({len(image_timeline)} images)...", flush=True)
    base_images = {}
    for item in sorted(image_timeline, key=lambda x: x["order"]):
        if item["local_path"] not in base_images:
            base_images[item["local_path"]] = preprocess_base_image(item["local_path"])
    frames = []
    for i, item in enumerate(image_timeline):
        dur = max(round(item["end"] - item["start"], 4), 1.0/FPS)
        p = os.path.join(tmpdir, f"frame_{i:05d}.jpg")
        base_images[item["local_path"]].save(p, "JPEG", quality=92)
        frames.append((p, dur))
    concat_path = os.path.join(tmpdir, "frames_concat.txt")
    with open(concat_path, "w") as f:
        for fp, dur in frames:
            f.write(f"file '{fp}'\nduration {dur:.4f}\n")
        if frames:
            f.write(f"file '{frames[-1][0]}'\n")
    print(f"   ✅ {len(frames)} frames built ({sum(d for _,d in frames):.1f}s)", flush=True)
    return concat_path

def _srt_ts(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60); ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def build_srt(words, out_path, max_words=8, max_dur=5.0):
    """Build an accurate .srt from the CTC word timestamps (the same data that
    used to drive the karaoke). Grouped into short readable cues."""
    if not words:
        return False
    cues, cur = [], []
    for w in words:
        cur.append(w)
        span = cur[-1].get("end", cur[-1]["start"]) - cur[0]["start"]
        if len(cur) >= max_words or span >= max_dur:
            cues.append(cur); cur = []
    if cur:
        cues.append(cur)
    with open(out_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            start = c[0]["start"]; end = c[-1].get("end", c[-1]["start"] + 0.4)
            text = " ".join(x.get("word", x.get("text", "")) for x in c).strip()
            f.write(f"{i}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n\n")
    return True

def save_frame(base_img, text_lines, font, output_path):
    frame = base_img.copy()
    draw  = ImageDraw.Draw(frame)
    for li, line in enumerate(text_lines):
        if not line.strip():
            continue
        y = LOWER_TOP + 20 + li * LINE_HEIGHT
        try:
            bbox = draw.textbbox((0, 0), line, font=font); tw = bbox[2]-bbox[0]
        except Exception:
            tw = len(line) * (FONT_SIZE // 2)
        x = max(20, (WIDTH - tw)//2)
        draw.text((x+2, y+2), line, font=font, fill=(0,0,0))
        draw.text((x, y), line, font=font, fill=(255,255,255))
    frame.save(output_path, "JPEG", quality=92)

def build_frame_sequence(image_timeline, screens, font_path, tmpdir):
    print(f"\n🖼️  Building composited frames ({len(screens)} screens)...", flush=True)
    try:
        font = ImageFont.truetype(font_path, FONT_SIZE)
    except Exception as e:
        print(f"   ⚠️  Font load failed ({e}) — default", flush=True)
        font = ImageFont.load_default()

    base_images = {}
    for item in sorted(image_timeline, key=lambda x: x["order"]):
        if item["local_path"] not in base_images:
            print(f"   Preprocessing image {item['order']}...", flush=True)
            base_images[item["local_path"]] = preprocess_base_image(item["local_path"])

    audio_duration = image_timeline[-1]["end"]
    def active_base(t):
        for item in image_timeline:
            if item["start"] <= t < item["end"]:
                return base_images[item["local_path"]]
        return base_images[image_timeline[-1]["local_path"]]

    frames, idx, cursor, blank = [], 0, 0.0, ["", "", ""]
    for screen in screens:
        s, e = screen["start"], screen["end"]
        if s > cursor + 0.01:
            p = os.path.join(tmpdir, f"frame_{idx:05d}.jpg")
            save_frame(active_base(cursor), blank, font, p)
            frames.append((p, round(s - cursor, 4))); idx += 1; cursor = s
        p = os.path.join(tmpdir, f"frame_{idx:05d}.jpg")
        save_frame(active_base(s), screen["lines"], font, p)
        frames.append((p, max(round(e - s, 4), 1.0/FPS))); idx += 1; cursor = e
    if cursor < audio_duration - 0.01:
        p = os.path.join(tmpdir, f"frame_{idx:05d}.jpg")
        save_frame(active_base(cursor), blank, font, p)
        frames.append((p, round(audio_duration - cursor, 4)))

    concat_path = os.path.join(tmpdir, "frames_concat.txt")
    with open(concat_path, "w") as f:
        for fp, dur in frames:
            f.write(f"file '{fp}'\nduration {dur:.4f}\n")
        if frames:
            f.write(f"file '{frames[-1][0]}'\n")
    print(f"   ✅ {len(frames)} frames built ({sum(d for _,d in frames):.1f}s)", flush=True)
    return concat_path

def make_circle_photo(input_path, output_path, size):
    subprocess.run(["ffmpeg", "-y", "-i", input_path,
        "-vf", (f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size},"
                f"format=yuva420p,geq=lum='p(X,Y)':a='if(gt(pow(X-{size//2},2)+pow(Y-{size//2},2),pow({size//2},2)),0,255)'"),
        "-frames:v", "1", output_path], capture_output=True)
    return os.path.exists(output_path)

# ── FFmpeg render (single stream, text burned in) ────────────
def render_video(frames_concat_path, audio_path, music_path, intro_path, outro_path,
                 photo_path, logo_path, audio_duration, output_path):
    print(f"\n🎬 FFmpeg render (veryfast)...", flush=True)
    inputs = ["-f", "concat", "-safe", "0", "-i", frames_concat_path]
    nxt = 1
    inputs += ["-i", audio_path]; audio_idx = nxt; nxt += 1
    inputs += ["-i", music_path]; music_idx = nxt; nxt += 1
    photo_idx = None
    if photo_path and os.path.exists(photo_path):
        inputs += ["-i", photo_path]; photo_idx = nxt; nxt += 1
    logo_idx = None
    if logo_path and os.path.exists(logo_path):
        inputs += ["-i", logo_path]; logo_idx = nxt; nxt += 1
    inputs += ["-loop", "1", "-t", str(INTRO_DUR+FADE_DUR), "-i", intro_path]; intro_idx = nxt; nxt += 1
    inputs += ["-loop", "1", "-t", str(OUTRO_DUR+FADE_DUR), "-i", outro_path]; outro_idx = nxt

    vf = f"[0:v]setsar=1,fps={FPS}[bg_main];"
    vout = "[bg_main]"
    if photo_idx is not None:
        vf += (f"[{photo_idx}:v]scale={PHOTO_SIZE}:{PHOTO_SIZE},format=yuva420p[ph];"
               f"[{vout[1:-1]}][ph]overlay={PHOTO_X}:{PHOTO_Y}:format=auto[bg_ph];")
        vout = "[bg_ph]"
    if logo_idx is not None:
        vf += (f"[{logo_idx}:v]scale={LOGO_SIZE}:{LOGO_SIZE},format=yuva420p[lo];"
               f"[{vout[1:-1]}][lo]overlay={LOGO_X}:{LOGO_Y}:format=auto[bg_lo];")
        vout = "[bg_lo]"
    vf += (f"[{intro_idx}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
           f"crop={WIDTH}:{HEIGHT},fade=t=out:st={INTRO_DUR-FADE_DUR}:d={FADE_DUR},setsar=1[intro_v];"
           f"[{outro_idx}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
           f"crop={WIDTH}:{HEIGHT},fade=t=in:st=0:d={FADE_DUR},setsar=1[outro_v];"
           f"[intro_v]{vout}[outro_v]concat=n=3:v=1:a=0[video_out];")
    total = INTRO_DUR + audio_duration + OUTRO_DUR
    vf += (f"[{audio_idx}:a]adelay={int(INTRO_DUR*1000)}|{int(INTRO_DUR*1000)}[vd];"
           f"[{music_idx}:a]aloop=loop=-1:size=2e+09,volume={MUSIC_VOL},atrim=duration={total}[ml];"
           f"[vd][ml]amix=inputs=2:duration=first[audio_out];")

    cmd = (["ffmpeg", "-y"] + inputs + [
        "-filter_complex", vf, "-map", "[video_out]", "-map", "[audio_out]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-pix_fmt", "yuv420p",
        output_path])
    print("   Running FFmpeg...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ❌ FFmpeg failed:\n{r.stderr[-3000:]}", flush=True)
        return False
    print(f"   ✅ Video: {os.path.getsize(output_path)/1024/1024:.1f}MB", flush=True)
    return True

# ── Main ──────────────────────────────────────────────────────
def main():
    def log(m): print(m, flush=True)
    log("="*60)
    log(f"🎬 Video Generator (CTC) — Episode {EPISODE_NUMBER} | {LANGUAGE.upper()}")
    log(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("="*60)

    table   = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
    ep_rows = db_get(table, {"episode_number": f"eq.{EPISODE_NUMBER}", "select": "*"})
    episode = ep_rows[0] if ep_rows else None
    if not episode:
        log(f"❌ Episode {EPISODE_NUMBER} not found in {table}"); return
    log(f"   ✅ {episode.get('title_english') or episode.get('title_tamil')}")
    db_patch(table, EPISODE_NUMBER, {"status": "generating_video"})

    with tempfile.TemporaryDirectory() as tmpdir:
        lang_code = "ta" if LANGUAGE == "ta" else "en"
        if LANGUAGE == "ta":
            font_path = "/usr/share/fonts/opentype/noto/NotoSansTamil-Regular.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            if not os.path.exists(font_path):
                font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        log(f"\n🔤 Font: {font_path} ({'found' if os.path.exists(font_path) else 'NOT FOUND'})")

        # Voice: AI master voice (Chirp 3 HD) if a voice is selected, else legacy upload
        tts_voice   = (episode.get("tts_voice") or "").strip()
        script_col  = "script_tamil" if LANGUAGE == "ta" else "script_english"
        script_text = episode.get(script_col, "") or ""
        voice_path  = os.path.join(tmpdir, "voice.mp3")
        if tts_voice:
            log(f"\n🎤 Step 1/8 — Synthesizing AI voice ({tts_voice})...")
            if not script_text.strip():
                log("❌ No script for TTS"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
            if not synthesize_chirp3(script_text, tts_voice, voice_path, tmpdir):
                db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        else:
            voice_url = episode.get("voice_url")
            if not voice_url:
                log("❌ No voice (no tts_voice selected and no uploaded voice_url)")
                db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
            log(f"\n🎤 Step 1/8 — Voice (uploaded)...")
            if not download_file(voice_url, voice_path, "Voice"):
                db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        audio_duration = get_audio_duration(voice_path)
        log(f"   ✅ Duration: {audio_duration:.1f}s ({audio_duration/60:.1f} min)")
        log(f"   ✅ Script: {len(script_text.split())} words")

        log(f"\n🎯 Step 2/8 — CTC forced alignment (downloads model first run)...")
        words = run_ctc_alignment(voice_path, script_text, LANGUAGE, tmpdir)
        log(f"   ✅ Alignment done — {datetime.now().strftime('%H:%M:%S')}")

        if BURN_CAPTIONS:
            log(f"\n📝 Step 3/8 — Karaoke screens...")
            screens = build_karaoke_screens(words, script_text, audio_duration, font_path)
        else:
            screens = None
            log(f"\n📝 Step 3/8 — Caption (.srt) from alignment (no burned-in text)...")
            srt_path = os.path.join(tmpdir, "captions.srt")
            if build_srt(words, srt_path):
                cap_lang = "ta" if LANGUAGE == "ta" else "en"
                cap_url = upload_to_gcs(srt_path, f"episodes/ep{EPISODE_NUMBER:03d}/{cap_lang}/captions.srt",
                                        content_type="application/x-subrip")
                if cap_url:
                    db_patch(table, EPISODE_NUMBER, {"captions_url": cap_url})
                    log("   ✅ Caption file stored — uploaded later as the video's subtitle track")

        raw = episode.get("episode_images") or []
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except: raw = []
        if not raw:
            log("❌ No images"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        log(f"\n📸 Step 4/8 — {len(raw)} images...")
        image_paths = []
        for img in sorted(raw, key=lambda x: x.get("order", 0)):
            dest = os.path.join(tmpdir, f"raw_{img.get('order',len(image_paths)+1)}.jpg")
            if download_file(img["url"], dest, f"Image {img.get('order','')}"):
                image_paths.append({**img, "local_path": dest})
        if not image_paths:
            log("❌ No images downloaded"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return

        image_timeline = build_image_timeline(image_paths, words or [], audio_duration, script_text)

        log(f"\n🖼️  Step 5/8 — Composited frames...")
        if BURN_CAPTIONS:
            frames_concat = build_frame_sequence(image_timeline, screens, font_path, tmpdir)
        else:
            frames_concat = build_frames_no_caption(image_timeline, tmpdir)
        log(f"   ✅ Frames ready — {datetime.now().strftime('%H:%M:%S')}")

        log(f"\n🖼️  Step 6/8 — Channel assets...")
        intro_path = os.path.join(tmpdir, "intro.png"); outro_path = os.path.join(tmpdir, "outro.png")
        download_file(episode.get("intro_image_url") or storage_url("channel-assets","default_intro.png"), intro_path, "Intro")
        download_file(episode.get("outro_image_url") or storage_url("channel-assets","default_outro.png"), outro_path, "Outro")
        # Narrator photo disabled — content is AI-narrated, no human presenter shown.
        # render_video skips the overlay when photo_final is None.
        photo_final = None
        logo_path = os.path.join(tmpdir, "logo.png"); logo_final = None
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests as gr
            ci = json.loads(GCP_CREDS_JSON)
            lc = service_account.Credentials.from_service_account_info(ci, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            lc.refresh(gr.Request())
            r = requests.get(f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/ihaveacause_logo.png?alt=media",
                headers={"Authorization": f"Bearer {lc.token}"}, timeout=15)
            if r.status_code == 200:
                with open(logo_path, "wb") as f: f.write(r.content)
                logo_final = logo_path; log(f"   ✅ Logo: {len(r.content)//1024}KB")
            else:
                log(f"   ⚠️  Logo failed {r.status_code}")
        except Exception as e:
            log(f"   ⚠️  Logo error: {e}")

        log(f"\n🎵 Step 7/8 — Music...")
        music_path = os.path.join(tmpdir, "music.mp3")
        if not download_file(storage_url("episode-music","background.mp3"), music_path, "Music"):
            subprocess.run(["ffmpeg","-y","-f","lavfi","-i","anullsrc=r=44100:cl=stereo","-t","1",music_path], capture_output=True)

        log(f"\n🎬 Step 8/8 — Render...")
        log(f"   Started: {datetime.now().strftime('%H:%M:%S')}")
        output_path = os.path.join(tmpdir, f"ep{EPISODE_NUMBER:03d}_{lang_code}.mp4")
        if not render_video(frames_concat, voice_path, music_path, intro_path, outro_path,
                            photo_final, logo_final, audio_duration, output_path):
            log("❌ Render failed"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        log(f"   ✅ Render done — {datetime.now().strftime('%H:%M:%S')}")

        log(f"\n☁️  Uploading...")
        video_url = upload_to_gcs(output_path, f"episodes/ep{EPISODE_NUMBER:03d}/{lang_code}/final.mp4")
        if not video_url:
            log("❌ Upload failed"); db_patch(table, EPISODE_NUMBER, {"status": "voice_approved"}); return
        db_patch(table, EPISODE_NUMBER, {"video_url": video_url, "status": "video_ready"})
        log(f"\n{'='*60}")
        log(f"✅ Episode {EPISODE_NUMBER} {LANGUAGE.upper()} — video ready!")
        log(f"   Finished: {datetime.now().strftime('%H:%M:%S')}")
        log(f"{'='*60}")

if __name__ == "__main__":
    main()
