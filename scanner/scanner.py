"""
I Have a Cause — News Scanner Bot
- Only fetches news < 48 hours old
- Auto-deletes pending stories > 48 hours at start of each run
- Recommends formats based on niche + viral score
- No script generation (handled by script_generator.py)
- VOLUME CONTROL: Top 10 per category from reliable sources only
"""

import os
import json
import re
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

# ── NEW: Category feeds (replaces generic GOOGLE_NEWS_FEEDS) ─────────────────
TOP_N = 10  # top stories per category

CATEGORY_FEEDS = {
    "tamil_politics": [
        "https://news.google.com/rss/search?q=Tamil+Nadu+politics+DMK+AIADMK&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Stalin+Edappadi+Seeman+TVK+Tamil+Nadu&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Tamil+Nadu+government+minister+assembly&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "tamil_cinema": [
        "https://news.google.com/rss/search?q=Kollywood+Tamil+cinema+movie+release&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Vijay+Ajith+Rajinikanth+Kamal+Tamil+film&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Tamil+OTT+box+office+Thalapathy&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "indian_politics": [
        "https://news.google.com/rss/search?q=India+Modi+BJP+Congress+parliament+Delhi&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=India+central+government+Rajya+Sabha+Lok+Sabha&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=India+Supreme+Court+national+policy&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "indian_cinema": [
        "https://news.google.com/rss/search?q=Bollywood+Hindi+cinema+movie+box+office&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Shah+Rukh+Khan+Salman+Deepika+Hindi+film&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=OTT+Netflix+Amazon+Hindi+web+series+India&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "geopolitics": [
        "https://news.google.com/rss/search?q=India+Pakistan+China+border+conflict&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Russia+Ukraine+US+Middle+East+global+war&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=India+foreign+policy+UN+international+relations&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "stock_market": [
        "https://news.google.com/rss/search?q=Nifty+Sensex+NSE+BSE+Indian+stock+market&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=RBI+India+economy+GDP+inflation+rupee&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=Indian+startup+IPO+investment+FII&hl=en-IN&gl=IN&ceid=IN:en",
    ],
    "viral_global": [
        "https://news.google.com/rss/search?q=viral+trending+shocking+world&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=viral+video+social+media+trending+2026&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=breaking+news+world+shocking+exposed&hl=en-IN&gl=IN&ceid=IN:en",
    ],
}

# ── NEW: Reliable sources only ────────────────────────────────────────────────
SOURCE_SCORES = {
    "reuters": 10, "bbc": 10, "bloomberg": 10, "associated press": 10, "ap news": 10,
    "the hindu": 9, "indian express": 9, "economic times": 9, "mint": 9,
    "livemint": 9, "business standard": 9, "hindustan times": 8, "ndtv": 8,
    "times of india": 8, "moneycontrol": 8, "financial express": 8, "india today": 8,
    "cnbctv18": 8, "firstpost": 7, "the print": 7, "scroll": 7, "the wire": 7,
    "news18": 7, "deccan herald": 7, "wion": 7, "zee business": 7,
    "dinamalar": 8, "vikatan": 8, "puthiyathalaimurai": 7,
    "sun tv": 7, "kalaignar tv": 7, "thanthi tv": 7, "polimer news": 6,
    "zee news": 6, "abp live": 6, "aaj tak": 6,
}

def get_source_score(source):
    if not source: return 0
    s = source.lower()
    for name, score in SOURCE_SCORES.items():
        if name in s: return score
    return 0  # unknown source — skip

# ── NEW: Title deduplication ──────────────────────────────────────────────────
def title_keywords(title):
    stop = {'a','an','the','in','on','at','to','for','of','and','or','but','is',
            'are','was','were','be','been','has','have','had','with','by','from',
            'as','its','this','that','how','why','what','who','says','said',
            'after','over','under','amid','about','up','down','into','out'}
    return set(w for w in re.findall(r'[a-z]{3,}', title.lower()) if w not in stop)

def is_duplicate(title, existing_titles, threshold=0.55):
    t_kw = title_keywords(title)
    if not t_kw: return False
    for et in existing_titles:
        e_kw = title_keywords(et)
        if not e_kw: continue
        if len(t_kw & e_kw) / max(len(t_kw), len(e_kw)) >= threshold:
            return True
    return False

