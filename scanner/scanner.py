"""
I Have a Cause — News Scanner Bot (Final)
- Only fetches news < 48 hours old
- Auto-deletes pending stories > 48 hours at start of each run
- Recommends formats based on niche + viral score
- No script generation (handled by script_generator.py)
"""

import os
import json
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timezone, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}

REST_URL = f"{SUPABASE_URL}/rest/v1"

GOOGLE_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=Tamil+Nadu&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Chennai+news&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=DMK+AIADMK+Tamil+Nadu&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Kollywood+Tamil+cinema&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Tamil+Nadu+crime&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=CSK+IPL+2026&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Tamil+Nadu+viral&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=India+trending+viral&hl=en-IN&gl=IN&ceid=IN:en",
]

NICHE_KEYWORDS = {
    "politics":      ["dmk","aiadmk","stalin","tamil nadu government","bjp","election","parliament","modi","edappadi","seeman","ntk","minister","mla","mp"],
    "crime":         ["arrest","murder","crime","scam","corruption","cbi","drug","robbery","fraud","cheating","accused","police"],
    "entertainment": ["kollywood","tamil cinema","vijay","ajith","rajinikanth","tamil movie","thalapathy","sun tv","vijay tv","kamal","actor","actress","ott","film","box office"],
    "sports":        ["csk","ipl","cricket","kabaddi","football","sports","karthik","ashwin","dhoni","match","tournament"],
    "business":      ["economy","startup","it","infosys","tcs","investment","jobs","employment","industry","manufacturing"],
    "viral":         ["viral","trending","social media","meme","twitter","funny","shock","outrage","controversy"],
}

VIRALITY_BOOSTERS = ["breaking","exclusive","arrest","viral","shocking","massive","historic","record","exposed","leaked","ban","death","scam","crisis","resign","fired","win","victory","controversy","outrage","protest"]

# ── Format recommendation engine ──────────────────────────────────────────────
def recommend_formats(niche, viral_score, title):
    title_lower = title.lower()
    recs = []

    breaking_words = ["breaking","arrest","resign","dies","death","ban","crisis","exposed","leaked","scam","exclusive"]
    is_breaking = viral_score >= 70 or any(w in title_lower for w in breaking_words)

    # YT Long — for all substantive stories
    if viral_score >= 45:
        recs.append("yt_long")

    # Breaking news — X first for speed
    if is_breaking:
        recs.append("x_post")
        recs.append("x_thread")

    # Visual niches — Reels + Meme
    if niche in ["viral", "entertainment"]:
        recs.append("reels")
        recs.append("meme")
        recs.append("yt_short")

    # Sports — short + x
    if niche == "sports":
        recs.append("yt_short")
        recs.append("x_post")

    # Analysis niches — depth first
    if niche in ["politics", "crime", "business"]:
        recs.append("yt_short")
        recs.append("x_thread")

    # Very viral — all formats
    if viral_score >= 80:
        for f in ["yt_long","yt_short","reels","meme","x_thread","x_post"]:
            if f not in recs:
                recs.append(f)

    # Always at least YT Short
    if "yt_short" not in recs:
        recs.append("yt_short")

    # Deduplicate preserving order
    seen, result = set(), []
    for f in recs:
        if f not in seen:
            seen.add(f)
            result.append(f)

    return json.dumps(result)

# ── Virality + niche ──────────────────────────────────────────────────────────
def score_virality(title, description=""):
    text = (title + " " + (description or "")).lower()
    score = 40
    for word in VIRALITY_BOOSTERS:
        if word in text:
            score += 8
    return min(score, 100)

def classify_niche(title, description=""):
    text = (title + " " + (description or "")).lower()
    best, best_count = "general", 0
    for niche, keywords in NICHE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count:
            best_count, best = count, niche
    return best

# ── Supabase helpers ──────────────────────────────────────────────────────────
def db_select(table, filters=None):
    url    = f"{REST_URL}/{table}"
    params = filters or {}
    resp   = requests.get(url, headers={**HEADERS, "Prefer": "return=representation"}, params=params, timeout=10)
    return resp.json() if resp.status_code == 200 else []

def db_insert(table, row):
    resp = requests.post(f"{REST_URL}/{table}", headers=HEADERS, json=row, timeout=10)
    return resp.status_code in (200, 201)

