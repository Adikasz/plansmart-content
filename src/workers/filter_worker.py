"""Filter worker — a még nem pontozott feed_items elemek szűrése + pontozása.

A 3 lépéses filter pipeline-t (dedup → keyword → Claude Haiku scoring) futtatja a
status='new' elemeken, és visszaírja az eredményt: score>=threshold → 'filtered'
(generálási sor), egyébként 'skipped'.

A main.py a collector után 30 perccel futtatja. Önállóan is fut:
    python -m src.workers.filter_worker
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv

from src.config.settings import get_settings
from src.filters import filter_worker as run_filter_pipeline  # a 3 lépéses pipeline függvény
from src.filters.keyword_filter import load_scoring_config
from src.storage import feed_items as feed_store
from src.storage.db import get_client, has_service_key
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

DEFAULT_LIMIT = get_settings().filter_batch_limit


def run_filter_cycle(limit: int = DEFAULT_LIMIT, dry_run: bool = False) -> dict:
    """Egy filter ciklus: pontatlan elemek lekérése, pontozás, visszaírás.

    dry_run=True: NEM ír vissza a feed_items-be, csak megmutatja, mi történne.
    """
    cfg = load_scoring_config()
    threshold = int(cfg.get("relevance_threshold", 6))
    client = get_client(use_service_key=has_service_key())

    rows = feed_store.get_unscored(limit=limit, client=client)
    filtered, skipped, errors = 0, 0, 0
    queued: list[dict] = []

    for row in rows:
        item = feed_store.row_to_item(row)
        try:
            res = run_filter_pipeline(item, config=cfg, client=client, check_dedup=False)
        except Exception as exc:
            errors += 1
            logger.warning("[filter] hiba (%s): %s", item.source_name, str(exc)[:80])
            continue

        passed = res.decision == "scored" and bool(res.passed_threshold)
        status = "filtered" if passed else "skipped"
        if passed:
            filtered += 1
            queued.append({"id": item.id, "title": item.title, "score": res.score})
        else:
            skipped += 1

        if not dry_run:
            feed_store.update_score(
                item.id, status=status, score=res.score, reason=res.reason,
                voice_fit=res.voice_fit, topics=res.topics, urgency=res.urgency, client=client,
            )

    summary = {
        "considered": len(rows), "filtered": filtered, "skipped": skipped,
        "errors": errors, "threshold": threshold, "queued": queued, "dry_run": dry_run,
    }
    logger.info(
        "[filter]%s %d vizsgálva | %d filtered (>=%d) | %d skipped | %d hiba",
        " [DRY]" if dry_run else "", len(rows), filtered, threshold, skipped, errors,
    )
    return summary


def main() -> int:
    setup_logging()
    run_filter_cycle()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
