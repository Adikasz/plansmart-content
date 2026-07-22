"""engagement_metrics tárolási réteg (Phase 21b) — manuális LinkedIn engagement mérés.

A LinkedIn API még nem éles, ezért Dávid/Ádám kézzel nézi meg a számokat LinkedInen és
jelenti be a /log_stats paranccsal (src/bots/engagement_bot.py). TÖBB sor is tartozhat egy
poszthoz (időbeli pillanatfelvételek, pl. 24h-nál és 48h-nál is mérve) -- lásd
src/storage/engagement_report.representative_snapshot_per_post a riporthoz használt
"melyik sort vegyük egy posztnál" logikáért.

A tábla sémáját a scripts/migration_22_engagement.sql hozza létre.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.db import get_client, has_service_key, table_exists

logger = logging.getLogger(__name__)

TABLE = "engagement_metrics"
FINAL_SNAPSHOT_HOURS = 48


def _c(client=None):
    return client or get_client(use_service_key=has_service_key())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def table_ready(client=None) -> bool:
    """True, ha az engagement_metrics tábla létezik (a migration_22 lefutott)."""
    return table_exists(_c(client), TABLE)


def log(
    post_id: str,
    *,
    views: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    is_final_snapshot: bool | None = None,
    client=None,
) -> dict[str, Any]:
    """Egy engagement pillanatfelvétel mentése.

    Legalább egy metrikát meg kell adni (üres sort nem mentünk -- ValueError). A
    hours_since_post a posts.sent_at-ból számolódik; ha az NULL (a poszt még nincs
    /mark_posted-del megjelölve), a sor MENTVE lesz (a human ne veszítse el a valós
    adatot), de hours_since_post=None és a válasz "warning" mezője figyelmeztet.

    is_final_snapshot: ha None, automatikusan igaz lesz, ha hours_since_post >= 48 --
    kézzel felülírható (pl. korai, de már láthatóan lecsengett engagementnél).

    Visszaad: {"id", "hours_since_post", "is_final_snapshot", "warning"}.
    """
    if views is None and likes is None and comments is None and shares is None:
        raise ValueError("legalább egy metrikát meg kell adni (views/likes/comments/shares)")

    from src.storage import posts as posts_store

    c = _c(client)
    post = posts_store.get_post(post_id, client=c)
    if not post:
        raise ValueError(f"nincs ilyen poszt: {post_id!r}")

    warning = None
    hours_since_post = None
    sent_at = _parse_dt(post.get("sent_at"))
    if sent_at is None:
        warning = (
            "posts.sent_at nincs beállítva -- hours_since_post ismeretlen. "
            "Használd a /mark_posted-et, amikor egy poszt ténylegesen kiment."
        )
    else:
        now = datetime.now(timezone.utc)
        hours_since_post = max(0, int((now - sent_at).total_seconds() // 3600))

    if is_final_snapshot is None:
        is_final_snapshot = hours_since_post is not None and hours_since_post >= FINAL_SNAPSHOT_HOURS

    row_id = uuid.uuid4().hex[:12]
    c.table(TABLE).insert({
        "id": row_id,
        "post_id": post_id,
        "voice": post.get("voice"),
        "measured_at": _now(),
        "hours_since_post": hours_since_post,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "is_final_snapshot": bool(is_final_snapshot),
    }).execute()

    return {
        "id": row_id,
        "hours_since_post": hours_since_post,
        "is_final_snapshot": bool(is_final_snapshot),
        "warning": warning,
    }


def delete_row(row_id: str, client=None) -> None:
    """Egy engagement sor törlése (teszt/cleanup célra)."""
    _c(client).table(TABLE).delete().eq("id", row_id).execute()


def for_post(post_id: str, client=None) -> list[dict[str, Any]]:
    """Egy poszt ÖSSZES pillanatfelvétele, legújabb elöl."""
    resp = (
        _c(client).table(TABLE).select("*")
        .eq("post_id", post_id).order("measured_at", desc=True).execute()
    )
    return resp.data or []


def all_rows(client=None) -> list[dict[str, Any]]:
    """MINDEN engagement_metrics sor (a /engagement_report bemenete)."""
    resp = _c(client).table(TABLE).select("*").execute()
    return resp.data or []
