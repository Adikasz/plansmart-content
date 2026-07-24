"""A relevancia-pontozó (Claude Haiku) LLM-kimenetének tipizált sémája.

A relevance_scorer._parse_json_strict() nyers `dict`-et ad; ez a séma megköti a
score tartományát (0–10) és a metaadat-mezők típusát. A meglévő `ScoreResult` dataclass
belső DTO marad; ez a modell a HATÁR (LLM JSON) validálására szolgál — új kód ezt hívhatja.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RelevanceScore(BaseModel):
    """Egy hír relevancia-pontszáma + voice-illeszkedés (Haiku kimenet)."""

    model_config = ConfigDict(extra="allow")

    score: int = Field(ge=0, le=10)
    reason: str = ""
    voice_fit: dict[str, bool] = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list)
    urgency: str = "low"


def validate_score(data: dict[str, Any]) -> tuple[RelevanceScore | None, str | None]:
    """Best-effort validáció: (RelevanceScore, None) vagy (None, hibaüzenet). Sosem dob."""
    try:
        return RelevanceScore.model_validate(data), None
    except Exception as exc:
        return None, str(exc)[:300]
