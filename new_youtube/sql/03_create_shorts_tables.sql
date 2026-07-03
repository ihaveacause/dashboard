-- ============================================================
--  SHORTS PIPELINE · Sprint 15
--  Database tables — 1 to 3 provocative Shorts PER episode
-- ------------------------------------------------------------
--  Creates two NEW tables — tamil_shorts and english_shorts —
--  ONE ROW PER SHORT (not per episode). Gemini decides how many
--  shorts (1-3) an episode deserves when the script step runs,
--  so this is a proper child table keyed on
--  (episode_number, short_index), not columns bolted onto the
--  episode row like the old status_shorts/video_url_short did.
--
--  • Your existing tables (including the old single-short
--    columns from Sprint 8) are NOT touched or removed.
--  • These start completely EMPTY.
--  • Safe to run once in the Supabase SQL Editor (re-runnable).
-- ============================================================


-- 1) The two shorts tables (one per language, independent lanes) ----------------
CREATE TABLE IF NOT EXISTS public.english_shorts (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at          timestamptz NOT NULL DEFAULT now(),

  -- which episode this short belongs to, and which of its (up to 3) shorts
  episode_number      integer NOT NULL,
  short_index         integer NOT NULL,          -- 1, 2, or 3

  -- produced by the script step (Gemini reads the APPROVED long script and
  -- decides how many standalone, provocative moments it can find — 1 to 3)
  title               text,                      -- short on-screen / YouTube title
  hook_line           text,                      -- the first line, must stop the scroll
  script              text,                      -- full self-contained short script (~45-60s)
  cta_line            text,                      -- the closing line that teases the long video

  -- produced by the images step
  image_urls_vertical jsonb DEFAULT '[]'::jsonb,  -- [{id,label,url,prompt}] 9:16 images

  -- produced by the render step (voice + ffmpeg baked in, same as render pipeline)
  video_url           text,                       -- GCS URL, 1080x1920

  -- publish
  youtube_url          text,
  youtube_video_id      text,
  parent_youtube_url    text,                      -- long video it points to (filled at publish time)
  scheduled_at          timestamptz,

  status                text NOT NULL DEFAULT 'pending',

  UNIQUE(episode_number, short_index)
);

CREATE TABLE IF NOT EXISTS public.tamil_shorts (LIKE public.english_shorts INCLUDING ALL);


-- 2) Row-level security + grants, exactly like your other tracks ----------------
ALTER TABLE public.english_shorts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tamil_shorts   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "shorts_full_access_en" ON public.english_shorts;
CREATE POLICY "shorts_full_access_en" ON public.english_shorts
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "shorts_full_access_ta" ON public.tamil_shorts;
CREATE POLICY "shorts_full_access_ta" ON public.tamil_shorts
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

GRANT ALL ON public.english_shorts TO anon, authenticated, service_role;
GRANT ALL ON public.tamil_shorts   TO anon, authenticated, service_role;


-- 3) Refresh the API schema cache so the new tables are visible immediately -----
NOTIFY pgrst, 'reload schema';


-- 4) Quick check — should return english_shorts and tamil_shorts ---------------
SELECT table_name, COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('english_shorts', 'tamil_shorts')
GROUP BY table_name
ORDER BY table_name;
