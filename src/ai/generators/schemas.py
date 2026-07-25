"""A voice-generátor LLM-kimenetének tipizált sémája (Pydantic).

Ez a projekt LEGKOCKÁZATOSABB határa: a modell szabad-szöveg JSON-je. Eddig a
base_generator.generate() `dict[str, Any]`-t adott vissza, séma-ellenőrzés nélkül —
egy hiányzó/rossz típusú mező csak lejjebb, KeyError/None formában robbant.

`extra="allow"`: a voice promptok fejlődnek, az ismeretlen mezőket MEGŐRIZZÜK; csak a
hordozó mezőket (skip, linkedin.content) kötjük meg. A `validate_generated()` best-effort:
sosem dob, így a generate() forró útja visszafelé kompatibilis marad (a hívó a nyers
dict-tel is dolgozhat), miközben a séma-eltérés naplózhatóvá válik.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HookVariant(BaseModel):
    """Egy 3-2-1 hook variáns (A–E típus, szöveg, pontszám)."""

    model_config = ConfigDict(extra="allow")

    type: str | None = None
    text: str
    score: float | None = None


class HookVariants(BaseModel):
    """A hook-variáns generálás eredménye (variants + a kiválasztott legjobb)."""

    model_config = ConfigDict(extra="allow")

    variants: list[HookVariant] = Field(default_factory=list)
    best_index: int | None = None
    best: HookVariant | None = None


class LinkedInContent(BaseModel):
    """A LinkedIn poszt törzse a generátor kimenetében."""

    model_config = ConfigDict(extra="allow")

    content: str | None = None
    hashtags: list[str] = Field(default_factory=list)


class GeneratedPost(BaseModel):
    """Egy voice-poszt generátor-kimenete. `skip=True` esetén a hívó eldobja a hírt."""

    model_config = ConfigDict(extra="allow")

    skip: bool = False
    reason: str | None = None
    linkedin: LinkedInContent | None = None
    twitter: dict[str, Any] | None = None
    hook_variants: HookVariants | None = None
    text_quality: dict[str, Any] | None = None


def validate_generated(data: dict[str, Any]) -> tuple[GeneratedPost | None, str | None]:
    """Best-effort validáció a generate() forró útjához.

    Returns:
        (GeneratedPost, None) ha érvényes; (None, hibaüzenet) ha nem. SOSEM dob —
        a hívó továbbra is a nyers dict-tel dolgozhat (visszafelé kompatibilis).
    """
    try:
        return GeneratedPost.model_validate(data), None
    except Exception as exc:  # pydantic.ValidationError is ide esik
        return None, str(exc)[:300]
