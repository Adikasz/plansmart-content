"""prospects tárolási réteg (Phase 18) — a LinkedIn kapcsolatépítés jelölt-adatbázisa.

Ugyanaz a minta mint a posts.py: get_client(use_service_key=has_service_key()) + upsert/update.
A tábla sémáját a scripts/migration_18_prospects.sql hozza létre.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.db import get_client, has_service_key, table_exists

logger = logging.getLogger(__name__)

TABLE = "prospects"


def _c(client=None):
    return client or get_client(use_service_key=has_service_key())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def table_ready(client=None) -> bool:
    """True, ha a prospects tábla létezik (a migráció lefutott a Supabase-ben)."""
    return table_exists(_c(client), TABLE)


def insert_prospect(p: dict[str, Any], client=None) -> str:
    """Beszúr (upsert) egy prospect sort; visszaadja az id-t. Hiányzó id-t generál."""
    pid = p.get("id") or uuid.uuid4().hex[:12]
    row = {
        "id": pid,
        "name": p["name"],
        "title": p.get("title"),
        "company": p.get("company"),
        "company_size_estimate": p.get("company_size_estimate"),
        "country": p.get("country"),
        "city": p.get("city"),
        "linkedin_url": p.get("linkedin_url") or None,  # sosem fabrikálunk URL-t
        "category": p["category"],
        "voice": p.get("voice"),
        "relevance_notes": p.get("relevance_notes"),
        "connection_note_draft": p.get("connection_note_draft"),
        "note_language": p.get("note_language"),
        "status": p.get("status", "researched"),
        "source": p.get("source"),
        "updated_at": _now(),
    }
    row = {k: v for k, v in row.items() if v is not None}
    _c(client).table(TABLE).upsert(row).execute()
    return pid


def save_note(pid: str, note: str, voice: str, note_language: str, client=None) -> None:
    """A generált kapcsolat-üzenet mentése + status='note_drafted'."""
    _c(client).table(TABLE).update({
        "connection_note_draft": note,
        "voice": voice,
        "note_language": note_language,
        "status": "note_drafted",
        "updated_at": _now(),
    }).eq("id", pid).execute()


def set_status(pid: str, status: str, client=None, **fields: Any) -> None:
    """Státusz frissítés (+ opcionális mezők, pl. sent_at)."""
    patch: dict[str, Any] = {"status": status, "updated_at": _now()}
    patch.update({k: v for k, v in fields.items() if v is not None})
    _c(client).table(TABLE).update(patch).eq("id", pid).execute()


def mark_sent(pid: str, client=None) -> None:
    """A human manuálisan elküldte a kapcsolatkérést → status='sent', sent_at=now()."""
    set_status(pid, "sent", client=client, sent_at=_now())


def by_status(status: str, client=None) -> list[dict[str, Any]]:
    resp = (_c(client).table(TABLE).select("*")
            .eq("status", status).order("added_at").execute())
    return resp.data or []


def get(pid: str, client=None) -> dict[str, Any] | None:
    resp = _c(client).table(TABLE).select("*").eq("id", pid).limit(1).execute()
    return (resp.data or [None])[0]


def count_by_status(status: str, client=None) -> int:
    resp = _c(client).table(TABLE).select("id").eq("status", status).execute()
    return len(resp.data or [])


def all_name_company(client=None) -> list[dict[str, Any]]:
    """MINDEN prospect (bármely státusz) neve+cége — a per-full-run de-dup forrása.

    A Phase 19 batch-orchestrator ebből építi a már-lefedett kulcsokat, hogy egy céget/
    embert az EGÉSZ futásban (és a korábbi futásokból, pl. a logisztika 5-ből) csak egyszer
    vegyünk fel.
    """
    resp = _c(client).table(TABLE).select("name,company,category,voice").execute()
    return resp.data or []


def added_count_since(since_iso: str, client=None) -> int:
    """Új kutatott jelöltek száma egy időpont óta (added_at -- sosem íródik felül)."""
    resp = _c(client).table(TABLE).select("id").gte("added_at", since_iso).execute()
    return len(resp.data or [])


def sent_count_since(since_iso: str, client=None) -> int:
    """Ténylegesen (/mark_sent-tel) elküldött jelöltek száma egy időpont óta (sent_at -- csak a
    mark_sent állítja be, később nem íródik felül, tehát pontos, nem közelítés)."""
    resp = _c(client).table(TABLE).select("id").gte("sent_at", since_iso).execute()
    return len(resp.data or [])


def approved_to_send_count_since(since_iso: str, client=None) -> int:
    """Jóváhagyva-küldésre-VAGY-tovább jelöltek, akiknek a legutóbbi státuszváltása erre az
    időszakra esik (updated_at).

    Közelítés: a prospects.updated_at minden státuszváltásnál felülíródik, tehát ha egy
    jelöltet a múlt héten hagytak jóvá, de csak MA küldték el, ez a lekérdezés a mai napon
    "sent"-ként (nem "approved_to_send"-ként) fogja számolni -- nincs külön esemény-napló a
    küldés ELŐTTI szakaszra (a prospect_interactions csak a küldés UTÁNI életciklust követi,
    lásd Part 2). Rövid (napi/heti) időszakokra ez a torzítás elhanyagolható.
    """
    resp = (
        _c(client).table(TABLE).select("id")
        .in_("status", ["approved_to_send", "sent", "connected", "declined"])
        .gte("updated_at", since_iso).execute()
    )
    return len(resp.data or [])


def all_full(client=None) -> list[dict[str, Any]]:
    """MINDEN prospect a verifikációhoz/exporthoz szükséges mezőkkel.

    A Phase 19 verifikációs pass (prospect_verifier) és a connect-lista export ezt hívja —
    a per-sor függetlenség-ellenőrzéshez (id/név/pozíció/cég/ország) és a note-mentes
    exporthoz (kategória/voice) kell, a nagy `relevance_notes`/`connection_note_draft`
    mezők nélkül (kisebb payload).
    """
    resp = (_c(client).table(TABLE)
            .select("id,name,title,company,country,category,voice,status")
            .execute())
    return resp.data or []
