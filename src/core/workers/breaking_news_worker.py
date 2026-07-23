"""Breaking news detektor — nagy pontszámú, friss top-cég hírek azonnali reakciója.

A filter_worker után 2 óránként fut (lásd main.py). Kritériumok (MIND igaz):
  • score >= 8
  • a cím VAGY a tartalom tartalmaz legalább egyet: BREAKING_KEYWORDS
  • published_at (vagy fetched_at) az elmúlt 4 órán belül
  • még nincs hozzá poszt (dedup: max 1 breaking / feed_item)

Találat esetén: Ádám hangú poszt (ő a news reactor) → linkedin_optimizer → magyar szöveges
vizuál → Telegram REACTIONS csatorna "🚨 BREAKING — Azonnali hír" előtaggal. Napi max
MAX_BREAKING_PER_DAY (alapból 3), időkorlát nélkül (24/7).

Önálló futtatás (dry-run, nem küld/ír):
    python -m src.core.workers.breaking_news_worker
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytz
from dotenv import load_dotenv

from src.integrations.bots import telegram_bot as tb
from src.core.config.settings import get_settings
from src.core.storage import feed_items as feed_store
from src.core.storage import posts as posts_store
from src.core.storage.db import get_client, has_service_key
from src.utils.logging import setup_logging
from src.core.workers.generator_worker import GENERATORS, _optimize_post

logger = logging.getLogger(__name__)
load_dotenv(override=False)

BREAKING_KEYWORDS = [
    "OpenAI", "Anthropic", "Google", "Apple", "GPT", "Claude", "Gemini",
    "ChatGPT", "Sora", "DeepMind", "Meta AI", "Mistral", "Cohere", "Hugging Face",
]
BREAKING_VOICE = "adam"  # ő a news reactor
BREAKING_MIN_SCORE = 8
BREAKING_WINDOW_HOURS = 4
BREAKING_HOOK_BIAS = ["C", "B"]  # Data / Curiosity — ezek viszik a breaking newst

MAX_BREAKING_PER_DAY = get_settings().max_breaking_per_day
BREAKING_NEWS_ENABLED = get_settings().breaking_news_enabled
TZ_NAME = get_settings().timezone


def _tz():
    try:
        return pytz.timezone(TZ_NAME)
    except Exception:
        return pytz.UTC


def _day_start_iso() -> str:
    """A mai nap kezdete a konfigurált időzónában, UTC ISO-ként (napi limithez)."""
    now = datetime.now(_tz())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _has_keyword(row: dict) -> bool:
    blob = f"{row.get('title') or ''} {row.get('content') or ''}".lower()
    return any(kw.lower() in blob for kw in BREAKING_KEYWORDS)


def _breaking_prefix(row: dict) -> str:
    """A reactions csatorna üzenet-előtagja: forrás + (budapesti) idő."""
    source = row.get("source_name") or "ismeretlen forrás"
    now_local = datetime.now(_tz()).strftime("%H:%M")
    return f"🚨 BREAKING — Azonnali hír\n📰 Forrás: {source}\n⏰ {now_local}\n\n"


def _is_fresh(row: dict, now: datetime) -> bool:
    """published_at (vagy fallback fetched_at) az elmúlt BREAKING_WINDOW_HOURS órán belül."""
    dt = _parse_dt(row.get("published_at")) or _parse_dt(row.get("fetched_at"))
    if dt is None:
        return False
    return (now - dt) <= timedelta(hours=BREAKING_WINDOW_HOURS)


def _qualifies(row: dict, now: datetime) -> bool:
    return (
        (row.get("score") or 0) >= BREAKING_MIN_SCORE
        and _has_keyword(row)
        and _is_fresh(row, now)
    )


async def run_breaking_check(dry_run: bool = False, send: bool = True, item: dict | None = None) -> dict:
    """Egy breaking-ellenőrzési ciklus.

    dry_run=True: generál + optimalizál + vizuál-prompt, de NEM ír DB-t, NEM küld Telegramra.
    item: explicit feed_items sor (teszthez); ha None, a DB-ből keresi a jelölteket.
    """
    if not BREAKING_NEWS_ENABLED:
        logger.info("[breaking] kikapcsolva (BREAKING_NEWS_ENABLED=false).")
        return {"enabled": False, "sent": 0, "candidates": 0}

    client = get_client(use_service_key=has_service_key())
    now = datetime.now(timezone.utc)

    sent_today = 0 if dry_run else posts_store.breaking_count_since(_day_start_iso(), client=client)
    if sent_today >= MAX_BREAKING_PER_DAY:
        logger.info("[breaking] napi limit elérve (%d/%d) — kihagyva.", sent_today, MAX_BREAKING_PER_DAY)
        return {"enabled": True, "sent": 0, "limit_reached": True, "candidates": 0}

    if item is not None:
        candidates = [item]
    else:
        candidates = feed_store.get_breaking_candidates(min_score=BREAKING_MIN_SCORE, client=client)

    qualified = [r for r in candidates if _qualifies(r, now)]
    logger.info("[breaking] %d jelölt | %d megfelel a kritériumoknak", len(candidates), len(qualified))

    produced: list[dict] = []
    for row in qualified:
        if sent_today + len(produced) >= MAX_BREAKING_PER_DAY:
            logger.info("[breaking] napi limit elérve futás közben — leállás.")
            break
        # Dedup: max 1 breaking / feed_item (van-e már bármilyen poszt ehhez a hírhez).
        if not dry_run and posts_store.has_post_for_feed_item(row["id"], client=client):
            continue

        item_obj = feed_store.row_to_item(row)
        result = await GENERATORS[BREAKING_VOICE](item_obj)
        if not result:
            logger.info("[breaking] Ádám skip-elte: %s", (row.get("title") or "")[:60])
            continue

        post = tb._result_to_post(result, BREAKING_VOICE, "linkedin")
        post.update({
            "feed_item_id": item_obj.id, "score": item_obj.score,
            "feed_item_url": item_obj.url, "title": item_obj.title, "is_breaking": True,
        })
        if not (post.get("content") or "").strip():
            continue

        post = await _optimize_post(post, "ai_news", hook_bias=BREAKING_HOOK_BIAS)

        entry = {
            "feed_item_id": item_obj.id, "title": item_obj.title, "score": item_obj.score,
            "source": row.get("source_name"), "hook_type": post.get("hook_type"),
            "tier": post.get("estimated_engagement_tier"),
            "preview": post["content"][:140].replace("\n", " "),
        }

        if dry_run:
            # Vizuál csak prompt szinten (kép nélkül) a dry-run gyors maradjon — a teszt külön kéri.
            produced.append(entry)
            continue

        post["sent_at"] = posts_store._now()
        post["id"] = posts_store.insert_post(post, client=client)
        await tb._attach_visual(post)
        entry["visual_url"] = post.get("visual_url")
        if send:
            await tb.send_for_approval(post, tb.REACTIONS_CHAT_ID, header=_breaking_prefix(row))
            posts_store.mark_sent(post["id"], post["sent_at"], client=client)
        feed_store.mark_breaking(item_obj.id, client=client)
        entry["post_id"] = post["id"]
        produced.append(entry)

    summary = {
        "enabled": True, "candidates": len(candidates), "qualified": len(qualified),
        "sent": (0 if dry_run else len(produced)), "produced": produced, "dry_run": dry_run,
    }
    logger.info("[breaking]%s %d breaking poszt", " [DRY]" if dry_run else "", len(produced))
    return summary


def main() -> int:
    setup_logging()
    asyncio.run(run_breaking_check(dry_run=True, send=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
