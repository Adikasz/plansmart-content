# PlanSmart Content Engine

Valós idejű content generator és scheduler 3 LinkedIn fiókra + X-re.
Figyeli a legnagyobb AI cégeket és kontentgyártókat, és 5-10 percen belül
generál releváns posztokat és reakciókat a 3 különböző hangon.

## Fiókok

| Fiók | Hang | Heti poszt | Fókusz |
|---|---|---|---|
| Dávid (LinkedIn + X) | Builder voice | 4 | Mit építünk, hogyan |
| Ádám (LinkedIn + X) | Strategist voice | 4 | Miért éri meg, ROI |
| PlanSmart (LinkedIn) | Hivatalos | 2-3 | Case study, workshop |

## Architektúra (high-level)

```
┌─────────────────────────────────────────────────────────────┐
│  REAL-TIME COLLECTORS (folyamatos, percenként)              │
│  ├── RSS feeds (60+ forrás)                                 │
│  ├── Twitter/X API (követett fiókok)                        │
│  ├── LinkedIn scraper (top creators)                        │
│  └── Reddit/HN API                                          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  FILTERS                                                    │
│  ├── Deduplication (URL hash + content similarity)          │
│  ├── Keyword prefilter (skip / pass)                        │
│  └── Claude Haiku relevancia scoring (1-10)                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  GENERATORS (3 voice-specific)                              │
│  ├── Builder voice (Dávid) → LinkedIn + X                   │
│  ├── Strategist voice (Ádám) → LinkedIn + X                 │
│  └── Brand voice (PlanSmart) → LinkedIn                     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  TELEGRAM BOT (approval UI)                                 │
│  ├── ✅ Approve  ✏️ Edit  🔄 Regenerate  ❌ Skip            │
│  └── Bot tracks state per post                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  PUBLISHERS                                                 │
│  ├── LinkedIn API (3 accounts)                              │
│  └── X API (2 accounts)                                     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  STORAGE (Supabase / SQLite)                                │
│  └── Feed items, posts, approvals, post history, metrics    │
└─────────────────────────────────────────────────────────────┘
```

## Stack

- **Python 3.12**
- **Supabase** (Postgres + Realtime) — már van a PPC monitorhoz
- **Railway.app** — deployment, cron jobs, services
- **Telegram Bot API** — approval flow
- **Claude API** (Sonnet generálásra, Haiku szűrésre)
- **GitHub** — Actions automated tests, Railway auto-deploy

## Setup

Lásd `docs/SETUP.md` a részletes beüzemelési útmutatóért.

## Quick start (Claude Code-dal)

```bash
# 1. Repo clone
git clone https://github.com/Adikasz/plansmart-content
cd plansmart-content

# 2. Claude Code indítása
claude

# 3. Első prompt:
> Read CLAUDE.md and PROJECT_PLAN.md.
> Initialize the project per Phase 1.
```

A teljes fejlesztési terv: `docs/PROJECT_PLAN.md`.
