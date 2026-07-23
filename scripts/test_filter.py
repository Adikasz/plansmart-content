"""Phase 5 teszt: filter pipeline 10 random feed_items elemen.

Futtatás:
    python scripts/test_filter.py

Kiírja a per-elem döntéseket és a score-eloszlást.
"""
from __future__ import annotations

import logging
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from src.core.filters import filter_worker  # noqa: E402
from src.core.filters.dedup import is_duplicate  # noqa: E402
from src.core.storage.db import get_client, has_service_key  # noqa: E402
from src.core.storage.models import FeedItem  # noqa: E402

logger = logging.getLogger("test_filter")
SAMPLE_SIZE = 10


def _row_to_item(row: dict) -> FeedItem:
    return FeedItem(
        id=row["id"],
        source_name=row.get("source_name") or "",
        source_priority=row.get("source_priority") or 3,
        url=row.get("url") or "",
        title=row.get("title"),
        content=row.get("content"),
        author=row.get("author"),
        published_at=row.get("published_at"),
        tags=row.get("topics") or [],
        raw_data=row.get("raw_data") or {},
        source_type=row.get("source_type") or "rss",
    )


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    client = get_client(use_service_key=has_service_key())

    pool = (client.table("feed_items").select("*").limit(300).execute().data) or []
    if not pool:
        logger.error("Nincs adat a feed_items tablaban.")
        return 1
    sample = random.sample(pool, min(SAMPLE_SIZE, len(pool)))
    logger.info("Mintavetel: %d random elem a lekert %d-bol\n", len(sample), len(pool))

    # Dedup sanity-check: a mintaelem a tablaban van -> True; kitalalt id -> False.
    logger.info(
        "Dedup ellenorzes: letezo id -> %s | hamis id -> %s\n",
        is_duplicate(sample[0]["id"], client=client),
        is_duplicate("0000deadbeef0000", client=client),
    )

    scores: list[int] = []
    outcome: Counter = Counter()
    logger.info("%-24s  %-9s  %5s  %-4s  %s", "FORRAS", "DONTES", "SCORE", "PASS", "INDOK")
    logger.info("-" * 96)
    for row in sample:
        item = _row_to_item(row)
        # check_dedup=False: a mintaelemek szandekosan a tablabol jonnek (kulonben mind duplicate).
        try:
            res = filter_worker(item, client=client, check_dedup=False)
        except Exception as exc:
            outcome["error"] += 1
            logger.warning("%-24s  ERROR      %s", (item.source_name or "")[:24], str(exc)[:55])
            continue
        outcome[res.decision] += 1
        if res.score is not None:
            scores.append(res.score)
        passed = "" if res.passed_threshold is None else ("IGEN" if res.passed_threshold else "nem")
        logger.info(
            "%-24s  %-9s  %5s  %-4s  %s",
            (item.source_name or "")[:24],
            res.decision,
            res.score if res.score is not None else "-",
            passed,
            (res.reason or "")[:42],
        )

    logger.info("\n=== SCORE ELOSZLAS (pontozott: %d) ===", len(scores))
    if scores:
        hist = Counter(scores)
        for s in range(11):
            n = hist.get(s, 0)
            mark = "  <- threshold (>=6 pass)" if s == 6 else ""
            logger.info("  %2d | %-15s %d%s", s, "#" * n, n, mark)
        passed = sum(1 for s in scores if s >= 6)
        logger.info(
            "\n  atlag: %.1f | min: %d | max: %d | threshold felett (>=6): %d/%d",
            sum(scores) / len(scores), min(scores), max(scores), passed, len(scores),
        )
    logger.info("\nOsszesites: %s", dict(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
