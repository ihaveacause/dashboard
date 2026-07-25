"""
anchor_upload_to_youtube.py — On Camera (Studio Desk) · Publish step
====================================================================
Uploads the studio render to YouTube as a scheduled video and routes it to a
playlist by `module`. Mirrors scripts/upload_to_youtube.py, but the anchor track
is keyed by the row's UUID (RECORD_ID), not an episode_number, and the title is
the one YOU picked (row.title).

Reuses the SAME secrets your existing uploader uses:
  YOUTUBE_TOKEN_JSON, YOUTUBE_CHANNEL_ID, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Args (from the workflow):  --record_id <uuid>  --language <ta|en>
"""

import argparse
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import requests
import google.oauth2.credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from supabase import create_client, Client

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

PUBLISH_HOUR_UTC, PUBLISH_MINUTE_UTC = 1, 30   # 7:00 AM IST


def table(language):
    return "tamil_anchor" if language in ("ta", "tamil") else "english_anchor"

def get_row(record_id, language):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = sb.table(table(language)).select("*").eq("id", record_id).single().execute()
    return res.data

def update_row(record_id, language, updates):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    sb.table(table(language)).update(updates).eq("id", record_id).execute()
    print(f"✅ Supabase updated: {updates}")

def count_published(language):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = (sb.table(table(language)).select("id", count="exact")
             .not_.is_("youtube_video_id", "null").execute())
    return res.count or 0

def calculate_publish_time(language):
    now = datetime.now(timezone.utc)
    if count_published(language) < 3:
        return now + timedelta(hours=1)            # first few go ~now (after review)
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=PUBLISH_HOUR_UTC, minute=PUBLISH_MINUTE_UTC,
                            second=0, microsecond=0)

def get_youtube():
    tok = os.environ.get("YOUTUBE_TOKEN_JSON")
    if not tok:
        raise ValueError("YOUTUBE_TOKEN_JSON not set")
    import json
    t = json.loads(tok)
    creds = google.oauth2.credentials.Credentials(
        token=t.get("token"), refresh_token=t.get("refresh_token"),
        token_uri=t.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=t.get("client_id"), client_secret=t.get("client_secret"), scopes=SCOPES)
    if not creds.valid:
        creds.refresh(Request()); print("✅ Token refreshed")
    return build("youtube", "v3", credentials=creds)

def get_or_create_playlist(youtube, module_name, language):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    lang_db = "tamil" if language in ("ta", "tamil") else "english"       # matches the actual
    lang_display = "Tamil" if language in ("ta", "tamil") else "English"  # DB check-constraint:
    # CHECK (language = ANY (ARRAY['tamil','english'])) — lowercase, confirmed via
    # pg_get_constraintdef. lang_display is only for the playlist title text.
    res = (sb.table("module_playlists").select("playlist_id")
             .eq("module_name", module_name).eq("language", lang_db).execute())
    if res.data:
        return res.data[0]["playlist_id"]
    resp = youtube.playlists().insert(part="snippet,status", body={
        "snippet": {"title": f"{module_name} | I Have a Cause ({lang_display})",
                    "description": f"On-camera commentary — module '{module_name}', I Have a Cause.",
                    "defaultLanguage": "ta" if language in ("ta", "tamil") else "en"},
        "status": {"privacyStatus": "public"}}).execute()
    pid = resp["id"]
    sb.table("module_playlists").insert(
        {"module_name": module_name, "language": lang_db, "playlist_id": pid}).execute()
    print(f"✅ Created playlist {pid}")
    return pid

def add_to_playlist(youtube, video_id, playlist_id):
    youtube.playlistItems().insert(part="snippet", body={
        "snippet": {"playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    print(f"✅ Added {video_id} to playlist {playlist_id}")

def download(url, dest):
    r = requests.get(url, stream=True, timeout=600)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for c in r.iter_content(1 << 16):
            f.write(c)
    return dest

def upload_video(youtube, video_path, row, language, publish_time):
    lang = "ta" if language in ("ta", "tamil") else "en"
    title_text = (row.get("title") or row.get("working_title") or "Commentary").strip()
    yt_title = f"{title_text} | I Have a Cause"
    if len(yt_title) > 100:
        yt_title = yt_title[:97] + "..."
    transcript_excerpt = (row.get("transcript") or "")[:300]
    description = (
        f"{title_text}\n\n{transcript_excerpt}...\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎙️ Opinion / commentary — I Have a Cause\n"
        "📺 https://www.youtube.com/@IHaveACause\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "This video expresses the host's personal views and opinions.\n\n"
        "#IHaveACause #Commentary #Opinion #Tamil"
    )
    body = {
        "snippet": {
            "title": yt_title, "description": description,
            "tags": ["I Have a Cause", "commentary", "opinion", row.get("module") or "Commentary"],
            "categoryId": "25",                    # News & Politics
            "defaultLanguage": lang, "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": "private",            # scheduled → private until publishAt
            "publishAt": publish_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    print("⬆️  Uploading to YouTube...")
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"   {int(status.progress()*100)}%")
    vid = resp["id"]
    print(f"✅ Uploaded: {vid}")
    return vid

def set_thumbnail(youtube, video_id, thumbnail_url, tmp):
    if not thumbnail_url:
        print("   ℹ️  No thumbnail_url on this record — YouTube will auto-pick a frame.")
        return
    try:
        tp = download(thumbnail_url, os.path.join(tmp, "thumb.jpg"))
        youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(tp, mimetype="image/jpeg"),
        ).execute()
        print("✅ Thumbnail set")
    except HttpError as e:
        print(f"⚠️  Thumbnail skipped — verify your YouTube channel to enable custom thumbnails: {e}")
    except Exception as e:
        print(f"⚠️  Thumbnail skipped — {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record_id", required=True)
    ap.add_argument("--language", required=True)
    args = ap.parse_args()
    language = "tamil" if args.language in ("ta", "tamil") else "english"

    row = get_row(args.record_id, args.language)
    if not row:
        print("❌ Record not found"); sys.exit(1)
    if not row.get("video_url"):
        print("❌ No rendered video_url — run Render first"); sys.exit(1)

    youtube = get_youtube()
    publish_time = calculate_publish_time(args.language)
    print(f"📅 Scheduled: {publish_time.isoformat()}")

    with tempfile.TemporaryDirectory() as tmp:
        vp = download(row["video_url"], os.path.join(tmp, "final.mp4"))
        vid = upload_video(youtube, vp, row, args.language, publish_time)
        set_thumbnail(youtube, vid, row.get("thumbnail_url"), tmp)

    module_name = row.get("module") or "Commentary"
    pid = get_or_create_playlist(youtube, module_name, args.language)
    add_to_playlist(youtube, vid, pid)

    yt_url = f"https://www.youtube.com/watch?v={vid}"
    update_row(args.record_id, args.language, {
        "youtube_video_id": vid, "youtube_url": yt_url, "playlist_id": pid,
        "scheduled_at": publish_time.isoformat(), "status": "published",
    })
    print(f"\n🎉 Done: {yt_url}")


if __name__ == "__main__":
    main()
