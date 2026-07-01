-- ════════════════════════════════════════════════════
-- Phase 7.7 migráció — LinkedIn optimalizálás (2026 keretrendszer)
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════

-- A nyers (voice) poszt a posts.content marad; a final_content az optimalizált,
-- élesben posztolt/megjelenített változat. A diagnosztika a hook típushoz + tier-hez.

ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS final_content TEXT;

ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS hook_type CHAR(1);

ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS hook_score INT;

ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS estimated_engagement_tier TEXT;

-- Megjegyzés: a raw_content, structure_score, optimizer_warnings és hook_variants a
-- posts.metadata JSONB mezőbe kerül (nincs hozzá külön oszlop szükséges).

-- Engagement tier összesítő (kényelmi nézet):
--   SELECT estimated_engagement_tier, count(*) FROM posts
--   WHERE final_content IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;
