# Architecture Decision Records (ADR)

Itt rögzítjük a fontos technikai döntéseket. Új döntésnél: új blokk a végére, dátummal.

---

## ADR-001 — Python, nem Node/TypeScript

**Dátum:** 2026-06-14
**Döntés:** Python 3.12 az egész stack-en.
**Miért:**
- Dávid Python-ban jártas (PPC monitor projektben már használja)
- `anthropic` SDK Python-ban a legjobban dokumentált
- `feedparser` + `aiohttp` ideális kombó RSS pollozáshoz
- Supabase Python kliens stabil
**Trade-off:** TypeScript talán jobb lenne Telegram bothoz, de a teljes stack-egységesség nyer.

---

## ADR-002 — Supabase + Postgres, nem MongoDB

**Dátum:** 2026-06-14
**Döntés:** Supabase (Postgres) az egész stack-en.
**Miért:**
- Már van Supabase account a PPC monitorhoz
- Strukturált relációkat akarunk (feed → posts → published)
- Realtime feature jó a Telegram bot updatekhez
- Ingyenes tier 500MB elég kezdetnek
**Trade-off:** Mongo gyorsabb lenne JSON-heavy adattal, de a kapcsolatok itt fontosabbak.

---

## ADR-003 — Make / n8n NÉLKÜL

**Dátum:** 2026-06-14
**Döntés:** Mindent Python kóddal oldunk meg, Make/n8n-t nem használunk.
**Miért:**
- Olcsóbb (no monthly platform fee)
- Verzionálható Git-ben
- Tesztelhető pytest-tel
- Operation limit nélkül (Make Pro: 10k ops/hó)
- Saját kontroll, debugolhatóság
**Trade-off:** Több fejlesztési idő upfront — de hosszú távon megéri.

---

## ADR-004 — Railway, nem AWS/GCP

**Dátum:** 2026-06-14
**Döntés:** Railway.app a deployment.
**Miért:**
- Push = deploy, nincs setup overhead
- 4 service ($5/db) olcsóbb mint AWS ECS
- Logging, monitoring beépített
- Cron job támogatás natív
- Már van Railway projekt a PPC monitorhoz
**Trade-off:** Railway nem skálázódik nagyon nagyra (millió felhasználó), de nekünk most ez sem cél.

---

## ADR-005 — 3 LinkedIn fiók 1 helyett

**Dátum:** 2026-06-14
**Döntés:** Dávid + Ádám + PlanSmart céges, 3 különböző voice.
**Miért:**
- Cégen belüli több hang nagyobb hitelességet ad
- Egy KKV-tulaj különböző "bizalmi pontokon" reagál
- 3× LinkedIn algoritmus elérés
- Több perspektíva ugyanarról a témáról
**Trade-off:** 3× fejlesztési komplexitás voice prompt-okra. Megéri.

---

## ADR-006 — Telegram approval flow, nem email

**Dátum:** 2026-06-14
**Döntés:** Telegram bot a poszt jóváhagyásra.
**Miért:**
- Gyorsabb mint email (push notification, gombok)
- Mobil-first (mindkét founder Telegramot használ)
- Inline buttons natívan támogatott
- Edit flow Telegram inline keyboard-dal egyszerű
- Csoport-channel megoldás: 2-en is jóváhagyhatnak
**Trade-off:** Telegram outage = approval blokk. De ritkán fordul elő.

---

## ADR-007 — Real-time polling (1 perc), nem webhook

**Dátum:** 2026-06-14
**Döntés:** RSS forrásokat 1-5 perces intervallumban polingoljuk.
**Miért:**
- A legtöbb forrás nem ad webhook-ot (RSS standard)
- Polling kód egyszerűbb és reliable
- 60 forrás × 1 perc = ~60 request/perc Railway-ről, ez OK
- Webhook setup minden forráshoz manuális lenne
**Trade-off:** 1 perc lag a forrás és a detection között. Elfogadható.

---

## ADR-008 — Claude Sonnet generáláshoz, Haiku szűréshez

**Dátum:** 2026-06-14
**Döntés:**
- Filter scoring: `claude-haiku-4-5-20251001` (olcsó, gyors)
- Voice generálás: `claude-sonnet-4-6` (minőség)
**Miért:**
- Haiku 80% olcsóbb és gyorsabb, scoring-hoz bőven elég
- Sonnet a generálásnál minőségi különbség
- Évi ~$10-25 a teljes API költség így
**Trade-off:** Opus jobb lenne generáláshoz, de 5× drágább. Sonnet a sweet spot.

---

## ADR-009 — Multi-account Twitter: 2 Basic-tier app

**Dátum:** 2026-06-14
**Döntés:** Dávid és Ádám fiókokhoz külön-külön Twitter Basic tier ($100/hó/db).
**Trade-off:**
- $200/hó drága lehet kezdetnek
- **Alternatíva 1:** Buffer-t használunk X-re a hivatalos API helyett (havi $0)
- **Alternatíva 2:** Twitter scraping (kockázatos, account ban risk)
- **Alternatíva 3:** Először csak Dávid X, Ádám később
**Megfontolásra:** Indulj 1 X fiókkal (Dávid), 3 hónap után ha van engagement, jöhet a második.

---

## ADR-010 — Twitter/X skipped initially

**Dátum:** 2026-06-15
**Döntés:** X automation skipped. LinkedIn 3 accounts only for now.
**Miért:** $100/month Twitter Basic API not justified before LinkedIn traction proven.
**Trade-off:** No X automation until LinkedIn shows engagement.
**Megjegyzés:** Felülírja az ADR-009-et (a 2 Basic-tier Twitter app tervet elhalasztjuk); a Fázis 3 (Twitter collector) kihagyva.

---

## Új döntésekhez sablon

```
## ADR-XXX — <Cím>

**Dátum:** YYYY-MM-DD
**Döntés:** <Mit döntöttünk>
**Miért:** <Indoklás>
**Trade-off:** <Mit veszítünk vele>
```
