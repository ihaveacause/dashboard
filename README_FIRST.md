# READ ME FIRST — Ideas pipeline + Vertex billing fix

This ZIP mirrors your repo. Everything inside goes into your GitHub repo at the
**same folder paths** shown here.

## Files in this ZIP

REPLACES an existing file (overwrite when prompted):
- index.html ............................ dashboard, now with the 💡 Ideas tab
- new_youtube/scripts/generate_images.py ..... SERIES images, now on Vertex (credit-covered)
- new_youtube/scripts/generate_thumbnail.py .. SERIES thumbnail, now on Vertex (credit-covered)

NEW files (added):
- new_youtube/scripts/idea_generate_script.py
- new_youtube/scripts/idea_generate_images.py        (Vertex)
- new_youtube/scripts/idea_generate_video.py
- new_youtube/scripts/idea_generate_thumbnail.py     (Vertex)
- scripts/idea_upload_to_youtube.py
- .github/workflows/generate_idea_script.yml
- .github/workflows/generate_idea_images.yml
- .github/workflows/generate_idea_video.yml
- .github/workflows/generate_idea_thumbnail.yml
- .github/workflows/upload_idea_to_youtube.yml
- supabase/functions/trigger-idea/index.ts
- 01_create_ideas_tables.sql   (reference copy — you RUN this in Supabase, see below)

## Deploy with GitHub Desktop

1. Unzip this file.
2. Copy everything inside it into your local repo folder. When asked, choose
   **Replace** (that overwrites index.html and the 2 series scripts — intended).
   The `01_create_ideas_tables.sql` and this README land in the repo root; you can
   leave them or untick them in step 3 — they don't affect the app.
3. Open GitHub Desktop. You'll see the changed + new files. Write a summary like
   "Add Ideas pipeline + move image/thumbnail to Vertex", then **Commit** and **Push**.
4. Vercel auto-rebuilds your dashboard a minute or two after the push.

## Two manual steps NOT done by the commit (both in Supabase)

A. **Create the tables** — Supabase → SQL Editor → paste `01_create_ideas_tables.sql` → Run.
B. **Deploy the trigger** — Supabase → Edge Functions → new function named EXACTLY
   `trigger-idea` → paste the contents of `supabase/functions/trigger-idea/index.ts` → Deploy.
   Check that the `GH_PAT` secret already exists (your current buttons use it).

## After deploying — your test

Run ONE idea from Add Idea → script → images → voice → video → publish, then check
tomorrow's billing report. The image SKU should show an "Other savings" offset like
your script (text) lines do — that means credits are covering it.

The image/thumbnail scripts log which backend they used:
"Vertex AI … global (credit-covered)" = working. A "falling back to AI Studio" warning
means the model isn't reachable on Vertex yet — send me that log line.

Nothing here changes your Series flow's behaviour; the only Series change is that its
images/thumbnails now bill to Vertex (your credits) instead of cash.
