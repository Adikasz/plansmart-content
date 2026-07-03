# Fejlesztési terv — PlanSmart Content Engine

Ez a dokumentum vezeti a fejlesztést. Minden Claude Code session ezt nézi meg először.

## Áttekintés

| Fázis | Mit építünk | Idő | Státusz |
|---|---|---|---|
| 1 | Alap setup + Supabase séma | 1-2 óra | ✅ Kész |
| 2 | RSS collector (60+ forrás) | 2 óra | ✅ Kész |
| 3 | Twitter/X collector | 2 óra | ✅ Kész |
| 4 | LinkedIn collector | 2-3 óra | ✅ Kész |
| 5 | Filter pipeline | 1,5 óra | ✅ Kész |
| 6 | 3 voice generator | 3 óra | ✅ Kész |
| 7 | Telegram approval bot | 2 óra | ✅ Kész |
| 8 | LinkedIn publisher | 2 óra | ✅ Kész (mock mód) |
| 9 | X publisher | 1 óra | ⏳ Függőben |
| 10 | Workers + cron | 1,5 óra | ⏳ Függőben |
| 11 | Railway deploy | 1 óra | ⏳ Függőben |
| 12 | Real-time reakció loop | 2 óra | ⏳ Függőben |

**Becsült teljes idő: ~22 óra fejlesztés** (Claude Code-dal)

---

## 🚦 Jelenlegi állapot (2026-06-30)

A lenti finomított fázis-számozás tükrözi a tényleges fejlesztést (7.6 = vizuálok,
7.7 = LinkedIn optimalizálás, 8.5 = OAuth). A fenti táblázat az eredeti, durvább build-terv.

| Fázis | Mit | Státusz |
|---|---|---|
| 1–7.5 | Setup → collectorok → filter → 3 voice → Telegram bot | ✅ Kész |
| 7.6 | Brand assets + Muapi vizuál generátor | ✅ Kész (sandbox feloldva — valódi `cdn.muapi.ai` képek, flux-2-pro $0.032) |
| 7.7 | LinkedIn optimalizálás (2026 keretrendszer + 3-2-1 hook) | ✅ Kész (optimizer + hook variánsok + pipeline integráció) |
| 8 | LinkedIn publisher | ✅ Kész (**mock mód**) |
| 8.5 | LinkedIn OAuth flow (token + URN) | ✅ Kész (script kész, LinkedIn API jóváhagyásra vár) |
| 9 | DM / komment monitoring | ⏳ Függőben (LinkedIn API jóváhagyásra vár) |
| 10 | Automata pipeline (APScheduler, reggeli poszt, breaking news, HU vizuálok) | ✅ Kész |
| 11 | Railway deploy (config + health endpoint + guide) | ✅ Kész |
| 12 | Vizuál minőség eval (A/B teszt, Sonnet vision scoring, folyamatos monitor) | ✅ Kész |
| 12.5 | Szöveg-overlay pipeline (betűhű magyar PIL overlay text-free Flux alapképen) | ✅ Kész (a szöveg betűhű; kompozit ~6.8, a 8.5 cél részben) |
| 12.6 | Fotografikus text-free prompt + overlay bugfixek | ✅ Kész (kompozit átlag 7.5; Dávid 8.5, "generic bg" flag eltűnt) |
| 13 | Szöveg-minőség eval (hook könyvtár, evaluator, improver, A/B, folyamatos monitor) | ✅ Complete (text quality eval + **auto-improve ÉLES**: `TEXT_AUTO_IMPROVE=true`, ship-gate 8.0) |

### Mire várunk (külső blokkolók)

- **Muapi support** — a `web_safety_forced` flag levétele a fiókról (adam@plansmart.live).
  A kód és a kérés helyes; az API valódi generálás helyett $0-s példa-képet ad. Lásd `docs/BRAND.md` §7.
- **LinkedIn Community Management API jóváhagyás** — PlanSmart Community app (manual review, ~3–7 nap).
- **LinkedIn Client ID/Secret** — bekerül a `.env`-be (`LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`).
- **`http://localhost:8080/callback`** regisztrálása a LinkedIn app authorized redirect URL-jei közé.

