"""Collector worker — RSS források lekérése és mentés a feed_items táblába.

A main.py 6 óránként futtatja (00:00, 06:00, 12:00, 18:00). Önállóan is fut:
    python -m src.workers.collector_worker
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

from dotenv import load_dotenv

from src.collectors.rss_collector import collect
from src.storage.feed_items import dedupe_and_save

logger = logging.getLogger(__name__)
load_dotenv(override=False)


async def run_collector_cycle() -> dict:
    """Egy collector ciklus: minden RSS forrás lekérése + dedup mentés.

    Visszaad egy összefoglalót (a scheduler/teszt logolja).
    """
    t0 = time.monotonic()
    results = await collect()
    all_items = [item for r in results for item in r.items]
    dedup = dedupe_and_save(all_items)
    elapsed = time.monotonic() - t0

    summary = {
        "sources": len(results),
        "sources_ok": sum(1 for r in results if r.label in ("OK", "FIXED")),
        "items_fetched": len(all_items),
        "saved": dedup.get("saved", 0),
        "duplicates": dedup.get("duplicates", 0),
        "connected": dedup.get("connected", False),
        "elapsed_s": round(elapsed, 1),
    }
    logger.info(
        "[collector] %d/%d forrás OK | %d elem | %d új mentve | %d duplikátum | %.1fs",
        summary["sources_ok"], summary["sources"], summary["items_fetched"],
        summary["saved"], summary["duplicates"], summary["elapsed_s"],
    )
    return summary


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run_collector_cycle())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
