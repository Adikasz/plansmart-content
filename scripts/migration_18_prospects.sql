-- ════════════════════════════════════════════════════
-- Phase 18 — prospects tábla (minőségi LinkedIn kapcsolatépítés)
-- ════════════════════════════════════════════════════
-- Kutatás + EMBERI jóváhagyás. NINCS automatizált kapcsolatkérés,
-- NINCS LinkedIn scraping, NINCS auto-küldés. A tényleges LinkedIn
-- akciót Dávid/Ádám végzi manuálisan, saját sessionben.
--
-- Futtatás: Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS prospects (
    id                    TEXT PRIMARY KEY,        -- rövid uuid hex
    name                  TEXT NOT NULL,
    title                 TEXT,                    -- pozíció (pl. ügyvezető, alapító)
    company               TEXT,
    company_size_estimate TEXT,                    -- pl. "10-50 fő" (becslés)
    country               TEXT,
    city                  TEXT,

    linkedin_url          TEXT,                    -- NULL: a human tölti ki (nem scrape-elünk)

    category              TEXT NOT NULL,           -- lásd CHECK
    voice                 TEXT,                    -- melyik alapító küldi (david | adam)

    relevance_notes       TEXT,                    -- miért releváns kapcsolat
    connection_note_draft TEXT,                    -- a generált kapcsolat-üzenet (<=300 kar)
    note_language         TEXT,                    -- 'hu' | 'en'

    status                TEXT DEFAULT 'researched',
    source                TEXT,                    -- hol találtuk (publikus forrás)

    added_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    sent_at               TIMESTAMPTZ,             -- mikor küldte el manuálisan a human

    CONSTRAINT prospects_category_check
        CHECK (category IN ('hu_sme_owner', 'intl_sme_owner', 'ai_specialist', 'industry_peer')),
    CONSTRAINT prospects_voice_check
        CHECK (voice IS NULL OR voice IN ('david', 'adam')),
    CONSTRAINT prospects_status_check
        CHECK (status IN ('researched', 'note_drafted', 'approved_to_send',
                          'sent', 'connected', 'declined', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_prospects_status   ON prospects(status);
CREATE INDEX IF NOT EXISTS idx_prospects_category ON prospects(category);
CREATE INDEX IF NOT EXISTS idx_prospects_added    ON prospects(added_at DESC);