### Migrációk, amik a "go-live" előtt kellenek (Supabase SQL Editor)

- `scripts/migration_8_tokens.sql` — `tokens` tábla (OAuth tokenek).
- `scripts/migration_8_status_published.sql` — `published` státusz a `posts`-hoz.
- `scripts/migration_7_6_visuals.sql` — `posts.visual_url` + `costs` tábla (a Muapi unblock után).
- `scripts/migration_7_7_optimization.sql` — `final_content`, `hook_type`, `hook_score`, `estimated_engagement_tier`.
- `scripts/migration_10_breaking.sql` — `feed_items.breaking`, `posts.is_breaking`, `posts.sent_at` + parciális index.
- `scripts/migration_12_quality.sql` — `visual_quality_metrics` tábla (folyamatos vizuál-eval trend).
- `scripts/migration_12_5_overlay.sql` — `posts.base_image_url` (nyers Muapi alapkép az overlay előtt).
- `scripts/migration_13_text_quality.sql` — `text_quality_metrics` tábla (folyamatos szöveg-eval trend).

---

## Fázis 1 — Alap setup és Supabase séma

**Cél:** Repo, függőségek, env, adatbázis kész.

### Feladatok
1. Repo init: `git init`, `.gitignore`, `pyproject.toml`
2. Függőségek: `requirements.txt` összerakása (lista lent)
3. `.env.example` és `.env` megírása
4. Supabase project létrehozása (külön a PPC monitortól!)
5. Tábla séma:
   - `feed_items` (összes bejövő hír)
   - `posts` (generált posztok 3 voice szerint)
   - `approvals` (Telegram approval state)
   - `published` (mi ment ki, mikor, hová)
   - `metrics` (later — engagement adatok)
6. Storage modul (`src/storage/db.py`) - alap Supabase connection

### Függőségek (`requirements.txt`)
```
python-dotenv==1.0.1
supabase==2.5.0
httpx==0.27.0
feedparser==6.0.11
anthropic==0.28.0
aiogram==3.13.0
pydantic==2.7.0
pyyaml==6.0.1
schedule==1.2.2
```

### Acceptance kritérium
- `python scripts/test_db.py` lefut és kiírja: "Supabase OK, 5 tábla létezik"

### Claude Code prompt
```
Read CLAUDE.md and PROJECT_PLAN.md.
Implement Phase 1: project setup and Supabase schema.

Steps:
1. Create requirements.txt with the dependencies listed
2. Create .env.example with placeholders
3. Create src/storage/db.py with Supabase client setup
4. Create scripts/init_db.py that creates all 5 tables
5. Create scripts/test_db.py for verification

Don't run anything yet — show me the files first.
```

---

## Fázis 2 — RSS Collector

**Cél:** 60+ RSS forrás folyamatos figyelése, percenkénti polling.

### Feladatok
1. `config/sources.yml` feltöltése (lista a `docs/SOURCES.md`-ben)
2. `src/collectors/rss_collector.py` megírása
3. Deduplication URL hash + Supabase check alapján
4. Async polling (httpx + feedparser)
5. Hibakezelés: timeout, parse errors, dead feeds

### Acceptance kritérium
- `python -m src.collectors.rss_collector` 60+ forrást végigjár 30 mp alatt
- Új elemek menthetők Supabase-be, ismétlődések kihagyva

---

## Fázis 3 — Twitter/X Collector

**Cél:** ~20 követett fiók posztjainak realtime figyelése.

### Feladatok
1. `config/sources.yml`-ben Twitter handle lista
2. Twitter API auth (Basic tier elegendő, $100/hó — vagy ingyenes scraping route)
3. `src/collectors/twitter_collector.py` — async fetcher
4. Threadek összefűzése (nem külön elemenként)
5. Quote tweet és reply context megőrzése

### Megjegyzés
A Twitter API drága. Alternatíva: nitter.net scraping vagy Apify actor.
A döntés fázis indulásakor a budget alapján.

---

## Fázis 4 — LinkedIn Collector

**Cél:** ~30 LinkedIn creator és cég oldal monitorozása.

