"""
post_to_x.py — Sprint 8
=========================
Posts a full thread to X (Twitter) with one image per tweet.
- Reads thread script from Supabase
- Reads pre-generated x_images from Supabase
- Optionally appends YouTube link to first tweet
- Posts via Twitter API v2 (tweepy)
- Saves x_post_url_tamil / x_post_url_english + sets status_x → published

Triggered by: post_to_x.yml
Env vars: SUPABASE_URL, SUPABASE_KEY,
          TWITTER_API_KEY, TWITTER_API_SECRET,
          TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET,
          EPISODE_NUMBER (or IDEA_ID),
          LANGUAGE (ta or en),
          YOUTUBE_LINK (optional)
"""

import os
import json
import tempfile
import requests
import time

import tweepy

SUPABASE_URL          = os.environ["SUPABASE_URL"]
SUPABASE_KEY          = os.environ["SUPABASE_KEY"]
TWITTER_API_KEY       = os.environ["TWITTER_API_KEY"]
TWITTER_API_SECRET    = os.environ["TWITTER_API_SECRET"]
TWITTER_ACCESS_TOKEN  = os.environ["TWITTER_ACCESS_TOKEN"]
TWITTER_ACCESS_SECRET = os.environ["TWITTER_ACCESS_SECRET"]
EPISODE_NUMBER = os.environ.get("EPISODE_NUMBER")
IDEA_ID        = os.environ.get("IDEA_ID")
LANGUAGE       = os.environ.get("LANGUAGE", "ta")   # ta or en
YOUTUBE_LINK   = os.environ.get("YOUTUBE_LINK", "").strip()

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# ── Tweepy clients ─────────────────────────────────────────────
auth = tweepy.OAuth1UserHandler(
    TWITTER_API_KEY,
    TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN,
    TWITTER_ACCESS_SECRET,
)
twitter_v1 = tweepy.API(auth)                     # v1.1 for media upload
twitter_v2 = tweepy.Client(
    consumer_key=TWITTER_API_KEY,
    consumer_secret=TWITTER_API_SECRET,
    access_token=TWITTER_ACCESS_TOKEN,
    access_token_secret=TWITTER_ACCESS_SECRET,
)

# ── Helpers ────────────────────────────────────────────────────
def sb_get(table, filters):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{filters}&limit=1", headers=SB_HEADERS)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No row in {table} with {filters}")
    return data[0]

def sb_patch(table, match_col, match_val, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{match_col}=eq.{match_val}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json=data,
    )
    r.raise_for_status()

def parse_thread(thread_text):
    """Split thread into individual tweets (blank-line separated)."""
    tweets = [t.strip() for t in thread_text.split("\n\n") if t.strip()]
    return tweets[:6]

def upload_media(image_url):
    """Download image and upload to Twitter, return media_id."""
    r = requests.get(image_url, timeout=30)
    r.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(r.content)
        tmp_path = f.name

    media = twitter_v1.media_upload(tmp_path)
    os.unlink(tmp_path)
    print(f"  Uploaded media: {media.media_id_string}")
    return media.media_id_string

def post_tweet(text, media_id=None, reply_to_id=None):
    """Post a single tweet, return tweet id."""
    kwargs = {"text": text}
    if media_id:
        kwargs["media_ids"] = [media_id]
    if reply_to_id:
        kwargs["in_reply_to_tweet_id"] = reply_to_id

    response = twitter_v2.create_tweet(**kwargs)
    tweet_id = response.data["id"]
    print(f"  Posted tweet: https://x.com/i/web/status/{tweet_id}")
    return tweet_id

# ── Main ───────────────────────────────────────────────────────
def main():
    is_idea = bool(IDEA_ID)
    lang_full = "tamil" if LANGUAGE == "ta" else "english"
    print(f"Sprint 8 | Post to X")
    print(f"  Source: {'idea ' + IDEA_ID if is_idea else 'episode ' + EPISODE_NUMBER}")
    print(f"  Language: {lang_full}")
    print(f"  YouTube link: {YOUTUBE_LINK or '(none)'}")

    # 1. Fetch row
    if is_idea:
        row = sb_get("ideas", f"id=eq.{IDEA_ID}")
        table = "ideas"
        match_col, match_val = "id", IDEA_ID
    else:
        table_name = "tamil_episodes" if LANGUAGE == "ta" else "english_episodes"
        row = sb_get(table_name, f"episode_number=eq.{EPISODE_NUMBER}")
        table = table_name
        match_col, match_val = "episode_number", EPISODE_NUMBER

    # 2. Get thread script
    thread_col = f"script_x_thread_{lang_full}"
    thread_text = row.get(thread_col, "")
    if not thread_text:
        raise ValueError(f"No X thread script found in '{thread_col}'")
    tweets = parse_thread(thread_text)
    print(f"  Thread: {len(tweets)} tweets")

    # 3. Get images
    images_col = f"x_images_{lang_full}"
    raw_images = row.get(images_col, [])
    if isinstance(raw_images, str):
        raw_images = json.loads(raw_images)

    # Build image map: tweet_index → url
    image_map = {img["tweet_index"]: img["url"] for img in (raw_images or [])}
    print(f"  Images: {len(image_map)} available")

    # 4. Post thread
    tweet_ids = []
    reply_to = None

    for i, tweet_text in enumerate(tweets):
        tweet_num = i + 1
        final_text = tweet_text

        # Append YouTube link to first tweet if provided
        if i == 0 and YOUTUBE_LINK:
            final_text = f"{tweet_text}\n\n🎬 {YOUTUBE_LINK}"

        # Enforce 280-char limit (Twitter limit)
        if len(final_text) > 280:
            final_text = final_text[:277] + "..."

        # Upload image if available
        media_id = None
        if tweet_num in image_map:
            try:
                media_id = upload_media(image_map[tweet_num])
            except Exception as e:
                print(f"  Warning: could not upload image for tweet {tweet_num}: {e}")

        print(f"  Posting tweet {tweet_num}/{len(tweets)}...")
        try:
            tweet_id = post_tweet(final_text, media_id, reply_to)
            tweet_ids.append(tweet_id)
            reply_to = tweet_id
            time.sleep(2)  # Brief pause between tweets
        except Exception as e:
            print(f"  ERROR posting tweet {tweet_num}: {e}")
            raise

    # 5. Save to Supabase
    first_id  = tweet_ids[0]
    post_url  = f"https://x.com/i/web/status/{first_id}"
    url_col   = f"x_post_url_{lang_full}"
    id_col    = f"x_post_id_{lang_full}"

    patch_data = {
        url_col: post_url,
        id_col:  str(first_id),
        "status_x": "published",
    }
    sb_patch(table, match_col, match_val, patch_data)

    print(f"\n✅ Thread posted: {post_url}")
    print(f"✅ Supabase updated — status_x → published")

if __name__ == "__main__":
    main()
