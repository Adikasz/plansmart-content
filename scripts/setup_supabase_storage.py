"""Supabase Storage beállítás — 'visuals' publikus bucket létrehozása.

    python -m scripts.setup_supabase_storage
"""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from src.visuals.uploader import BUCKET, ensure_bucket

load_dotenv(override=False)
logger = logging.getLogger("setup_supabase_storage")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")

    ok = ensure_bucket()
    if ok:
        logger.info("✓ Storage kész: '%s' bucket elérhető (publikus olvasás).", BUCKET)
    else:
        logger.warning("✗ Storage nem elérhető — a pipeline a lokális assets/generated/ fallbackre vált.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
