"""
upload_to_youtube.py
Uploads a video to YouTube with thumbnail, metadata, scheduling and playlist.

Usage (called by GitHub Actions):
  python upload_to_youtube.py \
    --episode_number   "1" \
    --language     "tamil" \
    --token_json   "/tmp/yt_token.json" \
    --client_secret_json "/tmp/client_secret.json"
"""

import argparse
import json
import os
import pickle
import sys
import time
from datetime import datetime, timedelta, timezone

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from supabase import create_client, Client

# ── Config ────────────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # needed for caption upload
]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
CHANNEL_ID   = os.environ["YOUTUBE_CHANNEL_ID"]

# Upload time — 7:00 AM IST = 01:30 UTC
PUBLISH_HOUR_UTC   = 1
PUBLISH_MINUTE_UTC = 30


# ── Supabase ──────────────────────────────────────────────────────────────────

def get_episode(episode_number: int, language: str) -> dict:
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = "tamil_episodes" if language == "tamil" else "english_episodes"
    res = sb.table(table).select("*").eq("episode_number", episode_number).single().execute()
    return res.data


def update_episode(episode_number: int, language: str, updates: dict):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = "tamil_episodes" if language == "tamil" else "english_episodes"
    sb.table(table).update(updates).eq("episode_number", episode_number).execute()
    print(f"✅ Supabase updated: {updates}")


def get_last_scheduled_time(language: str) -> datetime | None:
    """Get the latest scheduled_at across all episodes for staggering."""
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = "tamil_episodes" if language == "tamil" else "english_episodes"
    res = (sb.table(table)
             .select("scheduled_at")
             .not_.is_("scheduled_at", "null")
             .order("scheduled_at", desc=True)
             .limit(1)
             .execute())
    if res.data:
        dt_str = res.data[0]["scheduled_at"]
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return None


def count_published_episodes(language: str) -> int:
    """Count episodes already published — first 3 go together."""
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = "tamil_episodes" if language == "tamil" else "english_episodes"
    res = (sb.table(table)
             .select("id", count="exact")
             .not_.is_("youtube_video_id", "null")
             .execute())
    return res.count or 0


# ── Scheduling ────────────────────────────────────────────────────────────────

def calculate_publish_time(language: str) -> datetime:
    """
    First 3 videos: publish immediately (1 hour from now, private override).
    After that: next day after the last scheduled video, at 7 AM IST.
    """
    published_count = count_published_episodes(language)
    now_utc = datetime.now(timezone.utc)

    if published_count < 3:
        # First 3 — schedule 1 hour from now (effectively immediate after review)
        return now_utc + timedelta(hours=1)

    # After 3 — stagger 1 day after last scheduled
    last = get_last_scheduled_time(language)
    if last:
        base = last.replace(hour=PUBLISH_HOUR_UTC, minute=PUBLISH_MINUTE_UTC,
                            second=0, microsecond=0)
        return base + timedelta(days=1)
    else:
        # Fallback: tomorrow at 7 AM IST
        tomorrow = now_utc + timedelta(days=1)
        return tomorrow.replace(hour=PUBLISH_HOUR_UTC, minute=PUBLISH_MINUTE_UTC,
                                second=0, microsecond=0)


# ── YouTube Auth ──────────────────────────────────────────────────────────────

def get_youtube_service(client_secret_path: str, token_path: str):
    import google.oauth2.credentials

    token_json_env = os.environ.get("YOUTUBE_TOKEN_JSON")
    if not token_json_env:
        raise ValueError("YOUTUBE_TOKEN_JSON environment variable not set")

    token_data = json.loads(token_json_env)

    creds = google.oauth2.credentials.Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=SCOPES,
    )

    if not creds.valid:
        creds.refresh(Request())
        print("✅ Token refreshed")

    return build("youtube", "v3", credentials=creds)


# ── Playlist ──────────────────────────────────────────────────────────────────

