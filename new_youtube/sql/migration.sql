-- ============================================================
-- New YouTube Pipeline Migration
-- Run in Supabase SQL Editor
-- ============================================================

-- ── tamil_episodes ───────────────────────────────────────────
ALTER TABLE tamil_episodes
  ADD COLUMN IF NOT EXISTS voice_url        TEXT,
  ADD COLUMN IF NOT EXISTS intro_image_url  TEXT,
  ADD COLUMN IF NOT EXISTS outro_image_url  TEXT;

-- ── english_episodes ─────────────────────────────────────────
ALTER TABLE english_episodes
  ADD COLUMN IF NOT EXISTS voice_url        TEXT,
  ADD COLUMN IF NOT EXISTS intro_image_url  TEXT,
  ADD COLUMN IF NOT EXISTS outro_image_url  TEXT;

-- ── Verify ───────────────────────────────────────────────────
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'tamil_episodes'
  AND column_name IN (
    'status','script_tamil','image_urls','video_url',
    'voice_url','intro_image_url','outro_image_url',
    'youtube_url','youtube_video_id'
  )
ORDER BY column_name;
