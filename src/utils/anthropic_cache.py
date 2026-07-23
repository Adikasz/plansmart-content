"""Anthropic prompt-caching segéd — anthropic==0.28.0 (nyers-dict cache_control, SDK-bump NÉLKÜL).

A 0.28.0 transform-rétege (`_utils/_transform.py`) az általa nem ismert `cache_control` kulcsot
változatlanul továbbítja a wire-body-ba, és a `str`-nek típusozott `system=` egy listát is
érintetlenül átenged — így a prompt-caching működik bump nélkül (empirikusan igazolva: 7676 tokenes
blokk cache-write majd cache-read). Beta header NEM kell (a caching GA).

Csak a modell minimális cache-elhető prefixe FÖLÖTT van hatása:
    - claude-sonnet-4-6 : 2048 token
    - claude-haiku-4-5  : 4096 token
Ez alatt a `cache_control` némán figyelmen kívül marad (nincs hiba, nincs nyereség) — ezért csak a
nagy, ismételten küldött rendszerpromptokat érdemes így csomagolni (voice_*.md, _rewrite_system).

A cache-token elszámolást lásd src/storage/cost_tracking.py (a cache-write 1.25×, a cache-read 0.1×
az input-árazásnak) — caching bekapcsolása után az `usage.input_tokens` már CSAK a nem-cache-elt
maradék, ezért a költséglogolásnak a cache-mezőket is olvasnia kell, különben alul-számol.
"""
from __future__ import annotations

from typing import Any


def cached_system(text: str) -> list[dict[str, Any]]:
    """A teljes rendszerpromptot egyetlen, ephemeral-cache-elt text blokként adja vissza.

    A cache breakpoint a blokk végén van → az egész rendszerprompt a cache-elt prefix, az utána
    következő user üzenet nincs cache-elve. Voice-onként stabil prefix → voice-onként külön cache.
    """
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
