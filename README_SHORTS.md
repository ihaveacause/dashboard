# READ ME — Shorts track · Sprint 15

This ZIP mirrors your repo. Everything inside goes into your GitHub repo at the
**same folder paths** shown here. It is **purely additive** — it adds a new
**"🎬 Shorts"** button to the dashboard, before **"🎥 On Camera"**. Your existing
Series, Ideas, and On Camera tracks are untouched, including the old single-short
columns (`status_shorts`, `video_url_short`) from Sprint 8 — those stay as they
are, just unused by this new page.

---

## What this track does

```
episode's long script gets APPROVED (Series tab)
        →  Shorts tab  →  ⚡ Generate Shorts
        →  Gemini reads the full approved script, decides 1-3 moments
           strong enough to stand alone: self-sufficient, provocative,
           ends on a hook back to the long video
        →  you Approve Script  →  Generate Images (same visual style as
           the long episode, cropped vertical)
        →  you Approve Images  →  Render Video (AI voice + ffmpeg,
           same voice as the channel)
        →  you Approve & Publish → uploaded to YouTube as a Short,
           scheduled, description links to the full episode
```

Every short goes through the same manual review/approve gate at every stage —
script, images, video — exactly like your Series pipeline, just one short row
at a time. Gemini decides the count (1, 2, or 3) per episode; it never pads to
a fixed number.

---

## Files in this ZIP

CHANGED (overwrite when prompted):
- `index.html` ............ adds ONE new top-level button, **🎬 Shorts**, placed
  right before **🎥 On Camera**. Nothing else in the dashboard changes.

NEW — dashboard page:
- `shorts.html` ........... the whole Shorts UI: episodes grouped, up to 3 short
  cards each, review/approve at every stage. Self-contained, reuses your
  Supabase config + look.

NEW — pipeline scripts:
- `new_youtube/scripts/shorts_generate_scripts.py` .. Gemini finds 1-3 standalone
  provocative moments in the APPROVED long script, writes them as rows
- `new_youtube/scripts/shorts_generate_images.py` ... 3 vertical (9:16) images
  per short, same visual-direction approach as your episode/idea images
- `new_youtube/scripts/shorts_render_video.py` ...... AI voice (same TTS voice
  as the channel) + ffmpeg vertical render
- `new_youtube/scripts/shorts_upload_to_youtube.py` . publishes to YouTube as
  a Short, links the description to the parent long video

NEW — workflows:
- `.github/workflows/shorts_generate_scripts.yml`
- `.github/workflows/shorts_generate_images.yml`
- `.github/workflows/shorts_render_video.yml`
- `.github/workflows/shorts_upload_youtube.yml`

NEW — edge function + SQL (you deploy/run these — see below):
- `supabase/functions/trigger-shorts/index.ts` .. one function dispatches all 4
  pipeline steps
- `new_youtube/sql/03_create_shorts_tables.sql` . creates `tamil_shorts` and
  `english_shorts` — one row per short (not per episode), so an episode can
  have 1, 2, or 3 without wasting columns

---

## Deploy with GitHub Desktop

1. Unzip. Copy everything into your local repo folder. Choose **Replace** when
   asked (that only overwrites `index.html`, which gains the one new button).
2. In GitHub Desktop, review the changed + new files, write a summary like
   "Add Shorts track", then **Commit** and **Push**.
3. Vercel auto-rebuilds the dashboard a minute or two later.

## Two manual steps NOT done by the commit (both in Supabase)

**A. Create the tables** — Supabase → SQL Editor → paste
`new_youtube/sql/03_create_shorts_tables.sql` → Run. It creates `tamil_shorts`
and `english_shorts`. (Re-runnable; skips what already exists.)

**B. Deploy the edge function** — Supabase → Edge Functions → new function
named EXACTLY `trigger-shorts` → paste `supabase/functions/trigger-shorts/index.ts`
→ Deploy. Reuses your existing **`GH_PAT`** secret — nothing new to configure.

**No new GitHub or Supabase secrets are needed.** The workflows reuse the
secrets you already have: `SUPABASE_URL`, `SUPABASE_KEY`,
`GOOGLE_APPLICATION_CREDENTIALS_JSON`, `GEMINI_API_KEY`, and for publishing
`SUPABASE_SERVICE_ROLE_KEY`, `YOUTUBE_TOKEN_JSON`.

---

## Your first run

1. In **Series**, pick an episode whose long script is already **Script Approved**
   or further along — Shorts are cut from the approved script, not a draft.
2. Dashboard → **🎬 Shorts** → find that episode → **⚡ Generate Shorts**.
3. Wait ~1-2 min (GitHub Actions), refresh. You'll see 1-3 short cards, each
   with its hook line, full script, and closing hook line.
4. Per short: **Approve Script** → **Generate Images** → refresh → **Approve
   Images** → **Render Video** → refresh → **Preview render** → **Approve &
   Publish to YouTube**.

## Good to know

- **Engines** match your existing pipeline: script decisions = Claude/Gemini
  (`gemini-2.5-pro`); images = Vertex Imagen 3 (`imagen-3.0-fast-generate-001`,
  9:16); voice = Google Cloud TTS Neural2, same voice config as your other
  renders; render = FFmpeg; storage = Supabase Storage for images, your
  `ihaveacause-media` GCS bucket for finished videos.
- **Why never more than 3**: Gemini is instructed to only propose moments that
  are genuinely self-sufficient and provocative on their own — it can and will
  return 1 or 2 if that's all a given episode supports.
- **The hook, not a hard CTA**: each short's closing line is written to create
  an open question the long video resolves, rather than a flat "watch the full
  video" ask — that's deliberate, it converts better and it's the instruction
  baked into the Gemini prompt.
- **Regenerate**: if you don't like the 1-3 shorts Gemini picked, "↻ Regenerate
  shorts" on the episode group re-runs the whole selection and replaces them.
