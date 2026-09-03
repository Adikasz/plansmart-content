"""Worker orchestrator — APScheduler cron + Telegram bot + health szerver párhuzamosan.

Ütemezés (alapból Europe/Budapest; TIMEZONE/SCHEDULER_TZ-vel felülírható,
COLLECTOR_INTERVAL_HOURS-szal az óraköz):
  • collector    — N óránként (alap 2):          00:00, 02:00, 04:00 …
  • filter       — N óránként + 30 perc:         00:30, 02:30, 04:30 …
  • breaking     — N óránként + 45 perc (24/7):  CSAK ha a breaking hangja nem digest-hang
  • morning      — naponta 07:30 (hétvégén is):  a nem-digest fiókok reggeli posztja
  • video_idea   — hétfő 08:05:                  CSAK ha a videó hangja nem digest-hang
  • adam_digest  — naponta 07:50, de a worker 2 naponta enged át egy kiküldést

Fázis 23: a digest-hangok (DIGEST_VOICES, alapból `adam`) MINDEN ütemezett értesítése
egyetlen jobba (adam_digest) fut össze — a hozzájuk tartozó önálló jobok be sem kerülnek
az ütemezésbe. Dávid és PlanSmart ütemezése változatlan.

A Telegram approval bot és a health szerver (aiohttp, :8080) ugyanebben az event
loopban fut. Graceful shutdown SIGINT/SIGTERM-re.

Belépési pont:
    python -m src.core.workers.main                   # éles: ütemező + bot + health
    python -m src.core.workers.main --print-schedule  # csak az ütemezést írja ki, majd kilép
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytz
from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from src.core.config.settings import get_settings
from src.core.strategy import cadence
from src.core.workers import health
from src.core.workers import morning_post_worker as mpw
from src.core.workers.adam_digest_worker import run_adam_digest
from src.core.workers.breaking_news_worker import BREAKING_VOICE, run_breaking_check
from src.core.workers.collector_worker import run_collector_cycle
from src.core.workers.filter_worker import run_filter_cycle
from src.core.workers.morning_post_worker import run_morning_posts
from src.core.workers.video_idea_worker import VIDEO_IDEA_VOICE, run_video_idea_check
from src.integrations.bots import (
    engagement_bot,
    metrics_bot,
    prospect_review,
    reactions_bot,
    video_idea_bot,
)
from src.integrations.bots import telegram_bot as tb
from src.integrations.visuals import portrait as portrait_mod
from src.utils.logging import setup_logging

logger = logging.getLogger("workers.main")
load_dotenv(override=False)

# Tipizált config: a COLLECTOR_INTERVAL_HOURS clamp (>=1) és a defaultok a Settings-ben laknak.
INTERVAL_H = get_settings().collector_interval_hours
MORNING_TIME = get_settings().morning_post_time
DIGEST_TIME = get_settings().adam_digest_time


def _tz() -> pytz.BaseTzInfo:
    name = get_settings().timezone  # TIMEZONE, majd SCHEDULER_TZ, majd Europe/Budapest fallback
    try:
        return pytz.timezone(name)
    except Exception:
        logger.warning("Ismeretlen időzóna '%s' — UTC-re esem vissza.", name)
        return pytz.UTC


TZ = _tz()


def _hm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    """'HH:MM' -> (óra, perc); hibás/hiányzó értéknél a megadott default."""
    try:
        h, m = value.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return default


_MH, _MM = _hm(MORNING_TIME, (7, 30))
_DH, _DM = _hm(DIGEST_TIME, (7, 50))


def _build_schedule() -> list[tuple[str, str, CronTrigger]]:
    """(id, leírás, cron trigger) hármasok.

    Fázis 23: a digest-hangokhoz (alapból: adam) tartozó ÖNÁLLÓ jobok be sem kerülnek —
    nem "mindig no-op" ütemezett jobként lógnak itt, hanem hiányoznak a --print-schedule
    kimenetből is. Ha a DIGEST_VOICES config változik (pl. a breaking hangja David lesz),
    a job magától visszakerül.
    """
    sched: list[tuple[str, str, CronTrigger]] = [
        (
            "collector",
            "RSS + creator gyűjtés",
            CronTrigger(hour=f"*/{INTERVAL_H}", minute=0, timezone=TZ),
        ),
        (
            "filter",
            "szűrés + pontozás",
            CronTrigger(hour=f"*/{INTERVAL_H}", minute=30, timezone=TZ),
        ),
    ]

    # A breaking EGYETLEN hangja Ádám (BREAKING_VOICE) — amíg ő digest-hang, a job nem fut.
    if not cadence.is_digest_voice(BREAKING_VOICE):
        sched.append(
            (
                "breaking",
                f"breaking news 24/7 ({BREAKING_VOICE})",
                CronTrigger(hour=f"*/{INTERVAL_H}", minute=45, timezone=TZ),
            )
        )

    morning = [a for a in mpw.ALL_MORNING_ACCOUNTS if not cadence.is_digest_voice(a)]
    sched.append(
        (
            "morning",
            f"reggeli poszt ({len(morning)} fiók: {', '.join(morning)})",
            CronTrigger(hour=_MH, minute=_MM, timezone=TZ),
        )
    )

    # hétfő 08:05 -- NEM 08:00, mert az egybeesne a collector jobbal (ami INTERVAL_H óránként
    # :00-kor fut, és 8 osztható a 2 órás alapértékkel); a :05 offset bármilyen INTERVAL_H
    # mellett elkerüli a collector/filter/breaking :00/:30/:45 mintáját.
    if not cadence.is_digest_voice(VIDEO_IDEA_VOICE):
        sched.append(
            (
                "video_idea",
                f"heti videó-ötlet ({VIDEO_IDEA_VOICE})",
                CronTrigger(day_of_week="mon", hour=8, minute=5, timezone=TZ),
            )
        )

    # Ádám konszolidált digestje: MINDEN reggel elindul, de a 2 napos óraközt maga a worker
    # őrzi (adam_digest_min_hours, az utolsó SIKERES kiküldéstől számolva). Így egy kimaradt
    # reggel után a következő nap magától helyreáll — egy páros/páratlan nap-kapcsoló ezt nem
    # tudná, és év-/hónapfordulón is elcsúszna. A morning UTÁN fut (alapból 07:50), hogy a
    # két job ne egyszerre hívja Claude-ot.
    if cadence.digest_voices():
        sched.append(
            (
                "adam_digest",
                f"konszolidált digest / 2 nap ({', '.join(sorted(cadence.digest_voices()))})",
                CronTrigger(hour=_DH, minute=_DM, timezone=TZ),
            )
        )
    return sched


SCHEDULE = _build_schedule()

# A ritkán tüzelő jobok grace-ideje. Az APScheduler alapértelmezése 1 másodperc: ha az event
# loop pont a tüzelés pillanatában foglalt (szinkron Supabase/Muapi hívás egy async ágban),
# a futás CSENDBEN kimarad. Egy naponta egyszer tüzelő jobnál ez egy egész napot visz.
MISFIRE_GRACE_SEC = 3600


# ── Job futtatás start/end logolással + health STATE frissítéssel ──────
async def _run_job(job_id: str, coro_factory: Callable[[], Awaitable[object]]) -> None:
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


async def _job_video_idea() -> None:
    await _run_job("video_idea", lambda: run_video_idea_check())


async def _job_adam_digest() -> None:
    await _run_job("adam_digest", lambda: run_adam_digest())


JOB_FUNCS = {
    "collector": _job_collector,
    "filter": _job_filter,
    "breaking": _job_breaking,
    "morning": _job_morning,
    "video_idea": _job_video_idea,
    "adam_digest": _job_adam_digest,
}


def _print_schedule() -> None:
    now = datetime.now(TZ)
    logger.info("Ütemezés (időzóna: %s) — most: %s", TZ, now.strftime("%Y-%m-%d %H:%M:%S %Z"))
    logger.info(
        "Config: INTERVAL=%dh | MORNING=%02d:%02d | DIGEST=%02d:%02d (min %dh) | digest-hangok: %s",
        INTERVAL_H,
        _MH,
        _MM,
        _DH,
        _DM,
        get_settings().adam_digest_min_hours,
        ", ".join(sorted(cadence.digest_voices())) or "—",
    )
    logger.info("%-11s  %-26s  %-26s  %s", "JOB", "LEÍRÁS", "CRON", "KÖVETKEZŐ FUTÁS")
    logger.info("%s", "-" * 96)
    for job_id, desc, trigger in SCHEDULE:
        nxt = trigger.get_next_fire_time(None, now)
        logger.info(
            "%-11s  %-26s  %-26s  %s",
            job_id,
            desc,
            str(trigger).replace("cron[", "").rstrip("]"),
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
        scheduler.add_job(
            JOB_FUNCS[job_id],
            trigger,
            id=job_id,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=(MISFIRE_GRACE_SEC if job_id == "adam_digest" else None),
        )
    scheduler.start()
    health.STATE["scheduler_running"] = True
    _print_schedule()

    runner = await health.start_health_server()

    # Alapító-portré kivágatok előmelegítése (rembg/onnx, lassú) — a Railway FS ephemeral,
    # így minden redeploy után újra kellene vágni; ha ezt az első élő --portrait kérés
    # csinálná meg szinkron módon, percekre blokkolná az event loopot (lásd bug: /create
    # --portrait "nem válaszol" + hiányzó portré). Itt, indításkor, háttérszálon fut.
    async def _prewarm_portraits() -> None:
        try:
            paths = await asyncio.to_thread(portrait_mod.preprocess_all)
            logger.info("Portré cache előmelegítve: %s", {k: str(v) for k, v in paths.items()})
        except Exception:
            logger.exception("Portré cache előmelegítés hiba (nem blokkoló)")

    asyncio.create_task(_prewarm_portraits())

    bot = tb.get_bot()
    me = await bot.get_me()
    logger.info(
        "\nTelegram bot: @%s | posts=%s | reactions=%s",
        me.username,
        tb.POSTS_CHAT_ID,
        tb.REACTIONS_CHAT_ID,
    )
    logger.info("Orchestrator elindult — SIGINT/SIGTERM a leállításhoz.\n")

    dp = Dispatcher()
    # A reactions + prospect routerek ELŐBB futnak, mint a posts router: a posts botnak van egy
    # privát-chat catch-all handlere (dm_edit_reply), ami különben elnyelné a /reply_comment,
    # /prospects stb. parancsokat DM-ben. A reactions free-text handlere CSAK aktív flow-nál kap
    # el (más DM-et nem), a prospect routernek nincs catch-all-ja — így a nem-flow DM-ek rendben
    # átesnek a posts routerhez.
    dp.include_router(reactions_bot.router)
    dp.include_router(prospect_review.router)
    dp.include_router(metrics_bot.router)
    dp.include_router(engagement_bot.router)
    dp.include_router(video_idea_bot.router)
    dp.include_router(tb.router)

    stop = asyncio.Event()

    def _request_stop(*_a: object) -> None:
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
    setup_logging()  # UTF-8 streamek + basicConfig(%(message)s, LOG_LEVEL) + zajos libek WARNING-ra

    ap = argparse.ArgumentParser(
        description="PlanSmart worker orchestrator (APScheduler + Telegram + health)."
    )
    ap.add_argument(
        "--print-schedule", action="store_true", help="csak az ütemezést írja ki, majd kilép"
    )
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
