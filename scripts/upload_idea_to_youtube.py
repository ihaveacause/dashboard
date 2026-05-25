"""
upload_idea_to_youtube.py — Sprint 8
======================================
Uploads an idea's rendered video to YouTube.
Reuses the same OAuth2 flow as upload_to_youtube.py for episodes.
- Reads idea from Supabase by IDEA_ID
- Downloads video from video_url (GCS)
- Uploads to YouTube with title, description, thumbnail
- Saves youtube_video_id, youtube_url to Supabase
- Sets idea status → published

Triggered by: upload_idea_to_youtube.yml
Env vars: SUPABASE_URL, SUPABASE_KEY, YOUTUBE_OAUTH_JSON, IDEA_ID, LANGUAGE
"""

import os
import json
import tempfile
import requests

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
YOUTUBE_OAUTH  = os.environ["YOUTUBE_OAUTH_JSON"]  # Full OAuth2 token JSON
IDEA_ID        = os.environ["IDEA_ID"]
LANGUAGE       = os.environ.get("LANGUAGE", "ta")

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# ── Helpers ────────────────────────────────────────────────────
def sb_get(table, filters):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{filters}&limit=1", headers=SB_HEADERS)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No row found in {table} with {filters}")
    return data[0]

def sb_patch(table, match_col, match_val, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{match_col}=eq.{match_val}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json=data,
    )
    r.raise_for_status()

def get_youtube_service():
    token_data = json.loads(YOUTUBE_OAUTH)
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)

def build_description(idea, language):
    lang_full = "Tamil" if language == "ta" else "English"
    desc = idea.get("description", "")
    angle = idea.get("research_angle", "")
    return (
        f"{desc}\n\n"
        f"{angle}\n\n"
        f"I Have a Cause — {lang_full}\n\n"
        f"#IHaveACause #Tamil #Philosophy #SelfGrowth"
    ).strip()

# ── Main ───────────────────────────────────────────────────────
def main():
    print(f"Sprint 8 | Upload Idea to YouTube")
    print(f"  Idea ID: {IDEA_ID}")
    print(f"  Language: {LANGUAGE}")

    # 1. Fetch idea
    idea = sb_get("ideas", f"id=eq.{IDEA_ID}")
    title = idea.get("title", "")
    lang_full = "tamil" if LANGUAGE == "ta" else "english"

    # 2. Get video URL (language-specific)
    video_url = idea.get(f"video_url_{lang_full}") or idea.get("video_url")
    if not video_url:
        raise ValueError(f"No video_url found for idea {IDEA_ID}")
    print(f"  Video URL: {video_url}")

    # 3. Download video
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "idea_video.mp4")
        print("  Downloading video...")
        r = requests.get(video_url, stream=True, timeout=60)
        r.raise_for_status()
        with open(video_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        size_mb = os.path.getsize(video_path) / 1024 / 1024
        print(f"  Downloaded: {size_mb:.1f}MB")

        # 4. Build upload metadata
        yt_title = f"{title} | I Have a Cause"
        if LANGUAGE == "ta":
            yt_title = f"{title} | ஒரு காரணம் இருக்கிறது"
        description = build_description(idea, LANGUAGE)

        body = {
            "snippet": {
                "title": yt_title[:100],
                "description": description[:5000],
                "tags": ["IHaveACause", "Tamil", "Philosophy", "SelfGrowth", "Motivation"],
                "categoryId": "27",  # Education
                "defaultLanguage": "ta" if LANGUAGE == "ta" else "en",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        # 5. Upload to YouTube
        print("  Uploading to YouTube...")
        youtube = get_youtube_service()
        media = MediaFileUpload(video_path, chunksize=5 * 1024 * 1024, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  Upload: {pct}%")

    video_id  = response["id"]
    video_url_yt = f"https://www.youtube.com/watch?v={video_id}"
    print(f"  Uploaded: {video_url_yt}")

    # 6. Upload thumbnail if available
    thumbnail_url = idea.get("thumbnail_url")
    if thumbnail_url:
        try:
            print("  Uploading thumbnail...")
            r = requests.get(thumbnail_url, timeout=30)
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                tf.write(r.content)
                tmp_thumb = tf.name
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(tmp_thumb)).execute()
            os.unlink(tmp_thumb)
            print("  Thumbnail set")
        except Exception as e:
            print(f"  Warning: thumbnail upload failed: {e}")

    # 7. Save to Supabase
    url_col = "youtube_url"
    id_col  = "youtube_video_id"
    sb_patch("ideas", "id", IDEA_ID, {
        url_col: video_url_yt,
        id_col:  video_id,
        "status": "published",
    })
    print(f"✅ Supabase updated — ideas.status → published")
    print(f"✅ YouTube upload done: {video_url_yt}")

if __name__ == "__main__":
    main()
