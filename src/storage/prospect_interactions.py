"""prospect_interactions tárolási réteg (Phase 21) — a kapcsolatépítés élet-ciklus követése.

Ugyanaz a minta mint prospects.py: get_client(use_service_key=has_service_key()) + upsert/
insert, minden függvény client= paraméterrel tesztelhető. A tábla sémáját a
scripts/migration_21_prospect_tracking.sql hozza létre.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.db import get_client, has_service_key, table_exists

logger = logging.getLogger(__name__)

TABLE = "prospect_interactions"

# A 6 élet-ciklus típus (a migráció CHECK-je + a /update_prospect gombok) + 'note': kizárólag
# jegyzet, NEM stage-váltás -- a current_stage/status_summary a 'note' sorokat átugorja.
INTERACTION_TYPES = (
    "connection_sent", "connection_accepted", "replied",
    "meeting_booked", "went_cold", "not_interested", "note",
)
_STAGE_TYPES = tuple(t for t in INTERACTION_TYPES if t != "note")

# stage -> /prospects_status bucket felirat (went_cold + not_interested közös bucket-be esik,
# mert a userfacing /prospects_status specifikáció csak 5 bucket-et definiált).
_BUCKET_LABEL = {
    "connection_sent": "Küldve, nincs válasz",
    "connection_accepted": "Elfogadta, nem beszélgettünk",
    "replied": "Aktív beszélgetés",
    "meeting_booked": "Meeting/hívás foglalva",
    "went_cold": "Lezárva (nem érdekli)",
    "not_interested": "Lezárva (nem érdekli)",
}
BUCKET_ORDER = [
    "Küldve, nincs válasz", "Elfogadta, nem beszélgettünk", "Aktív beszélgetés",
    "Meeting/hívás foglalva", "Lezárva (nem érdekli)",
]


def _c(client=None):
    return client or get_client(use_service_key=has_service_key())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def table_ready(client=None) -> bool:
    """True, ha a prospect_interactions tábla létezik (a migration_21 lefutott)."""
    return table_exists(_c(client), TABLE)


def log_interaction(
    prospect_id: str, interaction_type: str, notes: str | None = None, client=None
) -> str:
    """Egy interakció rögzítése mai dátummal. interaction_type: lásd INTERACTION_TYPES."""
    if interaction_type not in INTERACTION_TYPES:
        raise ValueError(f"Ismeretlen interaction_type: {interaction_type!r}")
    row_id = uuid.uuid4().hex[:12]
    _c(client).table(TABLE).insert({
        "id": row_id,
        "prospect_id": prospect_id,
        "interaction_type": interaction_type,
        "notes": notes,
        "interaction_date": _now(),
    }).execute()
    return row_id


def delete_interaction(interaction_id: str, client=None) -> None:
    """Egy interakció-sor törlése (teszt/cleanup célra)."""
    _c(client).table(TABLE).delete().eq("id", interaction_id).execute()


def history_for(prospect_id: str, client=None) -> list[dict[str, Any]]:
    """Egy prospect ÖSSZES interakciója, legújabb elöl."""
    resp = (
        _c(client).table(TABLE).select("*")
        .eq("prospect_id", prospect_id).order("interaction_date", desc=True).execute()
    )
    return resp.data or []


def all_interactions(client=None) -> list[dict[str, Any]]:
    """MINDEN interakció, kis adatmennyiség -- kliens-oldali csoportosításhoz (status_summary)."""
    resp = _c(client).table(TABLE).select("prospect_id,interaction_type,interaction_date").execute()
    return resp.data or []


def current_stage(
    prospect_id: str, interactions: list[dict[str, Any]] | None = None, client=None
) -> str | None:
    """Egy prospect JELENLEGI stage-e: a legutóbbi NEM-'note' interaction_type. None, ha nincs."""
    rows = interactions if interactions is not None else history_for(prospect_id, client=client)
    stage_rows = [r for r in rows if r.get("interaction_type") in _STAGE_TYPES]
    if not stage_rows:
        return None
    stage_rows.sort(key=lambda r: r.get("interaction_date") or "", reverse=True)
    return stage_rows[0]["interaction_type"]


def bucket_label(stage: str | None) -> str:
    """Egy interaction_type -> /prospects_status bucket felirat (ismeretlen/None -> 1. bucket)."""
    return _BUCKET_LABEL.get(stage or "connection_sent", "Küldve, nincs válasz")


def status_summary(client=None) -> dict[str, int]:
    """/prospects_status bucket-számlálás.

    Minden "sent"-en túli prospect a legutóbbi (nem-'note') interaction_type-ja szerint egy
    bucket-be esik. Ha egy elküldött prospectnek MÉG nincs interaction sora (pl. migráció
    előtti /mark_sent, vagy /mark_sent hívás interaction-logolás nélkül), 'connection_sent'-
    ként számoljuk (biztonsági háló). A `connected`/`declined` prospects.status értékeket a
    kódbázis jelenleg sehol nem állítja be -- csak defenzíven szerepelnek a szűrésben.
    """
    from src.storage import prospects as prospects_store

    c = _c(client)
    sent_or_beyond = [
        p for p in prospects_store.all_full(client=c)
        if p.get("status") in ("sent", "connected", "declined")
    ]
    interactions_by_prospect: dict[str, list[dict]] = {}
    for row in all_interactions(client=c):
        interactions_by_prospect.setdefault(row["prospect_id"], []).append(row)

    counts: dict[str, int] = {label: 0 for label in BUCKET_ORDER}
    for p in sent_or_beyond:
        stage = current_stage(p["id"], interactions_by_prospect.get(p["id"], []))
        label = bucket_label(stage)
        counts[label] = counts.get(label, 0) + 1
    return counts


def replied_count_since(since_iso: str, client=None) -> int:
    """Hány EGYEDI prospect kapott 'replied' interakciót az adott időpont óta (/metrics-hez)."""
    resp = (
        _c(client).table(TABLE).select("prospect_id")
        .eq("interaction_type", "replied").gte("interaction_date", since_iso).execute()
    )
    return len({r["prospect_id"] for r in (resp.data or [])})
