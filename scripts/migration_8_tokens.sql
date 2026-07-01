-- ════════════════════════════════════════════════════
-- Phase 8 migráció — LinkedIn OAuth token tárolás
-- Futtatás: Supabase SQL Editor. Idempotens (IF NOT EXISTS).
-- ════════════════════════════════════════════════════
--
-- Egyszerű token tábla a publisher-hez. Az author_urn NEM ide kerül,
-- hanem a config/accounts.yml `linkedin_urn` mezőjébe.
--
-- FIGYELEM: érzékeny adat — RLS-sel csak a service_key férjen hozzá!

CREATE TABLE IF NOT EXISTS tokens (
    account_id      TEXT PRIMARY KEY,           -- 'david', 'adam', 'plansmart'
    access_token    TEXT NOT NULL,
    expires_at      TIMESTAMPTZ,                -- ~60 nap; get_token warn-ol < 7 napnál
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Lejáró tokenek gyors lekérdezése (refresh worker):
CREATE INDEX IF NOT EXISTS idx_tokens_expires ON tokens(expires_at);

-- RLS: csak a service_key olvashassa/írhassa (a tokenek titkosak).
ALTER TABLE tokens ENABLE ROW LEVEL SECURITY;
-- (Policy nélkül, RLS bekapcsolva: az anon kulcs nem fér hozzá; a service_key bypassol.)