def get_or_create_playlist(youtube, module_name: str, language: str) -> str:
    """Get existing playlist for module or create a new one."""
    # Check Supabase for existing playlist_id
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = (sb.table("module_playlists")
             .select("playlist_id")
             .eq("module_name", module_name)
             .eq("language", language)
             .execute())

    if res.data:
        return res.data[0]["playlist_id"]

    # Create new playlist
    lang_label = "Tamil" if language == "tamil" else "English"
    playlist_title = f"{module_name} | I Have a Cause ({lang_label})"

    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": playlist_title,
                "description": (
                    f"All episodes from the module '{module_name}' — "
                    "I Have a Cause channel. Consciousness, philosophy, Tamil wisdom."
                ),
                "defaultLanguage": "ta" if language == "tamil" else "en",
            },
            "status": {"privacyStatus": "public"},
        },
    ).execute()

    playlist_id = response["id"]

    # Store in Supabase
    sb.table("module_playlists").insert({
        "module_name": module_name,
        "language": language,
        "playlist_id": playlist_id,
    }).execute()

    print(f"✅ Created playlist: {playlist_title} ({playlist_id})")
    return playlist_id


def add_to_playlist(youtube, video_id: str, playlist_id: str):
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        },
    ).execute()
    print(f"✅ Added video {video_id} to playlist {playlist_id}")


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_video(
    youtube,
    video_path: str,
    thumbnail_path: str,
    episode: dict,
    language: str,
    publish_time: datetime,
) -> str:
    ep_num     = episode["episode_number"]
    # Use correct title column based on language
    if language == "tamil":
        title_text = episode.get("title_tamil") or episode.get("title") or episode.get("episode_title", "")
    else:
        title_text = episode.get("title_english") or episode.get("title") or episode.get("episode_title", "")
    module     = episode.get("module_name", "I Have a Cause")

    # Build YouTube title
    lang_label = "Tamil" if language == "tamil" else "English"
    yt_title = f"EP {ep_num:02d} | {title_text} | I Have a Cause"
    if len(yt_title) > 100:
        yt_title = yt_title[:97] + "..."

    # Description
    script_excerpt = (episode.get("script") or "")[:300]
    description = f"""{title_text}

{script_excerpt}...

━━━━━━━━━━━━━━━━━━━━━━
🌟 I Have a Cause | என் நோக்கம்
Exploring consciousness, Tamil philosophy, and the wisdom of the Mandukya Upanishad.

📺 Watch the full series: https://www.youtube.com/@IHaveACause
━━━━━━━━━━━━━━━━━━━━━━

#IHaveACause #TamilPhilosophy #Consciousness #MandukayUpanishad #TamilWisdom #எண்நோக்கம்
"""

    tags = [
        "Tamil philosophy", "consciousness", "Mandukya Upanishad",
        "Tamil wisdom", "I Have a Cause", "self awareness",
        "Tamil spirituality", "Tamil diaspora", module,
        f"Episode {ep_num}",
    ]

    publish_at = publish_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body = {
        "snippet": {
            "title": yt_title,
            "description": description,
            "tags": tags,
            "categoryId": "27",          # Education
            "defaultLanguage": "ta" if language == "tamil" else "en",
            "defaultAudioLanguage": "ta" if language == "tamil" else "en",
        },
        "status": {
            "privacyStatus": "private",   # Always private on upload
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,   # AI voice + AI visuals — auto-disclosure
        },
    }

    media = MediaFileUpload(
        video_path,
        chunksize=10 * 1024 * 1024,   # 10 MB chunks
        resumable=True,
        mimetype="video/mp4",
    )

    print(f"⬆️  Uploading: {yt_title}")
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"   Upload progress: {pct}%")

    video_id = response["id"]
    print(f"✅ Uploaded video ID: {video_id}")

    # Set thumbnail (requires verified channel — skips gracefully if not verified)
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
        ).execute()
        print(f"✅ Thumbnail set")
    except HttpError as e:
        print(f"⚠️  Thumbnail skipped — verify your YouTube channel to enable custom thumbnails: {e}")

    # Localized titles/descriptions → cross-language search discovery.
    # (Localized THUMBNAILS still attach manually in Studio; the API can't set those.)
    try:
        loc_titles = episode.get("localized_titles") or {}
        loc_descs  = episode.get("localized_descriptions") or {}
        if isinstance(loc_titles, str): loc_titles = json.loads(loc_titles)
        if isinstance(loc_descs, str):  loc_descs  = json.loads(loc_descs)
        localizations = {
            code: {"title": lt, "description": (loc_descs.get(code) or description)}
            for code, lt in loc_titles.items() if code != "en" and lt
        }
        if localizations:
            youtube.videos().update(
                part="localizations",
                body={"id": video_id, "localizations": localizations},
            ).execute()
            print(f"✅ Localized metadata set for {len(localizations)} languages")
    except (HttpError, ValueError) as e:
        print(f"⚠️  Localized metadata skipped: {e}")

    # Upload the accurate English caption track (powers clean per-language auto-translate).
    # Needs the 'youtube.force-ssl' OAuth scope; fails gracefully if absent.
    try:
        cap_url = episode.get("captions_url")
        if cap_url:
            import urllib.request
            cap_path = "/tmp/captions_upload.srt"
            urllib.request.urlretrieve(cap_url, cap_path)
            youtube.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": "en",
                                  "name": "English", "isDraft": False}},
                media_body=MediaFileUpload(cap_path, mimetype="application/octet-stream"),
            ).execute()
            print("✅ Caption track uploaded (English) — auto-translate will localize it")
    except Exception as e:
        print(f"⚠️  Caption upload skipped (check youtube.force-ssl scope): {e}")

    return video_id


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode_number",       required=True, type=int)
    parser.add_argument("--language",             required=True)  # ta/en or tamil/english
    parser.add_argument("--video_path",           required=True)
    parser.add_argument("--thumbnail_path",       required=True)
    parser.add_argument("--token_json",           default="/tmp/yt_token.json")
    parser.add_argument("--client_secret_json",   default="/tmp/client_secret.json")
    args = parser.parse_args()

    # Normalize language to the full form used throughout this script
    language = "tamil" if args.language in ("ta", "tamil") else "english"
    episode_number = args.episode_number

    # Load episode data
    print(f"📖 Loading episode {episode_number} ({language})...")
    episode = get_episode(episode_number, language)
    if not episode:
        print(f"❌ Episode not found: {episode_number} ({language})")
        sys.exit(1)

    # Auth
    print("🔐 Authenticating with YouTube...")
    youtube = get_youtube_service(args.client_secret_json, args.token_json)

    # Calculate publish time
    publish_time = calculate_publish_time(language)
    print(f"📅 Scheduled publish time: {publish_time.isoformat()}")

    # Upload
    video_id = upload_video(
        youtube,
        video_path      = args.video_path,
        thumbnail_path  = args.thumbnail_path,
        episode         = episode,
        language        = language,
        publish_time    = publish_time,
    )

    # Playlist — keyed on the episode's MODULE, so each module (Mandukya, etc.)
    # gets its own playlist, per language. Auto-creates on first publish.
    module_name = episode.get("module") or episode.get("module_name") or "Series"
    playlist_id = get_or_create_playlist(youtube, module_name, language)
    add_to_playlist(youtube, video_id, playlist_id)

    # Update Supabase
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    update_episode(episode_number, language, {
        "youtube_video_id":  video_id,
        "youtube_url":       yt_url,
        "playlist_id":       playlist_id,
        "scheduled_at":      publish_time.isoformat(),
        "status":            "published",
    })

    print(f"\n🎉 Done! YouTube URL: {yt_url}")


if __name__ == "__main__":
    main()
