"""
I Have a Cause — News Scanner Bot v2
Sprint 2: Google News RSS (free) + NewsAPI top-headlines
Runs daily at 8:30 AM IST via Render cron job
"""

import os
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

# ── Config ─────────────────────────────────────────────────────────────────
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
NEWS_API_KEY  = os.environ.get("NEWS_API_KEY", "")

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
    "politics": ["dmk","aiadmk","stalin","tamil nadu government","bjp","election","parliament","modi","edappadi","seeman","ntk","minister","mla","mp"],
    "crime":    ["arrest","murder","crime","scam","corruption","cbi","drug","robbery","fraud","cheating","accused","police"],
    "entertainment": ["kollywood","tamil cinema","vijay","ajith","rajinikanth","tamil movie","thalapathy","sun tv","vijay tv","kamal","actor","actress","ott","film","box office"],
    "sports":   ["csk","ipl","cricket","kabaddi","football","sports","karthik","ashwin","dhoni","match","tournament"],
    "business": ["economy","startup","it","infosys","tcs","investment","jobs","employment","industry","manufacturing"],
    "viral":    ["viral","trending","social media","meme","twitter","funny","shock","outrage","controversy"],
}

VIRALITY_BOOSTERS = ["breaking","exclusive","arrest","viral","shocking","massive","historic","first time","record","exposed","leaked","ban","death","scam","crisis","resign","fired","win","victory","controversy","outrage","protest","clash"]

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

def generate_scripts(title, description, niche):
    desc = (description or title)[:200]
    return {
        "youtube_tamil": f"""[யூடியூப் ஸ்கிரிப்ட்]\n\n🎬 HOOK: "{title}" — இதை பத்தி நீங்க தெரிஞ்சுக்கணும்!\n\n📖 BODY:\n• {desc}\n• இந்த நிகழ்வின் பின்னணி என்ன?\n• மக்களுக்கு என்ன தாக்கம்?\n\n✅ CTA: "வீடியோவை ஷேர் செய்யுங்கள். Channel-ஐ Subscribe பண்ணுங்கள்!\"""",
        "youtube_english": f"""[YouTube Script — English]\n\n🎬 HOOK: "{title}" — Here's what you need to know.\n\n📖 BODY:\n• {desc}\n• Background and context\n• What this means for Tamil Nadu / India\n\n✅ CTA: "Share this video. Subscribe for daily Tamil news coverage.\"""",
        "reel_tamil": f"""[Reel 30–60 sec — Tamil]\n\n🎙 VO: "{title}"\n{desc[:100]}...\nஉங்க கருத்து என்ன? Comment பண்ணுங்க! 👇\n\n#TamilNews #IHaveACause #TamilNadu #Trending""",
        "reel_english": f"""[Reel 30–60 sec — English]\n\n🎙 VO: "{title}"\n{desc[:100]}...\nWhat do you think? Drop your thoughts below! 👇\n\n#TamilNadu #IndiaNews #IHaveACause #Viral""",
        "meme": f"""[Meme]\nTop: "{title[:55]}..."\nBottom: "I Have a Cause 🔴 — Follow for Tamil news"\n\nAlt: POV — you found out about this {niche} story first 👀"""
    }

def fetch_google_news_rss(feed_url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; IHaveACauseBot/1.0)"}
        resp = requests.get(feed_url, headers=headers, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        articles = []
        for item in root.findall(".//item"):
            title  = item.findtext("title", "").strip()
            link   = item.findtext("link", "").strip()
            desc   = item.findtext("description", "").strip()
            pub    = item.findtext("pubDate", "")
            source_el = item.find("source")
            source = source_el.text if source_el is not None else "Google News"
            if title and link:
                articles.append({"title": title, "url": link, "description": desc, "publishedAt": pub, "source": source})
        return articles
    except Exception as e:
        print(f"  ⚠️  RSS error: {e}")
        return []

def fetch_newsapi_headlines():
    if not NEWS_API_KEY:
        return []
    try:
        resp = requests.get("https://newsapi.org/v2/top-headlines",
            params={"country": "in", "pageSize": 20, "apiKey": NEWS_API_KEY}, timeout=10)
        resp.raise_for_status()
        raw = resp.json().get("articles", [])
        return [{"title": a.get("title",""), "url": a.get("url",""), "description": a.get("description",""),
                 "publishedAt": a.get("publishedAt",""), "source": a.get("source",{}).get("name","NewsAPI")}
                for a in raw if a.get("title") and "[Removed]" not in a.get("title","")]
    except Exception as e:
        print(f"  ⚠️  NewsAPI error: {e}")
        return []

def fetch_all_stories():
    seen_urls, all_stories = set(), []
    for feed in GOOGLE_NEWS_FEEDS:
        articles = fetch_google_news_rss(feed)
        print(f"  📡 RSS: {len(articles)} stories")
        for a in articles:
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                all_stories.append(a)
    for a in fetch_newsapi_headlines():
        if a["url"] not in seen_urls:
            seen_urls.add(a["url"])
            all_stories.append(a)
    print(f"  ✅ Total unique stories: {len(all_stories)}")
    return all_stories

def run_scanner():
    print("=" * 60)
    print(f"🤖 Scanner started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
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
            scripts     = generate_scripts(title, desc, niche)
            row = {
                "title": title, "summary": desc[:500] or None,
                "source_url": url, "source_name": source, "published_at": pub_at or None,
                "niche": niche, "viral_score": viral_score, "status": "pending",
                "script_youtube_tamil":   scripts["youtube_tamil"],
                "script_youtube_english": scripts["youtube_english"],
                "script_reel_tamil":      scripts["reel_tamil"],
                "script_reel_english":    scripts["reel_english"],
                "meme_caption":           scripts["meme"],
            }
            try:
                existing = supabase.table("content_queue").select("id").eq("source_url", url).execute()
                if not existing.data:
                    supabase.table("content_queue").insert(row).execute()
                    stories_added += 1
                    print(f"  ✅ [{niche:13s}] score={viral_score:3d} — {title[:55]}")
                else:
                    print(f"  ⏭  skip — {title[:55]}")
            except Exception as e:
                print(f"  ❌ DB error: {e}")
        supabase.table("scanner_logs").insert({"stories_found": stories_found, "stories_added": stories_added, "status": "success"}).execute()
        print("=" * 60)
        print(f"✅ Done — Found: {stories_found}  |  Added: {stories_added}")
        print("=" * 60)
    except Exception as e:
        print(f"❌ Fatal: {e}")
        try:
            supabase.table("scanner_logs").insert({"stories_found": stories_found, "stories_added": stories_added, "status": "error", "error_message": str(e)}).execute()
        except Exception:
            pass
        raise

if __name__ == "__main__":
    run_scanner()
