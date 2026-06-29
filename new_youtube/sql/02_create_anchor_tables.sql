-- ============================================================
--  ON CAMERA (Studio Desk) PIPELINE · Sprint 14
--  Database tables (recordings live in Google Cloud Storage)
-- ------------------------------------------------------------
--  Creates two NEW tables — tamil_anchor and english_anchor —
--  for the video-first "you on camera" track. These are NOT
--  copies of the episode tables: the flow is different
--  (upload video -> transcribe -> beats -> studio render),
--  so the columns are purpose-built.
--
--  • Your existing tables are NOT touched.
--  • These start completely EMPTY.
--  • Safe to run once in the Supabase SQL Editor (re-runnable).
-- ============================================================


-- 1) The two anchor tables (one per language, independent lanes) ----------------
CREATE TABLE IF NOT EXISTS public.english_anchor (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at        timestamptz NOT NULL DEFAULT now(),

  -- entry point
  working_title     text,                      -- the rough label you type when you upload
  source_video_url  text,                      -- the recording you uploaded (GCS object URL)
  source_mode       text NOT NULL DEFAULT 'free',   -- 'free' = you spoke freely (Whisper)
                                               -- 'script' = you read script_text aloud
  script_text       text,                      -- only used when source_mode = 'script'
  studio_mode       text NOT NULL DEFAULT 'real_room', -- 'real_room' (no green) | 'green'
  module            text,                      -- playlist routing (optional)

  -- produced by transcribe step
  detected_lang     text,                      -- 'en' / 'ta' (Whisper auto-detect, sanity check)
  transcript        text,
  word_timings      jsonb DEFAULT '[]'::jsonb, -- [{word,start,end}] one per spoken word
  title_suggestions jsonb DEFAULT '[]'::jsonb, -- [{text,style}] — Claude's 3 options
  title             text,                      -- the one you pick (empty until you choose)

  -- produced by beats step
  beats             jsonb DEFAULT '[]'::jsonb, -- [{order,mode,headline,bullets,scene,image_prompt,
                                               --   image_url,trigger,start,end}]
  -- produced by render step
  thumbnail_url     text,
  captions_url      text,
  video_url         text,                      -- final studio render (GCS signed URL)

  -- publish
  youtube_url       text,
  youtube_video_id  text,
  playlist_id       text,
  scheduled_at      timestamptz,

  status            text NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS public.tamil_anchor (LIKE public.english_anchor INCLUDING ALL);


-- 2) Row-level security + grants, exactly like your ideas tables ----------------
ALTER TABLE public.english_anchor ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tamil_anchor   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anchor_full_access_en" ON public.english_anchor;
CREATE POLICY "anchor_full_access_en" ON public.english_anchor
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anchor_full_access_ta" ON public.tamil_anchor;
CREATE POLICY "anchor_full_access_ta" ON public.tamil_anchor
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

GRANT ALL ON public.english_anchor TO anon, authenticated, service_role;
GRANT ALL ON public.tamil_anchor   TO anon, authenticated, service_role;


-- 3) Refresh the API schema cache so the new tables are visible immediately ------
--    (Recordings upload straight to Google Cloud Storage — the same
--     ihaveacause-media bucket as every other output — so there is NO Supabase
--     storage bucket to create here.)
NOTIFY pgrst, 'reload schema';


-- 4) Quick check — should return english_anchor and tamil_anchor ----------------
SELECT table_name, COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('english_anchor', 'tamil_anchor')
GROUP BY table_name
ORDER BY table_name;
