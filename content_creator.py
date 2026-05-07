"""
I Have a Cause — Content Creator (Sprint 4)
- Triggered when user clicks "Create Content" on a Ready story (status → 'creating')
- Generates 6 publish-ready content packages per story
- Creates actual meme image (Tamil movie dramatic style)
- Sets status → 'created' when done
"""

import os
import json
import requests
from datetime import datetime
import base64
from io import BytesIO

SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

SB_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal"
}

REST_URL = f"{SUPABASE_URL}/rest/v1"

# ── Supabase helpers ──────────────────────────────────────────────────────────
def db_get(params):
    resp = requests.get(
        f"{REST_URL}/content_queue",
        headers={**SB_HEADERS, "Prefer": "return=representation"},
        params=params, timeout=15
    )
    return resp.json() if resp.status_code == 200 else []

def db_patch(story_id, data):
    resp = requests.patch(
        f"{REST_URL}/content_queue?id=eq.{story_id}",
        headers=SB_HEADERS, json=data, timeout=15
    )
    return resp.status_code in (200, 204)

# ── Claude API ────────────────────────────────────────────────────────────────
def call_claude(prompt, max_tokens=2000):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
        timeout=90
    )
    if resp.status_code == 200:
        return resp.json()["content"][0]["text"].strip()
    print(f"  Claude error {resp.status_code}: {resp.text[:200]}")
    return None

def parse_json(text):
    if not text:
        return {}
    try:
        clean = text.strip().replace("```json","").replace("```","").strip()
        return json.loads(clean)
    except:
        return {}

# ── Fetch stories ─────────────────────────────────────────────────────────────
def fetch_creating_stories():
    return db_get({"status":"eq.creating","select":"*","order":"created_at.desc","limit":"10"})

# ── Package generators ────────────────────────────────────────────────────────
def gen_yt_long(s):
    title     = s.get("title","")
    script_ta = s.get("script_youtube_tamil") or title
    script_en = s.get("script_youtube_english") or title

    # Tamil package
    raw_ta = call_claude(f"""Create a YouTube publishing package in TAMIL.

Title: {title}
Script: {script_ta[:1800]}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "title": "engaging Tamil title under 60 chars",
  "description": "Tamil description 150-200 words with keywords",
  "thumbnail_text": "5-7 dramatic Tamil words for thumbnail",
  "chapters": [
    {{"time":"0:00","title":"Tamil chapter title"}},
    {{"time":"2:00","title":"Tamil chapter title"}},
    {{"time":"5:00","title":"Tamil chapter title"}},
    {{"time":"8:00","title":"முடிவு"}}
  ]
}}""")
    print(f"  DEBUG Tamil raw: {str(raw_ta)[:300]}")
    ta = parse_json(raw_ta)
    print(f"  DEBUG Tamil parsed keys: {list(ta.keys()) if ta else 'EMPTY'}")
    print(f"  DEBUG thumbnail_text: {ta.get('thumbnail_text')}")
    print(f"  DEBUG chapters: {ta.get('chapters')}")

    # English package
    raw_en = call_claude(f"""Create a YouTube publishing package in ENGLISH.

Title: {title}
Script: {script_en[:1800]}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "title": "engaging English title under 60 chars",
  "description": "English description 150-200 words with keywords",
  "thumbnail_text": "5-7 dramatic English words for thumbnail",
  "chapters": [
    {{"time":"0:00","title":"Introduction"}},
    {{"time":"2:00","title":"English chapter title"}},
    {{"time":"5:00","title":"English chapter title"}},
    {{"time":"8:00","title":"Conclusion"}}
  ]
}}""")
    print(f"  DEBUG English raw: {str(raw_en)[:300]}")
    en = parse_json(raw_en)
    print(f"  DEBUG English parsed keys: {list(en.keys()) if en else 'EMPTY'}")
    print(f"  DEBUG thumbnail_text_en: {en.get('thumbnail_text')}")
    print(f"  DEBUG chapters_en: {en.get('chapters')}")

    # Tags (one call, language-neutral)
    tags = parse_json(call_claude(f"""Generate 10 YouTube tags for this video: {title}
Return ONLY valid JSON: {{"tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10"]}}"""))

    return {
        "title_tamil":            ta.get("title") or title,
        "description_tamil":      ta.get("description") or "",
        "thumbnail_text_tamil":   ta.get("thumbnail_text") or title[:40],
        "chapters_tamil":         ta.get("chapters") or [{"time":"0:00","title":"தொடக்கம்"},{"time":"3:00","title":"விவரம்"},{"time":"6:00","title":"கருத்து"},{"time":"9:00","title":"முடிவு"}],
        "title_english":          en.get("title") or title,
        "description_english":    en.get("description") or "",
        "thumbnail_text_english": en.get("thumbnail_text") or title[:40],
        "chapters_english":       en.get("chapters") or [{"time":"0:00","title":"Introduction"},{"time":"3:00","title":"Background"},{"time":"6:00","title":"Analysis"},{"time":"9:00","title":"Conclusion"}],
        "tags":                   tags.get("tags") or [],
    }

