-- ============================================================
-- Sprint 8 Migration — Run in Supabase SQL Editor
-- ============================================================

-- ── 1. tamil_episodes — X image columns + separate reels status ──
ALTER TABLE tamil_episodes
  ADD COLUMN IF NOT EXISTS x_images_tamil        JSONB,
  ADD COLUMN IF NOT EXISTS x_images_english      JSONB,
  ADD COLUMN IF NOT EXISTS status_reels          TEXT DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS reels_url             TEXT,
  ADD COLUMN IF NOT EXISTS reels_media_id        TEXT,
  ADD COLUMN IF NOT EXISTS youtube_shorts_url    TEXT,
  ADD COLUMN IF NOT EXISTS youtube_shorts_id     TEXT,
  ADD COLUMN IF NOT EXISTS x_post_url_tamil      TEXT,
  ADD COLUMN IF NOT EXISTS x_post_url_english    TEXT,
  ADD COLUMN IF NOT EXISTS x_post_id_tamil       TEXT,
  ADD COLUMN IF NOT EXISTS x_post_id_english     TEXT;

-- ── 2. english_episodes — same additions ─────────────────────
ALTER TABLE english_episodes
  ADD COLUMN IF NOT EXISTS x_images_tamil        JSONB,
  ADD COLUMN IF NOT EXISTS x_images_english      JSONB,
  ADD COLUMN IF NOT EXISTS status_reels          TEXT DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS reels_url             TEXT,
  ADD COLUMN IF NOT EXISTS reels_media_id        TEXT,
  ADD COLUMN IF NOT EXISTS youtube_shorts_url    TEXT,
  ADD COLUMN IF NOT EXISTS youtube_shorts_id     TEXT,
  ADD COLUMN IF NOT EXISTS x_post_url_tamil      TEXT,
  ADD COLUMN IF NOT EXISTS x_post_url_english    TEXT,
  ADD COLUMN IF NOT EXISTS x_post_id_tamil       TEXT,
  ADD COLUMN IF NOT EXISTS x_post_id_english     TEXT;

-- ── 3. ideas — x images + idea YouTube upload columns ────────
ALTER TABLE ideas
  ADD COLUMN IF NOT EXISTS x_images_tamil        JSONB,
  ADD COLUMN IF NOT EXISTS x_images_english      JSONB,
  ADD COLUMN IF NOT EXISTS youtube_url           TEXT,
  ADD COLUMN IF NOT EXISTS youtube_video_id      TEXT,
  ADD COLUMN IF NOT EXISTS youtube_shorts_url    TEXT,
  ADD COLUMN IF NOT EXISTS youtube_shorts_id     TEXT,
  ADD COLUMN IF NOT EXISTS reels_url             TEXT,
  ADD COLUMN IF NOT EXISTS reels_media_id        TEXT,
  ADD COLUMN IF NOT EXISTS x_post_url_tamil      TEXT,
  ADD COLUMN IF NOT EXISTS x_post_url_english    TEXT,
  ADD COLUMN IF NOT EXISTS x_post_id_tamil       TEXT,
  ADD COLUMN IF NOT EXISTS x_post_id_english     TEXT,
  ADD COLUMN IF NOT EXISTS status_reels          TEXT DEFAULT 'pending';

-- ── 4. Status check: verify columns exist ────────────────────
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'tamil_episodes'
  AND column_name IN (
    'status','status_shorts','status_reels','status_x',
    'x_images_tamil','x_images_english',
    'image_urls_vertical','video_url_short',
    'youtube_url','youtube_shorts_url','reels_url',
    'x_post_url_tamil','x_post_url_english'
  )
ORDER BY column_name;
