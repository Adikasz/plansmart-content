-- ════════════════════════════════════════════════════
-- Phase 19 — reactions tábla (manuális-módú reakció-asszisztens)
-- ════════════════════════════════════════════════════
-- Amíg a LinkedIn Community Management API nincs jóváhagyva, Dávid/Ádám
-- KÉZZEL kapja a kommenteket/DM-eket, beilleszti a Telegram botba, a bot
-- javasol egy választ a megfelelő hangon, ők szerkesztik és MANUÁLISAN
-- küldik el. NINCS auto-válasz, NINCS LinkedIn API — ez csak draft-segéd.
--
-- Ez a tábla a javaslatokat + a döntéseket rögzíti későbbi analitikához
-- (mely komment/DM-típusok kapnak leggyakrabban választ, minőség-trend stb.).
--
-- Futtatás: Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS reactions (
    id                TEXT PRIMARY KEY,          -- rövid uuid hex
    voice             TEXT NOT NULL,             -- david | adam | plansmart
    type              TEXT NOT NULL,             -- comment | dm
    incoming_text     TEXT NOT NULL,             -- amit a bejövő komment/DM tartalmaz
    context_text      TEXT,                      -- a poszt (kommentnél) / korábbi DM kontextus
    our_reply_draft   TEXT,                      -- a generált (majd esetleg szerkesztett) válasz
    classification    TEXT,                      -- lásd CHECK (a Haiku besorolása)
    language          TEXT,                      -- 'hu' | 'en' (a válasz nyelve)
    status            TEXT DEFAULT 'drafted',    -- drafted | approved | edited | skipped

    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW(),
    sent_at           TIMESTAMPTZ,               -- mikor jelezte a human, hogy elküldte

    CONSTRAINT reactions_voice_check
        CHECK (voice IN ('david', 'adam', 'plansmart')),
    CONSTRAINT reactions_type_check
        CHECK (type IN ('comment', 'dm')),
    CONSTRAINT reactions_status_check
        CHECK (status IN ('drafted', 'approved', 'edited', 'skipped')),
    CONSTRAINT reactions_classification_check
        CHECK (classification IS NULL OR classification IN (
            'spam_or_troll', 'competitor_pitch', 'appreciative_only',
            'question_or_engagement', 'lead_signal'
        ))
);

CREATE INDEX IF NOT EXISTS idx_reactions_status  ON reactions(status);
CREATE INDEX IF NOT EXISTS idx_reactions_type    ON reactions(type);
CREATE INDEX IF NOT EXISTS idx_reactions_created ON reactions(created_at DESC);
