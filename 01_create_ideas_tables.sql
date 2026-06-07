-- ============================================================
--  IDEAS PIPELINE  ·  STEP 1 of deployment: database tables
-- ------------------------------------------------------------
--  Creates two new tables — tamil_ideas and english_ideas —
--  as EXACT structural copies of your live episode tables,
--  plus two extra fields the Ideas flow needs.
--
--  • Your existing episode tables are NOT touched in any way.
--  • These new tables start completely EMPTY.
--  • Safe to run once in the Supabase SQL Editor.
--    (If you ever run it again, it skips what already exists.)
-- ============================================================


-- 1) Create the two idea tables as exact copies of your episode tables.
--    "LIKE ... INCLUDING ALL" tells Postgres: copy every column, type,
--    default and index straight from the LIVE table. This means we never
--    have to guess your real schema — Postgres reads it for us.
CREATE TABLE IF NOT EXISTS public.tamil_ideas   (LIKE public.tamil_episodes   INCLUDING ALL);
CREATE TABLE IF NOT EXISTS public.english_ideas (LIKE public.english_episodes INCLUDING ALL);


-- 2) Add the two Ideas-only fields:
--      working_title  = the rough/seed title you type in
--      description    = the extra context you give Gemini
ALTER TABLE public.tamil_ideas
  ADD COLUMN IF NOT EXISTS working_title TEXT,
  ADD COLUMN IF NOT EXISTS description   TEXT;

ALTER TABLE public.english_ideas
  ADD COLUMN IF NOT EXISTS working_title TEXT,
  ADD COLUMN IF NOT EXISTS description   TEXT;


-- 3) Let the dashboard read & write these tables, exactly like it does
--    with episodes. Your dashboard signs in with the public "anon" key,
--    so we explicitly allow that role full access on the new tables.
--    This is what prevents an empty list or a "permission denied" error.
ALTER TABLE public.tamil_ideas   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.english_ideas ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ideas_full_access_ta" ON public.tamil_ideas;
CREATE POLICY "ideas_full_access_ta" ON public.tamil_ideas
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "ideas_full_access_en" ON public.english_ideas;
CREATE POLICY "ideas_full_access_en" ON public.english_ideas
  FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);

GRANT ALL ON public.tamil_ideas   TO anon, authenticated, service_role;
GRANT ALL ON public.english_ideas TO anon, authenticated, service_role;


-- 4) Tell Supabase's API to notice the new tables right away.
NOTIFY pgrst, 'reload schema';


-- 5) Quick check — after running, this should show two rows:
--    tamil_ideas and english_ideas, each with its column count.
SELECT table_name, COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('tamil_ideas', 'english_ideas')
GROUP BY table_name
ORDER BY table_name;
