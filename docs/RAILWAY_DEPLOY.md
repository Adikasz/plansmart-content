# Railway deploy — PlanSmart Content Engine

A teljes rendszer **egy Railway service**-ként fut: a `src.core.workers.main` orchestrator
egyetlen processben futtatja az APScheduler ütemezőt (collector / filter / breaking /
morning), a Telegram approval botot és a health szervert (`:8080`).

A `railway.toml` és a `nixpacks.toml` már a repóban van — a build NIXPACKS-szel megy,
Python 3.12 + gcc, `pip install -r requirements.txt`.

---

## Előfeltételek

- Railway account + `railway` CLI: `npm i -g @railway/cli`
- A Supabase migrációk lefuttatva (lásd lentebb)
- Egy Telegram bot token + a 2 csatorna chat ID-ja (posts + reactions)

## Lépésről lépésre

```bash
# 1. Belépés
railway login

# 2. Projekt inicializálás (a repó gyökeréből)
railway init

# 3. Környezeti változók (lásd a checklistet lentebb) — dashboardon vagy CLI-vel:
railway variables set ANTHROPIC_API_KEY=sk-ant-...
railway variables set SUPABASE_URL=https://xxx.supabase.co
# ... (a teljes lista lentebb)

# 4. GitHub repo összekötése auto-deployhoz (ajánlott):
#    Railway dashboard → Project → Settings → Connect Repo → válaszd a main branchet.
#    Ettől kezdve a main-re pusholt commit automatikusan deploy-ol.

# 5. Deploy (manuális, CLI-ből):
railway up

# 6. Logok élőben:
railway logs

# 7. Health check (a Railway által adott domainen):
curl https://<your-app>.railway.app/health
curl https://<your-app>.railway.app/status
```

A `/health` egy `{"status":"ok", ...}` JSON-t ad 200-zal; a Railway healthcheck ezt
figyeli (`healthcheckPath = "/health"`). A `/status` mutatja az utolsó futásokat,
a napi breaking számot és a queue méreteket.

---

## Kötelező environment változók (checklist)

| Változó | Példa / megjegyzés |
|---|---|
| `SUPABASE_URL` | `https://xxx.supabase.co` |
| `SUPABASE_KEY` | anon kulcs (olvasás) |
| `SUPABASE_SERVICE_KEY` | service_role kulcs (írás — a workerek ezt használják) |
| `ANTHROPIC_API_KEY` | `sk-ant-...` (voice gen + scoring + optimalizálás + vizuál szöveg) |
| `MUAPI_API_KEY` | Muapi képgeneráláshoz (`x-api-key`) |
| `TELEGRAM_BOT_TOKEN` | a BotFather tokenje |
| `TELEGRAM_POSTS_CHAT_ID` | reggeli posztok + approval csatorna |
| `TELEGRAM_REACTIONS_CHAT_ID` | breaking news csatorna |
| `LINKEDIN_MOCK` | `true` — amíg nincs LinkedIn API jóváhagyás |
| `TIMEZONE` | `Europe/Budapest` |

### Ajánlott / opcionális

| Változó | Alap | Mit állít |
|---|---|---|
| `MORNING_POST_TIME` | `07:30` | reggeli poszt időpontja |
| `BREAKING_NEWS_ENABLED` | `true` | breaking detektor be/ki |
| `MAX_BREAKING_PER_DAY` | `3` | napi breaking limit |
| `COLLECTOR_INTERVAL_HOURS` | `2` | collector/filter/breaking óraköz |
| `PORT` | `8080` | health szerver portja (Railway adja meg) |
| `LOG_LEVEL` | `INFO` | logszint |

> A `PORT`-ot a Railway általában automatikusan beállítja — a `health.py` ezt használja,
> fallback a `8080`.

---

## Supabase migrációk (deploy ELŐTT futtasd a SQL editorban)

Sorrendben, idempotensek (`IF NOT EXISTS`):

1. `scripts/schema.sql` — alap táblák (ha még nem futott)
2. `scripts/migration_8_tokens.sql` — LinkedIn token tábla
3. `scripts/migration_8_status_published.sql` — `published` státusz
4. `scripts/migration_7_6_visuals.sql` — `visual_url`, `workshop_mentioned`, `costs` tábla
5. `scripts/migration_7_7_optimization.sql` — `final_content`, `hook_type`, `hook_score`, `estimated_engagement_tier`
6. `scripts/migration_10_breaking.sql` — `feed_items.breaking`, `posts.is_breaking`, `posts.sent_at` + parciális index

---

## Mit csinál élesben

- **collector** (N óránként): RSS + creator feedek begyűjtése
- **filter** (+30 perc): Haiku pontozás → `filtered` / `skipped`
- **breaking** (+45 perc, 24/7): score≥8 + top-cég kulcsszó + 4h friss → Ádám reakció
  a reactions csatornára, napi max 3
- **morning** (07:30): a 3 fiók reggeli posztja a POSTS csatornára, magyar szöveges vizuállal

A posztok **approval-re várnak** Telegramon (mock LinkedIn — `LINKEDIN_MOCK=true`).
LinkedIn API jóváhagyás után: `linkedin_oauth.py` a 3 tokenhez → `LINKEDIN_MOCK=false`.
