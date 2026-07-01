"""OAuth token tárolás fiókonként (Supabase `tokens` tábla).

A LinkedIn access token ~60 napig él. expires_at-et tárolunk; get_token
figyelmeztet, ha < 7 nap van hátra (refresh / újra-auth szükséges).

Tábla (scripts/migration_8_tokens.sql):
    tokens(account_id PK, access_token, expires_at, created_at)

Az author_urn (urn:li:person/organization) NEM itt él, hanem a
config/accounts.yml `linkedin_urn` mezőjében.

Önálló füstteszt:
    python -m src.publishers.token_store
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.storage.db import get_client, has_service_key

logger = logging.getLogger(__name__)

WARN_THRESHOLD_DAYS = 7
DEFAULT_TTL_DAYS = 60
TABLE = "tokens"


def _c(client=None):
    return client or get_client(use_service_key=has_service_key())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(expires_at: str | datetime | None) -> str:
    """expires_at normalizálás ISO stringgé; None esetén alapértelmezett TTL (+60 nap)."""
    if expires_at is None:
        return (_now() + timedelta(days=DEFAULT_TTL_DAYS)).isoformat()
    if isinstance(expires_at, datetime):
        return expires_at.astimezone(timezone.utc).isoformat()
    return str(expires_at)


def _days_left(expires_at: str | None) -> float | None:
    if not expires_at:
        return None
    exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (exp - _now()).total_seconds() / 86400


def save_token(
    account_id: str,
    access_token: str,
    expires_at: str | datetime | None = None,
    client=None,
) -> dict[str, Any]:
    """Token mentése/frissítése (upsert) account_id-ra a `tokens` táblába.

    account_id: 'david' | 'adam' | 'plansmart'.
    expires_at: ISO string vagy datetime; ha None, +60 nap az alapértelmezett TTL.
    """
    row = {
        "account_id": account_id,
        "access_token": access_token,
        "expires_at": _to_iso(expires_at),
        "created_at": _now().isoformat(),
    }
    _c(client).table(TABLE).upsert(row).execute()
    logger.info("Token mentve: account=%s expires_at=%s", account_id, row["expires_at"])
    return row


def get_token(account_id: str, client=None) -> dict[str, Any] | None:
    """Token lekérése account_id-ra. None, ha nincs. Warn-ol, ha < 7 nap / lejárt."""
    resp = _c(client).table(TABLE).select("*").eq("account_id", account_id).limit(1).execute()
    row = (resp.data or [None])[0]
    if not row:
        logger.warning("Nincs tárolt token: account=%s", account_id)
        return None

    days = _days_left(row.get("expires_at"))
    if days is not None:
        if days <= 0:
            logger.warning("LEJÁRT token: account=%s (%.1f nap)", account_id, days)
        elif days < WARN_THRESHOLD_DAYS:
            logger.warning(
                "Token hamarosan lejár: account=%s (%.1f nap van hátra) — refresh kell",
                account_id, days,
            )
    return row


def token_status(account_id: str, client=None) -> dict[str, Any]:
    """Token állapot a /status parancshoz — warning-spam nélkül, hibatűrően.

    Visszaad: {account, exists, days_left, expires_at} vagy {account, error}.
    """
    try:
        resp = _c(client).table(TABLE).select("expires_at").eq("account_id", account_id).limit(1).execute()
    except Exception as exc:  # pl. a tokens tábla még nincs migrálva
        return {"account": account_id, "error": str(exc)[:80]}
    row = (resp.data or [None])[0]
    if not row:
        return {"account": account_id, "exists": False}
    return {
        "account": account_id,
        "exists": True,
        "days_left": _days_left(row.get("expires_at")),
        "expires_at": row.get("expires_at"),
    }


if __name__ == "__main__":
    import os

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    # Füstteszt: lekér egy ismert account tokent (ha van a DB-ben).
    for acc in ("david", "adam", "plansmart"):
        tok = get_token(acc)
        logger.info("%s -> %s", acc, "van token" if tok else "nincs token")
