# Claude Code Útmutató — PlanSmart Content Engine

Ezt olvasd el a session elején. Ez a fejlesztési "bibliátok".

---

## Mi ez a projekt?

Valós idejű content generator 3 LinkedIn fiókra + 2 X fiókra.
A rendszer figyeli a legnagyobb AI cégek és kontentgyártók híreit,
és 5-10 perces lag-gel generál releváns posztokat a 3 különböző hangon.

## Mit NEM építünk?
- Email kampány rendszert (külön projekt — Instantly kezeli)
- Lead scoringot (külön projekt — Tally + Supabase)
- Instagram automation (kezdetben manuálisan kezeljük)

---

## A 3 hang — kritikus megérteni

A rendszer **3 különböző voice promptot** használ, amik soha nem keverednek.

### Dávid hangja — BUILDER VOICE
- **Mit ír:** technikai mélységű buildlog-ok, "így csináltuk" sztorik, kódrészletek
- **Hogyan beszél:** közvetlen, technikai, konkrét, sarkító ha kell
- **Példa nyitás:** "Tegnap este 2-kor jött az ötlet hogy átírjam a queue logikát..."
- **Soha nem ír:** marketing buzzword-öket, "AI forradalom"-szerűeket
- **Prompt fájl:** `prompts/voice_david.md`

### Ádám hangja — STRATEGIST VOICE
- **Mit ír:** üzleti perspektívájú insightok, ROI számítások, döntéshozói gondolatok
- **Hogyan beszél:** üzleti, érvelő, tulaj-tulajnak, soha nem fentről lefelé
- **Példa nyitás:** "Beszéltem egy 40 fős cég vezetőjével múlt héten. Egy mondat marad meg:"
- **Soha nem ír:** technikai részleteket, kódot, jargonokot
- **Prompt fájl:** `prompts/voice_adam.md`

### PlanSmart hangja — BRAND VOICE
- **Mit ír:** hivatalos esettanulmányok, szolgáltatás bemutatások, workshop hirdetések
- **Hogyan beszél:** "mi" forma, eredmény-orientált, számszerűsített
- **Példa nyitás:** "A múlt hónapban egy ügyfelünk..."
- **Soha nem ír:** személyes vélemény, építői részletek
- **Prompt fájl:** `prompts/voice_plansmart.md`

---

## Architektúra alapelvek

1. **Minden modul egy feladatot csinál.** Ha egy fájl 200 sornál hosszabb, bontsd.
2. **Self-contained modulok.** Minden `src/<modul>/<modul>.py` futtatható önállóan tesztelésre.
3. **A Supabase az egy igazság forrása.** SQLite csak lokális dev cache.
4. **Async by default.** I/O műveletek httpx-szel, Telegram aiogram-mal.
5. **3 voice = 3 prompt fájl.** Nem hardcodeolunk személyiséget kódba.

---

## Mappastruktúra magyarázat

```
plansmart-content/
├── CLAUDE.md                  # Ezt olvasod most
├── README.md                  # Projekt overview
├── requirements.txt
├── pyproject.toml
├── railway.toml               # Railway deploy config
├── .env.example
│
├── config/
│   ├── sources.yml            # MIT figyelünk (RSS, X handle, LinkedIn URL)
│   ├── accounts.yml           # 3 fiók credentials placeholder
│   └── scoring.yml            # Relevancia scoring szabályok
│
├── prompts/                   # Claude promptok markdown-ban
│   ├── voice_david.md
│   ├── voice_adam.md
│   ├── voice_plansmart.md
│   ├── filter_scoring.md
│   └── topic_extraction.md
│
├── src/
│   ├── collectors/            # Forrás-gyűjtők (egy fájl = egy forrástípus)
│   │   ├── rss_collector.py
│   │   ├── twitter_collector.py
│   │   ├── linkedin_collector.py
│   │   └── reddit_collector.py
│   │
│   ├── filters/               # Szűrés, dedup, scoring
│   │   ├── dedup.py
│   │   ├── keyword_filter.py
│   │   └── relevance_scorer.py
│   │
│   ├── generators/            # 3 voice-specific generátor
│   │   ├── base_generator.py  # Közös logika
│   │   ├── david_generator.py
│   │   ├── adam_generator.py
│   │   └── plansmart_generator.py
│   │
│   ├── publishers/            # API kliensek a posztoláshoz
│   │   ├── linkedin_publisher.py
│   │   └── twitter_publisher.py
│   │
│   ├── storage/               # Supabase + SQLite réteg
│   │   ├── db.py
│   │   ├── feed_items.py
│   │   ├── posts.py
│   │   └── metrics.py
│   │
│   ├── bots/
│   │   └── telegram_bot.py    # Approval UI
│   │
│   └── workers/               # Folyamatosan futó workerek
│       ├── collector_worker.py
│       ├── filter_worker.py
│       ├── generator_worker.py
│       └── publisher_worker.py
│
├── scripts/                   # Egyszeri / debug scriptek
│   ├── test_rss.py
│   ├── test_voice.py
│   ├── reset_db.py
│   └── seed_sources.py
│
├── tests/                     # pytest tesztek
│
├── data/                      # Lokális SQLite (gitignore-olva)
│
└── docs/
    ├── PROJECT_PLAN.md        # Fejlesztési fázisok
    ├── SETUP.md               # Beüzemelés
    ├── VOICES.md              # 3 hang részletes leírása
    └── DECISIONS.md           # Architecture Decision Records
```

