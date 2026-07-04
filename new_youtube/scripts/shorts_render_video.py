"""
shorts_render_video.py — Sprint 15 (Shorts track)
===================================================
Renders the final 1080x1920 vertical video for ONE short (by id), all motion
built with FREE local processing — no paid video-generation API, no added
per-short billing beyond what the pipeline already spends on TTS + Imagen:

- AI voice via Google Cloud TTS Chirp 3: HD — uses the SAME voice
  (episode.tts_voice) the parent long episode was rendered with, so the
  short sounds identical to the long video, not a different default voice.
- Each image gets a Ken Burns zoom/pan (direction alternates per image).
- Where a clear subject can be segmented (via rembg, running fully locally —
  no API call, no cost), the subject and background pan at different rates
  for a fake-depth parallax effect. Falls back to plain zoompan for any
  image where segmentation fails, is unavailable, or looks degenerate —
  this NEVER blocks or fails the render.
- The hook line fades onto screen over the first ~4s.
- Saves video_url, sets status -> video_ready

Chirp 3: HD voices do NOT accept speakingRate/pitch/SSML in the request —
sending them causes a 400 Bad Request, so audioConfig only sets audioEncoding.

Triggered by: shorts_render_video.yml
Env vars: SUPABASE_URL, SUPABASE_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON,
          SHORT_ID, LANGUAGE (ta or en)
"""

import os
import json
import base64
import tempfile
import subprocess
import requests
import random

CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
creds_path = "/tmp/gcp_creds.json"
with open(creds_path, "w") as f:
    f.write(CREDS_JSON)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials as SACreds

# PIL is used both for rembg's subject cutouts AND for measuring text width to
# wrap the hook overlay correctly — kept independent of rembg's availability so
# a rembg failure never also silently breaks text wrapping.
try:
    from PIL import Image, ImageFont
    PIL_AVAILABLE = True
except Exception as e:
    print(f"  ℹ️  PIL not available ({e}) — hook text will use approximate wrapping")
    PIL_AVAILABLE = False

# rembg is optional: if it's missing, fails to import, or its model download
# fails at runtime, parallax is simply skipped for the whole render (falls
# back to plain zoompan on every image) — never breaks the pipeline.
try:
    from rembg import remove as rembg_remove, new_session as rembg_new_session
    import numpy as np
    REMBG_AVAILABLE = True
except Exception as e:
    print(f"  ℹ️  rembg not available ({e}) — parallax disabled, using plain zoompan for all images")
    REMBG_AVAILABLE = False

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SHORT_ID     = os.environ["SHORT_ID"]
LANGUAGE     = os.environ.get("LANGUAGE", "ta")

SHORTS_TABLE  = "tamil_shorts"   if LANGUAGE == "ta" else "english_shorts"
EPISODE_TABLE = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
GCS_BUCKET = "ihaveacause-media"

# Fallback ONLY if the parent episode never had a voice picked/approved.
# Must match DEFAULT_VOICE_TA / DEFAULT_VOICE in index.html.
DEFAULT_VOICE = {
    "ta": "ta-IN-Chirp3-HD-Callirrhoe",
    "en": "en-GB-Chirp3-HD-Charon",
}

def sb_get(table, params):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB_HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def sb_get_one(table, params):
    data = sb_get(table, params)
    if not data:
        raise ValueError(f"No row found in {table} with {params}")
    return data[0]

def sb_patch(table, id_, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{id_}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json=data,
    )
    r.raise_for_status()