# ── UNCHANGED from original ───────────────────────────────────────────────────
NICHE_KEYWORDS = {
    "politics":      ["dmk","aiadmk","stalin","tamil nadu government","bjp","election","parliament","modi","edappadi","seeman","ntk","minister","mla","mp"],
    "crime":         ["arrest","murder","crime","scam","corruption","cbi","drug","robbery","fraud","cheating","accused","police"],
    "entertainment": ["kollywood","tamil cinema","vijay","ajith","rajinikanth","tamil movie","thalapathy","sun tv","vijay tv","kamal","actor","actress","ott","film","box office"],
    "sports":        ["csk","ipl","cricket","kabaddi","football","sports","karthik","ashwin","dhoni","match","tournament"],
    "business":      ["economy","startup","it","infosys","tcs","investment","jobs","employment","industry","manufacturing"],
    "viral":         ["viral","trending","social media","meme","twitter","funny","shock","outrage","controversy"],
}

VIRALITY_BOOSTERS = ["breaking","exclusive","arrest","viral","shocking","massive","historic","record","exposed","leaked","ban","death","scam","crisis","resign","fired","win","victory","controversy","outrage","protest"]

def strip_html(text):
    if not text: return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
                 .replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', clean).strip()

def recommend_formats(niche, viral_score, title):
    title_lower = title.lower()
    recs = []
    breaking_words = ["breaking","arrest","resign","dies","death","ban","crisis","exposed","leaked","scam","exclusive"]
    is_breaking = viral_score >= 70 or any(w in title_lower for w in breaking_words)
    if viral_score >= 45: recs.append("yt_long")
    if is_breaking: recs.append("x_post"); recs.append("x_thread")
    if niche in ["viral", "entertainment"]: recs.append("reels"); recs.append("meme"); recs.append("yt_short")
    if niche == "sports": recs.append("yt_short"); recs.append("x_post")
    if niche in ["politics", "crime", "business"]: recs.append("yt_short"); recs.append("x_thread")
    if viral_score >= 80:
        for f in ["yt_long","yt_short","reels","meme","x_thread","x_post"]:
            if f not in recs: recs.append(f)
    if "yt_short" not in recs: recs.append("yt_short")
    seen, result = set(), []
    for f in recs:
        if f not in seen: seen.add(f); result.append(f)
    return json.dumps(result)

def score_virality(title, description=""):
    text = (title + " " + (description or "")).lower()
    score = 40
    for word in VIRALITY_BOOSTERS:
        if word in text: score += 8
    return min(score, 100)

def classify_niche(title, description=""):
    text = (title + " " + (description or "")).lower()
    best, best_count = "general", 0
    for niche, keywords in NICHE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count: best_count, best = count, niche
    return best

def db_select(table, filters=None):
    url  = f"{REST_URL}/{table}"
    resp = requests.get(url, headers={**HEADERS, "Prefer": "return=representation"}, params=filters or {}, timeout=10)
    return resp.json() if resp.status_code == 200 else []

def db_insert(table, row):
    resp = requests.post(f"{REST_URL}/{table}", headers=HEADERS, json=row, timeout=10)
    return resp.status_code in (200, 201)

def db_delete_old_pending():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    resp   = requests.delete(f"{REST_URL}/content_queue", headers=HEADERS,
                params={"status": "eq.pending", "created_at": f"lt.{cutoff}"}, timeout=10)
    if resp.status_code in (200, 204): return True
    print(f"  ⚠️  Delete failed: {resp.status_code}")
    return False

def db_delete_old_rejected():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    requests.delete(f"{REST_URL}/content_queue", headers=HEADERS,
        params={"status": "eq.rejected", "created_at": f"lt.{cutoff}"}, timeout=10)