def gen_yt_short(s):
    title  = s.get("title","")
    script = s.get("script_youtube_short_tamil") or s.get("script_youtube_tamil") or title
    result = call_claude(f"""Create a YouTube Shorts content package.

Title: {title}
Script: {script[:1500]}

Return ONLY valid JSON (no markdown):
{{
  "hook_tamil": "3-second hook Tamil — must stop scroll",
  "hook_english": "3-second hook English",
  "scenes": [
    {{"scene":1,"duration":"3s","text_overlay":"bold text","visual":"what to show"}},
    {{"scene":2,"duration":"5s","text_overlay":"text","visual":"visual"}},
    {{"scene":3,"duration":"5s","text_overlay":"text","visual":"visual"}},
    {{"scene":4,"duration":"5s","text_overlay":"text","visual":"visual"}},
    {{"scene":5,"duration":"3s","text_overlay":"Follow for more!","visual":"subscribe"}}
  ],
  "caption_tamil": "Tamil caption with emojis under 100 chars",
  "caption_english": "English caption with emojis under 100 chars",
  "hashtags": ["#Shorts","#Tamil","#TamilNews","#tag1","#tag2","#tag3","#tag4","#tag5"]
}}""")
    return parse_json(result) or {"hook_tamil": title, "hook_english": title}

def gen_reels(s):
    title  = s.get("title","")
    script = s.get("script_reel_tamil") or s.get("script_youtube_tamil") or title
    result = call_claude(f"""Create an Instagram Reels content package. Tamil audience.

Title: {title}
Script: {script[:1500]}

Return ONLY valid JSON (no markdown):
{{
  "hook_tamil": "Opening Tamil hook — stops scroll in 2 seconds",
  "hook_english": "Opening English hook",
  "scenes": [
    {{"scene":1,"duration":"3s","narration":"what to say","text_overlay":"on screen text","visual_direction":"what to show"}},
    {{"scene":2,"duration":"5s","narration":"narration","text_overlay":"text","visual_direction":"visual"}},
    {{"scene":3,"duration":"5s","narration":"narration","text_overlay":"text","visual_direction":"visual"}},
    {{"scene":4,"duration":"5s","narration":"narration","text_overlay":"text","visual_direction":"visual"}},
    {{"scene":5,"duration":"3s","narration":"CTA","text_overlay":"Follow @ihaveacauseofficial","visual_direction":"logo"}}
  ],
  "caption_tamil": "Tamil caption 100-150 chars",
  "caption_english": "English caption 100-150 chars",
  "hashtags": ["#Tamil","#Reels","#TamilReels","#TamilNews","#IHaveACause","#tag1","#tag2","#tag3","#tag4","#tag5","#tag6","#tag7","#tag8","#tag9","#tag10"],
  "audio_mood": "energetic"
}}""")
    return parse_json(result) or {"hook_tamil": title, "hook_english": title}

def gen_meme(s):
    title   = s.get("title","")
    ta_meme = s.get("script_meme_tamil","")
    en_meme = s.get("script_meme_english","")
    caption = ta_meme or en_meme or title
    result = call_claude(f"""Create a Tamil movie style dramatic meme package.

News: {title}
Meme script: {caption[:300]}

Return ONLY valid JSON (no markdown):
{{
  "top_text": "DRAMATIC TAMIL SETUP MAX 8 WORDS",
  "bottom_text": "TAMIL PUNCHLINE MAX 8 WORDS",
  "top_text_english": "ENGLISH SETUP MAX 8 WORDS",
  "bottom_text_english": "ENGLISH PUNCHLINE MAX 8 WORDS",
  "color_scheme": "dark_red_gold",
  "emoji": "🔥",
  "viral_caption": "Short Tamil viral caption MAX 50 chars"
}}

color_scheme must be exactly one of: dark_red_gold, dark_blue_yellow, black_orange""")
    data = parse_json(result) or {"top_text": title[:40], "bottom_text": ""}
    print("    🎨 Generating meme image...")
    data["image_base64"] = create_meme_image(data)
    return data

