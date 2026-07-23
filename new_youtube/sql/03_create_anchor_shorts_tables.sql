-- ============================================================
--  ON CAMERA SHORTS PIPELINE · Sprint 16
--  Database tables (recordings live in Google Cloud Storage)
-- ------------------------------------------------------------
--  Creates two NEW tables — tamil_anchor_shorts and
--  english_anchor_shorts — for the "record yourself VERTICALLY"
--  track. Separate from:
--    • tamil_anchor / english_anchor      (existing On Camera → YouTube Long, landscape)
--    • tamil_shorts / english_shorts      (existing AI-cut Shorts from long episodes)
--  This one is: you record vertical → transcribe → pick a title →
--  render (your footage, cropped 9:16, bottom banner + logo) →
--  publish as a YouTube Short.
--
--  • Your existing tables are NOT touched.
--  • These start completely EMPTY.
--  • Safe to run once in the Supabase SQL Editor (re-runnable).
-- ============================================================

CREATE TABLE IF NOT EXISTS public.english_anchor_shorts (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at        timestamptz NOT NULL DEFAULT now(),

  -- entry point
  working_title     text,                      -- rough label you type when you upload
  source_video_url  text,                      -- the VERTICAL recording you uploaded (GCS object URL)
  source_mode       text NOT NULL DEFAULT 'free',   -- 'free' = you spoke freely (Whisper)
                                               -- 'script' = you read script_text aloud
  script_text       text,                      -- only used when source_mode = 'script'
  module            text,                      -- playlist routing (optional)

  -- produced by transcribe step
  detected_lang     text,                      -- 'en' / 'ta' (Whisper auto-detect, sanity check)
  transcript        text,
  word_timings      jsonb DEFAULT '[]'::jsonb, -- [{word,start,end}] one per spoken word
  title_suggestions jsonb DEFAULT '[]'::jsonb, -- [{text,style}] — Claude's 3 options
  title             text,                      -- the one you pick (empty until you choose)

  -- produced by render step
  thumbnail_url     text,
  video_url         text,                      -- final vertical render, banner+logo burned in (GCS)

  -- publish
  youtube_url       text,
  youtube_video_id  text,
  playlist_id       text,
  scheduled_at      timestamptz,

  status            text NOT NULL DEFAULT 'pending'
  -- pending -> transcribing -> transcribed -> rendering -> rendered
  -- -> publishing -> published
);

CREATE TABLE IF NOT EXISTS public.tamil_anchor_shorts (LIKE public.english_anchor_shorts INCLUDING ALL);


-- Row-level security + grants, exactly like your other tables ----------------
ALTER TABLE public.english_anchor_shorts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tamil_anchor_shorts   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anchor_shorts_full_access_en" ON public.english_anchor_shorts;
CREATE POLICY "anchor_shorts_full_access_en" ON public.english_anchor_shorts
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anchor_shorts_full_access_ta" ON public.tamil_anchor_shorts;
CREATE POLICY "anchor_shorts_full_access_ta" ON public.tamil_anchor_shorts
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

GRANT ALL ON public.english_anchor_shorts TO anon, authenticated, service_role;
GRANT ALL ON public.tamil_anchor_shorts   TO anon, authenticated, service_role;


-- Refresh the API schema cache so the new tables are visible immediately ------
-- (Recordings upload straight to Google Cloud Storage — same ihaveacause-media
--  bucket as everything else — so there is NO Supabase storage bucket to create.)
NOTIFY pgrst, 'reload schema';


-- Quick check — should return english_anchor_shorts and tamil_anchor_shorts --
SELECT table_name, COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('english_anchor_shorts', 'tamil_anchor_shorts')
GROUP BY table_name
ORDER BY table_name;
