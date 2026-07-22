"""Metrics összesítő (Phase 21 Part 1) — TARTALOM + KAPCSOLATÉPÍTÉS + KÖLTSÉG egy csomagban
a /metrics és /metrics_today Telegram parancsokhoz.

Tiszta adat-aggregáció, séma-módosítás nélkül épít a meglévő posts / prospects / costs /
prospect_interactions táblákra (lásd az egyes storage modulokat). A formázás (Telegram
szöveggé alakítás) a bot rétegben él (src/bots/metrics_bot.py) -- ez a modul csak dict-eket ad
vissza, hogy önállóan is tesztelhető/hívható legyen (pl. python -m src.storage.metrics).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.storage import posts as posts_store
from src.storage import prospect_interactions as interactions_store
from src.storage import prospects as prospects_store

logger = logging.getLogger(__name__)

VOICES = ("david", "adam", "plansmart")


def today_start_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def week_start_iso(now: datetime | None = None) -> str:
    """Hét eleje (hétfő 00:00 UTC) -- ugyanaz a konvenció, mint telegram_bot._week_start_iso."""
    now = now or datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def collect(since_iso: str, client=None) -> dict[str, Any]:
    """Egy időszak (since_iso óta, UTC ISO) teljes metrikacsomagja: content + outreach + cost."""
    by_voice = posts_store.generated_counts_by_voice_since(since_iso, client=client)
    actions = posts_store.approval_action_counts_since(since_iso, client=client)

    content = {
        "generated_total": sum(by_voice.values()),
        "by_voice": {v: by_voice.get(v, 0) for v in VOICES},
        "approved": actions.get("approve", 0),
        "rejected": actions.get("skip", 0),
        "edited": actions.get("edited", 0),
        "published": posts_store.published_count_since(since_iso, client=client),
        "breaking": posts_store.breaking_count_since(since_iso, client=client),
    }

    interactions_ready = False
    replied = None
    try:
        interactions_ready = interactions_store.table_ready(client=client)
        if interactions_ready:
            replied = interactions_store.replied_count_since(since_iso, client=client)
    except Exception as exc:  # noqa: BLE001 — a /metrics többi része nélküle is menjen
        logger.warning("[metrics] prospect_interactions lekérdezés hiba: %s", str(exc)[:120])

    outreach = {
        "researched": prospects_store.added_count_since(since_iso, client=client),
        "approved_to_send": prospects_store.approved_to_send_count_since(since_iso, client=client),
        "sent": prospects_store.sent_count_since(since_iso, client=client),
        "replied": replied,
        "interactions_ready": interactions_ready,
    }

    costs = posts_store.cost_summary_since(since_iso, client=client)

    return {"since": since_iso, "content": content, "outreach": outreach, "costs": costs}


def collect_today(client=None) -> dict[str, Any]:
    return collect(today_start_iso(), client=client)


def collect_week(client=None) -> dict[str, Any]:
    return collect(week_start_iso(), client=client)


if __name__ == "__main__":
    import json

    print(json.dumps({"today": collect_today(), "week": collect_week()}, indent=2, ensure_ascii=False))
