"""Tartalom-stratégia motor — eloszlás-alapú content_type választás + seed források.

A generator_worker és a /strategy parancs is ezt használja:
  • load_strategy()                      — config/content_strategy.yml
  • next_content_type(account, counts)   — a legnagyobb hiányú típus (target - actual)
  • get_seed(content_type, voice, used)  — manual_instruction seed a nem-news típusokhoz

A content_type-ok: educational | workshop_promo | case_study | ai_news.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from src.core.config import loaders

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG = PROJECT_ROOT / "config" / "content_strategy.yml"
PROMPTS = PROJECT_ROOT / "prompts"

CONTENT_TYPES = ["educational", "workshop_promo", "case_study", "ai_news"]

# Magyar címkék a /strategy kimenethez.
TYPE_LABEL = {
    "educational": "oktató",
    "workshop_promo": "workshop",
    "case_study": "case study",
    "ai_news": "news",
}


def load_strategy() -> dict[str, Any]:
    """content_strategy.yml betöltése (a közös, cache-elt config.loaders-en át)."""
    return loaders.load_content_strategy()


def _load_yaml(path: str, key: str) -> list[dict[str, Any]]:
    """Egy prompts/config YAML adott kulcsú listája (a közös, cache-elt betöltőn át)."""
    return cast(list[dict[str, Any]], loaders.load_yaml(path).get(key, []))


def target_distribution(account: str) -> dict[str, float]:
    return dict(load_strategy().get("content_distribution", {}).get(account, {}))


def weekly_target(account: str) -> int:
    freq = load_strategy().get("posting_frequency", {}).get("per_account_per_week", {})
    return int(freq.get(account, 0))


def accounts() -> list[str]:
    return list(load_strategy().get("content_distribution", {}).keys())


def _voice_ok(voice_fit: Any, voice: str) -> bool:
    """voice_fit lehet 'all', egy string, vagy lista — illik-e a megadott voice-ra."""
    if isinstance(voice_fit, str):
        voice_fit = [voice_fit]
    vf = [str(v).lower() for v in (voice_fit or [])]
    return "all" in vf or voice.lower() in vf


def next_content_type(account: str, counts: dict[str, int]) -> str:
    """A legnagyobb hiányú content_type (target_frac - actual_frac).

    counts: az account heti posztjainak content_type szerinti darabszáma.
    Ha még nincs poszt a héten, a legnagyobb target súlyú típust adja vissza.
    """
    target = target_distribution(account)
    if not target:
        return "ai_news"
    total = sum(counts.values())
    best, best_gap = None, None
    for ctype in CONTENT_TYPES:
        if ctype not in target:
            continue
        actual_frac = (counts.get(ctype, 0) / total) if total else 0.0
        gap = target[ctype] - actual_frac
        # Tie-break: nagyobb gap → nagyobb target → CONTENT_TYPES sorrend (stabil).
        key = (gap, target[ctype])
        if best_gap is None or key > best_gap:
            best, best_gap = ctype, key
    return best or "ai_news"


def distribution_report(account: str, counts: dict[str, int]) -> dict[str, Any]:
    """A /strategy parancshoz: cél vs tényleges eloszlás + következő javaslat."""
    target = target_distribution(account)
    total = sum(counts.values())
    actual = {ctype: (counts.get(ctype, 0) / total if total else 0.0) for ctype in target}
    return {
        "account": account,
        "target": target,
        "actual": actual,
        "counts": {c: counts.get(c, 0) for c in target},
        "total_posts": total,
        "weekly_target": weekly_target(account),
        "next_type": next_content_type(account, counts),
    }


# ── Seed források (nem-news content_type-ok) ───────────────────────────
def _educational_topics() -> list[dict[str, Any]]:
    return _load_yaml("prompts/educational_topics.yml", "educational_topics")


def _case_studies() -> list[dict[str, Any]]:
    return _load_yaml("prompts/case_studies.yml", "case_studies")


def _workshop_topics() -> list[dict[str, Any]]:
    return _load_yaml("prompts/workshop_topics.yml", "workshop_topics")


def _pick(
    entries: list[dict[str, Any]], voice: str, key_field: str, used: set[str]
) -> dict[str, Any] | None:
    """Első olyan entry, ami illik a voice-ra ÉS a kulcsa nincs a 'used' halmazban."""
    for e in entries:
        if _voice_ok(e.get("voice_fit"), voice) and str(e.get(key_field, "")) not in used:
            return e
    return None


def _seed_instruction(content_type: str, entry: dict[str, Any]) -> tuple[str, str]:
    """(instruction_text, seed_key) felépítése egy seed entry-ből."""
    if content_type == "educational":
        key = entry["topic"]
        txt = (
            "Content type: oktató (educational) poszt.\n"
            f"Téma: {entry['topic']}\n"
            f"Kategória: {entry.get('category', '')}\n"
            "Írj egy tanító, lépésről lépésre posztot erről a témáról a saját hangodon."
        )
    elif content_type == "case_study":
        key = entry["client"]
        txt = (
            "Content type: esettanulmány (case_study).\n"
            f"Ügyfél (anonimizált): {entry['client']}\n"
            f"Probléma: {entry['problem']}\n"
            f"Megoldás: {entry['solution']}\n"
            f"Eredmény: {entry['result']}\n"
            "Írj egy konkrét, számokkal alátámasztott esettanulmány posztot."
        )
    elif content_type == "workshop_promo":
        key = entry["topic"]
        txt = (
            "Content type: workshop hirdetés (workshop_promo).\n"
            f"Workshop: {entry['topic']}\n"
            f"Kinek: {entry.get('audience', '')}\n"
            f"Formátum: {entry.get('format', '')}\n"
            f"Időpont: {entry.get('date_window', '')}\n"
            "Írj egy érték-vezérelt, NEM nyomulós workshop-hirdetést."
        )
    else:
        key, txt = "", ""
    return txt, key


def get_educational_seed_by_category(
    category: str, voice: str, used_keys: set[str]
) -> dict[str, Any] | None:
    """Educational seed egy adott kategóriából (pl. 'consultant_builder') — vagy None.

    A morning_post_worker 'consultant_builder' content_type-hoz használja: az
    educational_topics.yml-ből csak az adott category-jú, voice-illő, nem-használt topicot.
    """
    entries = [e for e in _educational_topics() if e.get("category") == category]
    entry = _pick(entries, voice, "topic", used_keys)
    if entry is None:
        return None
    instruction, seed_key = _seed_instruction("educational", entry)
    return {
        "type": "manual_instruction",
        "instruction": instruction,
        "content_type": "educational",
        "seed_key": seed_key,
        "voice": voice,
        "platform": "linkedin",
    }


def get_seed(content_type: str, voice: str, used_keys: set[str]) -> dict[str, Any] | None:
    """manual_instruction seed a nem-news típusokhoz.

    Visszaad: {"type":"manual_instruction","instruction","content_type","seed_key"}
    vagy None, ha nincs elérhető (nem-használt, voice-illő) seed. ai_news → None
    (azt a hívó a feed_items-ből oldja meg).
    """
    if content_type == "ai_news":
        return None
    sources = {
        "educational": (_educational_topics(), "topic"),
        "case_study": (_case_studies(), "client"),
        "workshop_promo": (_workshop_topics(), "topic"),
    }
    entries, key_field = sources.get(content_type, ([], ""))
    entry = _pick(entries, voice, key_field, used_keys)
    if entry is None:
        return None
    instruction, seed_key = _seed_instruction(content_type, entry)
    return {
        "type": "manual_instruction",
        "instruction": instruction,
        "content_type": content_type,
        "seed_key": seed_key,
        "voice": voice,
        "platform": "linkedin",
    }
