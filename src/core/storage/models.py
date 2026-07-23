"""Megosztott adatmodellek a collectorokhoz és a tároláshoz.

- FeedItem: Pydantic modell, a feed_items tábla egy sora.
- SourceResult: egy forrás lekérésének eredménye (belső DTO).
- Státusz címkék + make_id segéd.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

# make_id / _utcnow_iso a src.utils.ids-ben lakik; itt re-exportáljuk, hogy a történeti
# `from src.core.storage.models import make_id` importok (collectors) érintetlenül maradjanak.
from src.utils.ids import _utcnow_iso, make_id  # noqa: F401

# Statusz cimkek
OK = "OK"
FIXED = "FIXED"
STILL_FAILING = "STILL_FAILING"
DISABLED = "DISABLED"


class FeedItem(BaseModel):
    """Egy bejovo hir — a feed_items tabla soranak felel meg."""

    id: str
    source_name: str
    source_priority: int
    url: str
    title: str | None = None
    content: str | None = None
    author: str | None = None
    published_at: str | None = None
    score: int | None = None  # relevance_scorer pontszáma (0-10) — generálás inputja
    tags: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "rss"
    fetched_at: str = Field(default_factory=_utcnow_iso)

    def to_row(self) -> dict[str, Any]:
        """Supabase insert-hez illeszkedo dict (feed_items oszlopok)."""
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "source_priority": self.source_priority,
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "author": self.author,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "topics": self.tags,
            "raw_data": self.raw_data,
            "status": "new",
        }


@dataclass
class SourceResult:
    """Egy forras lekeresenek eredmenye (belso DTO)."""

    name: str
    label: str = OK
    count: int = 0  # cap utan megtartott elemek szama
    raw_count: int = 0  # parse-olt nyers elemek szama
    error: str | None = None
    items: list[FeedItem] = field(default_factory=list)
