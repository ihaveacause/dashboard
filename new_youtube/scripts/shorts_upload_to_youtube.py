"""
shorts_upload_to_youtube.py — Sprint 15 (Shorts track)
========================================================
Publishes ONE rendered short to YouTube:
- Title tagged #Shorts (vertical + short duration = YouTube treats it as a Short)
- Description links straight to the parent long episode (once it has a
  youtube_url) so the provocation in the short has somewhere to land
- Always private-on-upload, scheduled like the rest of the channel

Mirrors idea_upload_to_youtube.py's auth/upload/playlist pattern.

Usage (called by GitHub Actions):
  python shorts_upload_to_youtube.py --short_id "<uuid>" --language "ta"
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from supabase import create_client, Client

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@IHaveACause"


def get_short(short_id: str, language: str) -> dict:
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = "tamil_shorts" if language == "tamil" else "english_shorts"
    return sb.table(table).select("*").eq("id", short_id).single().execute().data


def get_parent_episode(episode_number: int, language: str) -> dict | None:
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = "tamil_episodes" if language == "tamil" else "english_episodes"
    res = sb.table(table).select("title_tamil,title_english,youtube_url,module").eq(
        "episode_number", episode_number
    ).execute()
    return res.data[0] if res.data else None


def update_short(short_id: str, language: str, updates: dict):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = "tamil_shorts" if language == "tamil" else "english_shorts"
    sb.table(table).update(updates).eq("id", short_id).execute()
    print(f"✅ Supabase updated: {updates}")


def get_youtube_service():
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


def calculate_publish_time() -> datetime:
    # Shorts drop fast — 30 min after upload, ahead of / around the long video
    return datetime.now(timezone.utc) + timedelta(minutes=30)


def upload_short(youtube, video_path, short, episode, language, publish_time) -> str:
    lang_label = "Tamil" if language == "tamil" else "English"
    title_text = short.get("title") or "I Have a Cause"

    yt_title = f"{title_text} #Shorts"
    if len(yt_title) > 100:
        yt_title = yt_title[:90].rsplit(" ", 1)[0] + "… #Shorts"

    parent_title = (episode or {}).get("title_tamil") or (episode or {}).get("title_english") or ""
    parent_url   = (episode or {}).get("youtube_url") or YOUTUBE_CHANNEL_URL

    description = f"""{short.get('hook_line','')}

{"Full story here: " + parent_url if (episode or {}).get('youtube_url') else "Full episode dropping soon on the channel: " + YOUTUBE_CHANNEL_URL}
{("Episode: " + parent_title) if parent_title else ""}

━━━━━━━━━━━━━━━━━━━━━━
🌟 I Have a Cause
📺 {YOUTUBE_CHANNEL_URL}
━━━━━━━━━━━━━━━━━━━━━━

#IHaveACause #Shorts
"""

    body = {
        "snippet": {
            "title": yt_title,
            "description": description,
            "tags": ["Shorts", "I Have a Cause", lang_label],
            "categoryId": "27",
            "defaultLanguage": "ta" if language == "tamil" else "en",
            "defaultAudioLanguage": "ta" if language == "tamil" else "en",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(video_path, chunksize=10 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    print(f"⬆️  Uploading: {yt_title}")
    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   Upload progress: {int(status.progress()*100)}%")

    video_id = response["id"]
    print(f"✅ Uploaded short ID: {video_id}")
    return video_id


def get_or_create_playlist(youtube, module_name: str, language: str) -> str | None:
    if not module_name:
        return None
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = sb.table("module_playlists").select("playlist_id").eq("module_name", module_name).eq("language", language).execute()
    if res.data:
        return res.data[0]["playlist_id"]
    lang_label = "Tamil" if language == "tamil" else "English"
    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": f"{module_name} | I Have a Cause ({lang_label})",
                "description": f"Episodes and shorts from '{module_name}' — I Have a Cause.",
                "defaultLanguage": "ta" if language == "tamil" else "en",
            },
            "status": {"privacyStatus": "public"},
        },
    ).execute()
    playlist_id = response["id"]
    sb.table("module_playlists").insert({"module_name": module_name, "language": language, "playlist_id": playlist_id}).execute()
    return playlist_id


def add_to_playlist(youtube, video_id: str, playlist_id: str):
    youtube.playlistItems().insert(
        part="snippet",
        body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
    ).execute()
    print(f"✅ Added short {video_id} to playlist {playlist_id}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--short_id", required=True)
    parser.add_argument("--language", required=True)  # ta/en or tamil/english
    parser.add_argument("--video_path", default="/tmp/short.mp4")
    args = parser.parse_args()

    language = "tamil" if args.language in ("ta", "tamil") else "english"

    print(f"📖 Loading short {args.short_id} ({language})...")
    short = get_short(args.short_id, language)
    if not short:
        print(f"❌ Short not found: {args.short_id}")
        sys.exit(1)
    if not short.get("video_url"):
        print("❌ Short has no rendered video_url — render it first")
        sys.exit(1)

    print("⬇️  Downloading rendered video...")
    urllib.request.urlretrieve(short["video_url"], args.video_path)

    episode = get_parent_episode(short["episode_number"], language)

    print("🔐 Authenticating with YouTube...")
    youtube = get_youtube_service()

    publish_time = calculate_publish_time()
    print(f"📅 Scheduled publish time: {publish_time.isoformat()}")

    video_id = upload_short(youtube, args.video_path, short, episode, language, publish_time)

    module_name = (episode or {}).get("module")
    playlist_id = get_or_create_playlist(youtube, module_name, language)
    if playlist_id:
        add_to_playlist(youtube, video_id, playlist_id)

    yt_url = f"https://www.youtube.com/shorts/{video_id}"
    update_short(args.short_id, language, {
        "youtube_video_id": video_id,
        "youtube_url": yt_url,
        "parent_youtube_url": (episode or {}).get("youtube_url"),
        "scheduled_at": publish_time.isoformat(),
        "status": "published",
    })

    print(f"\n🎉 Done! Short URL: {yt_url}")


if __name__ == "__main__":
    main()
