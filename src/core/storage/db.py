"""Supabase kapcsolat — az egy igazság forrása.

A modul a `.env`-ből tölti a credentialeket (sosem hardcode), és egy
cache-elt Supabase klienst ad vissza. Server-side kódhoz a service key,
egyébként az anon key használandó.

Önállóan futtatható gyors kapcsolat-teszthez:
    python -m src.core.storage.db
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from dotenv import load_dotenv
from postgrest.exceptions import APIError
from supabase import Client, create_client

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)

# A .env-et import-időben egyszer betöltjük; meglévő env változókat nem írunk felül.
load_dotenv(override=False)


def _require_env(name: str) -> str:
    """Kötelező env változó kiolvasása, beszédes hibával ha hiányzik."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Hiányzó környezeti változó: {name}. " "Töltsd ki a .env fájlt a .env.example alapján."
        )
    return value


@lru_cache(maxsize=2)
def get_client(use_service_key: bool = False) -> Client:
    """Cache-elt Supabase kliens.

    Args:
        use_service_key: ha True, a SERVICE_KEY-t használja (RLS bypass,
            csak server-side workereknek). Egyébként az anon SUPABASE_KEY.
    """
    url = _require_env("SUPABASE_URL")
    key_name = "SUPABASE_SERVICE_KEY" if use_service_key else "SUPABASE_KEY"
    key = _require_env(key_name)
    logger.debug("Supabase kliens létrehozása (service_key=%s)", use_service_key)
    return create_client(url, key)


def has_service_key() -> bool:
    """True, ha a service key elérhető a környezetben."""
    return bool(os.environ.get("SUPABASE_SERVICE_KEY"))


def table_exists(client: Client, table: str) -> bool:
    """Megnézi, hogy egy tábla létezik-e (legfeljebb 1 sort kér le).

    Megjegyzés: a supabase 2.5.0 / postgrest 0.16.x `select()` NEM ismeri a `head`
    kwarg-ot, ezért `limit(1)`-et használunk. Csak az APIError-t (pl. hiányzó tábla)
    kezeljük "nincs ilyen tábla"-ként — minden más hiba (pl. TypeError, hálózat)
    feljebb propagál, hogy ne rejtsen el kódhibát.
    """
    try:
        client.table(table).select("*").limit(1).execute()
        return True
    except APIError as exc:
        logger.debug("Tábla nem elérhető: %s (%s)", table, exc)
        return False


def ping(use_service_key: bool | None = None) -> bool:
    """Egyszerű kapcsolat-ellenőrzés a feed_items táblán keresztül."""
    if use_service_key is None:
        use_service_key = has_service_key()
    client = get_client(use_service_key=use_service_key)
    return table_exists(client, "feed_items")


if __name__ == "__main__":
    setup_logging()
    ok = ping()
    logger.info("Supabase ping: %s", "OK" if ok else "SIKERTELEN")
    raise SystemExit(0 if ok else 1)
