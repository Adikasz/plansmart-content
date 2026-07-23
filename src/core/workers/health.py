"""Health check HTTP szerver (aiohttp) — Railway megköveteli a nyitott portot.

Végpontok:
  • GET /health → 200 OK, időbélyeg + scheduler állapot
  • GET /status → JSON: last_collector_run, last_filter_run, last_breaking_run,
    last_morning_post, breaking_count_today, queue_sizes

A main.py a scheduler mellett, ugyanabban az event loopban futtatja (asyncio.gather).
A job wrapperek a record_run()-nal frissítik a STATE-et.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiohttp import web

from src.core.config.settings import get_settings

logger = logging.getLogger(__name__)

HEALTH_PORT = get_settings().port
TZ_NAME = get_settings().timezone

# Megosztott futásidejű állapot — a job wrapperek frissítik (record_run).
STATE: dict[str, object] = {
    "started_at": None,
    "scheduler_running": False,
    "last_collector_run": None,
    "last_filter_run": None,
    "last_breaking_run": None,
    "last_morning_post": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_run(job_id: str) -> None:
    """Egy job utolsó futásának időbélyege a STATE-ben (last_<job>_run/post)."""
    key = "last_morning_post" if job_id == "morning" else f"last_{job_id}_run"
    if key in STATE:
        STATE[key] = _now_iso()


def _day_start_utc_iso() -> str:
    import pytz

    try:
        tz = pytz.timezone(TZ_NAME)
    except Exception:
        tz = pytz.UTC
    midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).isoformat()


def _breaking_count_today() -> int | None:
    try:
        from src.core.storage import posts as posts_store

        return posts_store.breaking_count_since(_day_start_utc_iso())
    except Exception as exc:
        logger.debug("breaking_count_today nem elérhető: %s", str(exc)[:80])
        return None


def _queue_sizes() -> dict[str, int | None]:
    """feed_items sorok: pontozásra váró ('new') + generálásra kész ('filtered')."""
    sizes: dict[str, int | None] = {"unscored_new": None, "ready_filtered": None}
    try:
        from src.core.storage.db import get_client, has_service_key

        c = get_client(use_service_key=has_service_key())
        new = c.table("feed_items").select("id").eq("status", "new").execute()
        filt = c.table("feed_items").select("id").eq("status", "filtered").eq("used_for_posts", False).execute()
        sizes["unscored_new"] = len(new.data or [])
        sizes["ready_filtered"] = len(filt.data or [])
    except Exception as exc:
        logger.debug("queue_sizes nem elérhető: %s", str(exc)[:80])
    return sizes


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "timestamp": _now_iso(),
        "scheduler_running": STATE.get("scheduler_running", False),
        "started_at": STATE.get("started_at"),
    })


async def handle_status(_request: web.Request) -> web.Response:
    return web.json_response({
        "timestamp": _now_iso(),
        "scheduler_running": STATE.get("scheduler_running", False),
        "started_at": STATE.get("started_at"),
        "last_collector_run": STATE.get("last_collector_run"),
        "last_filter_run": STATE.get("last_filter_run"),
        "last_breaking_run": STATE.get("last_breaking_run"),
        "last_morning_post": STATE.get("last_morning_post"),
        "breaking_count_today": _breaking_count_today(),
        "queue_sizes": _queue_sizes(),
    })


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/", handle_health)
    return app


async def start_health_server(port: int = HEALTH_PORT) -> web.AppRunner:
    """Elindítja a health szervert (AppRunner+TCPSite); a runnert visszaadja leállításhoz."""
    STATE["started_at"] = STATE.get("started_at") or _now_iso()
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("Health szerver fut: http://0.0.0.0:%d/health", port)
    return runner
