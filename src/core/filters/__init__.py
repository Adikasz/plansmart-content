"""Filter pipeline — dedup -> keyword_filter -> relevance_scorer.

A filter_worker egy FeedItem-en végigfuttatja a 3 lépést. Csak a mindhárom
lépésen átmenő (nem duplikált, nem skip-elt) elem jut el a Claude-pontozásig.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.filters.dedup import is_duplicate
from src.core.filters.keyword_filter import keyword_filter, load_scoring_config
from src.core.filters.relevance_scorer import score_item
from src.core.storage.models import FeedItem

__all__ = ["filter_worker", "FilterResult"]


@dataclass
class FilterResult:
    """A teljes filter pipeline eredménye egy elemre."""

    decision: str  # "duplicate" | "skip" | "scored"
    stage: str  # "dedup" | "keyword" | "scorer"
    reason: str
    score: int | None = None
    voice_fit: dict[str, bool] | None = None
    topics: list[str] | None = None
    urgency: str | None = None
    passed_threshold: bool | None = None


def filter_worker(
    item: FeedItem,
    *,
    config: dict[str, Any] | None = None,
    client: Any | None = None,
    check_dedup: bool = True,
) -> FilterResult:
    """Láncolja a 3 szűrőt: dedup -> keyword -> scorer.

    check_dedup=False esetén a dedup lépés kimarad (pl. már eltárolt elem újrapontozásához).
    """
    cfg = config if config is not None else load_scoring_config()
    threshold = int(cfg.get("relevance_threshold", 6))

    # 1. dedup — már láttuk ezt az URL-t?
    if check_dedup and is_duplicate(item.id, client=client):
        return FilterResult("duplicate", "dedup", "mar letezik a feed_items-ben")

    # 2. keyword filter — gyors skip / boost
    kw = keyword_filter(item, config=cfg)
    if kw.decision == "skip":
        return FilterResult("skip", "keyword", kw.reason)

    # 3. relevance scorer (Claude Haiku) + keyword boost
    sc = score_item(item)
    final_score = min(10, sc.score + kw.boost)
    return FilterResult(
        decision="scored",
        stage="scorer",
        reason=sc.reason,
        score=final_score,
        voice_fit=sc.voice_fit,
        topics=sc.topics,
        urgency=sc.urgency,
        passed_threshold=final_score >= threshold,
    )
