"""reactions tárolási réteg (Phase 19) — manuális-módú reakció-asszisztens.

Ugyanaz a minta mint a prospects.py: get_client(use_service_key=has_service_key())
+ upsert/update. A tábla sémáját a scripts/migration_19_reactions.sql hozza létre.

A tábla a Telegramban generált komment/DM-válasz-javaslatokat és a döntéseket
(drafted/approved/edited/skipped) rögzíti — sem itt, sem máshol NINCS automatikus
LinkedIn akció; a válasz elküldése kézzel történik.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.db import get_client, has_service_key, table_exists

logger = logging.getLogger(__name__)

TABLE = "reactions"


def _c(client=None):
    return client or get_client(use_service_key=has_service_key())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def table_ready(client=None) -> bool:
    """True, ha a reactions tábla létezik (a migráció lefutott a Supabase-ben)."""
    return table_exists(_c(client), TABLE)


def insert_reaction(r: dict[str, Any], client=None) -> str:
    """Beszúr (upsert) egy reaction sort; visszaadja az id-t. Hiányzó id-t generál."""
    rid = r.get("id") or uuid.uuid4().hex[:12]
    row = {
        "id": rid,
        "voice": r["voice"],
        "type": r["type"],
        "incoming_text": r["incoming_text"],
        "context_text": r.get("context_text"),
        "our_reply_draft": r.get("our_reply_draft"),
        "classification": r.get("classification"),
        "language": r.get("language"),
        "status": r.get("status", "drafted"),
        "updated_at": _now(),
    }
    row = {k: v for k, v in row.items() if v is not None}
    _c(client).table(TABLE).upsert(row).execute()
    return rid


def update_draft(rid: str, draft: str, status: str = "edited", client=None) -> None:
    """A válasz-draft frissítése (szerkesztés/regenerálás) + státusz."""
    _c(client).table(TABLE).update({
        "our_reply_draft": draft,
        "status": status,
        "updated_at": _now(),
    }).eq("id", rid).execute()


def set_status(rid: str, status: str, client=None, **fields: Any) -> None:
    """Státusz frissítés (+ opcionális mezők, pl. sent_at)."""
    patch: dict[str, Any] = {"status": status, "updated_at": _now()}
    patch.update({k: v for k, v in fields.items() if v is not None})
    _c(client).table(TABLE).update(patch).eq("id", rid).execute()


def mark_sent(rid: str, client=None) -> None:
    """A human manuálisan elküldte a választ → sent_at=now() (a státuszt nem bántjuk)."""
    _c(client).table(TABLE).update({"sent_at": _now(), "updated_at": _now()}).eq("id", rid).execute()


def get(rid: str, client=None) -> dict[str, Any] | None:
    resp = _c(client).table(TABLE).select("*").eq("id", rid).limit(1).execute()
    return (resp.data or [None])[0]


def by_status(status: str, client=None) -> list[dict[str, Any]]:
    resp = (_c(client).table(TABLE).select("*")
            .eq("status", status).order("created_at").execute())
    return resp.data or []


def count_by_status(status: str, client=None) -> int:
    resp = _c(client).table(TABLE).select("id").eq("status", status).execute()
    return len(resp.data or [])
