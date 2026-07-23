"""RSS collect + riport a Phase 'creator sources' bővítéshez.

- Lefuttatja a teljes collect()-et, ment Supabase-be (dedup).
- Külön kimutatja az ÚJ creator forrásokat: forrásonként hány elem / hány új.
- A frissen mentett creator elemekből pontoz egy batch-et (Claude Haiku) és
  kiírja a Top 5 legmagasabb pontszámút.

Futtatás:
    python scripts/report_new_sources.py [--score-limit 80]
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

from src.integrations.collectors.rss_collector import collect  # noqa: E402
from src.core.filters.relevance_scorer import score_item  # noqa: E402
from src.core.storage.db import get_client, has_service_key  # noqa: E402
from src.core.storage.feed_items import dedupe_and_save  # noqa: E402
from src.core.storage.models import OK, FIXED  # noqa: E402

logger = logging.getLogger("report_new_sources")

# A most hozzáadott creator források (a sources.yml két új szekciója).
NEW_SOURCES = {
    "Andrej Karpathy - YouTube", "Sam Altman - Blog", "Lex Fridman - YouTube",
    "Yann LeCun - YouTube", "Lenny's Newsletter",
    "Nate Herk - YouTube", "Liam Ottley - YouTube", "Jono Catliff - YouTube",
    "Nick Saraev - YouTube", "Cole Medin - YouTube", "Matt Wolfe - YouTube",
    "Matt Wolfe - Future Tools Blog", "Matthew Berman - YouTube", "Wes Roth - YouTube",
    "Greg Isenberg - YouTube", "Sabrina Ramonov - YouTube", "Jack Roberts - YouTube",
    "Logan Kilpatrick - YouTube",
}


def _existing_ids(client, ids: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        resp = client.table("feed_items").select("id").in_("id", chunk).execute()
        found.update(r["id"] for r in (resp.data or []))
    return found


async def _score_many(items: list, limit: int, concurrency: int = 8) -> list[tuple]:
    sem = asyncio.Semaphore(concurrency)
    sample = items[:limit]

    async def one(it):
        async with sem:
            try:
                res = await asyncio.to_thread(score_item, it)
                return (res.score, it.source_name, it.title or "", it.url)
            except Exception as exc:
                logger.warning("score hiba (%s): %s", it.source_name, str(exc)[:60])
                return None

    scored = await asyncio.gather(*[one(it) for it in sample])
    return [s for s in scored if s]


async def main(score_limit: int) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("src.integrations.collectors.rss_collector").setLevel(logging.WARNING)

    client = get_client(use_service_key=has_service_key())

    logger.info("RSS collect indul…")
    results = await collect()
    all_items = [it for r in results for it in r.items]

    ok = sum(1 for r in results if r.label in (OK, FIXED))
    failed = len(results) - ok

    # Creator forrásonkénti pre-save NEW számítás
    creator_items = [it for it in all_items if it.source_name in NEW_SOURCES]
    existing = _existing_ids(client, [it.id for it in creator_items]) if creator_items else set()
    creator_new = [it for it in creator_items if it.id not in existing]

    # Persist (minden forrás)
    dedup = dedupe_and_save(all_items)

    # ── Riport ──
    logger.info("\n%s", "=" * 78)
    logger.info("ÖSSZ FORRÁS: %d  |  OK: %d  |  FAILED: %d", len(results), ok, failed)
    logger.info("Összes elem (cap után): %d  |  ÚJ mentve (összes forrás): %d  |  duplikátum: %d",
                len(all_items), dedup.get("saved", 0), dedup.get("duplicates", 0))
    logger.info("%s", "=" * 78)

    # Új creator források státusza + per-source bontás
    by_name = {r.name: r for r in results}
    new_by_source: dict[str, int] = {}
    for it in creator_new:
        new_by_source[it.source_name] = new_by_source.get(it.source_name, 0) + 1

    logger.info("\nÚJ CREATOR FORRÁSOK (%d) — státusz | fetched | ebből új:", len(NEW_SOURCES))
    logger.info("%-34s  %-7s  %7s  %6s", "FORRÁS", "STÁTUSZ", "FETCHED", "ÚJ")
    logger.info("-" * 64)
    creator_ok = 0
    for name in sorted(NEW_SOURCES):
        r = by_name.get(name)
        label = r.label if r else "MISSING"
        if label in (OK, FIXED):
            creator_ok += 1
        fetched = r.count if r else 0
        logger.info("%-34s  %-7s  %7d  %6d", name[:34], label, fetched, new_by_source.get(name, 0))
    logger.info("-" * 64)
    logger.info("Creator források OK: %d/%d  |  összes új creator elem: %d",
                creator_ok, len(NEW_SOURCES), len(creator_new))

    # Top 5 pontszám a friss creator elemekből
    if not creator_new:
        logger.info("\nNincs új creator elem a pontozáshoz.")
        return 0
    logger.info("\nPontozás (Claude Haiku) — %d új creator elemből az első %d…",
                len(creator_new), min(score_limit, len(creator_new)))
    scored = await _score_many(creator_new, score_limit)
    scored.sort(key=lambda x: x[0], reverse=True)

    logger.info("\n%s\nTOP 5 LEGMAGASABB PONTSZÁM (új creator feedek)\n%s", "=" * 78, "=" * 78)
    for i, (score, source, title, url) in enumerate(scored[:5], 1):
        logger.info("%d. [score %d] %s", i, score, source)
        logger.info("   %s", (title or "")[:90])
        logger.info("   %s", url)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-limit", type=int, default=80, help="hány új creator elemet pontozzon")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.score_limit)))
