-- ════════════════════════════════════════════════════
-- Phase 10 migráció — breaking news + ütemezés metaadat
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════

-- 1) feed_items.breaking — igaz, ha az elem breaking news-ként ki lett küldve
ALTER TABLE feed_items
    ADD COLUMN IF NOT EXISTS breaking BOOLEAN DEFAULT FALSE;

-- 2) posts.is_breaking — igaz, ha a poszt breaking news reakció (Ádám, 24/7)
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS is_breaking BOOLEAN DEFAULT FALSE;

-- 3) posts.sent_at — mikor ment ki Telegramra (approval/reactions csatorna)
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;

-- Napi breaking limit ellenőrzéséhez parciális index (csak a breaking sorokra):
CREATE INDEX IF NOT EXISTS idx_posts_is_breaking ON posts(is_breaking) WHERE is_breaking = true;

-- Napi breaking darabszám (kényelmi lekérdezés):
--   SELECT count(*) FROM posts WHERE is_breaking AND sent_at >= date_trunc('day', now());
