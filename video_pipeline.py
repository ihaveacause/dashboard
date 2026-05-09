"""
I Have a Cause — Video Pipeline (Sprint 5)
==========================================
Flow:
  Supabase: status='published_ready'
      ↓
  prepare()   → edge-tts audio + subtitles
              → Pexels video clips (transcoded to 1080×1920)
              → background music via ffmpeg
              → writes video_renderer/public/ assets
              → writes data_{lang}.json
      ↓
  render()    → node render.mjs data.json output.mp4  (×2 languages)
      ↓
  upload()    → Supabase Storage bucket 'videos'
              → stores public URLs back to content_queue
              → status → 'video_ready'
"""

import os, json, asyncio, requests, subprocess, re, shutil
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
SB_URL      = os.environ["SUPABASE_URL"]
SB_KEY      = os.environ["SUPABASE_KEY"]
PEXELS_KEY  = os.environ["PEXELS_API_KEY"]

SB_HDR = {
    "apikey"       : SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type" : "application/json",
    "Prefer"       : "return=minimal",
}
PEXELS_HDR = {"Authorization": PEXELS_KEY}

BASE_DIR   = Path(__file__).parent
PUBLIC_DIR = BASE_DIR / "video_renderer" / "public"
OUTPUT_DIR = BASE_DIR / "output"
RENDER_DIR = BASE_DIR / "video_renderer"

PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VOICES = {
    "tamil"  : "ta-IN-ValluvarNeural",
    "english": "en-IN-NeerjaNeural",
}

# ── Supabase helpers ──────────────────────────────────────────────────────────
def db_get(params):
    r = requests.get(
        f"{SB_URL}/rest/v1/content_queue",
        headers={**SB_HDR, "Prefer": "return=representation"},
        params=params, timeout=15,
    )
    return r.json() if r.status_code == 200 else []

def db_patch(sid, data):
    r = requests.patch(
        f"{SB_URL}/rest/v1/content_queue?id=eq.{sid}",
        headers=SB_HDR, json=data, timeout=15,
    )
    ok = r.status_code < 300
    if not ok:
        print(f"  ⚠️  db_patch failed {r.status_code}: {r.text[:120]}")
    return ok

# ── edge-tts ──────────────────────────────────────────────────────────────────
async def generate_tts(text, language, audio_out: Path, subs_out: Path):
    """Generate MP3 + word-level VTT subtitles via edge-tts."""
    import edge_tts
    voice     = VOICES[language]
    communicate = edge_tts.Communicate(text, voice)
    submaker  = edge_tts.SubMaker()

    with open(audio_out, "wb") as af:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                af.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.create_sub(
                    (chunk["offset"], chunk["duration"]),
                    chunk["text"],
                )

    vtt = submaker.generate_subs()
    with open(subs_out, "w", encoding="utf-8") as sf:
        sf.write(vtt)

    size_kb = audio_out.stat().st_size // 1024
    print(f"    ✅ TTS: {audio_out.name} ({size_kb} KB), {len(vtt.splitlines())} sub lines")
    return vtt

def parse_vtt_to_frames(vtt: str, fps=30, hook_frames=90, words_per_group=3):
    """Convert word-level VTT (100ns ticks) to frame-stamped subtitle groups."""
    entries = []
    lines   = vtt.splitlines()
    i = 0
    while i < len(lines):
        if "-->" in lines[i]:
            parts = lines[i].split(" --> ")
            t0 = _vtt_secs(parts[0].strip())
            t1 = _vtt_secs(parts[1].strip())
            word = lines[i + 1].strip() if i + 1 < len(lines) else ""
            entries.append({"t0": t0, "t1": t1, "word": word})
            i += 2
        else:
            i += 1

    # Group into short phrases
    groups = []
    for j in range(0, len(entries), words_per_group):
        chunk = [e for e in entries[j:j + words_per_group] if e["word"]]
        if not chunk:
            continue
        text  = " ".join(e["word"] for e in chunk)
        start = int(chunk[0]["t0"]  * fps) + hook_frames
        end   = int(chunk[-1]["t1"] * fps) + hook_frames + 6
        groups.append({"start": start, "end": end, "text": text})

    return groups