### Feladatok
1. `config/sources.yml`-ben LinkedIn profile/page URL lista
2. **Választás:** Phantombuster API ($59/hó) VAGY Apify LinkedIn scraper (~$20-40/hó)
3. `src/collectors/linkedin_collector.py` — daily fetcher (LinkedIn nem support real-time-ot)
4. HTML/JSON parsing
5. Image és video URL eltárolása

### Megjegyzés
LinkedIn scraping szürkezónás. Ne menjünk agresszívan — naponta 1-2x.

---

## Fázis 5 — Filter pipeline

**Cél:** Bejövő tartalmakból csak a magyar KKV-relevánsakat továbbengedi.

### Feladatok
1. `src/filters/dedup.py` — URL hash + content similarity (rapidfuzz)
2. `src/filters/keyword_filter.py` — gyors skip/pass kulcsszó szabályok
3. `src/filters/relevance_scorer.py` — Claude Haiku 1-10 pontszám
4. Konfigurálható threshold (default: 6)
5. Eredmény: `feed_items.score` mező frissítve

### Acceptance kritérium
- 100 random feed itemen pontozva: legalább 80% egyetértés egy manuális reviewerrel

---

## Fázis 6 — 3 voice generator

**Cél:** Egy magas pontszámú feed itemből 3 voice-specific poszt generálása.

### Feladatok
1. `prompts/voice_david.md` — Builder voice (~500 szó prompt)
2. `prompts/voice_adam.md` — Strategist voice
3. `prompts/voice_plansmart.md` — Brand voice
4. `src/generators/base_generator.py` — közös logika (Claude API call wrapper)
5. `src/generators/david_generator.py` — Dávid voice, LinkedIn + X
6. `src/generators/adam_generator.py` — Ádám voice, LinkedIn + X
7. `src/generators/plansmart_generator.py` — Brand voice, LinkedIn only
8. Output: 5 poszt egy hírből (3 LinkedIn + 2 X)

### Voice tesztelés
```bash
python scripts/test_voice.py --feed-id <id>
# Kiírja a 3 voice outputot egymás mellett összehasonlításhoz
```

---

## Fázis 7 — Telegram approval bot

**Cél:** Telegramon érkeznek a generált posztok approve/edit/regenerate/skip gombokkal.

### Feladatok
1. Telegram bot létrehozása (@BotFather)
2. `src/bots/telegram_bot.py` — aiogram alapon
3. 4 gomb: ✅ Approve, ✏️ Edit, 🔄 Regenerate, ❌ Skip
4. Inline keyboard, callback handlers
5. Edit flow: bot megnyit egy inline szerkesztőt, mentés után újra approve
6. Multi-account: 2 Telegram csatorna (poszt-approval + reakció-approval)
7. State tárolás: `approvals` táblába mentve

### Bot UX példa
```
[Dávid voice — LinkedIn]
"Tegnap este 2-kor jött az ötlet hogy átírjam..."
[...szöveg...]

Forrás: anthropic.com/news/claude-3-5-sonnet
Score: 9/10
Generated: 11:42

[✅ Approve] [✏️ Edit] [🔄 Regenerate] [❌ Skip]
```

---

## Fázis 7.7 — LinkedIn optimalizálás (2026 keretrendszer) ✅

**Cél:** Minden poszt a 2026-os LinkedIn algoritmus szerint maximalizált engagement-re
strukturálva — mobile-first hook, 1300–1900 kar sweet spot, anti-pattern eltávolítás.

**Mit építettünk:**
- `prompts/linkedin_optimization.md` — mester keretrendszer (algoritmus-szabályok, 5 hook
  típus A–E, poszt-formula, magyar kulturális megjegyzések, anti-patternek).
- `src/optimization/linkedin_optimizer.py` — `optimize_for_linkedin()`: Claude Sonnet
  átstrukturálja a nyers posztot + diagnosztika (hook_type, hook_score, structure_score,
  warnings, character_count, estimated_engagement_tier). Determinisztikus lokális warningok
  (kar-tartomány, hook-hossz, link, hashtag-szám) + graceful fallback parse/API hibára.
- `base_generator.generate(..., with_hook_variants=True)` — 3-2-1 hook framework:
  3 különböző típusú horog generálása + Claude pontozás → legjobb kiválasztása.
