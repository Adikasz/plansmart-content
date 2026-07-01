"""feed_items tárolási réteg — dedup + mentés + a worker pipeline lekérdezései.

(PROJECT_PLAN.md Fázis 1: storage modul a feed_items táblához; Fázis 10: worker queryk.)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.storage.models import FeedItem

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client():
    from src.storage.db import get_client, has_service_key

    return get_client(use_service_key=has_service_key())


def row_to_item(row: dict[str, Any]) -> FeedItem:
    """Supabase feed_items sor -> FeedItem (a filter/generator workerekhez)."""
    return FeedItem(
        id=row["id"],
        source_name=row.get("source_name") or "",
        source_priority=row.get("source_priority") or 3,
        url=row.get("url") or "",
        title=row.get("title"),
        content=row.get("content"),
        author=row.get("author"),
        published_at=row.get("published_at"),
        score=row.get("score"),
        tags=row.get("topics") or [],
        raw_data=row.get("raw_data") or {},
        source_type=row.get("source_type") or "rss",
    )


def get_unscored(limit: int = 50, client=None) -> list[dict[str, Any]]:
    """Még nem pontozott elemek (status='new') — a filter_worker bemenete."""
    c = client or _client()
    resp = (
        c.table("feed_items").select("*").eq("status", "new")
        .order("fetched_at", desc=True).limit(limit).execute()
    )
    return resp.data or []


def update_score(
    item_id: str,
    *,
    status: str,
    score: int | None = None,
    reason: str | None = None,
    voice_fit: dict[str, bool] | None = None,
    topics: list[str] | None = None,
    urgency: str | None = None,
    client=None,
) -> None:
    """A filter eredmény visszaírása a feed_items sorba (status: 'filtered' | 'skipped')."""
    patch: dict[str, Any] = {"status": status, "scored_at": _now_iso()}
    for key, val in (
        ("score", score), ("score_reason", reason), ("voice_fit", voice_fit),
        ("topics", topics), ("urgency", urgency),
    ):
        if val is not None:
            patch[key] = val
    (client or _client()).table("feed_items").update(patch).eq("id", item_id).execute()


def get_for_generation(limit: int = 20, min_score: int = 6, client=None) -> list[dict[str, Any]]:
    """Generálásra váró elemek: status='filtered', score>=min, még nem használt — legjobb előre."""
    c = client or _client()
    resp = (
        c.table("feed_items").select("*")
        .eq("status", "filtered").gte("score", min_score).eq("used_for_posts", False)
        .order("score", desc=True).limit(limit).execute()
    )
    return resp.data or []


def mark_generated(item_id: str, client=None) -> None:
    """Az elem generálás után: status='generated', used_for_posts=True."""
    (client or _client()).table("feed_items").update(
        {"status": "generated", "used_for_posts": True}
    ).eq("id", item_id).execute()


def get_recent_top(since_iso: str, min_score: int = 6, limit: int = 20, client=None) -> list[dict[str, Any]]:
    """Friss (since óta), generálásra váró elemek — legjobb pontszám előre (reggeli poszt)."""
    c = client or _client()
    resp = (
        c.table("feed_items").select("*")
        .eq("status", "filtered").gte("score", min_score).eq("used_for_posts", False)
        .gte("fetched_at", since_iso).order("score", desc=True).limit(limit).execute()
    )
    return resp.data or []


def get_breaking_candidates(min_score: int = 8, limit: int = 30, client=None) -> list[dict[str, Any]]:
    """Breaking news jelöltek: score>=min, még nem 'breaking'-elt elemek — legjobb előre.

    A 4 órás időablakot + kulcsszó-szűrést a hívó (breaking_news_worker) végzi Pythonban,
    mert a published_at formátuma forrásonként eltérhet (és lehet null).
    """
    c = client or _client()
    resp = (
        c.table("feed_items").select("*")
        .gte("score", min_score).eq("breaking", False)
        .order("score", desc=True).limit(limit).execute()
    )
    return resp.data or []


def mark_breaking(item_id: str, client=None) -> None:
    """Az elemet breaking-ként jelöli (dedup: max 1 breaking / feed_item)."""
    (client or _client()).table("feed_items").update(
        {"breaking": True}
    ).eq("id", item_id).execute()


def _connect_supabase():
    """Supabase kliens, ha elerheto (valid kulcs). Egyebkent None."""
    try:
        from src.storage.db import get_client, has_service_key

        return get_client(use_service_key=has_service_key())
    except Exception as exc:
        logger.info("Supabase nem elerheto (%s) — in-memory dedup.", str(exc)[:60])
        return None


def dedupe_and_save(items: list[FeedItem]) -> dict[str, Any]:
    """Dedup + opcionalis mentes.

    - Futason beluli dedup mindig (id alapu in-memory set).
    - Ha Supabase elerheto: a mar letezo URL-hash-eket is kiszurjuk, es az ujakat mentjuk.
    """
    by_id: dict[str, FeedItem] = {}
    for item in items:
        by_id.setdefault(item.id, item)  # in-run dedup
    in_run_dupes = len(items) - len(by_id)

    client = _connect_supabase()
    if client is None:
        return {
            "connected": False, "new": len(by_id),
            "duplicates": in_run_dupes, "saved": 0,
        }

    ids = list(by_id.keys())
    existing: set[str] = set()
    try:
        for i in range(0, len(ids), 100):
            chunk = ids[i : i + 100]
            resp = client.table("feed_items").select("id").in_("id", chunk).execute()
            existing.update(r["id"] for r in (resp.data or []))

        new_items = [it for it in by_id.values() if it.id not in existing]
        rows = [it.to_row() for it in new_items]
        saved = 0
        for i in range(0, len(rows), 100):
            client.table("feed_items").insert(rows[i : i + 100]).execute()
            saved += len(rows[i : i + 100])
    except Exception as exc:
        logger.warning("Supabase mentes hiba (%s) — kihagyva.", str(exc)[:60])
        return {
            "connected": False, "new": len(by_id),
            "duplicates": in_run_dupes, "saved": 0,
        }

    return {
        "connected": True, "new": len(new_items),
        "duplicates": (len(by_id) - len(new_items)) + in_run_dupes, "saved": saved,
    }
