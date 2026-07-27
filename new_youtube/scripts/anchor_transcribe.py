"""
anchor_transcribe.py — On Camera (Studio Desk) Pipeline · Step 1 of 3
=====================================================================
Takes ONE uploaded recording and produces: a transcript, word-level
timings, the detected language, and 3 title suggestions for you to pick.

WHY WHISPER (not CTC here):
  For the script-driven episode pipeline we use CTC forced alignment, because
  there we have the EXACT script ahead of time. Here you are speaking — often
  freely — so there is no pre-written text to align. Whisper transcribes what
  you actually said AND returns word-level timestamps in one pass, and
  auto-detects Tamil vs English. Those timestamps are what later let headlines
  and lower-thirds land on the right words.

  ('script' mode — where you read script_text aloud — still uses Whisper here;
   its word timings are more than accurate enough to place graphics. CTC stays
   available as a future tightening if you ever want sub-100ms placement.)

FLOW:
  download recording  ->  ffmpeg extract 16k mono wav  ->  Whisper (transcript
  + word timings + language)  ->  Claude suggests 3 titles  ->  write row,
  status = 'transcribed'.  You then pick a title in the dashboard.

Env vars:
  SUPABASE_URL, SUPABASE_KEY
  ANTHROPIC_API_KEY
  RECORD_ID   (uuid of the row)
  LANGUAGE    (ta | en  — which table/lane this recording belongs to)
  WHISPER_MODEL (optional, default 'medium' — 'small' struggles noticeably on
                 Tamil and is prone to repetition-loop hallucinations; 'large-v3'
                 is more accurate still but much slower on a CPU runner)
"""

import os
import re
import json
import subprocess
import tempfile
from datetime import datetime

import requests
import anthropic

# ── Config ────────────────────────────────────────────────────
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
GCP_CREDS_JSON = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")  # to read the GCS recording
RECORD_ID     = os.environ["RECORD_ID"]
LANGUAGE      = os.environ.get("LANGUAGE", "en")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
GCS_BUCKET    = "ihaveacause-media"

CLAUDE_MODEL  = "claude-sonnet-4-6"
LANG_NAME     = {"ta": "Tamil", "en": "English"}.get(LANGUAGE, "English")
TABLE         = "tamil_anchor" if LANGUAGE == "ta" else "english_anchor"

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Supabase helpers (REST, same pattern as the rest of the repo) ─────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json", "Prefer": "return=minimal",
}
REST = f"{SUPABASE_URL}/rest/v1"

def db_get_one(record_id):
    r = requests.get(f"{REST}/{TABLE}",
                     headers={**SB_HEADERS, "Prefer": "return=representation"},
                     params={"id": f"eq.{record_id}", "select": "*"}, timeout=15)
    rows = r.json() if r.status_code == 200 else []
    return rows[0] if rows else None

def db_patch(record_id, data):
    r = requests.patch(f"{REST}/{TABLE}?id=eq.{record_id}",
                       headers=SB_HEADERS, json=data, timeout=30)
    if r.status_code not in (200, 204):
        print(f"   ❌ Supabase patch {r.status_code}: {r.text[:200]}", flush=True)
    return r.status_code in (200, 204)

def _sa_token(scope="https://www.googleapis.com/auth/devstorage.read_only"):
    """Service-account bearer token, for reading the recording from GCS."""
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON), scopes=[scope])
    creds.refresh(gr.Request())
    return creds.token

def _gcs_object_media_url(url):
    """If url is an UNSIGNED GCS object URL (https://storage.googleapis.com/<bucket>/<path>),
    return (True, authenticated_media_url). Otherwise (False, url)."""
    from urllib.parse import quote
    marker = "storage.googleapis.com/"
    if marker in url and "Signature=" not in url and "X-Goog-Signature" not in url \
       and "/storage/v1/b/" not in url and "/upload/storage/" not in url:
        rest = url.split(marker, 1)[1]
        if "/" in rest:
            bucket, path = rest.split("/", 1)
            return True, f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(path, safe='')}?alt=media"
    return False, url

