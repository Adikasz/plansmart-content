# Setup útmutató

Lépésről lépésre — első futtatástól a Railway deployig.

## Előfeltételek

- Python 3.12+
- Node.js (Telegram bot tesztelésére)
- Git
- VSC + Claude Code extension
- Railway account
- Supabase account

## 1. lépés — Repo clone

```bash
git clone https://github.com/Adikasz/plansmart-content
cd plansmart-content
```

## 2. lépés — Python venv

```bash
python -m venv .venv
source .venv/bin/activate     # Linux/Mac
# vagy: .venv\Scripts\activate  (Windows)

pip install -r requirements.txt
```

## 3. lépés — Supabase setup

1. Új Supabase project (ingyenes tier elég kezdetnek)
2. Régió: Frankfurt vagy közeli európai
3. Projektnév: `plansmart-content`
4. Mentsd el a URL-t és anon/service key-eket
5. Futtasd a séma SQL-t:
   - Supabase Dashboard → SQL Editor
   - Töltsd be: `scripts/schema.sql`
   - Run

## 4. lépés — Telegram bot setup

1. Telegramon: `@BotFather` → `/newbot`
2. Bot neve: `PlanSmart Content Bot`
3. Mentsd el a tokent
4. Hozz létre 2 csoportot:
   - "PlanSmart Posts" (poszt jóváhagyásokhoz)
   - "PlanSmart Reactions" (sürgős reakciók)
5. Add hozzá a botot mindkét csoporthoz mint adminisztrátor
6. Kérdezd le a chat ID-ket:
   ```bash
   python scripts/get_telegram_chat_ids.py
   ```

## 5. lépés — LinkedIn API setup (3 fiók)

LinkedIn API kétlépéses:

### a) Dávid és Ádám személyes fiókok
1. https://www.linkedin.com/developers/apps → Create app
2. App neve: "PlanSmart Personal Posting"
3. Products → kérvényezd: **Share on LinkedIn**, **Sign In with LinkedIn**
4. OAuth 2.0 → Authorized redirect URLs: `https://your-railway-url/auth/linkedin/callback`
5. Futtasd: `python scripts/linkedin_oauth.py --user david` és `--user adam`
6. Browser-ben elvégzed az OAuth flow-t
7. Token mentődik `.env`-be

### b) PlanSmart céges oldal
1. Hozz létre egy LinkedIn Company Page-et: PlanSmart
2. Az app-ban kérvényezd: **Marketing Developer Platform** (community management)
3. **Ez 1-2 hét manual review!** Kezdj itt korán!
4. Approval után: `python scripts/linkedin_oauth.py --user plansmart`

## 6. lépés — Twitter / X API setup

1. https://developer.x.com → Project létrehozás
2. Basic tier: $100/hó (1500 tweet/hó write limit elég nekünk)
3. App-on belül 2 sub-app: "PlanSmart David" és "PlanSmart Adam"
4. Mindkettőre Consumer Key + Secret + User Token + Secret
5. Mentsd `.env`-be

**Alternatíva** (ha drága): manual posting + scheduling Buffer-rel.

## 7. lépés — .env feltöltése

```bash
cp .env.example .env
```

Töltsd ki minden mezőt. Lásd `.env.example` kommenteket.

## 8. lépés — Lokális tesztelés

```bash
# Adatbázis kapcsolat tesztje
python scripts/test_db.py

# RSS collector tesztje (1 forrás)
python scripts/test_rss.py --source anthropic_blog

# 3 voice generálás tesztje
python scripts/test_voice.py --topic "Anthropic kihozott egy új modellt"

# Offline unit tesztek (zero-network, ~276 teszt — se hálózat, se API)
python -m pytest

# Az orchestrator lokálisan (ütemező + Telegram bot + health; Ctrl+C-ig fut)
DRY_RUN=true python -m src.workers.main
# Csak az ütemezés kiírása, majd kilép:
python -m src.workers.main --print-schedule
```

## 9. lépés — Telegram bot tesztelés

```bash
python -m src.bots.telegram_bot
```

Telegramon ellenőrizd hogy reagál a `/start`-ra.
Próbáld ki egy mock post-tal: `/test_post`.

## 10. lépés — Railway deploy

```bash
# Railway CLI
npm install -g @railway/cli
railway login

# Projekt linkelése
railway init
railway link

# Environment változók
railway variables set ANTHROPIC_API_KEY=sk-...
# ... és minden más változó .env-ből

# Deploy
railway up
```

A `railway.toml` **egyetlen monolit service**-t indít
(`startCommand = python -m src.workers.main`): APScheduler cron jobok
(collector / filter / breaking / morning) + Telegram approval bot + health
szerver — mind egy process-ben, egy event loopban. **Nincs külön `web` /
`worker-*` service** (a régi „4 service" felállás sosem épült meg). Push
`main`-re → a Railway automatikusan újradeployolja ezt az egy service-t.

## 11. lépés — Élesítés

```bash
# Először DRY_RUN mód
railway variables set DRY_RUN=true

# 24 órán át figyeld a Telegramon, hogy jó dolgokat generál
# Ha minden jól néz ki:
railway variables set DRY_RUN=false
```

## Hibakeresés

### "Supabase connection refused"
Ellenőrizd a SUPABASE_URL-t és service key-t. A service key NEM az anon key!

### "Telegram bot doesn't respond"
- Bot admin a csoportokban?
- Webhook beállítva? `python scripts/setup_telegram_webhook.py`

### "LinkedIn API 401"
Token expired. Refresh: `python scripts/linkedin_refresh.py --user <name>`

### "X API 429 rate limit"
Basic tier 1500 write / hónap. Ellenőrizd a posts táblát hogy nem ír túl.

## Költségek (havi)

| Tétel | Költség |
|---|---|
| Railway (1 monolit service) | $5-15 |
| Supabase | $0 (free tier) |
| Claude API | $10-25 |
| Twitter API Basic | $100 |
| Phantombuster / Apify | $20-60 |
| Telegram | $0 |
| **Összesen** | **~$135-200/hó** |

**Ha Twitter-t kihagyjuk és csak LinkedIn**: ~$35-75/hó.

## Mikor érdemes Twitter-t hozzáadni?

Akkor amikor:
- LinkedIn-en már stabil engagement (heti 50+ DM, kommentek)
- Van bandwidth-ünk X-en is válaszolni
- Mérhetjük hogy X-en mekkora elérést kapunk

Addig: LinkedIn 3 fiók + email + Instagram. X később.
