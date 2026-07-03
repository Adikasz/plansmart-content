-- ════════════════════════════════════════════════════
-- Phase 17 migráció — vizuál-változatosság rotáció-tracker
--
-- Miért: minden vizuál eddig ugyanazt az elrendezést + hangulatot használta voice-onként
-- (monoton feed). A no-immediate-repeat rotációhoz el kell tárolni az utolsó
-- template + mood választást voice-onként. Supabase (NEM lokális fájl — az a Railway
-- redeploynál nullázódna, lásd Phase 15).
--
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- A sorokat az alkalmazás hozza létre első használatkor (upsert) — nincs seed INSERT.
-- ════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS visual_variety_state (
    voice         TEXT PRIMARY KEY,                 -- 'david' | 'adam' | 'plansmart'
    last_template TEXT,                             -- STAT_CARD | QUOTE_STYLE | SPLIT_COMPARISON | MINIMAL_TYPOGRAPHIC
    last_mood     TEXT,                             -- voice-onkénti mood key (lásd layout_templates.MOODS)
    recent_combos JSONB NOT NULL DEFAULT '[]'::jsonb, -- opcionális előzmény (utolsó N kombó)
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ellenőrzés:
--   SELECT * FROM visual_variety_state;
