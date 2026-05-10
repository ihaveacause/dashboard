"""
I Have a Cause — Video Pipeline (New)
======================================
Triggered by GitHub Actions with EPISODE_NUMBER input.
Flow:
  1. Fetch episode + approved images + script from tamil_episodes
  2. Download approved images from Supabase Storage
  3. edge-tts → Tamil voice narration MP3
  4. FFmpeg → Ken Burns effect on each image
  5. FFmpeg → assemble final video with voice + transitions
  6. Upload MP4 to Supabase Storage 'episode-videos'
  7. Save video_url to tamil_episodes
  8. status → video_ready
"""

import os
import json
import asyncio
import requests
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

# ── Config ─────────────────────────────────────────────────
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
EPISODE_NUMBER = int(os.environ["EPISODE_NUMBER"])

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}
REST = f"{SUPABASE_URL}/rest/v1"

WORK_DIR = Path(tempfile.mkdtemp(prefix="ihac_video_"))

# ── Supabase helpers ────────────────────────────────────────
def db_get(table, params):
    r = requests.get(
        f"{REST}/{table}",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return r.json() if r.status_code == 200 else []

def db_patch(table, episode_number, data):
    r = requests.patch(
        f"{REST}/{table}?episode_number=eq.{episode_number}",
        headers=SB_HEADERS,
        json=data, timeout=15
    )
    return r.status_code in (200, 204)

def upload_video(path, storage_path):
    with open(path, "rb") as f:
        data = f.read()
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/episode-videos/{storage_path}",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "video/mp4",
            "x-upsert":      "true"
        },
        data=data, timeout=600
    )
    if r.status_code in (200, 201):
        return f"{SUPABASE_URL}/storage/v1/object/public/episode-videos/{storage_path}"
    print(f"  ❌ Upload failed {r.status_code}: {r.text[:200]}")
    return None

# ── Fetch episode ───────────────────────────────────────────
def fetch_episode():
    rows = db_get("tamil_episodes", {
        "episode_number": f"eq.{EPISODE_NUMBER}",
        "select": "*"
    })
    return rows[0] if rows else None

# ── Download images ─────────────────────────────────────────
def download_images(image_urls_json):
    print(f"\n📥 Downloading approved images...")
    image_urls = json.loads(image_urls_json) if isinstance(image_urls_json, str) else image_urls_json
    local_paths = []

    for img in image_urls:
        url = img["url"]
        local_path = WORK_DIR / f"scene_{img['id']}.jpg"
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                local_path.write_bytes(r.content)
                local_paths.append(str(local_path))
                print(f"   ✅ Scene {img['id']}: {img['label']}")
            else:
                print(f"   ❌ Failed to download scene {img['id']}: {r.status_code}")
        except Exception as e:
            print(f"   ❌ Error downloading scene {img['id']}: {e}")

    return local_paths

# ── Generate Tamil voice ────────────────────────────────────
async def generate_voice(script_text, output_path):
    print(f"\n🎙  Generating Tamil voice with edge-tts...")
    import edge_tts

    # Tamil voice — Valluvar neural voice
    voice = "ta-IN-ValluvarNeural"

    # Clean script for TTS (remove markdown headers, timestamps etc)
    clean_lines = []
    for line in script_text.split('\n'):
        line = line.strip()
        # Skip timestamp lines, headers, empty lines
        if not line:
            continue
        if line.startswith('#') or line.startswith('*') or ':' in line[:10]:
            # Keep the content after the colon for labeled sections
            if ':' in line and len(line) > 20:
                content = line.split(':', 1)[1].strip()
                if content:
                    clean_lines.append(content)
        else:
            clean_lines.append(line)

    clean_script = ' '.join(clean_lines)
    # Limit to reasonable length for the video
    if len(clean_script) > 8000:
        clean_script = clean_script[:8000]

    communicate = edge_tts.Communicate(clean_script, voice)
    await communicate.save(str(output_path))
    print(f"   ✅ Voice generated: {output_path}")
    return str(output_path)

# ── Get audio duration ──────────────────────────────────────
def get_duration(audio_path):
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ], capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except:
        return 600.0  # default 10 min

