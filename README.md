# PlanSmart Content Engine

**An autonomous, human-in-the-loop content & outreach agent for B2B LinkedIn growth — engineered like production infrastructure, not a script.**

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Pydantic" src="https://img.shields.io/badge/Pydantic-2.7-E92063?logo=pydantic&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-276%20passing-brightgreen">
  <img alt="Network in tests" src="https://img.shields.io/badge/test%20network%20calls-0-brightgreen">
  <img alt="Deploy" src="https://img.shields.io/badge/deploy-Railway%20(single%20service)-0B0D0E?logo=railway&logoColor=white">
  <img alt="LLM" src="https://img.shields.io/badge/LLM-Claude%20Sonnet%20%2B%20Haiku-D97757?logo=anthropic&logoColor=white">
</p>

The engine watches what the largest AI companies and top creators publish, and — within **5–10 minutes** — drafts on-brand LinkedIn posts across **three distinct voices**, generates a matching branded visual, and routes every draft to a human for one-tap approval in Telegram before anything is published. It also researches and drafts personalized connection notes for B2B outreach.

Nothing goes out without a human tap. **The AI does the 90% that is mechanical; the founder keeps the 10% that is judgment.**

---

## The Hybrid Advantage — Business Intelligence × AI Engineering

Most "AI content tools" are a prompt in a loop. This is a system where **go-to-market strategy and software engineering are first-class citizens of the same codebase**:

| Business Intelligence | ⇄ | AI Engineering |
|---|:---:|---|
| Three codified brand voices (Builder / Strategist / Brand) | ⇄ | Three isolated voice prompts — never cross-contaminated |
| Content-mix strategy (educational / case-study / workshop / news ratios) | ⇄ | Distribution engine picks the largest-gap content type per account |
| ROI-aware, decision-maker messaging | ⇄ | Relevance scoring (Claude Haiku, 0–10) gates what is worth a post |
| Anti-slop guarantee: no invented client metrics | ⇄ | Hard **fabrication gate** in the evaluator/improver pipeline |
| Human owns the brand's reputation | ⇄ | Telegram approval bot — mandatory human-in-the-loop |

The result is an agent a **CTO can put in production** and a **CMO can trust with the brand**.

---

## Business Case & ROI

### The problem it removes

Running a credible B2B LinkedIn presence across **three brand voices** — plus personalized outreach — is a real weekly job: monitoring the space, drafting in a consistent voice, producing visuals, scheduling, and researching prospects. It is high-effort, easy to drop, and expensive to hire out.

### Illustrative ROI model

