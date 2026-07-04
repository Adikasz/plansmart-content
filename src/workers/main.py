"""Worker orchestrator — APScheduler cron + Telegram bot + health szerver párhuzamosan.

Ütemezés (alapból Europe/Budapest; TIMEZONE/SCHEDULER_TZ-vel felülírható,
COLLECTOR_INTERVAL_HOURS-szal az óraköz):
  • collector  — N óránként (alap 2):            00:00, 02:00, 04:00 …
  • filter     — N óránként + 30 perc:           00:30, 02:30, 04:30 …
  • breaking   — N óránként + 45 perc (24/7):     00:45, 02:45, 04:45 …
  • morning    — naponta 07:30 (hétvégén is):     a 3 fiók reggeli posztja

A Telegram approval bot és a health szerver (aiohttp, :8080) ugyanebben az event
loopban fut. Graceful shutdown SIGINT/SIGTERM-re.

Belépési pont:
    python -m src.workers.main                   # éles: ütemező + bot + health
    python -m src.workers.main --print-schedule  # csak az ütemezést írja ki, majd kilép
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta

import pytz
from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from src.bots import prospect_review
from src.bots import telegram_bot as tb
from src.workers import health
from src.workers.breaking_news_worker import run_breaking_check
from src.workers.collector_worker import run_collector_cycle
from src.workers.filter_worker import run_filter_cycle
from src.workers.morning_post_worker import run_morning_posts

logger = logging.getLogger("workers.main")
load_dotenv(override=False)

INTERVAL_H = max(1, int(os.environ.get("COLLECTOR_INTERVAL_HOURS", "2")))
MORNING_TIME = os.environ.get("MORNING_POST_TIME", "07:30")


def _tz():
    name = os.environ.get("TIMEZONE", os.environ.get("SCHEDULER_TZ", "Europe/Budapest"))
    try:
        return pytz.timezone(name)
    except Exception:
        logger.warning("Ismeretlen időzóna '%s' — UTC-re esem vissza.", name)
        return pytz.UTC


TZ = _tz()


def _morning_hm() -> tuple[int, int]:
    try:
        h, m = MORNING_TIME.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return 7, 30


_MH, _MM = _morning_hm()

# (id, leírás, cron trigger)
SCHEDULE = [
    ("collector", "RSS + creator gyűjtés",
     CronTrigger(hour=f"*/{INTERVAL_H}", minute=0, timezone=TZ)),
    ("filter", "szűrés + pontozás",
     CronTrigger(hour=f"*/{INTERVAL_H}", minute=30, timezone=TZ)),
    ("breaking", "breaking news (24/7)",
     CronTrigger(hour=f"*/{INTERVAL_H}", minute=45, timezone=TZ)),
    ("morning", "reggeli poszt (3 fiók)",
     CronTrigger(hour=_MH, minute=_MM, timezone=TZ)),
]


# ── Job futtatás start/end logolással + health STATE frissítéssel ──────
async def _run_job(job_id: str, coro_factory) -> None:
    start = datetime.now(TZ)
    logger.info("[job:%s] ▶ START %s", job_id, start.strftime("%Y-%m-%d %H:%M:%S %Z"))
    try:
        await coro_factory()
    except Exception:
        logger.exception("[job:%s] ✖ HIBA", job_id)
    finally:
        health.record_run(job_id)
        end = datetime.now(TZ)
        dur = (end - start).total_seconds()
        logger.info("[job:%s] ■ END   %s (%.1fs)", job_id, end.strftime("%H:%M:%S %Z"), dur)


async def _job_collector() -> None:
    await _run_job("collector", lambda: run_collector_cycle())


async def _job_filter() -> None:
    await _run_job("filter", lambda: asyncio.to_thread(run_filter_cycle))


async def _job_breaking() -> None:
    await _run_job("breaking", lambda: run_breaking_check())


async def _job_morning() -> None:
    await _run_job("morning", lambda: run_morning_posts())


JOB_FUNCS = {
    "collector": _job_collector, "filter": _job_filter,
    "breaking": _job_breaking, "morning": _job_morning,
}


def _print_schedule() -> None:
    now = datetime.now(TZ)
    logger.info("Ütemezés (időzóna: %s) — most: %s", TZ, now.strftime("%Y-%m-%d %H:%M:%S %Z"))
    logger.info("Config: INTERVAL=%dh | MORNING=%02d:%02d | breaking=24/7", INTERVAL_H, _MH, _MM)
    logger.info("%-11s  %-26s  %-26s  %s", "JOB", "LEÍRÁS", "CRON", "KÖVETKEZŐ FUTÁS")
    logger.info("%s", "-" * 96)
    for job_id, desc, trigger in SCHEDULE:
        nxt = trigger.get_next_fire_time(None, now)
        logger.info(
            "%-11s  %-26s  %-26s  %s",
            job_id, desc, str(trigger).replace("cron[", "").rstrip("]"),
            nxt.strftime("%Y-%m-%d %H:%M %Z") if nxt else "—",
        )


def _next_24h_runs() -> list[tuple[str, str]]:
    """A következő 24 óra ütemezett futásai (job_id, időpont) — időrendben."""
    now = datetime.now(TZ)
    horizon = now.timestamp() + 24 * 3600
    runs: list[tuple[float, str, str]] = []
    for job_id, _desc, trigger in SCHEDULE:
        cursor = now  # a 'now' kurzort léptetjük minden tűz után (APScheduler now-ból számol)
        for _ in range(13):  # max 13 futás/job a 24h ablakban
            nxt = trigger.get_next_fire_time(None, cursor)
            if not nxt or nxt.timestamp() > horizon:
                break
            runs.append((nxt.timestamp(), job_id, nxt.strftime("%Y-%m-%d %H:%M %Z")))
            cursor = nxt + timedelta(seconds=1)
    runs.sort()
    return [(j, t) for _ts, j, t in runs]


async def _amain() -> None:
    scheduler = AsyncIOScheduler(timezone=TZ)
    for job_id, _desc, trigger in SCHEDULE:
        scheduler.add_job(JOB_FUNCS[job_id], trigger, id=job_id, max_instances=1, coalesce=True)
    scheduler.start()
    health.STATE["scheduler_running"] = True
    _print_schedule()

    runner = await health.start_health_server()

    bot = tb.get_bot()
    me = await bot.get_me()
    logger.info("\nTelegram bot: @%s | posts=%s | reactions=%s", me.username, tb.POSTS_CHAT_ID, tb.REACTIONS_CHAT_ID)
    logger.info("Orchestrator elindult — SIGINT/SIGTERM a leállításhoz.\n")

    dp = Dispatcher()
    # A prospect router ELŐBB fut, mint a posts router: a posts botnak van egy privát-chat
    # catch-all handlere (dm_edit_reply), ami különben elnyelné a /prospects, /mark_sent stb.
    # parancsokat DM-ben. A prospect routernek nincs catch-all-ja, így a nem-prospect DM-ek
    # rendben átesnek rajta a posts routerhez.
    dp.include_router(prospect_review.router)
    dp.include_router(tb.router)

    stop = asyncio.Event()

    def _request_stop(*_a) -> None:
        logger.info("Leállítási jelzés — kecses leállítás…")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, AttributeError):
            # Windows: add_signal_handler nem mindig támogatott.
            try:
                signal.signal(sig, lambda *_a: _request_stop())
            except (ValueError, OSError):
                pass

    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    await stop.wait()

    # Kecses leállítás: polling stop → scheduler → health → bot session.
    await dp.stop_polling()
    polling.cancel()
    try:
        await polling
    except asyncio.CancelledError:
        pass
    scheduler.shutdown(wait=False)
    health.STATE["scheduler_running"] = False
    await runner.cleanup()
    await bot.session.close()
    logger.info("Leállítva.")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="PlanSmart worker orchestrator (APScheduler + Telegram + health).")
    ap.add_argument("--print-schedule", action="store_true", help="csak az ütemezést írja ki, majd kilép")
    args = ap.parse_args()

    if args.print_schedule:
        _print_schedule()
        return 0

    try:
        asyncio.run(_amain())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Leállítva (KeyboardInterrupt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