def download_file(url, dest, desc="file"):
    """Download a recording. Reads unsigned GCS objects with the service account;
    otherwise fetches the URL directly (signed URLs, public URLs)."""
    is_gcs, fetch_url = _gcs_object_media_url(url)
    if is_gcs:
        if not GCP_CREDS_JSON:
            print(f"   ❌ {desc}: GCS object but GOOGLE_APPLICATION_CREDENTIALS_JSON not set", flush=True)
            return False
        headers = {"Authorization": f"Bearer {_sa_token()}"}
        r = requests.get(fetch_url, headers=headers, stream=True, timeout=600)
    else:
        r = requests.get(url, stream=True, timeout=600)
    if r.status_code == 200:
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        print(f"   ✅ {desc}: {os.path.getsize(dest)//1024}KB", flush=True)
        return True
    print(f"   ❌ {desc} failed {r.status_code}", flush=True)
    return False

# ── Claude JSON parsing (mirrors generate_images.parse_json) ──────────────────
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

# ── Audio extraction ──────────────────────────────────────────
def extract_audio(video_path, wav_path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path],
        check=True, capture_output=True)
    return os.path.exists(wav_path)

# ── Whisper transcription with word timestamps ────────────────
def dedupe_repeats(words, max_repeat=3):
    """Collapse a run of the same word repeated more than max_repeat times in a
    row down to max_repeat — Whisper's classic hallucination failure mode is
    getting stuck looping one syllable/word (e.g. 'விட்டுவிட்டுவிட்டு...'
    dozens of times), usually triggered by a rough patch of audio (noise, a
    long pause, or the mic clipping). This doesn't fix the source audio, but
    it stops that loop from wrecking the whole transcript, title suggestions,
    and beat planning that all read off of this text."""
    out, run = [], []
    def flush():
        if not run:
            return
        if len(run) > max_repeat:
            print(f"   ⚠️  Collapsed a {len(run)}x repeat of "
                  f"'{run[0]['word']}' — check that stretch of audio "
                  f"({run[0]['start']:.1f}s–{run[-1]['end']:.1f}s), it's likely noisy/unclear", flush=True)
            out.extend(run[:max_repeat])
        else:
            out.extend(run)
        run.clear()
    for w in words:
        if run and _norm(run[-1]["word"]) == _norm(w["word"]):
            run.append(w)
        else:
            flush()
            run.append(w)
    flush()
    return out

def _norm(w):
    return re.sub(r"[^\w]", "", w.lower())

def transcribe(wav_path):
    """faster-whisper → (transcript_text, [{word,start,end}], detected_lang)."""
    from faster_whisper import WhisperModel
    print(f"   🧠 Loading faster-whisper '{WHISPER_MODEL}' (CPU int8)...", flush=True)
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    # Pin the language instead of auto-detecting: for Tamil/English-mixed
    # commentary, auto-detect can flip mid-clip and drag the decoder into a
    # bad state — that's often exactly when repetition-loop hallucinations
    # start. LANGUAGE is already known (it's how you uploaded this recording).
    whisper_lang = LANGUAGE if LANGUAGE in ("ta", "en") else None
    segments, info = model.transcribe(
        wav_path, language=whisper_lang, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,   # stop one bad segment's errors from compounding into later ones
        compression_ratio_threshold=2.2,    # flag/drop segments that look like repetition loops
        no_repeat_ngram_size=3,             # discourage the decoder from looping short phrases
    )
    detected = info.language
    print(f"   🌐 Language: {detected} (p={info.language_probability:.2f})"
          f"{' [pinned]' if whisper_lang else ' [auto-detected]'}", flush=True)

    words, parts = [], []
    for seg in segments:
        parts.append(seg.text.strip())
        for w in (seg.words or []):
            tok = w.word.strip()
            if tok:
                words.append({"word": tok, "start": round(w.start, 3), "end": round(w.end, 3)})
    words = dedupe_repeats(words)
    transcript = " ".join(w["word"] for w in words).strip()
    print(f"   ✅ Transcript: {len(transcript.split())} words, {len(words)} timed tokens", flush=True)
    return transcript, words, detected

