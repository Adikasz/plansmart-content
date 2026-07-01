-- ════════════════════════════════════════════════════
-- PlanSmart Content Engine — Supabase séma
-- ════════════════════════════════════════════════════
-- Külön projekt a PPC monitortól!
-- Futtatás: Supabase SQL Editor

-- ────────────────────────────────────────────────────
-- Bejövő feed item-ek (RSS, X, LinkedIn)
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feed_items (
    id              TEXT PRIMARY KEY,           -- URL hash (sha256 első 16 kar)
    source_type     TEXT NOT NULL,              -- 'rss', 'twitter', 'linkedin', 'reddit'
    source_name     TEXT NOT NULL,              -- pl. 'anthropic_blog', 'simonw'
    source_priority INT  DEFAULT 3,             -- 1=highest, 4=lowest

    url             TEXT UNIQUE NOT NULL,
    title           TEXT,
    content         TEXT,                       -- summary vagy full body
    author          TEXT,
    raw_data        JSONB,                      -- eredeti payload további mezőkkel

    -- Lifecycle
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    published_at    TIMESTAMPTZ,                -- mikor jelent meg az eredeti

    -- Filtering eredmény
    score           INT,                        -- 0-10 Claude relevancia
    score_reason    TEXT,
    scored_at       TIMESTAMPTZ,
    voice_fit       JSONB,                      -- {david: bool, adam: bool, plansmart: bool}
    topics          TEXT[],
    urgency         TEXT,                       -- 'low', 'medium', 'high'

    -- Lifecycle
    status          TEXT DEFAULT 'new',         -- new, filtered, generated, skipped
    used_for_posts  BOOLEAN DEFAULT FALSE,

    CONSTRAINT feed_items_status_check
        CHECK (status IN ('new', 'filtered', 'generated', 'skipped'))
);

CREATE INDEX idx_feed_items_status_score    ON feed_items(status, score DESC);
CREATE INDEX idx_feed_items_source_fetched  ON feed_items(source_type, fetched_at DESC);
CREATE INDEX idx_feed_items_urgency         ON feed_items(urgency) WHERE urgency = 'high';

-- ────────────────────────────────────────────────────
-- Generált posztok (3 voice × LinkedIn + X)
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS posts (
    id              TEXT PRIMARY KEY,           -- UUID rövid
    feed_item_id    TEXT REFERENCES feed_items(id),

    voice           TEXT NOT NULL,              -- 'david', 'adam', 'plansmart'
    platform        TEXT NOT NULL,              -- 'linkedin', 'twitter'

    content         TEXT NOT NULL,
    content_type    TEXT,                       -- 'post', 'thread', 'tweet'
    hashtags        TEXT[],
    metadata        JSONB,                      -- további platform-spec adat

    -- Approval workflow
    status          TEXT DEFAULT 'pending',     -- pending, approved, edited, regenerated, skipped, posted
    approve_token   TEXT UNIQUE,                -- Telegram callback-hez
    approved_by     TEXT,                       -- 'david' vagy 'adam' (ki nyomta meg)
    approved_at     TIMESTAMPTZ,

    -- Edited version (ha módosítva volt)
    edited_content  TEXT,
    edit_count      INT DEFAULT 0,

    -- Generálás metaadat
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    model_used      TEXT,                       -- 'claude-sonnet-4-6' stb.
    generation_cost_cents NUMERIC,
    visual_url      TEXT,                       -- Muapi által hosztolt kép URL (Phase 7.6)

    CONSTRAINT posts_voice_check
        CHECK (voice IN ('david', 'adam', 'plansmart')),
    CONSTRAINT posts_platform_check
        CHECK (platform IN ('linkedin', 'twitter')),
    CONSTRAINT posts_status_check
        CHECK (status IN ('pending', 'approved', 'edited', 'regenerated', 'skipped', 'posted', 'published', 'failed'))
);

