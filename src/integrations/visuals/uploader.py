"""Vizuál feltöltő — Supabase Storage ('visuals' bucket), lokális fallbackkel.

A komponált (szöveg-overlay-es) képet publikus URL-re tölti, hogy a Telegram és az
értékelő is elérje. Ha a Storage nem elérhető, a lokális assets/generated/ utat adja vissza.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from src.core.storage.db import get_client, has_service_key

logger = logging.getLogger(__name__)

BUCKET = "visuals"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
GENERATED_DIR = PROJECT_ROOT / "assets" / "generated"


def ensure_bucket(name: str = BUCKET) -> bool:
    """Létrehozza a publikus bucketet, ha még nincs. True, ha elérhető/létrejött."""
    try:
        client = get_client(use_service_key=has_service_key())
        existing = {b.name if hasattr(b, "name") else b.get("name") for b in client.storage.list_buckets()}
        if name in existing:
            return True
        client.storage.create_bucket(name, options={"public": True})
        logger.info("[storage] '%s' bucket létrehozva (public).", name)
        return True
    except Exception as exc:
        msg = str(exc)
        if "already exists" in msg.lower() or "duplicate" in msg.lower():
            return True
        logger.warning("[storage] bucket nem elérhető (%s) — lokális fallback.", msg[:120])
        return False


def upload_visual(local_path: str, dest_name: str | None = None) -> str:
    """Feltölti a képet a 'visuals' bucketbe és visszaadja a publikus URL-t.

    Bármilyen hiba esetén a lokális utat adja vissza (a hívó kép nélkül/lokálisan kezeli).
    """
    p = Path(local_path)
    dest = dest_name or p.name
    try:
        client = get_client(use_service_key=has_service_key())
        content_type = mimetypes.guess_type(dest)[0] or "image/png"
        data = p.read_bytes()
        storage = client.storage.from_(BUCKET)
        try:
            storage.upload(dest, data, {"content-type": content_type, "upsert": "true"})
        except Exception as exc:
            # upsert nélküli újrapróbálkozás, ha a fájl már létezik
            if "exist" in str(exc).lower():
                storage.update(dest, data, {"content-type": content_type})
            else:
                raise
        url = storage.get_public_url(dest)
        return url
    except Exception as exc:
        logger.warning("[storage] feltöltés sikertelen (%s) — lokális út marad.", str(exc)[:120])
        return str(p)
