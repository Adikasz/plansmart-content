"""Phase 1 acceptance teszt: Supabase kapcsolat + tábla-ellenőrzés.

Ellenőrzi, hogy a `scripts/schema.sql`-ben definiált 6 tábla létezik-e.

Futtatás:
    python scripts/test_db.py

Sikeres kimenet:
    Supabase OK, 6 tábla létezik
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Projekt gyökér a path-ra, hogy a `src` csomag importálható legyen szkriptként futtatva is.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.storage.db import get_client, has_service_key, table_exists  # noqa: E402

logger = logging.getLogger("test_db")

# A schema.sql-ben ténylegesen definiált táblák.
# Megjegyzés: nincs külön `metrics` tábla — az engagement adatok a `published`
# táblába vannak beágyazva; a monitoringot a source_stats + events fedi le.
REQUIRED_TABLES = [
    "feed_items",
    "posts",
    "approvals",
    "published",
    "source_stats",
    "events",
]


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(message)s",
    )

    try:
        use_service = has_service_key()
        client = get_client(use_service_key=use_service)
    except RuntimeError as exc:
        logger.error("[HIBA] Konfiguracios hiba: %s", exc)
        return 1
    except Exception as exc:  # pl. supabase SupabaseException: Invalid API key
        logger.error("[HIBA] Nem sikerult letrehozni a Supabase klienst: %s", exc)
        logger.error(
            "Ellenorizd a SUPABASE_URL / SUPABASE_KEY / SUPABASE_SERVICE_KEY ertekeket "
            "a .env-ben — valodi kulcsok kellenek, nem a .env.example placeholderei."
        )
        return 1

    logger.info("Kapcsolodas Supabase-hez (%s key)...", "service" if use_service else "anon")

    existing: list[str] = []
    missing: list[str] = []
    try:
        for table in REQUIRED_TABLES:
            if table_exists(client, table):
                existing.append(table)
                logger.info("  [OK]   %s", table)
            else:
                missing.append(table)
                logger.info("  [HIANYZIK] %s", table)
    except Exception as exc:
        logger.error("[HIBA] Hiba a tablak lekerdezese kozben: %s", exc)
        return 1

    if missing:
        logger.error(
            "[HIBA] %d/%d tabla hianyzik: %s",
            len(missing),
            len(REQUIRED_TABLES),
            ", ".join(missing),
        )
        logger.error("Futtasd le a scripts/schema.sql-t a Supabase SQL Editorban.")
        return 1

    logger.info("Supabase OK, %d tabla letezik", len(existing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