CREATE INDEX idx_posts_status_voice   ON posts(status, voice);
CREATE INDEX idx_posts_approve_token  ON posts(approve_token) WHERE approve_token IS NOT NULL;
CREATE INDEX idx_posts_pending        ON posts(generated_at DESC) WHERE status = 'pending';

-- ────────────────────────────────────────────────────
-- Telegram approval state tracking
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS approvals (
    id              SERIAL PRIMARY KEY,
    post_id         TEXT REFERENCES posts(id) ON DELETE CASCADE,

    telegram_chat_id    BIGINT,
    telegram_message_id BIGINT,                 -- a Telegram üzenet ID-ja (gomb mellékhez)
    telegram_user_id    BIGINT,                 -- ki kattintott

    action          TEXT,                       -- 'approve', 'edit', 'regenerate', 'skip'
    action_data     JSONB,                      -- pl. új szöveg edit esetén
    actioned_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_approvals_post ON approvals(post_id);

-- ────────────────────────────────────────────────────
-- Published posts (mi ment ki ténylegesen)
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS published (
    id              SERIAL PRIMARY KEY,
    post_id         TEXT REFERENCES posts(id),

    platform        TEXT NOT NULL,
    platform_post_id TEXT,                      -- LinkedIn / X visszaadott ID
    platform_post_url TEXT,

    published_at    TIMESTAMPTZ DEFAULT NOW(),

    -- Engagement metrikák (később frissítjük)
    impressions     INT DEFAULT 0,
    likes           INT DEFAULT 0,
    comments        INT DEFAULT 0,
    shares          INT DEFAULT 0,
    last_metrics_at TIMESTAMPTZ
);

CREATE INDEX idx_published_post ON published(post_id);

-- ────────────────────────────────────────────────────
-- Source health monitoring
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source_stats (
    source_name     TEXT PRIMARY KEY,
    source_type     TEXT,
    last_polled_at  TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error      TEXT,
    error_count_24h INT DEFAULT 0,
    items_fetched_24h INT DEFAULT 0,
    items_total     INT DEFAULT 0,
    is_healthy      BOOLEAN DEFAULT TRUE
);

-- ────────────────────────────────────────────────────
-- Application log (csak fontos eventek — minden más loguruba)
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id              SERIAL PRIMARY KEY,
    level           TEXT,                       -- 'INFO', 'WARN', 'ERROR'
    component       TEXT,                       -- 'collector', 'filter', 'generator', etc.
    event           TEXT,                       -- pl. 'feed_polled', 'post_generated'
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_events_created ON events(created_at DESC);
CREATE INDEX idx_events_level   ON events(level) WHERE level IN ('WARN', 'ERROR');

-- ────────────────────────────────────────────────────
-- Külső API költségek (Muapi képgenerálás stb.) — havi költés monitor (Phase 7.6)
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS costs (
    id              SERIAL PRIMARY KEY,
    post_id         TEXT REFERENCES posts(id) ON DELETE SET NULL,
    kind            TEXT DEFAULT 'muapi_image', -- pl. 'muapi_image'
    model           TEXT,                       -- 'flux-2-pro', 'nano-banana-2'
    cost_usd        NUMERIC,
    request_id      TEXT,                       -- Muapi request_id (audithoz)
    generated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_costs_generated ON costs(generated_at DESC);
CREATE INDEX idx_costs_post      ON costs(post_id);

-- ────────────────────────────────────────────────────
-- OAuth tokenek fiókonként (LinkedIn / X publisher)
-- ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS oauth_tokens (
    account_id      TEXT PRIMARY KEY,           -- 'david', 'adam', 'plansmart'
    platform        TEXT NOT NULL DEFAULT 'linkedin',
    author_urn      TEXT,                        -- urn:li:person:... vagy urn:li:organization:...
    access_token    TEXT NOT NULL,
    refresh_token   TEXT,
    scope           TEXT,
    expires_at      TIMESTAMPTZ,                 -- ~60 nap; get_token warn-ol < 7 napnál
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
-- FIGYELEM: érzékeny adat — RLS-sel csak a service_key férjen hozzá az oauth_tokens-hez.
