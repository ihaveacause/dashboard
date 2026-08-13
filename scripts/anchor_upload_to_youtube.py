"""
anchor_upload_to_youtube.py — On Camera (YouTube Long) · Publish + GCS Cleanup
================================================================================
Sprint 19 changes:
  - Series episode: title pulled from tamil/english_episodes table by episode_number
  - Standalone: title used as entered in dashboard
  - Thumbnail upload REMOVED — set manually in YouTube Studio after publish
  - GCS delete step unchanged from Sprint 17

Args: --record_id <uuid>  --language <ta|en>  --step <upload|delete_gcs>
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

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

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
GCP_CREDS_JSON = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
GCS_BUCKET     = "ihaveacause-media"

PUBLISH_HOUR_UTC, PUBLISH_MINUTE_UTC = 1, 30   # 7:00 AM IST


# ── Supabase helpers ──────────────────────────────────────────
def anchor_table(language):
    return "tamil_anchor" if language in ("ta", "tamil") else "english_anchor"

def episode_table(language):
    return "tamil_episodes" if language in ("ta", "tamil") else "english_episodes"

def get_row(record_id, language):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = sb.table(anchor_table(language)).select("*").eq("id", record_id).single().execute()
    return res.data

def update_row(record_id, language, updates):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    sb.table(anchor_table(language)).update(updates).eq("id", record_id).execute()
    print(f"✅ Supabase updated: {list(updates.keys())}")

def get_episode_title(episode_number, language):
    """
    Fetch the episode title from tamil/english_episodes by episode_number.
    Returns title string or None if not found.
    """
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    table = episode_table(language)
    try:
        res = (sb.table(table)
                 .select("title_tamil,title_english")
                 .eq("episode_number", episode_number)
                 .single()
                 .execute())
        ep = res.data
        if not ep:
            return None
        # Use language-appropriate title, fall back to the other
        if language in ("ta", "tamil"):
            return ep.get("title_tamil") or ep.get("title_english")
        else:
            return ep.get("title_english") or ep.get("title_tamil")
    except Exception as e:
        print(f"   ⚠️  Episode lookup failed: {e}", flush=True)
        return None

def count_published(language):
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    res = (sb.table(anchor_table(language))
             .select("id", count="exact")
             .not_.is_("youtube_video_id", "null")
             .execute())
    return res.count or 0


# ── YouTube helpers ───────────────────────────────────────────
def get_youtube():
    token_json = os.environ["YOUTUBE_TOKEN_JSON"]
    info = json.loads(token_json)
    creds = google.oauth2.credentials.Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)

def calculate_publish_time(language):
    now = datetime.now(timezone.utc)
    if count_published(language) < 3:
        return now + timedelta(hours=1)
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=PUBLISH_HOUR_UTC, minute=PUBLISH_MINUTE_UTC,
                            second=0, microsecond=0)

def download(url, dest):
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest

def build_description(row, language, title):
    module  = row.get("module") or "Commentary"
    ep_num  = row.get("episode_number")
    lang    = "Tamil" if language in ("ta", "tamil") else "English"
    opinion = "இது என் தனிப்பட்ட கருத்து." if language in ("ta", "tamil") \
              else "This is my personal opinion."
    ep_line = f"Episode: {ep_num}\n" if ep_num else ""
    return (
        f"{title}\n\n"
        f"{ep_line}"
        f"{opinion}\n\n"
        f"Module: {module}\n\n"
        f"#IHaveACause #{lang} #Opinion #{module.replace(' ','')}"
    )

def resolve_title(row, language):
    """
    Series episode  → pull title from episodes table by episode_number
    Standalone      → use row.title directly
    Falls back to row.working_title if nothing else available.
    """
    ep_num = row.get("episode_number")
    if ep_num:
        print(f"   📺 Series episode {ep_num} — fetching title from episodes table…", flush=True)
        ep_title = get_episode_title(ep_num, language)
        if ep_title:
            print(f"   ✅ Episode title: {ep_title}", flush=True)
            return ep_title
        else:
            print(f"   ⚠️  Episode title not found — falling back to row title", flush=True)
    return row.get("title") or row.get("working_title") or "On Camera"

def upload_video(youtube, video_path, row, language, publish_time):
    lang    = "ta" if language in ("ta", "tamil") else "en"
    title   = resolve_title(row, language)
    suffix  = "| ஒரு காரணம் இருக்கிறது" if lang == "ta" else "| I Have a Cause"
    yt_title = f"{title} {suffix}"[:100]

    body = {
        "snippet": {
            "title": yt_title,
            "description": build_description(row, language, title),
            "tags": ["IHaveACause", "Opinion", "Commentary",
                     row.get("module") or "Commentary"],
            "categoryId": "25",  # News & Politics
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    print(f"⬆️  Uploading: {yt_title}", flush=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"   {int(status.progress()*100)}%", flush=True)
    vid = resp["id"]
    print(f"✅ Uploaded: {vid}", flush=True)
    print(f"   ℹ️  Add your custom thumbnail in YouTube Studio: "
          f"https://studio.youtube.com/video/{vid}/edit", flush=True)
    return vid

def get_or_create_playlist(youtube, module_name, language):
    lang     = "ta" if language in ("ta", "tamil") else "en"
    pl_title = f"{module_name} | I Have a Cause"
    try:
        res = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        for pl in res.get("items", []):
            if pl["snippet"]["title"] == pl_title:
                print(f"   📋 Playlist: {pl['id']}")
                return pl["id"]
        pl = youtube.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": pl_title, "defaultLanguage": lang},
                  "status": {"privacyStatus": "public"}}
        ).execute()
        print(f"   📋 Created playlist: {pl['id']}")
        return pl["id"]
    except Exception as e:
        print(f"   ⚠️  Playlist error: {e}")
        return None

def add_to_playlist(youtube, video_id, playlist_id):
    if not playlist_id:
        return
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video",
                                             "videoId": video_id}}}
        ).execute()
        print(f"✅ Added to playlist {playlist_id}")
    except Exception as e:
        print(f"⚠️  Playlist insert failed: {e}")


# ── GCS delete ────────────────────────────────────────────────
def _gcs_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON),
        scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    creds.refresh(gr.Request())
    return creds.token

def _list_gcs_objects(prefix, token):
    r = requests.get(
        f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o",
        headers={"Authorization": f"Bearer {token}"},
        params={"prefix": prefix}, timeout=30)
    return [item["name"] for item in r.json().get("items", [])] if r.status_code == 200 else []

def _delete_gcs_object(name, token):
    encoded = quote(name, safe="")
    r = requests.delete(
        f"https://storage.googleapis.com/storage/v1/b/{GCS_BUCKET}/o/{encoded}",
        headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code in (200, 204):
        print(f"   🗑  {name}", flush=True); return True
    print(f"   ⚠️  Failed {name}: {r.status_code}", flush=True); return False

def delete_gcs_files(record_id, language):
    print(f"\n🗑  Deleting GCS files for {record_id}…", flush=True)
    row = get_row(record_id, language)
    if not row:
        print("❌ Record not found"); return False
    if row.get("status") != "published":
        print(f"❌ Status is '{row.get('status')}' — only deletes after published"); return False
    if not row.get("youtube_video_id"):
        print("❌ No youtube_video_id — won't delete before confirmed YouTube upload"); return False

    token   = _gcs_token()
    prefix  = f"anchor/{record_id}/"
    objects = _list_gcs_objects(prefix, token)
    print(f"   Found {len(objects)} object(s) under {prefix}", flush=True)
    deleted = sum(1 for name in objects if _delete_gcs_object(name, token))

    # Also delete image overlay files
    img_overlays = row.get("image_overlays") or []
    if isinstance(img_overlays, str):
        try: img_overlays = json.loads(img_overlays)
        except: img_overlays = []
    for ov in img_overlays:
        url = ov.get("url", "")
        marker = f"storage.googleapis.com/{GCS_BUCKET}/"
        if marker in url:
            obj_name = url.split(marker, 1)[1].split("?")[0]
            _delete_gcs_object(obj_name, token)

    update_row(record_id, language, {
        "source_video_url": None, "video_url": None,
        "thumbnail_url": None, "gcs_deleted": True,
    })
    print(f"\n✅ GCS cleanup done — YouTube video unaffected.", flush=True)
    return True


# ── Main ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record_id", required=True)
    ap.add_argument("--language",  required=True)
    ap.add_argument("--step", default="upload", choices=["upload", "delete_gcs"])
    args = ap.parse_args()
    language = args.language

    if args.step == "delete_gcs":
        if not GCP_CREDS_JSON:
            print("❌ GOOGLE_APPLICATION_CREDENTIALS_JSON not set"); sys.exit(1)
        ok = delete_gcs_files(args.record_id, language)
        sys.exit(0 if ok else 1)

    # Upload step
    row = get_row(args.record_id, language)
    if not row:
        print("❌ Record not found"); sys.exit(1)
    if not row.get("video_url"):
        print("❌ No rendered video_url — run Render first"); sys.exit(1)

    youtube      = get_youtube()
    publish_time = calculate_publish_time(language)
    print(f"📅 Scheduled: {publish_time.isoformat()}", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        vp  = download(row["video_url"], os.path.join(tmp, "final.mp4"))
        vid = upload_video(youtube, vp, row, language, publish_time)

    module_name = row.get("module") or "Commentary"
    pid = get_or_create_playlist(youtube, module_name, language)
    if pid:
        add_to_playlist(youtube, vid, pid)

    yt_url = f"https://www.youtube.com/watch?v={vid}"
    update_row(args.record_id, language, {
        "youtube_video_id": vid,
        "youtube_url":      yt_url,
        "playlist_id":      pid,
        "scheduled_at":     publish_time.isoformat(),
        "status":           "published",
    })
    print(f"\n🎉 Done: {yt_url}")
    print(f"   👉 Add thumbnail: https://studio.youtube.com/video/{vid}/edit")


if __name__ == "__main__":
    main()