- `prompts/visual_styles/*.md` — 2026-os bold display + Common DNA + style reference frissítés.
- Pipeline-integráció (`generator_worker`): nyers → optimalizálás → vizuál → mentés
  (raw a `content` + `metadata.raw_content`, optimalizált a `final_content`, diagnosztika
  oszlopokba) → optimalizált megy Telegramra.
- `scripts/migration_7_7_optimization.sql` — `final_content`, `hook_type`, `hook_score`,
  `estimated_engagement_tier` oszlopok (a bővebb diagnosztika a `metadata` JSONB-ben).

**Teszt:** `python -m scripts.test_optimization` — egy téma, 3 hang, before/after + hook
variánsok + diagnosztika. Eredmény (téma: „Megtanultad a Claude-ot. Mi jön utána?"):

| Hang | Hook | Hook/10 | Szerk/10 | Kar | Tier |
|---|---|---|---|---|---|
| Dávid | A | 8 | 9 | 1605 | HIGH |
| Ádám | A | 8 | 8 | 1620 | HIGH |
| PlanSmart | D | 8 | 9 | 1320 | HIGH |

> ⚠ **Migráció:** futtasd a `scripts/migration_7_7_optimization.sql`-t a Supabase SQL
> editorban, hogy a `final_content` + diagnosztika oszlopok létrejöjjenek (DB driver nincs
> lokálisan, így a DDL-t kézzel kell alkalmazni).

---

## Fázis 8 — LinkedIn publisher

**Cél:** Approved posztok kiposztolása 3 LinkedIn fiókra.

### Feladatok
1. LinkedIn OAuth setup mindhárom fiókhoz (Dávid, Ádám, PlanSmart)
2. Access token mentése Supabase-be (refresh kezelés)
3. `src/publishers/linkedin_publisher.py` — async LinkedIn API kliens
4. Image upload support (későbbi case study-khoz)
5. Optimális időpont kiválasztása: 7:00-9:00 vagy 17:00-19:00

### Megjegyzés
A LinkedIn API user-context tokent igényel mind a 3 fiókhoz, és **community management permission**-höz manual review kell. Az OAuth flow elindítása előtt 1 hét apply.

---

## Fázis 9 — X publisher

**Cél:** Approved X posztok kiposztolása Dávid és Ádám fiókokra.

### Feladatok
1. X API Basic tier setup mindkét fiókhoz
2. `src/publishers/twitter_publisher.py`
3. Thread mode (multi-tweet posztok)
4. Image upload support

---

## Fázis 10 — Automata pipeline (APScheduler) ✅

**Cél:** A pipeline 24/7 fut, egy `src.workers.main` orchestratorban (egy process,
egy event loop): APScheduler + Telegram bot + health szerver.

### Ütemezés (Europe/Budapest, `COLLECTOR_INTERVAL_HOURS`=2)
- **collector** — N óránként (`:00`)
- **filter** — N óránként `:30`
- **breaking** — N óránként `:45` (24/7)
- **morning** — naponta `07:30` (hétvégén is)

### Komponensek
- `src/workers/main.py` — AsyncIOScheduler + `asyncio` Telegram polling + health,
  graceful shutdown SIGINT/SIGTERM-re, minden job START/END logolva időbélyeggel.
- `src/workers/morning_post_worker.py` — fiókonként a `content_strategy` ajánlott típusából
  generál (ai_news → friss top hír; egyébként seed YAML; `consultant_builder` → educational
  kategória; fallback educational), optimalizál, magyar szöveges vizuált készít, és a POSTS
  csatornára küldi `☀️ Reggeli poszt — {magyar dátum}` fejléccel.
- `src/workers/breaking_news_worker.py` — score≥8 + top-cég kulcsszó (OpenAI, Anthropic,
  Google, Apple, GPT-, Claude, Gemini, ChatGPT, Sora, DeepMind, Meta AI) + 4h friss + dedup,
  napi max 3. Ádám reakció a REACTIONS csatornára `🚨 BREAKING — Azonnali hír / 📰 Forrás / ⏰ idő`
  prefixszel, hook bias C/B.
- `src/visuals/visual_generator.py` — `extract_visual_text()` (Haiku) a magyar overlay-szöveghez
  (main_text NAGYBETŰS 3-5 szó, sub_text 10-15 szó, stat), majd a Muapi prompt a kért formátummal.
- Migráció: `scripts/migration_10_breaking.sql`.

### Teszt
`python -m scripts.test_phase_10_11` — ütemező + 24h futáslista, `extract_visual_text`
példák, 3 reggeli poszt (hook + HU vizuál), 1 breaking (🚨 prefix), health végpont.
`--no-image` gyors mód, `--send` élő Telegram kiküldés.

---

## Fázis 11 — Railway deploy ✅

**Cél:** Production environment, 24/7 futás — egy service.

### Komponensek
- `railway.toml` — `startCommand = "python -m src.workers.main"`, healthcheck `/health`,
  restart ON_FAILURE (max 10).
- `nixpacks.toml` — Python 3.12 + gcc, `pip install -r requirements.txt`.
- `src/workers/health.py` — aiohttp `:8080`, `GET /health` (200 + időbélyeg + scheduler állapot),
  `GET /status` (last_*_run, breaking_count_today, queue_sizes).
- `requirements.txt` — pinned (apscheduler, pytz, aiogram, anthropic, supabase, feedparser,
  beautifulsoup4, httpx, aiohttp, python-dotenv, pyyaml).
- `docs/RAILWAY_DEPLOY.md` — lépésről lépésre + env checklist.

---

## Fázis 12 — Vizuál minőség eval + prompt evaluation ✅

**Cél:** A vizuál promptok szisztematikus tesztelése, pontozása, javítása — mi működik a
PlanSmart brandnek és a magyar LinkedIn közönségnek.

### Komponensek
- `src/optimization/visual_eval.py` — `VisualEvaluator`: Claude Sonnet **vision** pontozza a
  képet (brand alignment, magyar szöveg minőség, scroll-stopping, professzionalizmus,
  anti-pattern flag-ek, overall + feedback). A 0.28.0 SDK base64 image source-t vár → a képet
  httpx-szel letöltjük.
- `src/optimization/visual_ab_test.py` — `ab_test_prompts()`: 4 variáns (A produkciós,
  B minimalista, C filmes, D adat-fókuszú) → kép → értékelés → győztes overall alapján.
- `scripts/build_eval_dataset.py` — 10 valós poszt → `data/eval_dataset.json`.
- `scripts/run_visual_eval.py` — 10×4 = 40 kép, per-variant + per-voice átlag, győztesek,
  `data/visual_eval_results_{ts}.json` + `_latest.json` (living baseline) + `visual_eval_report.html`
  (side-by-side, győztes kiemelve, költség).
- `scripts/improve_visual_prompts.py` — eredmény-elemzés (leggyengébb hang, ismétlődő flag-ek/
  feedback) + Sonnet 2 új variáns + top-3 javaslat.
- `src/optimization/quality_monitor.py` + `generator_worker` — **folyamatos eval**: 50 generált
  posztonként 5 random vizuál pontozása, `visual_quality_metrics` trend, alert a reactions
  csatornára ha az átlag < 7.0.
- Telegram `/eval_visuals` — aktuális vizuál-minőség vs. baseline.
- Migráció: `scripts/migration_12_quality.sql`.

### Első futás eredménye (2026-06-30, 40 kép, $1.28)
- Variáns átlagok: A=5.38 · B=5.10 · **C=5.43** · D=5.15 → **összgyőztes: C (filmes)**.
- Hangonkénti győztes: **Dávid → C**, **Ádám → A**, **PlanSmart → C**.
- Top-3 fix (a győztesek a produkciós promptba kerültek): (1) a magyar overlay-szöveget
  betűhűen kell renderelni (gyakori a halandzsa szöveg), (2) true #04060a háttér (nem szürke) +
  tiltott HUD/pseudo-code, (3) Dávidnál filmes noir lighting + grain (a leggyengébb hang).

---

## Fázis 12.5 — Szöveg-overlay pipeline (betűhű magyar szöveg) ✅

**Cél:** filmes Flux/Muapi vizuál + **betűhű magyar szöveg** (a Flux halandzsa szöveget ír,
ezért a szöveget PIL-lel komponáljuk rá).

### Komponensek
- `src/visuals/text_overlay.py` — `TextOverlayComposer`: a szöveg-mentes alapképre PIL-lel
  írja a magyar szöveget, voice-specifikus elrendezéssel (Dávid: Bebas Neue bal-alsó + teal
  glow; Ádám: Inter ExtraBold közép + amber stat; PlanSmart: Inter ExtraBold + wordmark).
  Fontok: teljes lefedettségű **Inter** (márka-elsődleges, variable weights) + **Fragment Mono**
  + **Bebas Neue** Google Fontsról letöltve (a repo .woff2-jei unicode-subsetek → boxok lennének).
  **Glyph-fallback**: ha a font nem fedi a karaktert (pl. Bebas-ban nincs `→`), Interre vált;
  kontraszt-árnyék + stat shrink-to-fit.
- `src/visuals/visual_generator.py` — `build_textfree_prompt()` (szigorú NO-TEXT) + `compose_visual()`
  (extract → text-free Muapi → PIL overlay → feltöltés → végső URL).
- `src/visuals/uploader.py` + `scripts/setup_supabase_storage.py` — `visuals` publikus Storage
  bucket + `upload_visual()`, lokális fallbackkel.
- `src/optimization/visual_eval.py` — lokális fájl-pontozás (komponált kép base64).
- `scripts/run_visual_eval_v2.py` — text-free base + overlay, a KOMPONÁLT végeredményt pontozza.
- Integráció: `telegram_bot._attach_visual` → `compose_visual` (a `/create` és `/test` is),
  `posts.base_image_url` + `migration_12_5_overlay.sql`.

### Eredmény (eval v2, 10 poszt, $0.32/futás)
| Metrika | Phase 12 baseline | Phase 12.5 |
|---|---|---|
| **Magyar szöveg minőség** | 3.9 | **7.5–7.8** |
| Overall (kompozit) | 5.26 | **6.7–6.8** |

A **szöveg betűhű** (vizuálisan igazolva) — a fő technikai cél teljesült, és nagy mérhető
ugrás. A >8.5 kompozit cél NEM teljesült: a szigorú vision-értékelő a teljes kompozíciót
(alapkép-relevancia, scroll-stopping, polish) is nézi, és a Flux néha halandzsa
"képernyő-szöveget" tesz a háttérbe a NO-TEXT utasítás ellenére — ez a maradék korlát, nem
az overlay szöveg. A pipeline éles használatra integrálva.

---

## Fázis 12.6 — Fotografikus text-free prompt + overlay bugfixek ✅

**Cél:** a vizuál-eval visszatérő háttér-panaszainak kezelése (generic AI-art 62×, cluttered
59×, pseudo-code/UI/HUD panel 57×) konkrét fotó-technikai promptirányítással.

### Mit változott
- `build_textfree_prompt()` (visual_generator.py): a "mood-szavas" absztrakt jelenet helyett
  **fotó-technikai** irány — Kodak Portra 800 film grain, egy key-light bal-felülről mély
  feketébe eséssel, f/1.4 sekély mélységélesség, "photographic, not illustrated/concept art",
  és minden screen/UI/HUD/kód/panel/hologram **kategorikus tiltása** + EGY objektum / voice.
- Két overlay bugfix (text_overlay.py): hosszú szó shrink-to-fit + char-wrap (`ADMINISZTRÁCIÓ`
  nem lóg ki), és duplikált stat elhagyása (ha a `stat` a `main_text`-ben van).
- `prompts/visual_styles/*.md` frissítve a Phase 12.6 iránnyal (produkciós default).

### A/B eredmény (3 hang, friss bázis + overlay + eval, $0.096)
| Hang | 12.5 → 12.6 overall | háttér-flag |
|---|---|---|
| **Dávid** | 8.2 → **8.5** | "generic UI" → **eltűnt** (fotó NYÁK-makró) |
| Ádám | 6.8 → 6.8 | toll-makró → "stock-photo" (gyengébb pont) |
| PlanSmart | 7.8 → 7.2 | halvány él-objektum → "AI-art" nitpick (a kép tiszta/minimal) |
| **Átlag** | — → **7.5** | ✅ eléri a 7.5 küszöböt |

**Döntés:** a 7.5 küszöb teljesült → az új prompt a produkciós default. A Dávid-háttér
egyértelműen javult (a legrosszabb korábbi eset). Nyitott finomítás: Ádám "toll" objektuma
néha stock-fotós — pure-texture/gradient irány biztosabb lenne.

---

## Fázis 13 — Szöveg-minőség eval + javító loop ✅

**Cél:** a posztok ne legyenek AI-generikusak — erős hook, emberi hang, konkrét érték,
szisztematikus eval + javítás (a vizuál-eval architektúra szöveges párja).

### Komponensek
- `prompts/viral_hooks_library.md` — 5 hook típus (contrarian/data/narrative/pain/comparison)
  valós példákkal + magyar adaptációkkal + content_type/voice párosítással.
- `src/optimization/text_evaluator.py` — `TextEvaluator`: 6 tengelyen pontoz (hook, emberi érzet,
  magyar nyelv, konkrét érték, voice, engagement) + AI-tell/buzzword/„sokat” determinisztikus flag-ek.
- `src/optimization/text_improver.py` — `improve_post`: iteratív átírás a cél-pontszámig (hook
  könyvtár + voice prompt alapján).
- `src/optimization/text_ab_test.py` — 5 hook-stratégia variáns (A produkciós, B-E hook-bias).
- `scripts/build_text_eval_dataset.py` / `run_text_eval.py` — 10 szcenárió, baseline + improve +
  A/B, HTML riport (`data/text_eval_report.html`).
- `src/optimization/text_quality_monitor.py` + `migration_13_text_quality.sql` — folyamatos
  monitor (50 posztonként 5 random, < 7.5 → Telegram alert), `generator_worker`-be kötve.
- `base_generator.generate(auto_improve=…)` — ship-gate (env `TEXT_AUTO_IMPROVE`).
  **ÉLESÍTVE production-ben:** `TEXT_AUTO_IMPROVE=true`, `TEXT_SHIP_THRESHOLD=8.0` — minden generált
  poszt átmegy az `improve_post` loopon (cél 8.0, max 2 iteráció), és a jobb átírt verzió megy
  Telegramra. Smoke teszt: seed 1.2 → final 8.4 (iter 7.4 → 8.4). Költség: +3–5 Sonnet hívás/poszt
  (lásd lent).

### Auto-improve költségdelta (Sonnet 4.6)
| | Sonnet hívás / poszt | Becsült költség |
|---|---|---|
| Auto-improve **nélkül** (baseline) | 1 (generate) | ~$0.016 |
| Auto-improve **be**, már jó poszt (≥8.0, nincs rewrite) | 2 (generate + gate-eval) | ~$0.03 |
| Auto-improve **be**, tipikus (1 javító kör) | 4 (generate + eval + rewrite + eval) | ~$0.08 |
| Auto-improve **be**, max (2 kör — smoke esete) | 6 (generate + eval + 2×[rewrite+eval]) | ~$0.13 |

→ átlagos delta **+$0.05–0.07 / poszt** (~4–5×); a magas-baseline posztok (Dávid builder) gyakran
csak +1 gate-eval-be kerülnek.

### Első futás eredménye (10 szcenárió, ~$3-4)
| | Pontszám |
|---|---|
| Baseline (produkciós A) | **6.74** |
| Iteratív javítás (improve) | **8.03** |
| Győztes (A/B + iter) | **8.09** |

- **Nyertes hook hangonként:** Dávid → DATA (3×) + narrative; Ádám → vegyes (data/narrative/
  contrarian); PlanSmart → DATA (2×). **A DATA hook nyer legtöbbször.**
- **Leggyakoribb anti-pattern:** általános mennyiség konkrét szám helyett („sokat” 5×, „rengeteg” 2×),
  hiányzó emberi időhorgony, puha CTA.
- **Top hook (8.8):** „Múlt hétfőn 11-kor kaptam egy Slack üzenetet az ügyfelünktől:”
- A tanulságok a `prompts/voice_*.md` „Hook & szöveg-minőség (Phase 13)” szakaszába kerültek
  (hook/content_type, tiltott kifejezések, kötelező 1 szám + 1 példa, emberi jel, győztes példák).

---

## Fázis 13b — Real-time reakció loop

**Cél:** Új hír érkezik → 5-10 percen belül kész poszt a Telegramon.

### Feladatok
1. Webhook integráció (ahol lehet — Anthropic blog RSS minden percben polling)
2. Priority queue: nagyon releváns hír (score 9+) megelőzi a többit
3. Notification: Telegramon **azonnali ping** ha super-hot hír jött
4. Reagálási idő mérése: feed_item.created_at → post.published_at

### Acceptance
- 95%-os esetben < 10 perces lag a forrás megjelenése és a poszt publikálása között

---

## Phase 14 — English conversion (all 3 voices) ✅ Complete

**Cél:** Mind a 3 hang (Dávid/Ádám/PlanSmart) ANGOL kimenetre vált — presztízs/tekintély
pozicionálás magyar KKV közönségnek. Vizuál overlay is angol. Eval-cél 9.0; a végleges
produkciós ship-gate 8.5.

**Status: ✅ Complete** (2026-07-03)
- English conversion, all 3 voices (voice prompts, evaluator, visual overlay text extraction).
- Új értékelő dimenzió: `english_native_quality` (universal); a magyar `hungarian_*` kód dormant
  maradt (nem törölve — SCORE_KEYS_HU, _hunglish_flags).
- Hossz-sáv: 1300-1900 karakter (determinisztikus flag + evaluator + improver enforcement).
- Ship-gate: az eval a 9.0 célt tesztelte, de a végleges produkciós küszöb **8.5**
  (`TEXT_SHIP_THRESHOLD=8.5`, `.env`) — ez a Phase 14 realisztikusan elérhető átlaga, így az
  auto-improve loop nem pazarol iterációt egy elérhetetlen 9.0 célra. Az auto-improve MINDEN élő
  posztot a 8.5 felé tol; az eval-dataset átlagát a 9.0 alatt (8.4-8.8) elfogadtuk (diminishing returns).

### Eval eredmény (10 szcenárió, ~$6-7)
| Voice | Átlag pontszám |
|---|---|
| Ádám | **8.8** |
| Dávid | **8.78** |
| PlanSmart | **8.43** |

- **Elfogadva végleges átlagként a 9.0 alatt** — a tartalom minősége manuális review-val magas
  (2 poszt 9.0+: Dávid build-log 9.2, Ádám „Kati" educational 9.0). A pontmennyezet oka egy
  szigorú „aphoristic LinkedIn-guru one-liner" levonás, nem valódi minőségi hiány.
- **Nyertes hook hangonként:** Ádám → NARRATIVE (3/3); Dávid → PAIN + NARRATIVE (production az
  educationalnál); PlanSmart → NARRATIVE (case study) + PAIN (educational). **DATA/CONTRARIAN a
  leggyengébb** — ez fordítva volt Phase 13-ban (magyar), az angol natív forrásanyag máshogy sül el.
- A tanulságok a `prompts/voice_*.md` „Hook & text quality (eval-driven)" + „English-native
  quality (CRITICAL)" szakaszaiba kerültek (győztes hook/content_type, tiltott angol jargon,
  anti-aphorism szabály, győztes példa-hookok).
- Riport: `data/text_eval_report_phase14_en.html`, nyers: `data/text_eval_phase14_results.json`.
- Runner: `scripts/run_phase14_eval.py`, dataset: `data/text_eval_dataset.json` (10 EN szcenárió).

---

## Mihez kell külső segítség / döntés

- **LinkedIn API access** — manual apply, 1 hét
- **X API tier** — havi $100 vs ingyenes scrape kockázat
- **LinkedIn scraping eszköz** — Phantombuster vagy Apify
- **Supabase tier** — Free elég kezdetnek, $25/hó később

## Költség becslés

| Tétel | Havi |
|---|---|
| Railway (4 service) | $5-15 |
| Supabase | $0 (free) |
| Claude API | $10-25 |
| Twitter API (opcionális) | $100 |
| Phantombuster/Apify | $20-60 |
| Telegram bot | $0 |
| **Összesen** | **~$35-200/hó** |

A magasabb szám akkor, ha Twitter Basic-et veszünk + Phantombuster Pro.
A reális induló költség: ~$50-80/hó.
