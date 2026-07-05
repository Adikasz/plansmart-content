"""A Muapi képgenerálás NORMALIZÁLT eredményének tipizált sémája (külső API határ).

A Muapi nyers válasza sokféle alakú lehet (a muapi_client._extract_* helyerei kezelik).
Ez a modell a KINYERT, tiszta eredményt köti meg (URL + modell + költség), és tükrözi a
`muapi_client.GenerationResult` dataclass-t. A `from_generation()` egy GenerationResult-ból
(vagy bármely azonos attribútumú objektumból) épít validált, szerializálható modellt.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class MuapiResult(BaseModel):
    """Egy sikeres Muapi generálás normalizált eredménye."""

    model_config = ConfigDict(extra="allow")

    image_url: HttpUrl
    model: str
    request_id: str | None = None
    cost_usd: float | None = Field(default=None, ge=0)

    @classmethod
    def from_generation(cls, result: Any) -> "MuapiResult":
        """muapi_client.GenerationResult (vagy azonos attribútumú objektum) -> validált modell."""
        return cls(
            image_url=result.image_url,
            model=result.model,
            request_id=getattr(result, "request_id", None),
            cost_usd=getattr(result, "cost_usd", None),
        )
