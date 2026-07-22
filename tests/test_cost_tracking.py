"""Zero-network tests for src.storage.cost_tracking."""
from __future__ import annotations

from src.storage import cost_tracking


def test_estimate_cost_usd_known_model_sonnet():
    # 1M input + 1M output tokens at the pricing table's Sonnet rate: $3 + $15 = $18.
    cost = cost_tracking.estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert cost == 18.0


def test_estimate_cost_usd_known_model_haiku_partial_tokens():
    cost = cost_tracking.estimate_cost_usd("claude-haiku-4-5-20251001", 500_000, 100_000)
    # 0.5 * $1.00 + 0.1 * $5.00 = $0.50 + $0.50 = $1.00
    assert cost == 1.0


def test_estimate_cost_usd_unknown_model_returns_none():
    assert cost_tracking.estimate_cost_usd("some-future-model", 1000, 1000) is None


def test_record_claude_usage_missing_usage_attr_is_noop():
    class NoUsage:
        pass

    # Sosem dob, ha a válasznak nincs .usage attribútuma (pl. régi fixture/mock).
    cost_tracking.record_claude_usage(NoUsage(), "claude-sonnet-4-6")


def test_record_claude_usage_never_raises_on_client_error():
    class Usage:
        input_tokens = 100
        output_tokens = 50

    class Msg:
        usage = Usage()

    class ExplodingClient:
        def table(self, *_a, **_k):
            raise RuntimeError("boom")

    # Best-effort: egy DB hiba sem terjedhet fel a hívóhoz.
    cost_tracking.record_claude_usage(Msg(), "claude-sonnet-4-6", client=ExplodingClient())