def gen_x_thread(s):
    title = s.get("title","")
    ta    = s.get("script_x_thread") or s.get("script_youtube_tamil") or title
    en    = s.get("script_x_thread_english") or s.get("script_youtube_english") or title
    result = call_claude(f"""Create an X (Twitter) Thread for Tamil news.

Title: {title}
Tamil: {ta[:800]}
English: {en[:400]}

Return ONLY valid JSON (no markdown):
{{
  "thread_tamil": [
    {{"tweet":1,"text":"🧵 Hook Tamil max 260 chars"}},
    {{"tweet":2,"text":"Context Tamil max 260 chars"}},
    {{"tweet":3,"text":"Key point 1 Tamil"}},
    {{"tweet":4,"text":"Key point 2 Tamil"}},
    {{"tweet":5,"text":"Opinion Tamil"}},
    {{"tweet":6,"text":"CTA: Follow @ihaveacause2win | Tamil"}}
  ],
  "thread_english": [
    {{"tweet":1,"text":"🧵 Hook English max 260 chars"}},
    {{"tweet":2,"text":"Context English"}},
    {{"tweet":3,"text":"Key point 1 English"}},
    {{"tweet":4,"text":"Key point 2 English"}},
    {{"tweet":5,"text":"Opinion English"}},
    {{"tweet":6,"text":"CTA: Follow @ihaveacause2win | English"}}
  ],
  "hashtags": ["#TamilNadu","#Tamil","#IHaveACause"]
}}""")
    return parse_json(result) or {"thread_tamil":[], "thread_english":[]}

def gen_x_post(s):
    title = s.get("title","")
    ta    = s.get("script_x_post") or title
    result = call_claude(f"""Create a viral X (Twitter) post for Tamil news.

Title: {title}
Script: {ta[:400]}

Return ONLY valid JSON (no markdown):
{{
  "post_tamil": "Tamil post MAX 250 chars — punchy hook + content + hashtags",
  "post_english": "English post MAX 250 chars — punchy hook + content + hashtags",
  "hashtags": ["#TamilNadu","#Tamil","#IHaveACause","#tag1","#tag2"]
}}""")
    return parse_json(result) or {"post_tamil": title, "post_english": title}

