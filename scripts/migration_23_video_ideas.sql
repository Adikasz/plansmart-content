-- ════════════════════════════════════════════════════
-- Phase 22 — video_ideas tábla (heti reakció-videó ötletek, Ádám hangján)
-- ════════════════════════════════════════════════════
-- Talking-point váz Ádámnak egy 2-4 perces reakció-videóhoz (NEM szó szerint felolvasandó
-- szkript, kivéve a hook -- lásd src/generators/video_idea_generator.py). A hetente futó
-- src/workers/video_idea_worker.py és a /create_video Telegram parancs tölti fel.
--
-- edited_notes/edit_count: ugyanaz a minta mint posts.edited_content/edit_count -- a ✏️ Edit
-- gomb NEM próbálja szét-parse-olni a strukturált mezőket, egy szabad szöveges jegyzet
-- lecseréli/kiegészíti a megjelenítést (lásd /edit_video parancs).
-- approved_by/filmed_by: ugyanaz a minta mint posts.approved_by (ki nyomta meg a gombot/adta
-- ki a parancsot).
--
-- Futtatás: Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS video_ideas (
    id                          TEXT PRIMARY KEY,        -- rövid uuid hex
    voice                       TEXT NOT NULL,            -- 'adam' egyelőre, bővíthető
    feed_item_id                TEXT REFERENCES feed_items(id),  -- a hír, amire reagál

    title                       TEXT,                     -- belső munkacím (nem publikus)
    hook                        TEXT NOT NULL,            -- az első 1-2 mondat, közel szó szerint
    talking_points              JSONB NOT NULL,           -- 3-5 elemű string tömb, vázlatpontok
    closing_thought             TEXT,                     -- laza lezárás, nincs kemény CTA
    suggested_caption           TEXT,                     -- a VALÓDI LinkedIn caption a feltöltéshez
    estimated_duration_seconds  INT,                      -- becslés, cél 120-240s

    status                      TEXT DEFAULT 'drafted',
    fabrication_risk            BOOLEAN DEFAULT FALSE,
    fabrication_reason          TEXT,

    edited_notes                TEXT,
    edit_count                  INT DEFAULT 0,

    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    approved_at                 TIMESTAMPTZ,
    approved_by                 TEXT,
    filmed_at                   TIMESTAMPTZ,
    filmed_by                   TEXT,

    CONSTRAINT video_ideas_voice_check
        CHECK (voice IN ('david', 'adam', 'plansmart')),
    CONSTRAINT video_ideas_status_check
        CHECK (status IN ('drafted', 'approved', 'edited', 'skipped', 'filmed'))
);

CREATE INDEX IF NOT EXISTS idx_video_ideas_status     ON video_ideas(status);
CREATE INDEX IF NOT EXISTS idx_video_ideas_voice      ON video_ideas(voice);
CREATE INDEX IF NOT EXISTS idx_video_ideas_created    ON video_ideas(created_at DESC);

-- UNIQUE, nem sima index: a "max 1 video_idea / feed_item" dedupot (has_video_idea_for_feed_item)
-- az alkalmazás check-then-insert módon ellenőrzi, de a generálás (Claude-hívás) másodperceket
-- vesz igénybe a check és az insert között -- a heti cron és egy egyidejű /create_video ugyanarra
-- a jelöltre futhat rá. Ez az egyedi index garantálja az invariánst az adatbázis szintjén is
-- (23505 hibát dob a második írásra, amit a hívó kód lekezel -- lásd video_ideas.py
-- DuplicateVideoIdeaError).
CREATE UNIQUE INDEX IF NOT EXISTS idx_video_ideas_feed_item_unique
    ON video_ideas(feed_item_id) WHERE feed_item_id IS NOT NULL;
