"""Cserélhető szolgáltató-réteg az extract_visual_text-hez (Part 4 — olcsóbb modell A/B).

Az ALAPÉRTELMEZETT MINDIG az Anthropic/Haiku (a jelenlegi, változatlan viselkedés); az OpenRouter
(OpenAI-kompatibilis, sok olcsó modell: deepseek, qwen, llama) OPCIONÁLIS és külön OPENROUTER_API_KEY
kulcsot igényel. A protokoll szándékosan szűk: (system, user, max_tokens) -> NYERS szöveg; a
JSON-parse-t és a heurisztikus fallbacket a hívó (extract_visual_text) végzi közösen mindkét
szolgáltatóra, így az összehasonlítás csak a modellt cseréli, semmi mást.

Ez csak a seam + az összehasonlító harness eszköze — a produkciós default NEM változik, amíg
explicit döntés nem születik róla.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Protocol, cast, runtime_checkable

from anthropic import AsyncAnthropic
from anthropic.types import TextBlock

from src.core.storage.cost_tracking import record_claude_usage

HAIKU_MODEL = "claude-haiku-4-5-20251001"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@runtime_checkable
class VisualTextProvider(Protocol):
    name: str

    async def complete(self, system: str, user: str, *, max_tokens: int) -> str: ...


@lru_cache(maxsize=1)
def _anthropic_client() -> AsyncAnthropic:
    return AsyncAnthropic()  # ANTHROPIC_API_KEY a környezetből


class AnthropicProvider:
    """Alapértelmezett szolgáltató: a jelenlegi Haiku-hívás, VÁLTOZATLAN költséglogolással."""

    name = "anthropic/" + HAIKU_MODEL

    async def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        msg = await _anthropic_client().messages.create(
            model=HAIKU_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        record_claude_usage(msg, HAIKU_MODEL, kind="visual_text_extract")
        return cast(TextBlock, msg.content[0]).text if msg.content else ""


class OpenRouterProvider:
    """OpenAI-kompatibilis OpenRouter hívás httpx-szel (NINCS új függőség — httpx már pinnelt).

    json_mode=True: response_format={"type":"json_object"} — a legtöbb OpenRouter modell tiszteli,
    így a _repair_and_parse szinte no-op lesz. A költséget itt NEM logoljuk a costs táblába (idegen
    usage-alak + ismeretlen modell-árazás) — ez kísérleti arm, nem a produkciós út.
    """

    def __init__(self, model: str, api_key: str | None = None, *, json_mode: bool = True):
        self.model = model
        self.name = "openrouter/" + model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.json_mode = json_mode

    async def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY nincs beállítva — az OpenRouter arm nem futtatható."
            )
        import httpx  # lazy: a modul prod-ban is importálható maradjon key/hálózat nélkül

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return (data["choices"][0]["message"]["content"] or "") if data.get("choices") else ""
