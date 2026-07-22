"""Claude API költség-becslés + logolás a costs táblába.

Ugyanaz a minta mint a visual_generator._record_cost_safe (Muapi): best-effort,
SOSEM dob — egy logolási hiba nem akaszthatja meg a tényleges generálást/pontozást.

Árazás: $/1M token, (input, output). A MODEL konstansok minden hívó fájlban stringként
élnek (nincs központi enum) — ha egy modellnév változik, ITT is frissíteni kell, mert a
kulcs maga a modellnév-string. Ismeretlen modellnél a becslés None (nem logolunk 0-t).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """A modell $/1M token árazásából a tényleges token-felhasználás dollárköltsége."""
    rates = PRICING.get(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    return round((input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate, 6)


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
        cost = estimate_cost_usd(model, usage.input_tokens, usage.output_tokens)
        from src.storage import posts as posts_store

        posts_store.record_cost(post_id, model, cost, kind=kind, client=client)
    except Exception as exc:  # noqa: BLE001 — sosem akaszthatja meg a hívót
        logger.warning("[cost] Claude usage logolás sikertelen (%s): %s", model, str(exc)[:120])
