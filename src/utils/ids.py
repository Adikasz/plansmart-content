"""Determinisztikus azonosító- és idő-segédek (tiszta függvények, nincs I/O).

Korábban a src/core/storage/models.py-ban laktak; ide kerültek, hogy több domain
(collectors, storage) egy közös, önállóan tesztelhető helyről használja őket.
A models.py visszafelé kompatibilisen re-exportálja a `make_id` / `_utcnow_iso` neveket.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def make_id(url: str) -> str:
    """URL -> determinisztikus rövid hash (sha256 első 16 karaktere)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def utcnow_iso() -> str:
    """Aktuális UTC időbélyeg ISO-8601 formátumban (timezone-aware)."""
    return datetime.now(timezone.utc).isoformat()


# Visszafelé kompatibilis privát alias — a models.py default_factory-ja ezen a néven hívta.
_utcnow_iso = utcnow_iso