> ⚠️ **Illustrative, not measured.** The figures below are a transparent estimation model with stated assumptions so you can plug in your own numbers — they are **not** a claim of guaranteed results. (Consistent with this repo's strict no-fabrication policy.)

**Assumptions:** ~10 posts/week across 3 accounts (per `config/accounts.yml`) + ongoing prospect research. Manual production ≈ 45–60 min per quality, on-voice post *with a custom visual*; prospect research + a personalized note ≈ 15–20 min each.

| Workstream | Manual (per week) | With the engine | Reclaimed |
|---|---:|---:|---:|
| Monitoring the AI space | ~2–3 h | automated | ~2–3 h |
| Drafting 3-voice content | ~7–9 h | review & approve | ~6–8 h |
| Branded visuals | ~2 h | automated | ~2 h |
| Outreach research + notes | ~3–5 h | drafted for approval | ~2–4 h |
| **Total** | **~14–19 h/week** | **~1.5–2 h/week** (approvals) | **~12–17 h/week** |

At a blended founder/marketer cost of **$50–100/h**, ~12–17 reclaimed hours/week is **$2.5k–$7k/month of capacity** returned to higher-leverage work.

### Running cost (grounded in `docs/SETUP.md`, LinkedIn-only)

| Line item | Monthly |
|---|---:|
| Railway — **single** monolith service | $5–15 |
| Supabase (Postgres, source of truth) | $0 (free tier) |
| Claude API — Sonnet (generation) + Haiku (filtering) | $10–25 |
| Muapi images — `flux-2-pro` @ **$0.032/image** | ~$5–15 |
| **Total (LinkedIn-only)** | **≈ $20–70 / month** |

**The math:** a few hundred dollars of infra per quarter against thousands of dollars/month of reclaimed senior capacity.

---

## The Three Voices

The engine runs **three voice prompts that never mix** — each a separate file in `prompts/`, versioned in git because a prompt change changes the output.

| Voice | Persona | Writes | Never writes | Channel |
|---|---|---|---|---|
| **Dávid** — *Builder* | The engineer | Technical build-logs, "how we shipped it", concrete specifics | Marketing buzzwords | LinkedIn (+ X planned) |
| **Ádám** — *Strategist* | The operator | Business insight, ROI, owner-to-owner takes | Code, jargon | LinkedIn (+ X planned) |
| **PlanSmart** — *Brand* | The company ("we") | Case studies, service showcases, workshop promos | Personal opinion; **skips all news by design** | LinkedIn |

---

## How It Works — the pipeline

```
   ┌──────────────┐   every N hours (APScheduler cron, one event loop)
   │  COLLECTORS  │   RSS feeds  +  Next.js (__NEXT_DATA__) scraper
   └──────┬───────┘   → writes new items to Supabase
          ▼
   ┌──────────────┐   dedup (URL hash)  →  keyword pre-filter (skip/boost)
   │   FILTERS    │   →  Claude Haiku relevance score (0–10)
   └──────┬───────┘   → only high-signal items pass the threshold
          ▼
   ┌──────────────┐   3 voice-specific generators (Claude Sonnet)
   │  GENERATORS  │   → strict JSON out → GeneratedPost schema validation
   └──────┬───────┘   → 3-2-1 hook framework, fabrication gate, ship-gate
          ▼
   ┌──────────────┐   Muapi flux-2-pro base image  →  PIL brand overlay
   │   VISUALS    │   → 4 layout templates × per-voice mood, no-repeat rotation
   └──────┬───────┘   → uploaded to Supabase Storage
          ▼
   ┌──────────────┐   ✅ Approve   ✏️ Edit   🔄 Regenerate   ❌ Skip
   │  TELEGRAM    │   → mandatory human-in-the-loop (aiogram)
   └──────┬───────┘
          ▼
   ┌──────────────┐   LinkedIn UGC Posts API  (X/Twitter on the roadmap)
   │  PUBLISHERS  │   → post URN persisted; status → published
   └──────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │  BREAKING (24/7)  high-score + fresh + keyword → Ádám reacts  │
   │  MORNING (daily)  strategy-driven post for each of the 3      │
   │  OUTREACH         prospect research → personalized note → 👤  │
   └──────────────────────────────────────────────────────────────┘
```

Everything above runs in **one process, one event loop** (`python -m src.workers.main`): APScheduler cron jobs + the Telegram approval bot + an aiohttp health server.

---

## Enterprise Architecture

### Four engineering pillars

**1 · Domain-Driven layout** — 11 bounded contexts under `src/`, each a single responsibility. No "god modules"; the dependency hub is the storage layer, and every cross-domain call is an explicit `from src.<domain> import …`.

**2 · Typed Pydantic `Settings` layer** — every environment variable is read once through a validated, cached `Settings` object. No scattered `os.environ.get`, no untyped config, no import-time crash on a missing secret (secrets are `Optional` and fail at the point of use, matching the lazy client pattern).

```python
from src.config.settings import get_settings

settings = get_settings()          # cached, validated, typed
settings.collector_interval_hours  # int, clamped ≥ 1
settings.text_ship_threshold       # float
settings.timezone                  # str (TIMEZONE → SCHEDULER_TZ → default)
```

**3 · Boundary Schemas for strict LLM-output validation** — the two riskiest boundaries in any AI system are **the model's JSON** and **third-party API responses**. Both are typed:

```
generators/schemas.py   GeneratedPost   ← validates every LLM draft (skip/linkedin/hooks/quality)
filters/schemas.py      RelevanceScore  ← Haiku score constrained to 0–10
publishers/schemas.py   LinkedInUGCResponse
visuals/schemas.py      MuapiResult
```

Malformed model output is caught at the boundary and logged — it never propagates as a downstream `KeyError` or a silently-wrong post.

**4 · 276 zero-network tests** — a fast, CI-safe suite that touches **no** network, DB, LLM, or paid image API. Every external client is built lazily inside `@lru_cache`, so pure logic tests import cleanly with fixtures only.

```
$ python -m pytest -q
276 passed in ~4s          # 0 network calls · 0 API spend · fully deterministic
```

> The `scripts/test_*.py` files are **live smoke scripts** (real APIs, real spend) — deliberately kept separate from the offline `tests/` suite.

### Repository structure

```
plansmart-content/
├── src/
│   ├── config/          # ⭐ typed Settings (Pydantic) + cached YAML loaders
│   ├── utils/           # ⭐ ids · json_repair (LLM-JSON state machine) · logging
│   ├── collectors/      # RSS + Next.js scraper
│   ├── filters/         # dedup → keyword → Haiku relevance   (+ schemas.py)
│   ├── generators/      # 3 voices + shared base              (+ schemas.py ⭐)
│   ├── optimization/    # evaluate · improve · A/B · ship-gate · fabrication gate
│   ├── visuals/         # Muapi client · PIL overlay · layout templates · variety
│   ├── publishers/      # LinkedIn UGC API + OAuth token store (+ schemas.py)
│   ├── outreach/        # prospect research + personalized notes
│   ├── strategy/        # content-mix distribution engine
│   ├── storage/         # Supabase (source of truth) + models
│   ├── bots/            # Telegram approval UI (aiogram)
│   └── workers/         # main.py orchestrator · cron job bodies · health server
├── prompts/             # 3 voice prompts + scoring/optimization guides (git-versioned)
├── config/              # sources.yml · accounts.yml · scoring.yml · content_strategy.yml
├── tests/               # ⭐ 276 zero-network pytest tests + conftest fixtures
└── docs/                # PROJECT_PLAN · SETUP · VOICES · DECISIONS (ADRs)

⭐ = introduced/expanded in the production-hardening refactor
```

### Tech stack

| Layer | Technology |
|---|---|
| Language / runtime | Python 3.12, `asyncio` |
| Config & validation | **Pydantic 2.7** (`Settings` + boundary schemas) |
| LLM | **Claude** — Sonnet (generation), Haiku (relevance), Sonnet-vision (visual eval) |
| Data | **Supabase** (Postgres, single source of truth) |
| Async I/O | `httpx`, `aiohttp` |
| Approval UI | **Telegram** via `aiogram` |
| Scheduling | APScheduler (cron in one event loop) |
| Visuals | Muapi `flux-2-pro`, Pillow (overlay), rembg/ONNX (portrait cutout) |
| Ingestion | `feedparser`, BeautifulSoup |
| Testing / quality | `pytest`, `pytest-asyncio`, `ruff` |
| Deploy | **Railway** (RAILPACK, single service, auto-deploy on `main`) |

---

## Quickstart

```bash
# 1) Clone
git clone https://github.com/Adikasz/plansmart-content
cd plansmart-content

# 2) Python 3.12 virtualenv  (3.12 is required — pinned deps)
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3) Configure — copy the template and fill it in
cp .env.example .env         # credentials live ONLY in .env (git-ignored)

# 4) Run the offline test suite (no network, no API spend)
python -m pytest             # → 276 passed

# 5) Inspect the schedule without starting anything
python -m src.workers.main --print-schedule

# 6) Run the full orchestrator locally (scheduler + bot + health)
DRY_RUN=true python -m src.workers.main
```

Every module is independently runnable for isolated testing, e.g. `python -m src.collectors.rss_collector`. Full walkthrough: [`docs/SETUP.md`](docs/SETUP.md).

## Configuration

Secrets are **never** committed — `config/*.yml` store only the *names* of environment variables (e.g. `access_token_env: LINKEDIN_DAVID_TOKEN`); the values live in a git-ignored `.env` and are read through the typed `Settings` layer. See [`.env.example`](.env.example) for the full key list.

## Deployment

Railway runs a **single monolith service** started by `railway.toml`:

```toml
[deploy]
startCommand = "python -m src.workers.main"
```

One process, one event loop: APScheduler cron (collector / filter / breaking / morning) + Telegram approval bot + aiohttp health server on `/health` and `/status`. Push to `main` → Railway auto-redeploys. See [`docs/RAILWAY_DEPLOY.md`](docs/RAILWAY_DEPLOY.md).

## Quality bar

- **`ruff`-clean** (`E, F, I, N, W, B`) across the codebase.
- **276 zero-network tests**, deterministic, no API spend.
- **Behavior-preserving refactors** verified against the live config before every commit.
- **Fabrication gate** — posts may never invent unsourced specifics; valid sources are `case_studies.yml`, a manual `/create`, or a real feed item.

## Status & roadmap

| Capability | Status |
|---|:---:|
| RSS + Next.js collection, dedup, Haiku relevance filtering | ✅ Live |
| 3-voice generation + hook framework + ship-gate | ✅ Live |
| Branded visuals (Muapi + PIL, 4 templates, no-repeat rotation) | ✅ Live |
| Telegram approval flow | ✅ Live |
| LinkedIn publishing (UGC API) | ✅ Live |
| Breaking-news reactor + daily strategy posts | ✅ Live |
| LinkedIn outreach (research + human-approved notes) | ✅ Live |
| Typed config · boundary schemas · zero-network test suite | ✅ Live |
| X / Twitter publishing | 🗺️ Planned |

---

<sub>Architecture notes and decision records live in [`CLAUDE.md`](CLAUDE.md) and [`docs/DECISIONS.md`](docs/DECISIONS.md). Built by the PlanSmart team — where the builder and the strategist are the same shop.</sub>
