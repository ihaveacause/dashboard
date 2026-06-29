# READ ME FIRST — On Camera (Studio Desk) track · Sprint 14

This ZIP mirrors your repo. Everything inside goes into your GitHub repo at the
**same folder paths** shown here. It is **purely additive**: it adds a new
"🎥 On Camera" track for videos where **you speak to camera**, and the system
transcribes you, suggests titles, plans studio graphics, and composites a
news-studio video. Your existing Series and Ideas tracks are untouched.

> Note: the previous README only covered the Ideas/Vertex work (Sprint 11). This
> replaces it and now documents the current On Camera track.

---

## What this track does

```
record yourself talking  →  Transcribe (Whisper) + 3 title suggestions
        →  you pick a title  →  Beats: Claude tags each moment image/text,
           writes the catchy lower-third lines, Vertex renders the image beats
        →  Render: studio composite (your footage + panel + lower-thirds + OPINION tag)
        →  Publish to YouTube (scheduled, playlist by module)
```

Two studio modes, one flag (set per recording, override per render):
- **real_room** — no green screen needed. Your real footage fills the frame; a
  graphics panel sits on the right, a lower-third strap carries the headline, and
  a persistent **OPINION / கருத்து** tag marks it as your view. *Use this to test now.*
- **green** — when you get a green cloth: you're keyed out, the studio image fills
  the whole frame behind you on image beats. Same graphics on top. No rebuild — just flip the mode.

---

## Files in this ZIP

CHANGED (overwrite when prompted):
- `index.html` ............ adds ONE new top-level button, **🎥 On Camera**, that opens `anchor.html`. Nothing else in the dashboard changes.

NEW — dashboard page:
- `anchor.html` ........... the whole On Camera UI (upload, title picker, beat review, staged buttons). Self-contained, reuses your Supabase config + look.

NEW — pipeline scripts:
- `new_youtube/scripts/anchor_transcribe.py` ... Whisper transcript + word timings + 3 titles
- `new_youtube/scripts/anchor_beats.py` ........ Claude beat sheet (image/text tags) + Vertex images
- `new_youtube/scripts/anchor_render.py` ....... the studio compositor (real_room + green)
- `scripts/anchor_upload_to_youtube.py` ........ publish (mirrors your existing uploader, keyed by record id)

NEW — workflows:
- `.github/workflows/anchor_transcribe.yml`
- `.github/workflows/anchor_beats.yml`
- `.github/workflows/anchor_render.yml`
- `.github/workflows/anchor_upload.yml`

NEW — edge functions + SQL (you deploy/run these — see below):
- `supabase/functions/trigger-anchor/index.ts` .. one function dispatches all 4 pipeline steps
- `supabase/functions/anchor-upload-url/index.ts` mints a GCS upload link so recordings go straight to Google Cloud Storage
- `new_youtube/sql/02_create_anchor_tables.sql` . creates the two tables

---

## Deploy with GitHub Desktop

1. Unzip. Copy everything into your local repo folder. Choose **Replace** when asked
   (that only overwrites `index.html`, which gains the one new button).
2. In GitHub Desktop, review the changed + new files, write a summary like
   "Add On Camera (Studio Desk) track", then **Commit** and **Push**.
3. Vercel auto-rebuilds the dashboard a minute or two later.

## Two manual steps NOT done by the commit (both in Supabase)

**A. Create the tables** — Supabase → SQL Editor → paste
`new_youtube/sql/02_create_anchor_tables.sql` → Run. It creates `english_anchor`
and `tamil_anchor`. (Re-runnable; skips what already exists.) No storage bucket is
created here — recordings go straight to your existing GCS bucket.

**B. Deploy the two edge functions** — Supabase → Edge Functions:
- new function named EXACTLY `trigger-anchor` → paste `supabase/functions/trigger-anchor/index.ts` → Deploy. Reuses your existing **`GH_PAT`** secret.
- new function named EXACTLY `anchor-upload-url` → paste `supabase/functions/anchor-upload-url/index.ts` → Deploy. This one needs **one new Supabase secret, `GCP_SA_JSON`** = the full service-account JSON (the same value you already use as the GitHub secret `GOOGLE_APPLICATION_CREDENTIALS_JSON`). Set it under Edge Functions → Secrets.

**No new GitHub secrets are needed.** The workflows reuse the secrets you already
have: `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`,
`ANTHROPIC_API_KEY`, and for publishing `SUPABASE_SERVICE_ROLE_KEY`,
`YOUTUBE_TOKEN_JSON`, `YOUTUBE_CHANNEL_ID`.

## One-time GCS bucket CORS (so the browser can upload to GCS)

Recordings upload directly from the dashboard to the `ihaveacause-media` bucket.
The browser is allowed to do that only if the bucket has a CORS rule for your
dashboard's domain. Run this once (Google Cloud Shell or local `gcloud`):

```
cat > cors.json <<'JSON'
[{
  "origin": ["https://YOUR-DASHBOARD-DOMAIN.vercel.app", "http://localhost:3000"],
  "method": ["PUT", "POST", "GET", "HEAD"],
  "responseHeader": ["Content-Type", "Location", "Range", "x-goog-resumable"],
  "maxAgeSeconds": 3600
}]
JSON
gcloud storage buckets update gs://ihaveacause-media --cors-file=cors.json
```

Replace `YOUR-DASHBOARD-DOMAIN` with your actual Vercel URL. (If a browser upload
ever fails with a CORS error, this rule is missing or the origin doesn't match.)

---

## Your test (no green screen — natural room)

1. Record a short clip (~60–90s) in your room. Sit slightly to one side (leave the
   other side clear for the panel), window light to your **side** not behind you,
   frame mid-chest up.
2. Dashboard → **🎥 On Camera** → **Upload Recording**. Pick the file, give it a
   working title, leave **Freely** + **Real room** selected, Upload.
3. Click **Transcribe + suggest titles**. Wait ~1 min (GitHub Actions), refresh.
4. **Pick one of the 3 titles** (or type your own). Beats start automatically.
5. Refresh → **Review beats** (toggle any beat image/text, tweak a headline) →
   **Render studio video**.
6. Refresh → **Preview render**. Happy? **Publish to YouTube** (uploads scheduled,
   private until its publish time, routed to a playlist by module).

The first image of each video is the **anchor** image; the rest are conditioned on
it so the set shares one look (same trick as your Series images), in the bright
daylight illustration style.

---

## Good to know

- **Engines** match your existing pipeline: transcript = faster-whisper (auto-detects
  Tamil/English); beat planning = Anthropic Claude (`claude-sonnet-4-6`); images =
  Vertex nano-banana (`gemini-3.1-flash-image`, credit-covered); render = Pillow + FFmpeg;
  storage = GCS for everything (recordings in, images + render out).
- **Timing**: graphics are placed by finding each beat's opening words in your speech
  (Whisper word timings). Script-read mode also uses Whisper here; CTC forced alignment
  (as in the episode pipeline) is an available future tightening if you ever want it.
- **Storage**: everything lives in your `ihaveacause-media` GCS bucket — recordings
  upload there directly from the dashboard (via a one-shot resumable session minted
  by the `anchor-upload-url` function), and all outputs (images, render) are written
  there too. The pipeline scripts read the recording back with the service account.
  Resumable upload means long takes won't hit a single-request size ceiling.
- **OPINION tag** is always on, marking the content as your personal view; the YouTube
  category is set to News & Politics and the description carries an opinion disclaimer.
