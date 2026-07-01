-- ════════════════════════════════════════════════════
-- Phase 8 migráció — 'published' státusz a posts táblához
-- A LinkedIn publisher sikeres posztolásnál status='published'-re vált.
-- Futtatás: Supabase SQL Editor.
-- ════════════════════════════════════════════════════

ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_status_check;

ALTER TABLE posts ADD CONSTRAINT posts_status_check
    CHECK (status IN ('pending', 'approved', 'edited', 'regenerated',
                      'skipped', 'posted', 'published', 'failed'));
