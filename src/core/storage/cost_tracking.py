"""Claude API költség-becslés + logolás a costs táblába.

Ugyanaz a minta mint a visual_generator._record_cost_safe (Muapi): best-effort,
SOSEM dob — egy logolási hiba nem akaszthatja meg a tényleges generálást/pontozást.

Árazás: $/1M token, (input, output). A MODEL konstansok minden hívó fájlban stringként
élnek (nincs központi enum) — ha egy modellnév változik, ITT is frissíteni kell, mert a
kulcs maga a modellnév-string. Ismeretlen modellnél a becslés None (nem logolunk 0-t).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Generálás-scope post_id: a generator_worker beállítja EGY poszt teljes generálási ablakára
# (voice-gen + eval + rewrite + optimizer + vizuál), így a record_claude_usage automatikusan a
# helyes post_id-hez rendeli a költséget, anélkül hogy ~9 függvény-szignatúrán át kellene fűzni.
# Async-biztos: a generálás egy Taskon belül, szekvenciális await-ekkel fut. Generáláson kívül
# (filter-scoring, outreach, video-idea) a default None marad → a viselkedés változatlan.
current_post_id: ContextVar[str | None] = ContextVar("current_post_id", default=None)

PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}

# Prompt-caching szorzók az input-árazásra (Anthropic GA árazás): a cache-be ÍRÁS 1.25×, a
# cache-ből OLVASÁS 0.1× az adott modell input-áránál. Lásd src/utils/anthropic_cache.py.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float | None:
    """A modell $/1M token árazásából a tényleges token-felhasználás dollárköltsége.

    Prompt-caching esetén az `input_tokens` MÁR CSAK a nem-cache-elt maradék; a cache-be írt és a
    cache-ből olvasott tokeneket külön, a megfelelő szorzóval árazzuk (különben a költség alul-
    számolna). Caching nélkül a két cache-argumentum 0 → visszafelé teljesen kompatibilis.
    """
    rates = PRICING.get(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    return round(
        (input_tokens / 1_000_000) * in_rate
        + (cache_write_tokens / 1_000_000) * in_rate * CACHE_WRITE_MULT
        + (cache_read_tokens / 1_000_000) * in_rate * CACHE_READ_MULT
        + (output_tokens / 1_000_000) * out_rate,
        6,
    )


def record_claude_usage(
    msg,
    model: str,
    *,
    post_id: str | None = None,
    kind: str = "claude_api",
    client=None,
) -> None:
    """Egy Anthropic Message válasz usage-éből (input_tokens/output_tokens) költség-sor
    logolása a costs táblába. Hívja MINDEN messages.create() hívás után, best-effort.
    """
    try:
        usage = getattr(msg, "usage", None)
        if usage is None:
            return
        # A cache-mezők a 0.28.0 Usage modelljén NINCSENEK típusozva, de a BaseModel extra="allow",
        # így futásidőben elérhetők; getattr-rel biztonságos (caching nélkül hiányoznak → 0).
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = estimate_cost_usd(
            model, usage.input_tokens, usage.output_tokens, cache_write, cache_read
        )
        # Explicit post_id nyer; különben a generálás-scope ContextVar (ha épp egy poszt generálódik).
        if post_id is None:
            post_id = current_post_id.get()
        from src.core.storage import posts as posts_store

        posts_store.record_cost(post_id, model, cost, kind=kind, client=client)
    except Exception as exc:  # noqa: BLE001 — sosem akaszthatja meg a hívót
        logger.warning("[cost] Claude usage logolás sikertelen (%s): %s", model, str(exc)[:120])