def parse_pub_date(pub_str):
    if not pub_str: return None
    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT", "%Y-%m-%dT%H:%M:%SZ"]:
        try: return datetime.strptime(pub_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError: continue
    return None

def is_within_48_hours(pub_str):
    pub_dt = parse_pub_date(pub_str)
    if not pub_dt: return True
    return pub_dt >= datetime.now(timezone.utc) - timedelta(hours=48)

def fetch_google_news_rss(feed_url):
    try:
        resp = requests.get(feed_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; IHaveACauseBot/1.0)"}, timeout=15)
        resp.raise_for_status()
        root     = ET.fromstring(resp.content)
        articles = []
        for item in root.findall(".//item"):
            title     = strip_html(item.findtext("title", "")).strip()
            link      = item.findtext("link", "").strip()
            desc      = strip_html(item.findtext("description", "")).strip()
            pub       = item.findtext("pubDate", "")
            source_el = item.find("source")
            source    = source_el.text.strip() if source_el is not None and source_el.text else ""
            # Extract source from "Title - Source Name" format
            if not source and " - " in title:
                parts  = title.rsplit(" - ", 1)
                title  = parts[0].strip()
                source = parts[1].strip()
            if title and link:
                articles.append({"title": title, "url": link, "description": desc,
                                  "publishedAt": pub, "source": source})
        return articles
    except Exception as e:
        print(f"  ⚠️  RSS error: {e}")
        return []

# ── Main ──────────────────────────────────────────────────────────────────────
def run_scanner():
    print("=" * 60)
    print(f"🤖 Scanner started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Top {TOP_N} per category × {len(CATEGORY_FEEDS)} = max {TOP_N * len(CATEGORY_FEEDS)} stories")
    print("=" * 60)

    # Step 1 — cleanup
    print("\n  🗑️  Deleting old pending & rejected (48h+)...")
    db_delete_old_pending()
    db_delete_old_rejected()
    print("  ✅ Cleanup done")

    # Step 2 — get existing to avoid duplicates
    print("\n  🔍 Loading existing stories...")
    existing_rows  = db_select("content_queue", {"select": "source_url,title", "limit": "2000"})
    existing_urls  = {r['source_url'] for r in existing_rows if r.get('source_url')}
    existing_titles = [r['title'] for r in existing_rows if r.get('title')]
    print(f"  Found {len(existing_urls)} existing stories in DB")

    total_added = 0

    # Step 3 — process each category
    for category, feeds in CATEGORY_FEEDS.items():
        print(f"\n  📂 [{category}]")

        # Fetch all articles for this category
        raw_articles = []
        for feed in feeds:
            raw_articles.extend(fetch_google_news_rss(feed))

        # Filter + score
        candidates      = []
        seen_in_cat     = []

        for article in raw_articles:
            url    = article.get("url", "")
            title  = article.get("title", "").strip()
            desc   = article.get("description", "") or ""
            source = article.get("source", "")
            pub_at = article.get("publishedAt")

            if not title or not url or len(title) < 10:    continue
            if not is_within_48_hours(pub_at):              continue
            if get_source_score(source) == 0:               continue  # unreliable source
            if url in existing_urls:                        continue  # already in DB
            if is_duplicate(title, existing_titles):        continue  # same story in DB
            if is_duplicate(title, seen_in_cat):            continue  # same story in batch

            niche       = classify_niche(title, desc)
            viral_score = score_virality(title, desc)
            src_score   = get_source_score(source)

            candidates.append({
                "title": title, "url": url, "description": desc,
                "source": source, "publishedAt": pub_at,
                "niche": niche, "viral_score": viral_score,
                "total_score": viral_score + (src_score * 2),
            })
            seen_in_cat.append(title)

        # Sort by score and keep top N
        candidates.sort(key=lambda x: x['total_score'], reverse=True)
        top = candidates[:TOP_N]
        print(f"  Candidates: {len(candidates)} → keeping top {len(top)}")

        # Insert
        for article in top:
            rec_formats = recommend_formats(article['niche'], article['viral_score'], article['title'])
            row = {
                "title":               article['title'],
                "summary":             article['description'][:500] if article['description'] else None,
                "source_url":          article['url'],
                "source_name":         article['source'],
                "published_at":        parse_pub_date(article['publishedAt']).isoformat() if parse_pub_date(article['publishedAt']) else None,
                "niche":               article['niche'],
                "viral_score":         article['viral_score'],
                "status":              "pending",
                "formats":             "[]",
                "recommended_formats": rec_formats,
            }
            if db_insert("content_queue", row):
                total_added += 1
                existing_urls.add(article['url'])
                existing_titles.append(article['title'])
                print(f"  ✅ [{article['niche']:13s}] 🔥{article['viral_score']:3d} | {article['title'][:50]}")
            else:
                print(f"  ❌ Insert failed — {article['title'][:50]}")

    print("\n" + "=" * 60)
    print(f"✅ Done — Added: {total_added} stories across {len(CATEGORY_FEEDS)} categories")
    print("=" * 60)

if __name__ == "__main__":
    run_scanner()
