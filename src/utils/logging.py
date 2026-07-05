"""Egységes logging beállítás — a main.py és a standalone modulok basicConfig-ját váltja ki.

A projekt konvenciója (CLAUDE.md: „Print helyett logging"):
  • format="%(message)s", a szint a LOG_LEVEL env-ből (alap: INFO),
  • a zajos third-party loggerek (aiogram, apscheduler, aiohttp, httpx) WARNING-ra halkítva,
  • a stdout/stderr UTF-8-ra hangolva (Windows-barát, a magyar/emoji log ne törjön el).

Fokozatosan bevezethető: a meglévő inline basicConfig hívások ezzel válthatók ki, de
nem kötelező — a modul önmagában (import-mellékhatás nélkül) használható.
"""
from __future__ import annotations

import logging
import os
import sys

# A projektben ismerten zajos loggerek (a runtime logot elárasztanák DEBUG/INFO szinten).
NOISY_LOGGERS = ("aiogram", "apscheduler", "aiohttp", "httpx")


def setup_logging(
    level: str | None = None,
    *,
    quiet_libs: bool = True,
    reconfigure_streams: bool = True,
) -> None:
    """Beállítja a gyökér loggert a projekt konvenciója szerint.

    Args:
        level: log szint (pl. "DEBUG"); None esetén a LOG_LEVEL env, alapból "INFO".
        quiet_libs: ha True, a NOISY_LOGGERS loggereket WARNING-ra állítja.
        reconfigure_streams: ha True, a stdout/stderr-t UTF-8-ra hangolja (best-effort).
    """
    if reconfigure_streams:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass

    logging.basicConfig(
        level=(level or os.environ.get("LOG_LEVEL", "INFO")),
        format="%(message)s",
    )

    if quiet_libs:
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
