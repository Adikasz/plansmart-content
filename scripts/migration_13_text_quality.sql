-- ════════════════════════════════════════════════════
-- Phase 13 migráció — szöveg-minőség trend
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════

-- Folyamatos szöveg-eval (generator_worker: 50 posztonként 5 random) trend-adatai.
CREATE TABLE IF NOT EXISTS text_quality_metrics (
    id           SERIAL PRIMARY KEY,
    date         TIMESTAMPTZ DEFAULT NOW(),
    voice        TEXT,
    avg_score    NUMERIC,
    sample_size  INT
);

CREATE INDEX IF NOT EXISTS idx_tqm_date  ON text_quality_metrics(date DESC);
CREATE INDEX IF NOT EXISTS idx_tqm_voice ON text_quality_metrics(voice);

-- Trend lekérdezés:
--   SELECT date_trunc('day', date) d, voice, round(avg(avg_score),2), sum(sample_size)
--   FROM text_quality_metrics GROUP BY 1,2 ORDER BY 1 DESC;