def db_delete_old_pending():
    """Permanently delete pending stories older than 48 hours"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    url    = f"{REST_URL}/content_queue"
    params = {
        "status":     "eq.pending",
        "created_at": f"lt.{cutoff}"
    }
    resp = requests.delete(url, headers=HEADERS, params=params, timeout=10)
    if resp.status_code in (200, 204):
        return True
    print(f"  ⚠️  Delete old pending failed: {resp.status_code} {resp.text[:100]}")
    return False

# ── Parse pub date ────────────────────────────────────────────────────────────
def parse_pub_date(pub_str):
    """Parse RSS pubDate and return datetime or None"""
    if not pub_str:
        return None
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(pub_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

def is_within_48_hours(pub_str):
    """Return True if article is less than 48 hours old"""
    pub_dt = parse_pub_date(pub_str)
    if not pub_dt:
        return True  # if no date, include it (can't verify)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    return pub_dt >= cutoff

# ── RSS fetch ─────────────────────────────────────────────────────────────────
def fetch_google_news_rss(feed_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; IHaveACauseBot/1.0)"}
        resp    = requests.get(feed_url, headers=headers, timeout=15)
        resp.raise_for_status()
        root     = ET.fromstring(resp.content)
        articles = []
        for item in root.findall(".//item"):
            title     = item.findtext("title", "").strip()
            link      = item.findtext("link", "").strip()
            desc      = item.findtext("description", "").strip()
            pub       = item.findtext("pubDate", "")
            source_el = item.find("source")
            source    = source_el.text if source_el is not None else "Google News"
            if title and link:
                articles.append({
                    "title": title, "url": link, "description": desc,
                    "publishedAt": pub, "source": source
                })
        return articles
    except Exception as e:
        print(f"  ⚠️  RSS error: {e}")
        return []

def fetch_all_stories():
    seen_urls, all_stories = set(), []
    for feed in GOOGLE_NEWS_FEEDS:
        for a in fetch_google_news_rss(feed):
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                all_stories.append(a)
    if NEWS_API_KEY:
        try:
            resp = requests.get("https://newsapi.org/v2/top-headlines",
                params={"country":"in","pageSize":20,"apiKey":NEWS_API_KEY}, timeout=10)
            for a in resp.json().get("articles", []):
                url = a.get("url","")
                if url and url not in seen_urls and "[Removed]" not in a.get("title",""):
                    seen_urls.add(url)
                    all_stories.append({
                        "title":       a.get("title",""),
                        "url":         url,
                        "description": a.get("description",""),
                        "publishedAt": a.get("publishedAt",""),
                        "source":      a.get("source",{}).get("name","NewsAPI")
                    })
        except Exception as e:
            print(f"  ⚠️  NewsAPI: {e}")
    return all_stories

# ── Main ──────────────────────────────────────────────────────────────────────
def run_scanner():
    print("=" * 60)
    print(f"🤖 Scanner started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1 — Delete stale pending stories (> 48 hrs)
    print("\n  🗑️  Deleting pending stories older than 48 hours...")
    db_delete_old_pending()
    print("  ✅ Old pending stories removed")

    # Step 2 — Fetch fresh news
    print("\n  📡 Fetching fresh news...")
    articles      = fetch_all_stories()
    stories_found = len(articles)
    print(f"  ✅ Fetched {stories_found} articles total")

    stories_added = skipped_old = skipped_dup = 0

    print("\n  📝 Processing articles...\n")
    for article in articles:
        url   = article.get("url","")
        title = article.get("title","").strip()
        desc  = article.get("description","") or ""
        source = article.get("source","Unknown")
        pub_at = article.get("publishedAt")

        if not title or not url or len(title) < 10:
            continue

        # Skip articles older than 48 hours
        if not is_within_48_hours(pub_at):
            skipped_old += 1
            continue

        niche       = classify_niche(title, desc)
        viral_score = score_virality(title, desc)
        rec_formats = recommend_formats(niche, viral_score, title)

        # Check duplicate
        existing = db_select("content_queue", {"source_url": f"eq.{url}", "select": "id"})
        if existing:
            skipped_dup += 1
            continue

        row = {
            "title":               title,
            "summary":             desc[:500] or None,
            "source_url":          url,
            "source_name":         source,
            "published_at":        pub_at or None,
            "niche":               niche,
            "viral_score":         viral_score,
            "status":              "pending",
            "formats":             "[]",
            "recommended_formats": rec_formats,
        }

        if db_insert("content_queue", row):
            stories_added += 1
            recs = json.loads(rec_formats)
            print(f"  ✅ [{niche:13s}] 🔥{viral_score:3d} | Rec: {', '.join(recs)} | {title[:45]}")
        else:
            print(f"  ❌ Insert failed — {title[:45]}")

    db_insert("scanner_logs", {
        "stories_found": stories_found,
        "stories_added": stories_added,
        "status":        "success"
    })

    print("\n" + "=" * 60)
    print(f"✅ Done — Added: {stories_added} | Skipped old: {skipped_old} | Skipped dup: {skipped_dup}")
    print("=" * 60)

if __name__ == "__main__":
    run_scanner()
