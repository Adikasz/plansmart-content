"""Ádám (strategist) voice generátor — LinkedIn + X.

Vékony wrapper a base_generator köré a prompts/voice_adam.md prompttal.
"""
from __future__ import annotations

from typing import Any

from src.generators.base_generator import generate
from src.storage.models import FeedItem

VOICE_PROMPT = "prompts/voice_adam.md"


async def generate_adam(feed_item: FeedItem) -> dict[str, Any] | None:
    """Ádám hangú poszt (LinkedIn + Twitter) egy feed_item-ből, vagy None (skip)."""
    return await generate(feed_item, VOICE_PROMPT)
