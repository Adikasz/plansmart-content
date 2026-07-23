"""Tipizált, cache-elt YAML konfiguráció-betöltők — a szétszórt `yaml.safe_load` kiváltása.

Több modul (keyword_filter, content_strategy, rss_collector, telegram_bot) külön-külön
hívott `yaml.safe_load`-ot, saját útvonal-számítással. Ez a modul EGY közös, cache-elt,
hibatűrő belépési pontot ad. Behavior-preserving: `load_yaml(path)` == `yaml.safe_load(path) or {}`.

A relatív útvonalak a repo gyökeréhez képest oldódnak fel (ahogy a hívó modulok is tették).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPTS_DIR = PROJECT_ROOT / "prompts"


@lru_cache(maxsize=64)
def load_yaml(path: str | Path) -> dict[str, Any]:
    """Egy YAML fájl betöltése dict-ként (cache-elve). Üres/None tartalom -> {}.

    Relatív útvonal a repo gyökeréhez képest oldódik fel.
    """
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(name: str) -> dict[str, Any]:
    """config/<name> betöltése (pl. load_config('scoring.yml'))."""
    return load_yaml(CONFIG_DIR / name)


def load_scoring() -> dict[str, Any]:
    return load_config("scoring.yml")


def load_accounts() -> dict[str, Any]:
    """config/accounts.yml `accounts` szekciója (üres dict, ha hiányzik)."""
    return load_config("accounts.yml").get("accounts", {})


def load_sources() -> dict[str, Any]:
    return load_config("sources.yml")


def load_content_strategy() -> dict[str, Any]:
    return load_config("content_strategy.yml")


def load_prompt_list(filename: str, key: str) -> list[dict]:
    """prompts/<filename> adott kulcsú listája (pl. 'educational_topics.yml', 'educational_topics')."""
    return load_yaml(PROMPTS_DIR / filename).get(key, [])
