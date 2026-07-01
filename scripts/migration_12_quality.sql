-- ════════════════════════════════════════════════════
-- Phase 12 migráció — vizuál minőség trend
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════

-- A folyamatos eval (generator_worker: 50 posztonként 5 random) trend-adatai.
CREATE TABLE IF NOT EXISTS visual_quality_metrics (
    id           SERIAL PRIMARY KEY,
    date         TIMESTAMPTZ DEFAULT NOW(),
    voice        TEXT,
    avg_score    NUMERIC,
    sample_size  INT
);

CREATE INDEX IF NOT EXISTS idx_vqm_date  ON visual_quality_metrics(date DESC);
CREATE INDEX IF NOT EXISTS idx_vqm_voice ON visual_quality_metrics(voice);

-- Trend lekérdezés (kényelmi):
--   SELECT date_trunc('day', date) d, voice, round(avg(avg_score),2), sum(sample_size)
--   FROM visual_quality_metrics GROUP BY 1,2 ORDER BY 1 DESC;
