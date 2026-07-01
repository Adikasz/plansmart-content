-- ════════════════════════════════════════════════════
-- Phase 7.6 migráció — vizuál generálás (Muapi)
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════

-- 1) posts.visual_url — a generált kép (Muapi R2) URL-je
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS visual_url TEXT;

-- 1b) posts.workshop_mentioned — igaz, ha a poszt workshopot/eseményt említ (workshop_promo)
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS workshop_mentioned BOOLEAN DEFAULT FALSE;

-- 2) costs tábla — minden külső API hívás (Muapi) költsége a havi monitorhoz
CREATE TABLE IF NOT EXISTS costs (
    id              SERIAL PRIMARY KEY,
    post_id         TEXT REFERENCES posts(id) ON DELETE SET NULL,
    kind            TEXT DEFAULT 'muapi_image',
    model           TEXT,
    cost_usd        NUMERIC,
    request_id      TEXT,
    generated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_costs_generated ON costs(generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_costs_post      ON costs(post_id);

-- Havi költés összesítő (kényelmi nézet):
--   SELECT date_trunc('month', generated_at) AS month, model, count(*), sum(cost_usd)
--   FROM costs GROUP BY 1, 2 ORDER BY 1 DESC;
