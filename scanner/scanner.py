"""
I Have a Cause — News Scanner Bot v3
Uses direct Supabase REST API (no supabase-py library issues)
Fetches Tamil/Indian news daily, scores virality, stores in Supabase
Scripts are generated separately by the AI Script Generator (Sprint 3)
"""

import os
import json
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timezone, timedelta

# ── Config ─────────────────────────────────────────────────────────────────
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
NEWS_API_KEY  = os.environ.get("NEWS_API_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
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

def db_select(table, filters=None):
    url = f"{REST_URL}/{table}"
    params = filters or {}
    resp = requests.get(url, headers={**HEADERS, "Prefer": "return=representation"}, params=params, timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return []

def db_insert(table, row):
    url = f"{REST_URL}/{table}"
    resp = requests.post(url, headers=HEADERS, json=row, timeout=10)
    return resp.status_code in (200, 201)

def fetch_google_news_rss(feed_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; IHaveACauseBot/1.0)"}
        resp = requests.get(feed_url, headers=headers, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        articles = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            desc  = item.findtext("description", "").strip()
            pub   = item.findtext("pubDate", "")
            source_el = item.find("source")
            source = source_el.text if source_el is not None else "Google News"
            if title and link:
                articles.append({"title": title, "url": link, "description": desc, "publishedAt": pub, "source": source})
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
    # NewsAPI bonus
    if NEWS_API_KEY:
        try:
            resp = requests.get("https://newsapi.org/v2/top-headlines",
                params={"country": "in", "pageSize": 20, "apiKey": NEWS_API_KEY}, timeout=10)
            for a in resp.json().get("articles", []):
                url = a.get("url","")
                if url and url not in seen_urls and "[Removed]" not in a.get("title",""):
                    seen_urls.add(url)
                    all_stories.append({"title": a.get("title",""), "url": url,
                        "description": a.get("description",""), "publishedAt": a.get("publishedAt",""),
                        "source": a.get("source",{}).get("name","NewsAPI")})
        except Exception as e:
            print(f"  ⚠️  NewsAPI: {e}")
    print(f"  ✅ Total unique stories: {len(all_stories)}")
    return all_stories

def run_scanner():
    print("=" * 60)
    print(f"🤖 Scanner started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Test DB connection
    test = db_select("content_queue", {"limit": "1"})
    print(f"  ✅ DB connected — test query returned {len(test)} rows")

    stories_found = stories_added = 0
    try:
        articles = fetch_all_stories()
        stories_found = len(articles)

        for article in articles:
            url, title = article.get("url",""), article.get("title","").strip()
            desc, source = article.get("description","") or "", article.get("source","Unknown")
            pub_at = article.get("publishedAt")
            if not title or not url or len(title) < 10:
                continue

            niche       = classify_niche(title, desc)
            viral_score = score_virality(title, desc)

            # Check duplicate
            existing = db_select("content_queue", {"source_url": f"eq.{url}", "select": "id"})
            if existing:
                print(f"  ⏭  skip — {title[:55]}")
                continue

            # Store only raw news data — scripts generated separately by Sprint 3
            row = {
                "title":        title,
                "summary":      desc[:500] or None,
                "source_url":   url,
                "source_name":  source,
                "published_at": pub_at or None,
                "niche":        niche,
                "viral_score":  viral_score,
                "status":       "pending",
                "formats":      "[]",
            }

            if db_insert("content_queue", row):
                stories_added += 1
                print(f"  ✅ [{niche:13s}] score={viral_score:3d} — {title[:55]}")
            else:
                print(f"  ❌ insert failed — {title[:55]}")

        db_insert("scanner_logs", {"stories_found": stories_found, "stories_added": stories_added, "status": "success"})
        print("=" * 60)
        print(f"✅ Done — Found: {stories_found}  |  Added: {stories_added}")
        print("=" * 60)

    except Exception as e:
        print(f"❌ Fatal: {e}")
        db_insert("scanner_logs", {"stories_found": stories_found, "stories_added": stories_added, "status": "error", "error_message": str(e)})
        raise

if __name__ == "__main__":
    run_scanner()