# ── Claude: 3 title suggestions (2 punchy + 1 descriptive) ────
def suggest_titles(transcript):
    print(f"\n🏷️  Claude suggesting 3 titles ({LANG_NAME})...", flush=True)
    prompt = f"""You are titling ONE episode of a {LANG_NAME} opinion / commentary
video where the host speaks to camera. Below is the transcript of what the host
actually said.

Propose exactly THREE YouTube titles, as a JSON array of 3 objects, each:
  "text"  : the title, in {LANG_NAME}, under ~70 characters, no surrounding quotes
  "style" : either "punchy" or "descriptive"

Rules:
- Return TWO "punchy" titles (click-earning, a little provocative, curiosity-driven)
  and ONE "descriptive" title (a clean, accurate summary of the content).
- Base every title strictly on what was actually said. Invent no facts.
- No clickbait that misrepresents the content. No ALL CAPS. No emoji.

TRANSCRIPT:
{transcript[:6000]}

Return ONLY the JSON array. No prose, no markdown."""

    last_err = None
    for attempt in range(3):
        extra = "" if attempt == 0 else (
            "\n\nIMPORTANT: your previous reply was NOT valid JSON. Return ONLY a "
            "valid JSON array of 3 objects with keys \"text\" and \"style\".")
        msg = claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=1000,
            messages=[{"role": "user", "content": prompt + extra}])
        try:
            arr = parse_json(msg.content[0].text)
            # Defensive against Claude occasionally wrapping the array one level
            # deeper (e.g. [[{...},{...},{...}]]) or keying it under a field
            # (e.g. {"titles": [...]}) — normalize back to a flat list of dicts.
            if isinstance(arr, dict):
                arr = next((v for v in arr.values() if isinstance(v, list)), [])
            while isinstance(arr, list) and len(arr) == 1 and isinstance(arr[0], list):
                arr = arr[0]
            arr = [o for o in arr if isinstance(o, dict)] if isinstance(arr, list) else []
            out = []
            for o in arr[:3]:
                t = str(o.get("text", "")).strip().strip('"')
                s = str(o.get("style", "punchy")).strip().lower()
                if t:
                    out.append({"text": t, "style": "descriptive" if s == "descriptive" else "punchy"})
            if out:
                for o in out:
                    print(f"      • [{o['style']:>11}] {o['text']}", flush=True)
                return out
            raise ValueError("no titles parsed")
        except Exception as e:
            last_err = e
            print(f"   ⚠️  Title JSON invalid (attempt {attempt+1}/3): {e} — retrying...", flush=True)
    print(f"   ⚠️  Title suggestion failed ({last_err}); leaving empty for manual entry", flush=True)
    return []

# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 60, flush=True)
    print(f"🎥 Anchor Transcribe — {RECORD_ID} | {LANGUAGE.upper()}", flush=True)
    print(f"   {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    print("=" * 60, flush=True)

    row = db_get_one(RECORD_ID)
    if not row:
        print(f"❌ Record {RECORD_ID} not found in {TABLE}"); return
    video_url = row.get("source_video_url")
    if not video_url:
        print("❌ No source_video_url on this record"); return

    db_patch(RECORD_ID, {"status": "transcribing"})

    with tempfile.TemporaryDirectory() as tmp:
        video_path = os.path.join(tmp, "source.mp4")
        wav_path   = os.path.join(tmp, "audio.wav")

        print("\n⬇️  Step 1/3 — Downloading recording...", flush=True)
        if not download_file(video_url, video_path, "Recording"):
            db_patch(RECORD_ID, {"status": "pending"}); return

        print("\n🎚️  Step 2/3 — Extracting audio + transcribing...", flush=True)
        if not extract_audio(video_path, wav_path):
            print("❌ Audio extraction failed"); db_patch(RECORD_ID, {"status": "pending"}); return
        try:
            transcript, words, detected = transcribe(wav_path)
        except Exception as e:
            print(f"❌ Transcription failed: {e}")
            import traceback; traceback.print_exc()
            db_patch(RECORD_ID, {"status": "pending"}); return
        if not transcript.strip():
            print("❌ Empty transcript"); db_patch(RECORD_ID, {"status": "pending"}); return

        print("\n🏷️  Step 3/3 — Title suggestions...", flush=True)
        titles = suggest_titles(transcript)

        db_patch(RECORD_ID, {
            "transcript":        transcript,
            "word_timings":      words,
            "detected_lang":     detected,
            "title_suggestions": titles,
            "status":            "transcribed",
        })

    print(f"\n{'='*60}", flush=True)
    print(f"✅ Transcribed — pick a title in the dashboard, then run Beats.", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
