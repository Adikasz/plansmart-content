"""Phase 10 dry-run: egy teljes pipeline ciklus szimulációja Telegram küldés NÉLKÜL.

Megmutatja, mit gyűjtene / szűrne / generálna:
  1. collector — valós RSS gyűjtés (idempotens, dedup) → feed_items
  2. filter    — kis batch pontozása (Claude Haiku), DRY: nem ír vissza a DB-be
  3. generator — a filteren átment hírekből posztok (valós Claude), DRY: nincs DB írás, nincs Telegram

Futtatás:
    python scripts/test_workers.py                 # alapból kis batch (olcsó)
    python scripts/test_workers.py --filter-limit 8 --max-posts 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from src.core.storage.db import get_client, has_service_key  # noqa: E402
from src.core.storage import feed_items as feed_store  # noqa: E402
from src.core.workers.collector_worker import run_collector_cycle  # noqa: E402
from src.core.workers.filter_worker import run_filter_cycle  # noqa: E402
from src.core.workers.generator_worker import GENERATION_VOICES, run_generator_cycle  # noqa: E402

logger = logging.getLogger("test_workers")


def _hr(title: str) -> None:
    logger.info("\n%s\n%s\n%s", "=" * 78, title, "=" * 78)


async def run(filter_limit: int, max_posts: int, skip_collect: bool) -> int:
    client = get_client(use_service_key=has_service_key())

    # 1) COLLECTOR
    _hr("1) COLLECTOR — RSS gyűjtés (valós, idempotens)")
    if skip_collect:
        logger.info("(kihagyva: --skip-collect)")
    else:
        c = await run_collector_cycle()
        logger.info("Eredmény: %d/%d forrás OK | %d elem | %d új mentve | %d duplikátum (%.1fs)",
                    c["sources_ok"], c["sources"], c["items_fetched"], c["saved"], c["duplicates"], c["elapsed_s"])

    # 2) FILTER (DRY)
    _hr(f"2) FILTER — {filter_limit} elem pontozása (DRY, nem ír DB-t)")
    f = run_filter_cycle(limit=filter_limit, dry_run=True)
    logger.info("Vizsgálva: %d | filtered (>=%d): %d | skipped: %d | hiba: %d",
                f["considered"], f["threshold"], f["filtered"], f["skipped"], f["errors"])
    for q in f["queued"]:
        logger.info("   ✓ score=%s  %s", q["score"], (q["title"] or "")[:70])
    if not f["queued"]:
        logger.info("   (nincs küszöb feletti elem ebben a batch-ben — a generátor lépésnek nincs bemenete)")

    # 3) GENERATOR (DRY) — a filteren átment sorokat használjuk (DB-állapottól függetlenül)
    _hr(f"3) GENERATOR — DRY (valós Claude, nincs DB/Telegram) | voices={','.join(GENERATION_VOICES)} max={max_posts}")
    queued_ids = [q["id"] for q in f["queued"]]
    rows: list[dict] = []
    if queued_ids:
        resp = client.table("feed_items").select("*").in_("id", queued_ids).execute()
        by_id = {r["id"]: r for r in (resp.data or [])}
        for q in f["queued"]:  # score-eloszlás sorrend megtartása + score injektálás
            row = by_id.get(q["id"])
            if row:
                row["score"] = q["score"]  # a DB-ben dry miatt még nincs score
                rows.append(row)

    if not rows:
        logger.info("(nincs generálandó elem)")
    else:
        g = await run_generator_cycle(dry_run=True, send=False, max_posts=max_posts, rows=rows)
        logger.info("Generálva: %d poszt | már megvolt: %d | voice-skip: %d | hiba: %d",
                    g["generated"], g["skipped_existing"], g["skipped_voice_skip"], g["errors"])
        for post in g["posts"]:
            logger.info("\n   ── %s | score=%s | %s", post["voice"].upper(), post["score"], (post["title"] or "")[:60])
            logger.info("      %s…", post["preview"])

    _hr("DRY RUN KÉSZ — semmit nem küldtünk Telegramra, posztot nem mentettünk.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="Phase 10 worker dry-run (egy ciklus, Telegram nélkül).")
    ap.add_argument("--filter-limit", type=int, default=6, help="hány elemet pontozzon (Haiku) — default 6")
    ap.add_argument("--max-posts", type=int, default=2, help="max poszt a generátor lépésben — default 2")
    ap.add_argument("--skip-collect", action="store_true", help="a collector lépés kihagyása")
    args = ap.parse_args()

    return asyncio.run(run(args.filter_limit, args.max_posts, args.skip_collect))


if __name__ == "__main__":
    raise SystemExit(main())
