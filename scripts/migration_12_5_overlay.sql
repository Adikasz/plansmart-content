-- ════════════════════════════════════════════════════
-- Phase 12.5 migráció — szöveg-overlay pipeline
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════

-- A nyers Muapi alapkép (szöveg NÉLKÜL) URL-je; a posts.visual_url a végleges,
-- magyar szöveggel komponált (PIL overlay) kép a 'visuals' Storage bucketben.
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS base_image_url TEXT;
