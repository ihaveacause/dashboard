# READ ME — On Camera Shorts · Sprint 16

This ZIP mirrors your repo. Everything inside goes into your GitHub repo at the
**same folder paths** shown here. It is **purely additive** — it adds a new
**"🤳 On Camera Shorts"** track and relabels the existing On Camera button so
the two don't get confused. Nothing else changes:

- `tamil_anchor` / `english_anchor` (landscape, long-form On Camera) — untouched
- `tamil_shorts` / `english_shorts` (AI-cut Shorts from long episodes) — untouched
- `shorts.html`, `anchor.html` and all their pipeline scripts — untouched

---

## What this track does

```
you record yourself talking, phone held VERTICALLY
        →  Shorts tab → 🤳 On Camera Shorts → Upload Vertical Recording
        →  Whisper transcribes + Claude suggests 3 titles
        →  you pick a title (or type your own)
        →  Render: your footage cropped to 1080×1920, "I Have a Cause"
           bottom banner + logo burned in for the whole clip
        →  you preview → Publish as Short → uploaded to YouTube,
           tagged #Shorts, scheduled, routed into the module playlist
```

Deliberately simpler than the landscape On Camera track: no beats/graphics
planning step, no studio-mode choice. A Short is just your footage + the
brand bar — one thing to review (the title), one thing to approve (the
render) before it publishes.

**The banner:** a semi-transparent bar across the bottom ~11% of the frame —
your footage still shows through it — carrying the logo and "I Have a
Cause" / "@IHaveACause" wordmark. It's burned into every render
automatically; there's nothing to configure per-video.

**Duration:** YouTube's Shorts ceiling is 3 minutes. The render step still
processes anything longer, but logs a warning — if you go over 3 minutes,
YouTube will publish it as a regular (non-Short) video since it's vertical
but too long. Aim for well under that; 15–60s tends to perform best.

---

## Files in this ZIP

CHANGED (overwrite when prompted):
- `index.html` ............ relabels the existing button to **"🎥 On Camera
  (YouTube Long)"** (same page, same pipeline, unchanged — just clearer that
  it's the landscape/long-form track) and adds ONE new button, **"🤳 On
  Camera Shorts"**, right after it. Nothing else in the dashboard changes.

NEW — dashboard page:
- `anchor_shorts.html` .... the whole On Camera Shorts UI: upload, title
  picker, render preview, publish. Self-contained, reuses your Supabase
  config + look.

NEW — pipeline scripts:
- `new_youtube/scripts/anchor_shorts_transcribe.py` .. Whisper transcript +
  word timings + 3 Shorts-style title suggestions
- `new_youtube/scripts/anchor_shorts_render.py` ...... crops your recording
  to 1080×1920, burns in the bottom banner + logo, makes a branded thumbnail
- `new_youtube/scripts/anchor_shorts_upload_to_youtube.py` . publishes to
  YouTube as a Short (`#Shorts`, `/shorts/{id}` URL, scheduled)

NEW — workflows:
- `.github/workflows/anchor_shorts_transcribe.yml`
- `.github/workflows/anchor_shorts_render.yml`
- `.github/workflows/anchor_shorts_upload.yml`

NEW — edge function + SQL (you deploy/run these — see below):
- `supabase/functions/trigger-anchor-shorts/index.ts` .. one function
  dispatches all 3 pipeline steps (transcribe / render / upload)
- `new_youtube/sql/03_create_anchor_shorts_tables.sql` . creates
  `tamil_anchor_shorts` and `english_anchor_shorts`

Recordings upload straight to your existing `ihaveacause-media` GCS bucket
via your existing **`anchor-upload-url`** edge function — that function is
generic (just mints a signed upload session) and is reused as-is, not
duplicated.

---

## Deploy with GitHub Desktop

1. Unzip. Copy everything into your local repo folder. Choose **Replace**
   when asked (that only overwrites `index.html`, which just gains a
   relabeled button + one new button).
2. In GitHub Desktop, review the changed + new files, write a summary like
   "Add On Camera Shorts track", then **Commit** and **Push**.
3. Vercel auto-rebuilds the dashboard a minute or two later.

## Two manual steps NOT done by the commit (both in Supabase)

**A. Create the tables** — Supabase → SQL Editor → paste
`new_youtube/sql/03_create_anchor_shorts_tables.sql` → Run. It creates
`tamil_anchor_shorts` and `english_anchor_shorts`. (Re-runnable; skips what
already exists.)

**B. Deploy the edge function** — Supabase → Edge Functions → new function
named EXACTLY `trigger-anchor-shorts` → paste
`supabase/functions/trigger-anchor-shorts/index.ts` → Deploy. Reuses your
existing **`GH_PAT`** secret — nothing new to configure.

**No new GitHub or Supabase secrets are needed.** The workflows reuse the
secrets you already have: `SUPABASE_URL`, `SUPABASE_KEY`,
`GOOGLE_APPLICATION_CREDENTIALS_JSON`, `ANTHROPIC_API_KEY`, and for
publishing `SUPABASE_SERVICE_ROLE_KEY`, `YOUTUBE_TOKEN_JSON`.

---

## Your first run

1. Dashboard → **🤳 On Camera Shorts** → **⬆ Upload Vertical Recording**.
   Film with your phone held vertically, under 3 minutes.
2. Give it a working title, pick free-speech or read-a-script, optionally a
   module. Upload.
3. **Transcribe + suggest titles** → wait ~1-2 min (GitHub Actions), refresh.
   Pick one of the 3 suggested titles, or type your own — this immediately
   kicks off the render.
4. Wait for render → refresh → **Preview render** to check the crop + banner
   look right → **Publish as Short**.

## Good to know

- **Why no beats/studio-mode step**: those exist on the landscape On Camera
  track to plan side-panel graphics for a 16:9 frame. A vertical Short is
  just your footage full-frame + the bottom banner — there's nothing to plan
  per-video, so that stage is skipped entirely here.
- **Vertical footage only**: the render step scales-to-fill and center-crops
  to 1080×1920. If you accidentally film landscape, it will zoom in hard and
  crop the sides off rather than pillarbox — so hold the phone vertically
  going in.
- **Playlists**: published Shorts are routed into the same
  `module_playlists` your long-form On Camera videos use, so a module's
  Shorts and long videos sit together.
- **Real footage, not AI**: unlike the Sprint 15 AI Shorts track, the upload
  does NOT set `containsSyntheticMedia` — this is your real recorded video.