---

## Fejlesztési alapszabályok

- **Új fájl előtt** mindig kérdezd meg magadtól: "fér ez egy meglévő fájlba?"
- **Új függőség** csak akkor, ha tényleg kell. Mindig `requirements.txt`-be, sosem `pip install` globálisan.
- **Tesztelés:** minden `src/<modul>/` mappához van `tests/test_<modul>.py`
- **Hard-coded értékek tilosak.** Minden config: `.env` vagy `config/*.yml`
- **Print helyett logging** (`logging` modul)
- **Async funkciók** mindig `async def`, sosem keverjük sync-szel

## Tesztelési stratégia

```bash
# Egy modul önálló futtatása
python -m src.collectors.rss_collector

# Unit tesztek
pytest tests/

# Integration test (élesben):
python scripts/test_full_pipeline.py --dry-run

# Voice teszt — egy hír, 3 hang
python scripts/test_voice.py --feed-id <id>
```

## Config, utils és tesztek (production hardening)

Moduláris config- és segéd-réteg + valódi offline teszt-suite:

- **`src/config/`** — tipizált konfiguráció:
  - `settings.py` — `Settings` (Pydantic) az összes env változóhoz, `get_settings()` cache-elve.
    Az import-idejű config-olvasás MÁR EZEN megy át az egész kódbázisban (workers, collectors,
    generators, bots, visuals stb.) — új kód is INNEN olvasson. KIVÉTEL (szándékos): a call-time
    titok/kapcsoló olvasások (`LINKEDIN_MOCK`, `TELEGRAM_BOT_TOKEN`, `MUAPI_API_KEY`,
    `ANTHROPIC_API_KEY`, `SUPABASE_*`) közvetlen `os.environ`-ból jönnek — ezeket NE fagyaszd
    cache-elt Settings-be. A titkok Optional-ök: az import sosem bukik hiányzó kulcson (a lazy
    `@lru_cache` kliensekhez illeszkedve).
  - `loaders.py` — cache-elt YAML betöltők (`load_yaml`, `load_scoring`, `load_content_strategy`, …);
    a szétszórt `yaml.safe_load` EGY helyen (a `keyword_filter` és `content_strategy` már ezt hívja).
- **`src/utils/`** — kereszt-metsző segédek, projekt-belső (src.*) függőség NÉLKÜL:
  - `ids.py` (`make_id`, `utcnow_iso`), `json_repair.py` (LLM-JSON repair állapotgép), `logging.py` (`setup_logging`).
  - A történeti helyeken (`storage.models`, `generators.base_generator`) **re-export marad** — a régi
    `from src.storage.models import make_id` / `from src.generators.base_generator import _repair_and_parse`
    importok érintetlenek. Ne a régi helyre írj új logikát — a kanonikus otthon a `utils/`.
- **`schemas.py` a domainekben** — a külső határok Pydantic validációja:
  `generators/schemas.py` (`GeneratedPost` — az LLM kimenet, a legkockázatosabb határ),
  `filters/schemas.py` (`RelevanceScore` 0–10), `publishers/schemas.py`, `visuals/schemas.py`.
- **`tests/`** — valódi **zero-network** pytest suite (`.venv/Scripts/python -m pytest`): SEMMILYEN
  hálózat / Supabase / Anthropic / Muapi / Telegram. A `conftest.py` adja a fixtúrákat (FeedItem gyár,
  fake Anthropic üzenet, fluent fake Supabase, dummy env). ⚠️ A `scripts/test_*.py` továbbra is
  **manuális, ÉLES smoke script** (valódi API-t + pénzt hív) — az NEM CI-teszt.

## Deployment

Railway-en **egy monolit service** fut (`plansmart-content`), amit a
`railway.toml` `startCommand`-je indít:

```
python -m src.workers.main
```

Ez egyetlen process, egy event loopban futtat mindent (lásd
`src/workers/main.py`):
- **APScheduler** cron jobok: collector / filter / breaking / morning
- **Telegram approval bot** (aiogram)
- **health szerver** (aiohttp, `:$PORT`, `/health`, `/status`)

Nincs külön `web` / `worker-*` service — a régi „4 service" felállás
sosem épült meg így. Push `main`-re → a Railway automatikusan
újradeploy-olja ezt az egy service-t (RAILPACK builder, `restartPolicyType
= ON_FAILURE`, max 10 retry).

## Amit NE csinálj

- Ne keverd a 3 hangot egy generator fájlban
- Ne tegyél API kulcsot Git-be
- Ne hardcode-olj sources-okat — `config/sources.yml` van rá
- Ne futtass `pip install`-t globálisan
- Ne módosítsd a `prompts/` fájlokat csendben — minden voice prompt változás Git history-ba menjen, mert eredményt befolyásol

## Hol találod a választ ha elakadsz

1. `docs/PROJECT_PLAN.md` — milyen fázisban tartunk
2. `docs/DECISIONS.md` — miért így döntöttünk
3. `docs/VOICES.md` — voice prompt részletek
4. GitHub Issues — nyitott problémák, tickets
