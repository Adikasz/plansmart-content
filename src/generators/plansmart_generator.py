"""PlanSmart (brand) voice generátor — CSAK LinkedIn (nincs Twitter).

Vékony wrapper a base_generator köré a prompts/voice_plansmart.md prompttal.
A céges fiók nem postázik X-en, ezért a twitter mezőt defenzíven eltávolítjuk.
"""
from __future__ import annotations

from typing import Any

from src.generators.base_generator import generate
from src.storage.models import FeedItem

VOICE_PROMPT = "prompts/voice_plansmart.md"


async def generate_plansmart(feed_item: FeedItem) -> dict[str, Any] | None:
    """PlanSmart céges hangú LinkedIn poszt, vagy None (skip). Twitter mezőt nem ad vissza."""
    result = await generate(feed_item, VOICE_PROMPT)
    if result is not None:
        result.pop("twitter", None)  # brand fiók nem postázik X-en
    return result
