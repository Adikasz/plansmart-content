"""Relevancia pontozó — Claude Haiku a prompts/filter_scoring.md alapján.

Egy FeedItem-et pontoz 0-10 skálán, és visszaadja a voice_fit / topics / urgency
mezőket is. A modelltől KIZÁRÓLAG JSON-t várunk, és szigorúan parse-oljuk.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from src.storage.cost_tracking import record_claude_usage
from src.storage.models import FeedItem

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = PROJECT_ROOT / "prompts" / "filter_scoring.md"

MODEL = "claude-haiku-4-5-20251001"  # ADR-008: filter scoring olcsó/gyors modellje
MAX_TOKENS = 600
SUMMARY_CHAR_CAP = 2000  # ~400 szó — a prompt input limitje


@dataclass
class ScoreResult:
    """A relevancia-pontozás eredménye (a feed_items scoring mezőihez illeszkedik)."""

    score: int
    reason: str
    voice_fit: dict[str, bool]
    topics: list[str]
    urgency: str
    raw: dict[str, Any] | None = None


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _client() -> Anthropic:
    return Anthropic()  # az ANTHROPIC_API_KEY-t a környezetből olvassa


def _parse_json_strict(text: str) -> dict[str, Any]:
    """Szigorú JSON parse — megengedi a ```json code fence-t, egyébként az első {..} blokkot."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            return json.loads(t[start : end + 1])
        raise


def score_item(item: FeedItem) -> ScoreResult:
    """FeedItem -> relevancia pontszám + metaadat (Claude Haiku)."""
    payload = json.dumps(
        {
            "title": item.title or "",
            "summary": (item.content or "")[:SUMMARY_CHAR_CAP],
            "source": item.source_name,
            "url": item.url,
            "tags": item.tags,
        },
        ensure_ascii=False,
    )

    msg = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_load_prompt(),
        messages=[{"role": "user", "content": payload}],
    )
    record_claude_usage(msg, MODEL, kind="relevance_scoring")
    data = _parse_json_strict(msg.content[0].text)

    return ScoreResult(
        score=int(data["score"]),
        reason=str(data.get("reason", "")),
        voice_fit=data.get("voice_fit", {}),
        topics=data.get("topics", []),
        urgency=data.get("urgency", "low"),
        raw=data,
    )
