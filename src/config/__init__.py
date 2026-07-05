"""Moduláris, tipizált konfigurációs réteg.

  • settings.py — env változók egy validált Pydantic objektumban (Settings, get_settings)
  • loaders.py  — cache-elt YAML betöltők (a szétszórt yaml.safe_load kiváltása)
"""
from __future__ import annotations

from src.config.loaders import (
    load_accounts,
    load_config,
    load_content_strategy,
    load_prompt_list,
    load_scoring,
    load_sources,
    load_yaml,
)
from src.config.settings import Settings, get_settings

__all__ = [
    "Settings",
    "get_settings",
    "load_yaml",
    "load_config",
    "load_scoring",
    "load_accounts",
    "load_sources",
    "load_content_strategy",
    "load_prompt_list",
]
