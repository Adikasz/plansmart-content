"""video_ideas tárolási réteg (Phase 22) — Ádám heti reakció-videó ötletek.

Ugyanaz a minta mint posts.py: _c(client)/_now() helperek, uuid hex12 id, upsert-alapú insert,
generikus update_status patch-elő, vékony mark_* wrapperek. A tábla sémáját a
scripts/migration_23_video_ideas.sql hozza létre.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, cast

from postgrest.exceptions import APIError
from supabase import Client

from src.core.storage.db import get_client, has_service_key, table_exists

logger = logging.getLogger(__name__)

TABLE = "video_ideas"


class DuplicateVideoIdeaError(Exception):
    """Már létezik video_idea ehhez a feed_item_id-hoz (idx_video_ideas_feed_item_unique).

    A has_video_idea_for_feed_item check-then-insert dedup a Claude-hívás (másodpercek) miatt
    versenyhelyzetnek van kitéve -- a heti cron és egy egyidejű /create_video ugyanarra a
    jelöltre futhat. Ez az egyedi index (lásd migration_23_video_ideas.sql) garantálja az
    invariánst az adatbázis szintjén; ezt az kivételt a hívó (worker/bot) fogja el és kezeli
    kecsesen (nem crash, csak egy "már létezik" üzenet/log).
    """

    def __init__(self, feed_item_id: str | None):
        self.feed_item_id = feed_item_id
        super().__init__(f"már van video_idea ehhez a feed_item_id-hoz: {feed_item_id}")


def _c(client: Client | None = None) -> Client:
    return client or get_client(use_service_key=has_service_key())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def table_ready(client: Client | None = None) -> bool:
    """True, ha a video_ideas tábla létezik (a migration_23 lefutott)."""
    return table_exists(_c(client), TABLE)


def insert_video_idea(idea: dict[str, Any], client: Client | None = None) -> str:
    """Beszúr (upsert) egy video_ideas sort; visszaadja az id-t. Hiányzó id-t generál.

    DuplicateVideoIdeaError-t dob, ha időközben már létrejött egy video_idea ugyanahhoz a
    feed_item_id-hoz (lásd idx_video_ideas_feed_item_unique + a DuplicateVideoIdeaError
    docstringjét a race-condition háttérért).
    """
    idea_id = idea.get("id") or uuid.uuid4().hex[:12]
    row = {
        "id": idea_id,
        "voice": idea["voice"],
        "feed_item_id": idea.get("feed_item_id"),
        "title": idea.get("title"),
        "hook": idea["hook"],
        "talking_points": idea.get("talking_points") or [],
        "closing_thought": idea.get("closing_thought"),
        "suggested_caption": idea.get("suggested_caption"),
        "estimated_duration_seconds": idea.get("estimated_duration_seconds"),
        "status": idea.get("status", "drafted"),
        "fabrication_risk": bool(idea.get("fabrication_risk")),
    }
    if idea.get("fabrication_reason"):
        row["fabrication_reason"] = idea["fabrication_reason"]
    try:
        _c(client).table(TABLE).upsert(row).execute()
    except APIError as exc:
        if exc.code == "23505" or "duplicate key" in str(exc.message or "").lower():
            raise DuplicateVideoIdeaError(idea.get("feed_item_id")) from exc
        raise
    return idea_id


def get_video_idea(idea_id: str, client: Client | None = None) -> dict[str, Any] | None:
    resp = _c(client).table(TABLE).select("*").eq("id", idea_id).limit(1).execute()
    return cast("dict[str, Any] | None", (resp.data or [None])[0])


def has_video_idea_for_feed_item(feed_item_id: str, client: Client | None = None) -> bool:
    """Van-e már video_idea ehhez a hírhez? (dedup: max 1 video_idea / feed_item)."""
    if not feed_item_id:
        return False
    resp = _c(client).table(TABLE).select("id").eq("feed_item_id", feed_item_id).limit(1).execute()
    return bool(resp.data)


def update_status(idea_id: str, status: str, client: Client | None = None, **fields: Any) -> None:
    """Frissíti a video_ideas.status-t (és opcionális mezőket: approved_at, filmed_at, ...)."""
    patch: dict[str, Any] = {"status": status}
    patch.update({k: v for k, v in fields.items() if v is not None})
    _c(client).table(TABLE).update(patch).eq("id", idea_id).execute()


def mark_approved(idea_id: str, by: str, client: Client | None = None) -> None:
    update_status(idea_id, "approved", client=client, approved_at=_now(), approved_by=by)


def mark_filmed(idea_id: str, by: str, client: Client | None = None) -> None:
    """A human ténylegesen leforgatta a videót (manuális log, ugyanaz az elv mint
    posts.mark_posted -- a tényleges produkciót senki mást nem tudja automatikusan detektálni)."""
    update_status(idea_id, "filmed", client=client, filmed_at=_now(), filmed_by=by)


def mark_edited(idea_id: str, edited_notes: str, client: Client | None = None) -> None:
    current = get_video_idea(idea_id, client=client) or {}
    update_status(
        idea_id,
        "edited",
        client=client,
        edited_notes=edited_notes,
        edit_count=(current.get("edit_count") or 0) + 1,
    )


def update_content(idea_id: str, idea: dict[str, Any], client: Client | None = None) -> None:
    """Regenerálás után: felülírja a tartalmi mezőket, státuszt visszaállítja 'drafted'-re."""
    patch: dict[str, Any] = {
        "status": "drafted",
        "title": idea.get("title"),
        "hook": idea.get("hook"),
        "talking_points": idea.get("talking_points") or [],
        "closing_thought": idea.get("closing_thought"),
        "suggested_caption": idea.get("suggested_caption"),
        "estimated_duration_seconds": idea.get("estimated_duration_seconds"),
        "fabrication_risk": bool(idea.get("fabrication_risk")),
        "fabrication_reason": idea.get("fabrication_reason") or None,
    }
    _c(client).table(TABLE).update(patch).eq("id", idea_id).execute()
