# READ ME — On Camera Shorts · PiP Clips add-on

This ZIP mirrors your repo. Everything inside goes into your GitHub repo at
the **same folder paths** shown here. It is **purely additive** — it adds an
optional way to mix a publicly-sourced clip (e.g. a news clip) into an On
Camera Short as a small picture-in-picture box. Nothing else changes:

- Every other track (Series, Ideas, Shorts, landscape On Camera) — untouched
- A recording with no clips attached renders **exactly as before** — this is
  fully opt-in, per video

---

## What this adds

```
On Camera Shorts card, at the "Pick a Title" stage
        →  🎥 Optional: mix in a clip (collapsed section)
        →  scrub your own recording to find the moment (video preview)
        →  upload a clip (news footage etc.), set start time + duration
        →  add as many as you like, in any order
        →  pick a title (as before) → this now renders WITH your clips
```

**How it looks:** each clip appears **as-is, never cropped** — letterboxed
with plain black bars if it isn't exactly 16:9 — inside a small box that
floats just above the bottom banner, top-right, for exactly the
start→start+duration window you set. Your face stays fully visible the
whole time (the box sits in the empty space above the banner, not over your
face), and your own audio keeps playing — a video clip's original audio is
muted by default (there's a checkbox per clip if you specifically want the
clip's own sound instead, e.g. a quote where the exact audio matters). You
can attach either a **video clip** or a **still image** — a still just
holds in the box for its whole window, same idea as a source photo or
screenshot, no audio to worry about.

**If you never add a clip:** the card looks and behaves exactly like it does
today. This is a genuinely optional layer, not a new mode.

**On sourcing clips — worth keeping in mind:** publicly viewable isn't the
same as free-to-reuse. News/media clips can trigger a YouTube Content ID
claim even when used briefly with your own commentary over them. This
track deliberately stays manual — you pick and upload each clip yourself —
so you stay the one making that call per clip, video by video.

---

## Files in this ZIP

CHANGED (overwrite when prompted):
- `anchor_shorts.html` ......... adds a collapsible "🎥 Optional: mix in a
  clip" section to the **Pick a Title** card — a preview of your own
  recording to scrub for timing, your transcript for reference, the list of
  clips already attached (with Remove), and a small form to add another
  (file + start + duration + mute toggle). Everything else on this page is
  unchanged.
- `new_youtube/scripts/anchor_shorts_render.py` ......... the render step now
  reads an optional `clips` array off the record. For each clip it downloads
  the file, scales it to fit the PiP box with **no crop**, time-shifts it to
  appear at the right moment, and overlays it under the banner (which stays
  the topmost layer, same as before). If `clips` is empty, the render graph
  collapses back to exactly the original single-overlay version — verified
  with a real ffmpeg run before packaging this.

NEW — SQL:
- `new_youtube/sql/04_add_anchor_shorts_clips.sql` ......... adds ONE column,
  `clips jsonb DEFAULT '[]'`, to both `english_anchor_shorts` and
  `tamil_anchor_shorts`. Every existing row gets the empty-array default —
  nothing changes for recordings you've already made.

---

## Deploy with GitHub Desktop

1. Unzip. Copy everything into your local repo folder. Choose **Replace**
   when asked (only overwrites the two CHANGED files above).
2. In GitHub Desktop, review the changed + new files, write a summary like
   "Add optional PiP clips to On Camera Shorts", then **Commit** and
   **Push**.
3. Vercel auto-rebuilds the dashboard a minute or two later.

## One manual step (Supabase)

Supabase → SQL Editor → paste `new_youtube/sql/04_add_anchor_shorts_clips.sql`
→ Run. (Re-runnable — skips the column if it already exists.)

**No new secrets, no new edge function, no new GitHub workflow.** Clip
uploads reuse the existing `anchor-upload-url` function (it's generic — just
mints a signed GCS upload session for any file), and the render step runs
inside the existing `anchor_shorts_render.yml` workflow you already have.

---

## Your first run with a clip

1. Upload and transcribe a recording as usual, same as any On Camera Short.
2. On the **Pick a Title** card, open **🎥 Optional: mix in a clip**.
3. Scrub the video preview to find where you want the clip — note the
   second, e.g. `14`.
4. Upload the clip file, set **start** to `14`, **duration** to however long
   you want it visible (e.g. `6`), leave **mute clip audio** checked unless
   you specifically want its sound. Click **+ Add clip**.
5. Repeat for as many clips as you want, in any order.
6. Pick a title as usual — this kicks off the render, now including your
   clip(s).
7. **Preview render** before publishing, same as always.