# ── Assemble video with FFmpeg ──────────────────────────────
def assemble_video(image_paths, audio_path, output_path, episode):
    print(f"\n🎬 Assembling video with FFmpeg...")

    audio_duration = get_duration(audio_path)
    print(f"   Audio duration: {audio_duration:.1f}s ({audio_duration/60:.1f} min)")

    num_images = len(image_paths)
    if num_images == 0:
        print("   ❌ No images to assemble")
        return False

    # Duration per image (distribute evenly across audio)
    duration_per_image = audio_duration / num_images
    print(f"   {num_images} images × {duration_per_image:.1f}s each")

    # Ken Burns directions — alternate for visual variety
    kb_directions = [
        # zoom_start, zoom_end, x_start, y_start, x_end, y_end
        (1.0, 1.08, 0.0, 0.0, 0.05, 0.03),   # slow zoom in, drift right-down
        (1.08, 1.0, 0.05, 0.03, 0.0, 0.0),   # slow zoom out, drift left-up
        (1.0, 1.08, 0.05, 0.0, 0.0, 0.05),   # slow zoom in, drift left-down
        (1.05, 1.05, 0.0, 0.05, 0.05, 0.0),  # pan right-up
        (1.08, 1.0, 0.02, 0.02, 0.03, 0.03), # slow zoom out, center
    ]

    W, H = 1920, 1080
    fps = 24
    clip_paths = []

    for i, img_path in enumerate(image_paths):
        clip_out = WORK_DIR / f"clip_{i:02d}.mp4"
        d = duration_per_image
        frames = int(d * fps)

        zs, ze, xs, ys, xe, ye = kb_directions[i % len(kb_directions)]

        # FFmpeg Ken Burns: scale image large, then apply zoompan
        # zoompan: z=zoom, x=pan_x, y=pan_y, d=duration_frames
        zoom_expr   = f"'if(eq(on,1),{zs},{zs}+({ze}-{zs})*on/{frames})'"
        x_expr      = f"'if(eq(on,1),{int(xs*W)},({int(xs*W)}+({int(xe*W)}-{int(xs*W)})*on/{frames}))'"
        y_expr      = f"'if(eq(on,1),{int(ys*H)},({int(ys*H)}+({int(ye*H)}-{int(ys*H)})*on/{frames}))'"

        vf = (
            f"scale={W*2}:{H*2},"
            f"zoompan=z={zoom_expr}:x={x_expr}:y={y_expr}:d={frames}:s={W}x{H}:fps={fps},"
            f"scale={W}:{H}"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(img_path),
            "-vf", vf,
            "-t", str(d),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(clip_out)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            clip_paths.append(str(clip_out))
            print(f"   ✅ Clip {i+1}/{num_images} rendered ({d:.1f}s)")
        else:
            print(f"   ❌ Clip {i+1} failed: {result.stderr[-300:]}")

    if not clip_paths:
        print("   ❌ No clips rendered")
        return False

    # Concatenate clips
    print(f"\n   🔗 Concatenating {len(clip_paths)} clips...")
    concat_list = WORK_DIR / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p}'" for p in clip_paths))

    video_only = WORK_DIR / "video_only.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(video_only)
    ]
    subprocess.run(cmd, capture_output=True)

    # Add title overlay + mix audio
    print(f"   🎵 Adding voice narration...")

    title_ta = episode.get('title_tamil', '')
    title_en = episode.get('title_english', '')

    # Overlay: episode number + Tamil title at start (first 6 seconds)
    drawtext = (
        f"drawtext=text='Episode {EPISODE_NUMBER}':fontsize=32:fontcolor=white@0.7:"
        f"x=(w-text_w)/2:y=h-120:enable='between(t,0,5)',"
        f"drawtext=text='{title_en[:50]}':fontsize=24:fontcolor=white@0.5:"
        f"x=(w-text_w)/2:y=h-80:enable='between(t,0,5)'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_only),
        "-i", str(audio_path),
        "-vf", drawtext,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        size_mb = Path(output_path).stat().st_size / (1024*1024)
        print(f"   ✅ Final video: {output_path} ({size_mb:.1f} MB)")
        return True
    else:
        print(f"   ❌ Final assembly failed: {result.stderr[-500:]}")
        return False

# ── Main ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"🎬 Video Pipeline — Episode {EPISODE_NUMBER}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    episode = fetch_episode()
    if not episode:
        print(f"❌ Episode {EPISODE_NUMBER} not found")
        return

    print(f"\n📖 {episode['title_english']}")

    if not episode.get("image_urls"):
        print("❌ No approved images found — run image pipeline first")
        return

    db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "rendering_video"})

    try:
        # Download images
        image_paths = download_images(episode["image_urls"])
        if not image_paths:
            print("❌ Failed to download images")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        # Generate voice
        script = episode.get("script_tamil", episode.get("title_tamil", ""))
        audio_path = WORK_DIR / "narration.mp3"
        asyncio.run(generate_voice(script, audio_path))

        if not audio_path.exists():
            print("❌ Voice generation failed")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        # Assemble video
        video_path = WORK_DIR / f"episode_{EPISODE_NUMBER:03d}_tamil.mp4"
        success = assemble_video(image_paths, audio_path, video_path, episode)

        if not success:
            print("❌ Video assembly failed")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
            return

        # Upload to Supabase Storage
        print(f"\n☁️  Uploading video to Supabase Storage...")
        storage_path = f"ep{EPISODE_NUMBER:03d}/episode_{EPISODE_NUMBER:03d}_tamil.mp4"
        video_url = upload_video(str(video_path), storage_path)

        if video_url:
            db_patch("tamil_episodes", EPISODE_NUMBER, {
                "video_url": video_url,
                "status":    "video_ready",
                "voice_url": f"{SUPABASE_URL}/storage/v1/object/public/episode-videos/ep{EPISODE_NUMBER:03d}/narration.mp3"
            })
            print(f"\n{'='*60}")
            print(f"✅ Episode {EPISODE_NUMBER} — Video ready for review!")
            print(f"   Open dashboard to preview and approve.")
            print(f"{'='*60}")
        else:
            print("❌ Video upload failed")
            db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})

    except Exception as e:
        import traceback
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        db_patch("tamil_episodes", EPISODE_NUMBER, {"status": "images_approved"})
    finally:
        # Cleanup temp files
        shutil.rmtree(str(WORK_DIR), ignore_errors=True)

if __name__ == "__main__":
    main()
