"""Reggeli poszt worker — naponta 07:30 (Europe/Budapest), hétvégén is.

Fiókonként (david, adam, plansmart) a content_strategy ajánlott típusából generál egy
posztot, LinkedIn-optimalizál, magyar szöveges vizuált készít, és a POSTS csatornára küldi
jóváhagyásra "☀️ Reggeli poszt — {magyar dátum}" fejléccel.

Tartalom-forrás fiókonként:
  • ai_news          → az elmúlt 24h legjobb, még nem használt feed_item-je
  • educational / case_study / workshop_promo → nem-használt seed a megfelelő YAML-ből
  • consultant_builder → educational_topics.yml, category=consultant_builder
  • ha nincs jó tartalom (pl. nincs friss hír Ádámnak) → educational seed fallback

Önálló futtatás (dry-run, nem küld/ír):
    python -m src.core.workers.morning_post_worker
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytz
from dotenv import load_dotenv

from src.integrations.bots import telegram_bot as tb
from src.core.config.settings import get_settings
from src.ai.generators.base_generator import generate as generate_post
from src.core.storage import feed_items as feed_store
from src.core.storage import posts as posts_store
from src.core.storage.db import get_client, has_service_key
from src.core.strategy import content_strategy
from src.utils.logging import setup_logging
from src.core.workers.generator_worker import GENERATORS, VOICE_PROMPTS, _optimize_post

logger = logging.getLogger(__name__)
load_dotenv(override=False)

MORNING_ACCOUNTS = ["david", "adam", "plansmart"]
EDUCATIONAL_REUSE_DAYS = 30
TZ_NAME = get_settings().timezone

HU_MONTHS = [
    "január", "február", "március", "április", "május", "június",
    "július", "augusztus", "szeptember", "október", "november", "december",
]
HU_WEEKDAYS = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]


def _tz():
    try:
        return pytz.timezone(TZ_NAME)
    except Exception:
        return pytz.UTC


def hungarian_date(now: datetime | None = None) -> str:
    """Magyar dátum, pl. '2026. június 22., vasárnap' (locale-független)."""
    now = now or datetime.now(_tz())
    return f"{now.year}. {HU_MONTHS[now.month - 1]} {now.day}., {HU_WEEKDAYS[now.weekday()]}"


def _week_start_iso() -> str:
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _hours_ago_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed_for(account: str, ctype: str, client) -> dict | None:
    """Seed dict a megadott (nem-news) típushoz, a használt kulcsok kihagyásával."""
    since = _days_ago_iso(EDUCATIONAL_REUSE_DAYS) if ctype == "educational" else None
    used = posts_store.used_seed_keys(account, ctype, since, client=client)
    return content_strategy.get_seed(ctype, account, used)


def _build_attempts(account: str, ctype: str, top_item, client) -> list[tuple]:
    """Sorrendezett tartalom-jelöltek: (kind, payload, resolved_type, seed_key).

    Az első működő (nem-skip, nem üres) generálás nyer. Mindig van fallback:
    educational seed, majd a friss top hír.
    """
    attempts: list[tuple] = []

    if ctype == "ai_news":
        if top_item is not None:
            attempts.append(("news", top_item, "ai_news", top_item.id))
    elif ctype == "consultant_builder":
        used = posts_store.used_seed_keys(account, "educational", _days_ago_iso(EDUCATIONAL_REUSE_DAYS), client=client)
        seed = content_strategy.get_educational_seed_by_category("consultant_builder", account, used)
        if seed is not None:
            attempts.append(("seed", seed, "educational", seed["seed_key"]))
    else:  # educational | case_study | workshop_promo
        seed = _seed_for(account, ctype, client)
        if seed is not None:
            attempts.append(("seed", seed, ctype, seed["seed_key"]))

    # Univerzális fallback: educational seed, majd friss hír.
    edu = _seed_for(account, "educational", client)
    if edu is not None:
        attempts.append(("seed", edu, "educational", edu["seed_key"]))
    if top_item is not None:
        attempts.append(("news", top_item, "ai_news", top_item.id))

    # Dedup (kind, kulcs) megtartva a sorrendet.
    seen, unique = set(), []
    for a in attempts:
        key = (a[0], a[3])
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


async def _generate_from_attempts(account: str, attempts: list[tuple]):
    """Végigpróbálja a jelölteket; visszaadja (result, resolved_type, seed_key, feed_item|None)."""
    for kind, payload, resolved, seed_key in attempts:
        try:
            if kind == "news":
                result = await GENERATORS[account](payload)
                feed_item = payload
            else:
                result = await generate_post(payload, VOICE_PROMPTS[account])
                feed_item = None
        except Exception as exc:
            logger.warning("[morning] %s generálás hiba (%s): %s", account, resolved, str(exc)[:90])
            continue
        if result:
            return result, resolved, seed_key, feed_item
    return None, None, None, None


async def run_morning_posts(dry_run: bool = False, send: bool = True, accounts: list[str] | None = None) -> dict:
    """Egy reggeli ciklus: fiókonként 1 poszt → optimalizál → vizuál → Telegram (POSTS)."""
    accounts = accounts or MORNING_ACCOUNTS
    client = get_client(use_service_key=has_service_key())
    week_start = _week_start_iso()
    since_24h = _hours_ago_iso(24)

    news_rows = feed_store.get_recent_top(since_24h, min_score=6, client=client)
    top_item = feed_store.row_to_item(news_rows[0]) if news_rows else None
    header = f"☀️ Reggeli poszt — {hungarian_date()}\n\n"

    produced: list[dict] = []
    plan: list[dict] = []

    for account in accounts:
        counts = posts_store.weekly_content_type_counts(account, week_start, client=client)
        ctype = content_strategy.next_content_type(account, counts)
        rec = {"account": account, "recommended_type": ctype, "status": None}

        attempts = _build_attempts(account, ctype, top_item, client)
        result, resolved, seed_key, feed_item = await _generate_from_attempts(account, attempts)
        if not result:
            rec["status"] = "no_content"
            plan.append(rec)
            logger.info("[morning] %s — nincs használható tartalom", account)
            continue

        post = tb._result_to_post(result, account, "linkedin")
        post.update({
            "feed_item_id": feed_item.id if feed_item else None,
            "score": feed_item.score if feed_item else None,
            "feed_item_url": feed_item.url if feed_item else "",
            "title": feed_item.title if feed_item else seed_key,
            "strategy_type": resolved, "seed_key": seed_key,
        })
        if resolved == "workshop_promo":
            post["workshop_mentioned"] = True
        if not (post.get("content") or "").strip():
            rec["status"] = "empty"
            plan.append(rec)
            continue

        post = await _optimize_post(post, resolved)
        rec.update({"resolved_type": resolved, "seed_key": seed_key,
                    "hook_type": post.get("hook_type"), "tier": post.get("estimated_engagement_tier"),
                    "status": "generated"})

        entry = {
            "account": account, "content_type": resolved, "seed_key": seed_key,
            "hook_type": post.get("hook_type"), "tier": post.get("estimated_engagement_tier"),
            "preview": post["content"][:140].replace("\n", " "),
        }

        if not dry_run:
            post["sent_at"] = posts_store._now()
            post["id"] = posts_store.insert_post(post, client=client)
            await tb._attach_visual(post)
            entry["visual_url"] = post.get("visual_url")
            if send:
                await tb.send_for_approval(post, tb.POSTS_CHAT_ID, header=header)
                posts_store.mark_sent(post["id"], post["sent_at"], client=client)
            if feed_item is not None:
                feed_store.mark_generated(feed_item.id, client=client)
            entry["post_id"] = post["id"]

        produced.append(entry)
        plan.append(rec)

    summary = {
        "date": hungarian_date(), "accounts": accounts, "generated": len(produced),
        "sent": (0 if dry_run or not send else len(produced)),
        "posts": produced, "plan": plan, "dry_run": dry_run,
    }
    logger.info("[morning]%s %d/%d poszt | %s", " [DRY]" if dry_run else "",
                len(produced), len(accounts),
                ", ".join(f"{p['account']}→{p.get('resolved_type', p['recommended_type'])}({p['status']})" for p in plan))
    return summary


def main() -> int:
    setup_logging()
    asyncio.run(run_morning_posts(dry_run=True, send=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