def get_gcs_token():
    creds = SACreds.from_service_account_file(creds_path, scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    creds.refresh(Request())
    return creds.token

def upload_to_gcs(local_path, gcs_path):
    """Uploads to GCS and returns a 30-day V2 signed URL — NOT a plain public
    URL. The bucket is private (confirmed: a plain storage.googleapis.com/...
    URL returns AccessDenied to an unauthenticated browser), so every other
    working pipeline in this repo (generate_video.py, generate_images.py,
    anchor_render.py, etc.) signs its GCS URLs instead of relying on public
    ACLs. This mirrors that exact, already-proven pattern.
    """
    import base64
    import datetime as dt
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    token = get_gcs_token()
    with open(local_path, "rb") as f:
        data = f.read()
    # GCS JSON API simple upload requires POST — the /upload/storage/v1/b/.../o
    # endpoint has no PUT route mapped, which is why the wrong verb 404s instead
    # of failing auth or bucket-lookup: it never gets that far.
    r = requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{GCS_BUCKET}/o?uploadType=media&name={gcs_path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
        data=data,
    )
    if r.status_code != 200:
        print(f"  ❌ GCS upload error {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    print(f"  ✅ GCS upload complete")

    creds_info = json.loads(CREDS_JSON)
    expiry_ts = int((dt.datetime.utcnow() + dt.timedelta(days=30)).timestamp())
    sts = "\n".join(["GET", "", "", str(expiry_ts), f"/{GCS_BUCKET}/{gcs_path}"])
    pk = serialization.load_pem_private_key(
        creds_info["private_key"].encode("utf-8"), password=None, backend=default_backend())
    sig = pk.sign(sts.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    esig = requests.utils.quote(base64.b64encode(sig).decode("utf-8"), safe="")
    signed_url = (
        f"https://storage.googleapis.com/{GCS_BUCKET}/{gcs_path}"
        f"?GoogleAccessId={creds_info['client_email']}&Expires={expiry_ts}&Signature={esig}"
    )
    print(f"  ✅ Signed URL generated (30 days)")
    return signed_url

def get_episode_voice(episode_number):
    """Reads the exact tts_voice the parent long episode was approved with,
    so the short matches it exactly. Falls back to the channel default."""
    rows = sb_get(EPISODE_TABLE, {"episode_number": f"eq.{episode_number}", "select": "tts_voice"})
    voice = (rows[0].get("tts_voice") if rows else None) or DEFAULT_VOICE[LANGUAGE]
    print(f"  Voice: {voice}")
    return voice

def synthesize_speech(text, voice_name, output_path):
    creds = SACreds.from_service_account_file(creds_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    lang_code = "-".join(voice_name.split("-")[:2])  # e.g. "ta-IN-Chirp3-HD-Callirrhoe" -> "ta-IN"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": lang_code, "name": voice_name},
        # Chirp 3: HD rejects speakingRate/pitch/SSML — audioEncoding only.
        "audioConfig": {"audioEncoding": "MP3"},
    }
    r = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json=payload,
    )
    if r.status_code != 200:
        print(f"  ❌ TTS error {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    audio_b64 = r.json()["audioContent"]
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))
    print(f"  Audio saved: {output_path} ({os.path.getsize(output_path)} bytes)")

def download_images(image_urls, tmpdir):
    paths = []
    for i, img in enumerate(image_urls):
        url = img.get("url") if isinstance(img, dict) else img
        out_path = os.path.join(tmpdir, f"img_{i:02d}.jpg")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        paths.append(out_path)
        print(f"  Downloaded image {i+1}: {out_path}")
    return paths

_rembg_session = None
_rembg_session_failed = False

def get_rembg_session():
    """Lazily creates the rembg session once per run. If model download or
    init fails (network hiccup, etc.), remembers that and never retries
    within this run — every image just falls back to plain zoompan."""
    global _rembg_session, _rembg_session_failed
    if _rembg_session_failed or not REMBG_AVAILABLE:
        return None
    if _rembg_session is None:
        try:
            _rembg_session = rembg_new_session("u2net")
        except Exception as e:
            print(f"  ⚠️  rembg session init failed ({e}) — parallax disabled for this render")
            _rembg_session_failed = True
            return None
    return _rembg_session

def try_extract_subject(image_path, out_path):
    """Attempts to cut the subject out of image_path into a transparent-
    background RGBA PNG at out_path. Returns True only if segmentation
    succeeded AND looks like a real subject (not near-empty or near-total
    coverage, which usually means the model failed to find a clean subject —
    common on painterly/symbolic/abstract images). Any exception anywhere
    in this function is caught and treated as "skip parallax for this image".
    """
    session = get_rembg_session()
    if session is None:
        return False
    try:
        with open(image_path, "rb") as f:
            input_bytes = f.read()
        out_bytes = rembg_remove(input_bytes, session=session)
        with open(out_path, "wb") as f:
            f.write(out_bytes)

        im = Image.open(out_path).convert("RGBA")
        alpha = np.array(im)[:, :, 3]
        coverage = float((alpha > 10).mean())
        if coverage < 0.03 or coverage > 0.90:
            print(f"    Subject coverage {coverage:.2f} out of usable range — skipping parallax for this image")
            return False
        print(f"    Subject cutout OK (coverage {coverage:.2f})")
        return True
    except Exception as e:
        print(f"    Subject cutout failed ({e}) — skipping parallax for this image")
        return False

# Fonts installed by the workflow (fonts-noto-tamil / fonts-noto-core) — using
# fontconfig family names via drawtext's `font=` (not a hardcoded file path)
# so this doesn't break if the runner's font paths shift.
DRAWTEXT_FONT = {"ta": "Noto Sans Tamil", "en": "Noto Sans"}

# Style presets for the hook overlay — each is a distinct "3D-ish" look built
# purely from stacked drawtext layers (extrusion offset-copies, or a soft
# glow via widening semi-transparent borders). No external rendering, no
# extra API cost — just layered FFmpeg text. One style is picked per SHORT
# (seeded by the short's own ID, so re-rendering the same short keeps its
# look instead of flickering to a new style every retry) — different shorts
# get different looks.
HOOK_STYLES = [
    {  # solid red extrusion block
        "kind": "extrude", "fontcolor": "white", "bordercolor": "black", "borderw": 5,
        "shadow_color": "0xB5121B", "layers": 6, "step": 3,
    },
    {  # solid cyan/navy extrusion block
        "kind": "extrude", "fontcolor": "white", "bordercolor": "0x0A2540", "borderw": 5,
        "shadow_color": "0x0E7C86", "layers": 6, "step": 3,
    },
    {  # gold pop with dark extrusion
        "kind": "extrude", "fontcolor": "0xFFD54A", "bordercolor": "black", "borderw": 6,
        "shadow_color": "0x3A2B00", "layers": 5, "step": 3,
    },
    {  # neon magenta glow
        "kind": "glow", "fontcolor": "white", "bordercolor": "0xE0218A", "borderw": 4,
        "glow_color": "0xE0218A", "glow_layers": 4, "glow_start": 22, "glow_step": 6,
    },
    {  # neon cyan glow
        "kind": "glow", "fontcolor": "white", "bordercolor": "0x22D3EE", "borderw": 4,
        "glow_color": "0x22D3EE", "glow_layers": 4, "glow_start": 22, "glow_step": 6,
    },
    {  # purple extrusion block
        "kind": "extrude", "fontcolor": "0xF5F0FF", "bordercolor": "black", "borderw": 5,
        "shadow_color": "0x4A1D6E", "layers": 6, "step": 3,
    },
]

def build_hook_layers(stage_in, stage_out, textfile, font, fontsize, alpha_expr, enable_expr, style):
    """Builds the stacked-drawtext filter string for ONE hook phrase in the
    given style, threading intermediate ffmpeg labels from stage_in to
    stage_out. Returns the filter fragment (caller appends with a leading ';').
    """
    parts = []
    stage = stage_in
    common = (
        f"textfile='{textfile}':font='{font}':fontsize={fontsize}:line_spacing=14:"
        f"x=(w-text_w)/2:alpha='{alpha_expr}':enable='{enable_expr}'"
    )
    y_base = "h*0.14"

    if style["kind"] == "extrude":
        # Draw N offset copies behind the main text, stepping diagonally,
        # solid shadow color — reads as a solid "3D block letter" extrusion.
        # Box goes on the BOTTOM-most layer so it sits behind the whole
        # stack, not painted over the shadow layers on top of it.
        n = style["layers"]
        step = style["step"]
        for i in range(n, 0, -1):
            label = f"{stage_out}_e{i}"
            box_part = "box=1:boxcolor=black@0.35:boxborderw=26:" if i == n else ""
            parts.append(
                f"[{stage}]drawtext={common}:fontcolor={style['shadow_color']}:{box_part}"
                f"y=({y_base})+{i*step}:x=(w-text_w)/2+{i*step}[{label}]"
            )
            stage = label
        parts.append(
            f"[{stage}]drawtext={common}:fontcolor={style['fontcolor']}:"
            f"bordercolor={style['bordercolor']}:borderw={style['borderw']}:"
            f"y={y_base}[{stage_out}]"
        )
    else:  # glow
        n = style["glow_layers"]
        for i in range(n, 0, -1):
            bw = style["glow_start"] + i * style["glow_step"]
            alpha_scale = 0.12 + (0.10 * (n - i))
            label = f"{stage_out}_g{i}"
            box_part = "box=1:boxcolor=black@0.35:boxborderw=26:" if i == n else ""
            parts.append(
                f"[{stage}]drawtext={common}:fontcolor={style['glow_color']}@{alpha_scale:.2f}:"
                f"bordercolor={style['glow_color']}@{alpha_scale:.2f}:borderw={bw}:{box_part}y={y_base}[{label}]"
            )
            stage = label
        parts.append(
            f"[{stage}]drawtext={common}:fontcolor={style['fontcolor']}:"
            f"bordercolor={style['bordercolor']}:borderw={style['borderw']}:"
            f"y={y_base}[{stage_out}]"
        )

    return ";".join(parts)

_font_file_cache = {}

def resolve_font_file(font_family):
    """Asks fontconfig for the actual .ttf path behind a family name, so text
    width can be measured precisely instead of guessed. Cached per family."""
    if font_family in _font_file_cache:
        return _font_file_cache[font_family]
    path = None
    try:
        r = subprocess.run(["fc-match", "-f", "%{file}", font_family],
                            capture_output=True, text=True, timeout=10)
        candidate = r.stdout.strip()
        if candidate and os.path.exists(candidate):
            path = candidate
    except Exception as e:
        print(f"    ⚠️  fc-match failed for '{font_family}' ({e})")
    _font_file_cache[font_family] = path
    return path

def wrap_text_for_overlay(text, font_family, max_width_px=920, start_fontsize=84,
                           min_fontsize=52, max_lines=2):
    """Word-wraps text to fit max_width_px, shrinking fontsize if needed to
    stay within max_lines. Measures against the REAL font file via PIL when
    available; falls back to a rough per-character estimate otherwise (still
    functional, just less precise) — either way this always returns something
    renderable, never raises.
    """
    words = text.split()
    font_path = resolve_font_file(font_family) if PIL_AVAILABLE else None

    fontsize = start_fontsize
    lines = [text]  # last-resort fallback if the loop below never sets it
    while fontsize >= min_fontsize:
        if font_path:
            try:
                font = ImageFont.truetype(font_path, fontsize)
                measure = lambda s: font.getlength(s)
            except Exception:
                measure = lambda s, fs=fontsize: len(s) * fs * 0.55
        else:
            measure = lambda s, fs=fontsize: len(s) * fs * 0.55

        lines = []
        current = ""
        for w in words:
            candidate = f"{current} {w}".strip()
            if not current or measure(candidate) <= max_width_px:
                current = candidate
            else:
                lines.append(current)
                current = w
        if current:
            lines.append(current)

        if len(lines) <= max_lines:
            return "\n".join(lines), fontsize
        fontsize -= 8

    # Even the smallest allowed fontsize still wraps to more than max_lines —
    # take the first max_lines and accept it rather than looping forever.
    return "\n".join(lines[:max_lines]), fontsize

def render_vertical_video(image_paths, audio_path, on_screen_texts, output_path, tmpdir, fps=25):
    """
    Renders a 1080x1920 vertical video with FREE motion (no AI video generation,
    no extra API cost — pure local processing on the already-generated Imagen
    stills):
      - Each image gets a slow Ken Burns zoom + pan (direction alternates per
        image so consecutive beats don't feel identical).
      - Where rembg can cleanly cut out a subject, that subject and its
        background pan at different rates for a fake-depth parallax effect.
        Any image where segmentation isn't available/clean just gets plain
        zoompan — never blocks the render.
      - Crossfade between images, same as before.
      - on_screen_texts is a LIST of short punchy phrases (not one hook line) —
        they're spread evenly across the whole short's runtime, each popping
        on, holding, and popping off in its own time slice, properly word-
        wrapped against the real font so nothing crowds or overflows.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    audio_dur = float(probe.stdout.strip())
    n = len(image_paths)
    per_img = audio_dur / n
    fade_dur = min(0.5, per_img * 0.15)
    seg_dur = per_img + fade_dur
    frames = max(1, round(seg_dur * fps))

    print(f"  Audio duration: {audio_dur:.1f}s | {n} images | {per_img:.1f}s each | fade {fade_dur:.2f}s")

    # zoompan expressions run per OUTPUT FRAME — use 'on' (frame index), NOT
    # 't' (seconds), which zoompan doesn't expose. Verified against a real render.
    BG_PAN_VARIANTS = [
        ("min(zoom+0.0009,1.18)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)"),
        ("min(zoom+0.0009,1.18)", "iw/2-(iw/zoom/2)+on*1.4",  "ih/2-(ih/zoom/2)"),
        ("min(zoom+0.0009,1.18)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)-on*1.0"),
    ]
    # Foreground (subject layer) moves at a visibly different rate/direction
    # from its background — that mismatch IS the parallax effect.
    FG_PAN_VARIANTS = [
        ("min(zoom+0.0016,1.30)", "iw/2-(iw/zoom/2)-on*1.6",  "ih/2-(ih/zoom/2)"),
        ("min(zoom+0.0016,1.30)", "iw/2-(iw/zoom/2)",         "ih/2-(ih/zoom/2)+on*1.3"),
        ("min(zoom+0.0016,1.30)", "iw/2-(iw/zoom/2)-on*1.2",  "ih/2-(ih/zoom/2)-on*0.8"),
    ]

    inputs = []       # flat ffmpeg -i args
    input_count = 0    # logical input index counter (for filter references)
    chain = ""

    for i, path in enumerate(image_paths):
        bg_z, bg_x, bg_y = BG_PAN_VARIANTS[i % len(BG_PAN_VARIANTS)]
        fg_path = os.path.join(tmpdir, f"fg_{i:02d}.png")
        has_subject = try_extract_subject(path, fg_path)

        bg_idx = input_count
        inputs += ["-loop", "1", "-t", str(seg_dur), "-i", path]
        input_count += 1

        if has_subject:
            fg_idx = input_count
            inputs += ["-loop", "1", "-t", str(seg_dur), "-i", fg_path]
            input_count += 1
            fg_z, fg_x, fg_y = FG_PAN_VARIANTS[i % len(FG_PAN_VARIANTS)]
            chain += (
                f"[{bg_idx}:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
                f"zoompan=z='{bg_z}':x='{bg_x}':y='{bg_y}':d={frames}:s=1080x1920:fps={fps},"
                f"format=yuva420p[bg{i}];"
                f"[{fg_idx}:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
                f"zoompan=z='{fg_z}':x='{fg_x}':y='{fg_y}':d={frames}:s=1080x1920:fps={fps},"
                f"format=yuva420p[fg{i}];"
                f"[bg{i}][fg{i}]overlay=format=auto,setsar=1,format=yuva420p[v{i}];"
            )
        else:
            chain += (
                f"[{bg_idx}:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
                f"zoompan=z='{bg_z}':x='{bg_x}':y='{bg_y}':d={frames}:s=1080x1920:fps={fps},"
                f"setsar=1,format=yuva420p[v{i}];"
            )

    inputs += ["-i", audio_path]
    audio_idx = input_count

    if n == 1:
        filtergraph = chain.rstrip(";") + ";[v0]setpts=PTS-STARTPTS[vbase]"
    else:
        xfade_chain = ""
        for i in range(1, n):
            offset = per_img * i - fade_dur * (i - 1)
            out_label = f"xf{i}" if i < n - 1 else "vbase"
            src = "v0" if i == 1 else f"xf{i-1}"
            xfade_chain += f"[{src}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset:.3f}[{out_label}];"
        filtergraph = chain + xfade_chain.rstrip(";")

    # Hook overlays: on_screen_texts is a LIST — each phrase gets its own
    # equal time-slice across the full runtime (not just the first few
    # seconds), so new punchy text keeps appearing as the short plays instead
    # of a single opening hook that then goes quiet. Each phrase pops in fast
    # (not a slow fade), holds, pops out — top third, high-contrast, properly
    # word-wrapped against the REAL font so it never crowds or overflows.
    texts = [t.strip() for t in (on_screen_texts or []) if t and t.strip()]
    lang = LANGUAGE if LANGUAGE in DRAWTEXT_FONT else "en"
    font = DRAWTEXT_FONT[lang]

    if texts:
        style = random.Random(SHORT_ID).choice(HOOK_STYLES)
        print(f"  Hook overlays ({len(texts)}), style={style['kind']}: {texts}")
        slice_dur = audio_dur / len(texts)
        stage_label = "vbase"
        for idx, raw_text in enumerate(texts):
            text = raw_text.upper() if LANGUAGE == "en" else raw_text
            wrapped, fontsize = wrap_text_for_overlay(text, font)
            textfile = os.path.join(tmpdir, f"hook_{idx}.txt")
            with open(textfile, "w", encoding="utf-8") as f:
                f.write(wrapped)

            start = idx * slice_dur
            end = (idx + 1) * slice_dur
            fade_in_end = start + 0.12
            fade_out_start = max(end - 0.25, fade_in_end + 0.1)
            alpha_expr = (
                f"if(lt(t,{start}),0,"
                f"if(lt(t,{fade_in_end}),(t-{start})/0.12,"
                f"if(lt(t,{fade_out_start}),1,"
                f"if(lt(t,{end}),({end}-t)/0.25,0))))"
            )
            enable_expr = f"between(t,{start:.3f},{end:.3f})"
            out_label = f"txt{idx}" if idx < len(texts) - 1 else "vout"
            filtergraph += ";" + build_hook_layers(
                stage_label, out_label, textfile, font, fontsize, alpha_expr, enable_expr, style
            )
            stage_label = out_label
        vout_label = "vout"
    else:
        print(f"  ⚠️  Hook overlays SKIPPED — on_screen_texts was empty for this short "
              f"(received: {on_screen_texts!r}). No text will appear on screen.")
        vout_label = "vout"
        filtergraph += f";[vbase]null[{vout_label}]"

    cmd = (
        ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filtergraph,
            "-map", f"[{vout_label}]", "-map", f"{audio_idx}:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart", "-pix_fmt", "yuv420p",
            output_path,
        ]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("FFmpeg stderr:", result.stderr[-2000:])
        raise RuntimeError("FFmpeg failed")
    print(f"  Video rendered: {output_path} ({os.path.getsize(output_path)//1024}KB)")

def main():
    print(f"Sprint 15 | Shorts Render Pipeline — {SHORT_ID} ({LANGUAGE})")

    short = sb_get_one(SHORTS_TABLE, {"id": f"eq.{SHORT_ID}", "select": "*"})

    script = short.get("script", "")
    if not script:
        raise ValueError("No script found on this short")
    print(f"  Script length: {len(script)} chars")

    raw_images = short.get("image_urls_vertical") or []
    if isinstance(raw_images, str):
        raw_images = json.loads(raw_images)
    if not raw_images:
        raise ValueError("No approved vertical images — approve images first")
    print(f"  Images: {len(raw_images)} vertical images found")

    voice_name = get_episode_voice(short["episode_number"])

    sb_patch(SHORTS_TABLE, SHORT_ID, {"status": "rendering"})

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "narration.mp3")
            print("Synthesizing speech...")
            synthesize_speech(script, voice_name, audio_path)

            print("Downloading images...")
            image_paths = download_images(raw_images, tmpdir)

            video_path = os.path.join(tmpdir, "short.mp4")
            print("Rendering vertical video (Ken Burns motion + multi-hook overlay)...")
            raw_texts = short.get("on_screen_texts")
            if isinstance(raw_texts, str):  # defensive: guard against a double-encoded jsonb string
                try:
                    raw_texts = json.loads(raw_texts)
                except Exception:
                    raw_texts = None
            if isinstance(raw_texts, list) and raw_texts:
                overlay_texts = raw_texts
            elif short.get("on_screen_text"):
                overlay_texts = [short["on_screen_text"]]
            elif short.get("hook_line"):
                overlay_texts = [short["hook_line"]]
            else:
                overlay_texts = []
            print(f"  DB values — on_screen_texts: {raw_texts!r} | on_screen_text: {short.get('on_screen_text')!r} | hook_line: {short.get('hook_line')!r}")
            render_vertical_video(image_paths, audio_path, overlay_texts, video_path, tmpdir)

            print("Uploading to GCS...")
            gcs_path = f"shorts/{short['episode_number']}/{LANGUAGE}_{short['short_index']}.mp4"
            video_url = upload_to_gcs(video_path, gcs_path)
            print(f"  Uploaded: {video_url}")

        sb_patch(SHORTS_TABLE, SHORT_ID, {"video_url": video_url, "status": "video_ready"})
        print(f"✅ Shorts video done: {video_url}")
    except Exception as e:
        sb_patch(SHORTS_TABLE, SHORT_ID, {"status": "images_approved"})
        raise

if __name__ == "__main__":
    main()
