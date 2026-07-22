-- ════════════════════════════════════════════════════
-- Phase 21b — engagement_metrics tábla (manuális LinkedIn engagement mérés)
-- ════════════════════════════════════════════════════
-- A LinkedIn API még nem éles -- Dávid/Ádám kézzel nézi meg a számokat LinkedInen és jelenti
-- be a /log_stats paranccsal. TÖBB sor is tartozhat egy poszthoz (időbeli pillanatfelvételek,
-- pl. 24h-nál és 48h-nál is mérve) -- lásd src/storage/engagement.py reprezentatív-sor
-- kiválasztás logikáját a /engagement_report-hoz.
--
-- Nem kell új oszlop a posts táblára a vizuál-template/portré követéséhez -- azt a már
-- létező posts.metadata JSONB kapja (visual_template/portrait_used kulcsok), ugyanúgy mint a
-- strategy_type/seed_key (lásd insert_post/save_visual a src/storage/posts.py-ban).
--
-- Futtatás: Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS engagement_metrics (
    id                TEXT PRIMARY KEY,               -- rövid uuid hex
    post_id           TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    voice             TEXT NOT NULL,                  -- denormalizált posts.voice-ból (gyors csoportosítás)

    measured_at       TIMESTAMPTZ DEFAULT NOW(),       -- mikor jelentette be a human
    hours_since_post  INT,                             -- posts.sent_at-ból számolva; NULL, ha sent_at
                                                        -- nem volt beállítva méréskor (lásd /mark_posted)

    views             INT,
    likes             INT,
    comments          INT,
    shares            INT,

    is_final_snapshot BOOLEAN DEFAULT FALSE,           -- true, ha hours_since_post >= 48 (auto-számolt,
                                                        -- de a /log_stats final=true/false paranccsal felülírható)

    CONSTRAINT engagement_metrics_voice_check
        CHECK (voice IN ('david', 'adam', 'plansmart'))
);

CREATE INDEX IF NOT EXISTS idx_engagement_post     ON engagement_metrics(post_id);
CREATE INDEX IF NOT EXISTS idx_engagement_measured ON engagement_metrics(measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_engagement_final    ON engagement_metrics(post_id, is_final_snapshot);