# ── Meme image ────────────────────────────────────────────────────────────────
def create_meme_image(data):
    try:
        from PIL import Image, ImageDraw, ImageFont
        W, H = 1080, 1080
        img  = Image.new("RGB",(W,H),"#0a0a0a")
        draw = ImageDraw.Draw(img)
        schemes = {
            "dark_red_gold":    {"bg1":(15,0,0),  "bg2":(80,10,10), "accent":(212,175,55),"text":(255,255,255),"shadow":(100,40,0)},
            "dark_blue_yellow": {"bg1":(0,5,30),  "bg2":(10,20,80), "accent":(255,200,0), "text":(255,255,255),"shadow":(0,0,80)},
            "black_orange":     {"bg1":(5,5,5),   "bg2":(40,20,0),  "accent":(255,100,0), "text":(255,255,255),"shadow":(80,30,0)},
        }
        c = schemes.get(data.get("color_scheme","dark_red_gold"), schemes["dark_red_gold"])
        for y in range(H):
            r=int(c["bg1"][0]+(c["bg2"][0]-c["bg1"][0])*y/H)
            g=int(c["bg1"][1]+(c["bg2"][1]-c["bg1"][1])*y/H)
            b=int(c["bg1"][2]+(c["bg2"][2]-c["bg1"][2])*y/H)
            draw.line([(0,y),(W,y)],fill=(r,g,b))
        for i in range(-H,W,60):
            draw.line([(i,0),(i+H,H)],fill=(*c["accent"],10),width=1)
        draw.rectangle([8,8,W-8,H-8],outline=c["accent"],width=5)
        draw.rectangle([18,18,W-18,H-18],outline=(*c["accent"],100),width=2)
        for cx,cy in [(42,42),(W-42,42),(42,H-42),(W-42,H-42)]:
            draw.polygon([(cx,cy-16),(cx+16,cy),(cx,cy+16),(cx-16,cy)],fill=c["accent"])
        TAMIL_FONTS = [
            "/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf",
            "/usr/share/fonts/truetype/fonts-tamilsupplement/TamilSupplementRegular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        TAMIL_FONTS_REG = [
            "/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        def load_font(paths, size):
            for p in paths:
                try: return ImageFont.truetype(p, size)
                except: continue
            return ImageFont.load_default()
        try:
            fxl = load_font(TAMIL_FONTS, 78)
            fsm = load_font(TAMIL_FONTS_REG, 28)
        except:
            fxl = fsm = ImageFont.load_default()
        def draw_wrapped(text,font,color,y0,max_w=900,shadow=None):
            words=text.upper().split(); lines=[]; line=[]
            for w in words:
                line.append(w)
                bb=draw.textbbox((0,0)," ".join(line),font=font)
                if bb[2]-bb[0]>max_w and len(line)>1:
                    line.pop(); lines.append(" ".join(line)); line=[w]
            if line: lines.append(" ".join(line))
            y=y0
            for ln in lines[:3]:
                bb=draw.textbbox((0,0),ln,font=font)
                x=(W-(bb[2]-bb[0]))//2
                if shadow: draw.text((x+4,y+4),ln,fill=shadow,font=font)
                draw.text((x,y),ln,fill=color,font=font)
                y+=bb[3]-bb[1]+16
        brand="✦ I HAVE A CAUSE ✦"
        bb=draw.textbbox((0,0),brand,font=fsm)
        draw.text(((W-(bb[2]-bb[0]))//2,38),brand,fill=c["accent"],font=fsm)
        draw.line([(80,86),(W-80,86)],fill=c["accent"],width=2)
        if data.get("top_text"):
            draw_wrapped(data["top_text"],fxl,c["text"],125,shadow=c["shadow"])
        mid=H//2-25
        draw.line([(80,mid),(W-80,mid)],fill=c["accent"],width=3)
        draw.polygon([(W//2,mid-24),(W//2+24,mid),(W//2,mid+24),(W//2-24,mid)],fill=c["accent"])
        draw.line([(80,mid+3),(W-80,mid+3)],fill=c["accent"],width=3)
        if data.get("bottom_text"):
            draw_wrapped(data["bottom_text"],fxl,c["accent"],mid+30,shadow=c["shadow"])
        cap=data.get("viral_caption","")
        if cap:
            bb=draw.textbbox((0,0),cap,font=fsm)
            draw.text(((W-(bb[2]-bb[0]))//2,H-55),cap,fill=(*c["accent"],200),font=fsm)
        buf=BytesIO()
        img.save(buf,format="PNG",quality=95)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"    Meme image error: {e}")
        return None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("="*55)
    print(f"🎬 Sprint 4 Content Creator — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*55)
    stories = fetch_creating_stories()
    if not stories:
        print("✅ Nothing to do — no stories in creating status")
        return
    print(f"📋 Found {len(stories)} stories\n")
    success = 0
    for story in stories:
        sid   = story["id"]
        title = story.get("title","Unknown")[:60]
        print(f"🔄 [{success+1}/{len(stories)}] {title}")
        try:
            print("  📺 YT Long..."); yt_long  = gen_yt_long(story)
            print("  ▶️  YT Short..."); yt_short = gen_yt_short(story)
            print("  📱 Reels...");    reels    = gen_reels(story)
            print("  😂 Meme...");     meme     = gen_meme(story)
            print("  🧵 X Thread..."); x_thread = gen_x_thread(story)
            print("  📣 X Post...");   x_post   = gen_x_post(story)
            ok = db_patch(sid, {
                "content_yt_long":    json.dumps(yt_long),
                "content_yt_short":   json.dumps(yt_short),
                "content_reels":      json.dumps(reels),
                "content_meme":       json.dumps(meme),
                "content_x_thread":   json.dumps(x_thread),
                "content_x_post":     json.dumps(x_post),
                "status":             "created",
                "content_created_at": datetime.utcnow().isoformat()
            })
            if ok:
                print(f"  ✅ Done → created\n"); success += 1
            else:
                print(f"  ❌ Save failed → reverting\n"); db_patch(sid,{"status":"ready"})
        except Exception as e:
            print(f"  ❌ Error: {e}\n"); db_patch(sid,{"status":"ready"})
    print(f"🎉 Done — {success}/{len(stories)} created!")

if __name__ == "__main__":
    main()
