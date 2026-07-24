"""Kereszt-metsző segédek — ids, json_repair, logging.

Ezek a modulok SEMMILYEN projekt-belső (src.*) modult nem importálnak, így bárhonnan
biztonságosan használhatók import-ciklus nélkül. A történelmi otthonukban (storage.models,
generators.base_generator) visszafelé kompatibilis re-export marad.
"""

from __future__ import annotations

from src.utils.ids import make_id, utcnow_iso
from src.utils.json_repair import repair_and_parse
from src.utils.logging import setup_logging

__all__ = ["make_id", "utcnow_iso", "repair_and_parse", "setup_logging"]
