-- ════════════════════════════════════════════════════
-- Phase 15 migráció — portré-számláló Supabase-ben
--
-- Miért: a számláló eddig a data/portrait_state.json-ban élt, ami a Railway
-- EFEMER filerendszerén minden redeploynál nullázódott → a david/adam portré
-- (minden 3-4. poszt) gyakorlatilag sosem sült el éles környezetben.
-- Ez a tábla túléli a redeployt (az egy igazság forrása: Supabase).
--
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS portrait_counters (
    voice      TEXT PRIMARY KEY,                                    -- 'david' | 'adam' (plansmart SOHA nem kap portrét)
    count      INT         NOT NULL DEFAULT 0,                      -- posztok száma a legutóbbi portré óta
    threshold  INT         NOT NULL DEFAULT (3 + floor(random() * 2)::int),  -- 3 vagy 4 (a köv. portréig)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- A sorokat az alkalmazás hozza létre első használatkor (upsert), illetve a
-- portrait.seed_from_local_state() egyszeri migráció a régi JSON-értékekből.
-- Ezért itt NINCS seed INSERT — nehogy felülírjuk az átemelt állapotot.

-- Ellenőrzés:
--   SELECT * FROM portrait_counters;