def _vtt_secs(ts: str) -> float:
    """Parse VTT timestamp (HH:MM:SS.mmm or MM:SS.mmm) to seconds."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(ts)

# ── Pexels video ──────────────────────────────────────────────────────────────
def pexels_search(query: str):
    """Return a download URL for the best-fit Pexels video clip."""
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers=PEXELS_HDR,
            params={"query": query, "per_page": 6, "orientation": "portrait"},
            timeout=12,
        )
        if r.status_code != 200:
            # Fall back to landscape
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=PEXELS_HDR,
                params={"query": query, "per_page": 6},
                timeout=12,
            )
        videos = r.json().get("videos", []) if r.status_code == 200 else []
        for vid in videos:
            for f in vid.get("video_files", []):
                if f.get("quality") in ["hd", "sd"] and f.get("link"):
                    return f["link"]
        if videos:
            files = videos[0].get("video_files", [])
            if files:
                return files[0]["link"]
    except Exception as e:
        print(f"    ⚠️  Pexels search '{query}': {e}")
    return None

def download(url: str, dest: Path, label=""):
    """Download a URL to dest. Returns True on success."""
    try:
        r = requests.get(url, stream=True, timeout=45)
        if r.status_code == 200:
            with open(dest, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
            print(f"    ✅ Downloaded {label}: {dest.name} ({dest.stat().st_size // 1024} KB)")
            return True
    except Exception as e:
        print(f"    ❌ Download failed {label}: {e}")
    return False

def transcode_portrait(src: Path, dst: Path):
    """Transcode any video to 1080×1920 H264, max 30 sec, no audio."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
               "crop=1080:1920",
        "-c:v", "libx264", "-preset", "fast", "-crf", "24",
        "-an", "-t", "30",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=180)
    if result.returncode == 0:
        print(f"    ✅ Transcoded: {dst.name}")
        return True
    print(f"    ❌ Transcode error: {result.stderr.decode()[:180]}")
    return False

def make_placeholder(dest: Path, scene_idx: int):
    """Create a solid-colour MP4 when Pexels fails."""
    colours = ["0d0f14", "1a0a0a", "0a1a0a", "0a0a1a"]
    c = colours[scene_idx % len(colours)]
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=color=#{c}:size=1080x1920:duration=20:rate=30",
        "-c:v", "libx264", "-preset", "fast", "-an",
        str(dest),
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)
    print(f"    ⚠️  Placeholder created for scene {scene_idx + 1}")

def generate_ambient_music(dest: Path, niche: str, duration=75):
    """Generate subtle ambient audio with ffmpeg (no external download needed)."""
    freq = 55  if niche in ["crime","politics","tamil_politics","indian_politics"] else \
           110 if niche in ["sports","business"] else 65
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration}",
        "-af", "volume=0.06",
        "-ar", "44100", "-ac", "2",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode == 0:
        print(f"    ✅ Ambient music generated ({niche})")
    else:
        print(f"    ⚠️  Music generation failed, proceeding without music")

# ── Story data helpers ────────────────────────────────────────────────────────
def get_script(story, language):
    """Return the short-form narration script, cleaned up."""
    col = "script_youtube_short_tamil" if language == "tamil" \
          else "script_youtube_short_english"
    text = story.get(col) or story.get("title", "")
    # Strip stage directions / brackets
    text = re.sub(r"\[.*?\]", "", text, flags=re.DOTALL)
    text = re.sub(r"\(.*?\)", "", text, flags=re.DOTALL)
    return text.strip()[:2200]

def get_scene_queries(story):
    """Extract 4 English visual search queries from content_yt_short."""
    raw = story.get("content_yt_short") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}

    niche   = story.get("niche", "india news")
    queries = []
    for sc in (raw.get("scenes") or [])[:4]:
        visual = (sc.get("visual") or sc.get("visual_direction") or "").strip()
        if visual:
            queries.append(visual[:60])

    defaults = [
        f"{niche} india news",
        "parliament india government",
        "india people city crowd",
        "india news broadcast media",
    ]
    while len(queries) < 4:
        queries.append(defaults[len(queries)])
    return queries[:4]

def get_hook_text(story, language):
    """Return hook text for the opening 3 seconds."""
    raw = story.get("content_yt_short") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    key  = "hook_tamil" if language == "tamil" else "hook_english"
    hook = (raw.get(key) or story.get("title") or "")[:100]
    return hook

# ── Per-language preparation ──────────────────────────────────────────────────
async def prepare_language(story, language, scene_queries):
    """Generate TTS, download Pexels clips, write data JSON → public/."""
    print(f"\n  ── {language.upper()} ──────────────────────────────────────")

    # 1. TTS
    script = get_script(story, language)
    if not script:
        print(f"    ❌ No script for {language}, skipping")
        return False

    audio_out = PUBLIC_DIR / f"{language}_audio.mp3"
    subs_out  = PUBLIC_DIR / f"{language}_subs.vtt"
    vtt = await generate_tts(script, language, audio_out, subs_out)
    subtitles = parse_vtt_to_frames(vtt)

    # 2. Pexels video clips
    scene_entries = []
    for i, query in enumerate(scene_queries):
        raw_path  = PUBLIC_DIR / f"scene{i+1}_raw.mp4"
        done_path = PUBLIC_DIR / f"scene{i+1}.mp4"

        print(f"    🎬 Scene {i+1}: '{query}'")
        url = pexels_search(query)
        if url and download(url, raw_path, f"scene{i+1}"):
            if not transcode_portrait(raw_path, done_path):
                shutil.move(str(raw_path), str(done_path))  # use raw if transcode fails
        else:
            make_placeholder(done_path, i)

        if raw_path.exists():
            raw_path.unlink()

        scene_entries.append({"videoFile": f"scene{i+1}.mp4", "textOverlay": query})

    # 3. Background music (generated once, shared between languages)
    music_path = PUBLIC_DIR / "music.mp3"
    if not music_path.exists():
        generate_ambient_music(music_path, story.get("niche", "general"))

    # 4. Write data JSON for Remotion
    data = {
        "storyId"    : story["id"],
        "storyTitle" : story.get("title", ""),
        "language"   : language,
        "hookText"   : get_hook_text(story, language),
        "audioFile"  : f"{language}_audio.mp3",
        "musicFile"  : "music.mp3" if music_path.exists() else None,
        "subtitles"  : subtitles,
        "scenes"     : scene_entries,
        "niche"      : story.get("niche", "general"),
        "channelName": "I Have a Cause",
    }

    data_file = PUBLIC_DIR / f"data_{language}.json"
    data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    ✅ Data JSON: {data_file.name}")
    return True

