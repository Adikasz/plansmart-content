-- ════════════════════════════════════════════════════
-- Phase 21 — prospect_interactions tábla (kapcsolatépítés élet-ciklus követés)
-- ════════════════════════════════════════════════════
-- A prospects.status a "sent"-nél megáll (lásd migration_18_prospects.sql) -- ez a tábla
-- követi a KAPCSOLATFELVÉTEL UTÁNI életciklust (elfogadta / válaszolt / meeting / lezárva),
-- egy prospecthez több sorral (idővonal), a prospects.status oszlopot NEM írja felül.
--
-- Futtatás: Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS prospect_interactions (
    id                TEXT PRIMARY KEY,               -- rövid uuid hex
    prospect_id       TEXT NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,

    interaction_type  TEXT NOT NULL,                  -- lásd CHECK
    notes             TEXT,                           -- szabad szöveg (mit mondott / kontextus)
    interaction_date  TIMESTAMPTZ DEFAULT NOW(),

    created_at        TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT prospect_interactions_type_check
        CHECK (interaction_type IN (
            'connection_sent', 'connection_accepted', 'replied',
            'meeting_booked', 'went_cold', 'not_interested',
            'note'                                    -- csak jegyzet, nem stage-váltás
        ))
);

CREATE INDEX IF NOT EXISTS idx_prospect_interactions_prospect ON prospect_interactions(prospect_id);
CREATE INDEX IF NOT EXISTS idx_prospect_interactions_date     ON prospect_interactions(interaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_prospect_interactions_type     ON prospect_interactions(interaction_type);
