"""Kulcsszó-alapú gyors szűrő — "skip" / "pass" + boost.

A config/scoring.yml skip_keywords és boost_keywords listáit használja:
- skip_keyword találat  -> azonnali "skip" (nem megy a drága scorerre)
- boost_keyword találat -> "pass" + boost (+2 a relevancia score-hoz)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config import loaders
from src.core.storage.models import FeedItem

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCORING_FILE = PROJECT_ROOT / "config" / "scoring.yml"

BOOST_POINTS = 2


@dataclass
class KeywordResult:
    """A kulcsszó-szűrő eredménye."""

    decision: str  # "skip" | "pass"
    reason: str
    boost: int = 0


def load_scoring_config(path: Path = SCORING_FILE) -> dict[str, Any]:
    """scoring.yml betoltese (a közös, cache-elt config.loaders.load_yaml-en át)."""
    return loaders.load_yaml(path)


def _matches(text: str, keyword: str) -> bool:
    """Szo-hatar alapu, kis/nagybetu-fuggetlen illesztes (a 'paper' nem matchel 'newspaper'-t)."""
    return re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE) is not None


def keyword_filter(item: FeedItem, config: dict[str, Any] | None = None) -> KeywordResult:
    """Gyors kulcsszavas döntés egy FeedItem-ről (skip / pass + boost)."""
    cfg = config if config is not None else load_scoring_config()
    skip_keywords = cfg.get("skip_keywords") or []
    boost_keywords = cfg.get("boost_keywords") or []

    text = " ".join(p for p in (item.title, item.content, item.url) if p)

    skip_hits = [kw for kw in skip_keywords if _matches(text, kw)]
    if skip_hits:
        return KeywordResult("skip", f"skip_keyword: {', '.join(skip_hits)}")

    boost_hits = [kw for kw in boost_keywords if _matches(text, kw)]
    if boost_hits:
        return KeywordResult("pass", f"boost_keyword: {', '.join(boost_hits)}", BOOST_POINTS)

    return KeywordResult("pass", "nincs kulcsszo-talalat", 0)