# ── Render ────────────────────────────────────────────────────────────────────
def render_video(language):
    """Call Remotion render script. Returns Path to rendered MP4 or None."""
    data_file  = PUBLIC_DIR / f"data_{language}.json"
    output_mp4 = OUTPUT_DIR / f"video_short_{language}.mp4"
    script     = RENDER_DIR / "render.mjs"

    if not data_file.exists():
        print(f"  ❌ Data file missing for {language}")
        return None

    print(f"\n  🖥️  Rendering {language.upper()} video (this takes ~10–20 min)...")
    result = subprocess.run(
        ["node", str(script), str(data_file), str(output_mp4)],
        cwd=str(RENDER_DIR),
        timeout=1800,   # 30 min hard limit
    )

    if result.returncode == 0 and output_mp4.exists():
        size_mb = output_mp4.stat().st_size / (1024 * 1024)
        print(f"  ✅ Rendered: {output_mp4.name} ({size_mb:.1f} MB)")
        return output_mp4
    else:
        print(f"  ❌ Render failed for {language} (exit {result.returncode})")
        return None

# ── Supabase Storage ──────────────────────────────────────────────────────────
def ensure_bucket():
    r = requests.post(
        f"{SB_URL}/storage/v1/bucket",
        headers={**SB_HDR, "Content-Type": "application/json"},
        json={"name": "videos", "public": True},
        timeout=10,
    )
    ok = r.status_code in [200, 201, 409]   # 409 = already exists
    print(f"  {'✅' if ok else '⚠️ '} Storage bucket 'videos' {'ready' if ok else r.text[:80]}")

def upload_video(file_path: Path, storage_path: str):
    """Upload MP4 to Supabase Storage; return public URL or None."""
    data = file_path.read_bytes()
    r = requests.post(
        f"{SB_URL}/storage/v1/object/videos/{storage_path}",
        headers={
            "apikey"       : SB_KEY,
            "Authorization": f"Bearer {SB_KEY}",
            "Content-Type" : "video/mp4",
            "x-upsert"     : "true",
        },
        data=data,
        timeout=600,    # 10 min for large uploads
    )
    if r.status_code in [200, 201]:
        url = f"{SB_URL}/storage/v1/object/public/videos/{storage_path}"
        print(f"  ✅ Uploaded: {storage_path}")
        return url
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:160]}")
    return None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("🎬  I Have a Cause — Video Pipeline  (Sprint 5)")
    print("=" * 62)

    stories = db_get({
        "status": "eq.published_ready",
        "select": "*",
        "order" : "created_at.asc",
        "limit" : "1",
    })

    if not stories:
        print("✅  No stories in 'published_ready'. Nothing to render.\n")
        return

    ensure_bucket()

    for story in stories:
        sid   = story["id"]
        title = story.get("title", "")[:55]
        niche = story.get("niche", "general")
        print(f"\n📰  Processing: {title}")

        db_patch(sid, {"video_render_status": "rendering"})

        try:
            queries = get_scene_queries(story)
            print(f"  🔍 Scene queries: {queries}")

            ok_ta = asyncio.run(prepare_language(story, "tamil",   queries))
            ok_en = asyncio.run(prepare_language(story, "english", queries))

            if not ok_ta and not ok_en:
                db_patch(sid, {"video_render_status": "failed"})
                print("  ❌ Both language preps failed. Skipping.")
                continue

            ta_mp4 = render_video("tamil")   if ok_ta else None
            en_mp4 = render_video("english") if ok_en else None

            updates = {"video_render_status": "complete", "status": "video_ready"}

            if ta_mp4:
                url = upload_video(ta_mp4, f"{sid}/video_short_tamil.mp4")
                if url:
                    updates["video_short_tamil"] = url

            if en_mp4:
                url = upload_video(en_mp4, f"{sid}/video_short_english.mp4")
                if url:
                    updates["video_short_english"] = url

            db_patch(sid, updates)
            print(f"\n✅  Done: {title}")
            print(f"   Tamil  : {updates.get('video_short_tamil',  '—failed—')}")
            print(f"   English: {updates.get('video_short_english','—failed—')}")

        except Exception as exc:
            import traceback
            print(f"  ❌ Exception: {exc}")
            traceback.print_exc()
            db_patch(sid, {"video_render_status": "failed"})

    print("\n" + "=" * 62)
    print("✅  Video Pipeline finished")
    print("=" * 62)

if __name__ == "__main__":
    main()
