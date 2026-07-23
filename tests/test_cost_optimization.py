"""Zero-network tesztek a költség-optimalizációs változásokhoz (Phase: cost initiative):
prompt-caching helper, cache-tudatos árazás, és az extract_visual_text cserélhető szolgáltatója.
SEMMILYEN hálózat / valódi Anthropic / OpenRouter hívás — minden mock.
"""
from __future__ import annotations

import pytest

from src.core.storage import cost_tracking as ct
from src.utils.anthropic_cache import cached_system


# ── cached_system ────────────────────────────────────────────────────────
def test_cached_system_shape():
    blocks = cached_system("SYSTEM PROMPT")
    assert blocks == [
        {"type": "text", "text": "SYSTEM PROMPT", "cache_control": {"type": "ephemeral"}}
    ]


def test_cached_system_preserves_text_and_is_single_block():
    big = "x" * 5000
    blocks = cached_system(big)
    assert len(blocks) == 1
    assert blocks[0]["text"] == big
    assert blocks[0]["cache_control"]["type"] == "ephemeral"


def test_cached_system_empty_string():
    assert cached_system("")[0]["text"] == ""


# ── estimate_cost_usd: cache-tudatos árazás ──────────────────────────────
def test_estimate_cost_no_cache_is_backward_compatible():
    # 1000 input @ $3/M + 500 output @ $15/M = 0.003 + 0.0075
    assert ct.estimate_cost_usd("claude-sonnet-4-6", 1000, 500) == pytest.approx(0.0105)


def test_estimate_cost_unknown_model_returns_none():
    assert ct.estimate_cost_usd("gpt-5-imaginary", 1000, 500) is None
    assert ct.estimate_cost_usd("gpt-5-imaginary", 1000, 500, 100, 100) is None


def test_estimate_cost_cache_write_is_1_25x_input():
    # csak a cache-write komponens: 7000 tok @ ($3/M * 1.25)
    base = ct.estimate_cost_usd("claude-sonnet-4-6", 0, 0)
    with_write = ct.estimate_cost_usd("claude-sonnet-4-6", 0, 0, 7000, 0)
    assert base == 0.0
    assert with_write == pytest.approx(7000 / 1_000_000 * 3.0 * 1.25)  # 0.02625


def test_estimate_cost_cache_read_is_0_1x_input():
    with_read = ct.estimate_cost_usd("claude-sonnet-4-6", 0, 0, 0, 7000)
    assert with_read == pytest.approx(7000 / 1_000_000 * 3.0 * 0.10)  # 0.0021


def test_estimate_cost_combined_components_sum():
    # 100 input full + 7000 write + 500 read + 50 output
    got = ct.estimate_cost_usd("claude-sonnet-4-6", 100, 50, 7000, 500)
    expected = (
        100 / 1e6 * 3.0
        + 7000 / 1e6 * 3.0 * 1.25
        + 500 / 1e6 * 3.0 * 0.10
        + 50 / 1e6 * 15.0
    )
    assert got == pytest.approx(round(expected, 6))


def test_cache_read_far_cheaper_than_full_input():
    # a caching lényege: ugyanaz a tokenszám cache-readként ~10% az áron
    full = ct.estimate_cost_usd("claude-sonnet-4-6", 7000, 0)
    cached = ct.estimate_cost_usd("claude-sonnet-4-6", 0, 0, 0, 7000)
    assert cached < full * 0.15


# ── record_claude_usage: olvassa a cache-mezőket a usage-ről ──────────────
class _Usage:
    def __init__(self, i, o, cw=None, cr=None):
        self.input_tokens = i
        self.output_tokens = o
        if cw is not None:
            self.cache_creation_input_tokens = cw
        if cr is not None:
            self.cache_read_input_tokens = cr


class _Msg:
    def __init__(self, usage):
        self.usage = usage


@pytest.fixture
def capture_record_cost(monkeypatch):
    """Elkapja, mit adna át a record_claude_usage a posts_store.record_cost-nak."""
    calls = []

    def _fake_record_cost(post_id, model, cost, *, kind="claude_api", client=None):
        calls.append({"post_id": post_id, "model": model, "cost": cost, "kind": kind})

    import src.core.storage.posts as posts_store

    monkeypatch.setattr(posts_store, "record_cost", _fake_record_cost)
    return calls


def test_record_usage_without_cache_fields_backward_compatible(capture_record_cost):
    # régi usage (nincs cache mező) -> getattr 0 -> a régi költség
    ct.record_claude_usage(_Msg(_Usage(1000, 500)), "claude-sonnet-4-6")
    assert len(capture_record_cost) == 1
    assert capture_record_cost[0]["cost"] == pytest.approx(0.0105)


def test_record_usage_includes_cache_tokens(capture_record_cost):
    # caching aktív: input_tokens a maradék, a 7000 cache-read külön áron
    msg = _Msg(_Usage(100, 50, cw=0, cr=7000))
    ct.record_claude_usage(msg, "claude-sonnet-4-6")
    got = capture_record_cost[0]["cost"]
    expected = ct.estimate_cost_usd("claude-sonnet-4-6", 100, 50, 0, 7000)
    assert got == pytest.approx(expected)


def test_record_usage_cache_write_counted(capture_record_cost):
    msg = _Msg(_Usage(100, 50, cw=7000, cr=0))
    ct.record_claude_usage(msg, "claude-sonnet-4-6")
    assert capture_record_cost[0]["cost"] == pytest.approx(
        ct.estimate_cost_usd("claude-sonnet-4-6", 100, 50, 7000, 0)
    )


def test_record_usage_never_raises_on_bad_msg(capture_record_cost):
    # best-effort: usage nélküli objektum -> csendes return, nincs kivétel, nincs record
    ct.record_claude_usage(object(), "claude-sonnet-4-6")
    assert capture_record_cost == []


def test_record_usage_passes_kind_through(capture_record_cost):
    ct.record_claude_usage(_Msg(_Usage(10, 5)), "claude-sonnet-4-6", kind="voice_generation")
    assert capture_record_cost[0]["kind"] == "voice_generation"


# ── #4: generation-scope post_id via ContextVar ──────────────────────────
def test_record_usage_uses_contextvar_post_id_when_not_explicit(capture_record_cost):
    token = ct.current_post_id.set("abc123def456")
    try:
        ct.record_claude_usage(_Msg(_Usage(10, 5)), "claude-sonnet-4-6", kind="text_eval")
    finally:
        ct.current_post_id.reset(token)
    assert capture_record_cost[0]["post_id"] == "abc123def456"
    assert capture_record_cost[0]["kind"] == "text_eval"


def test_record_usage_explicit_post_id_beats_contextvar(capture_record_cost):
    token = ct.current_post_id.set("ctxvar-id")
    try:
        ct.record_claude_usage(_Msg(_Usage(10, 5)), "claude-sonnet-4-6", post_id="explicit-id")
    finally:
        ct.current_post_id.reset(token)
    assert capture_record_cost[0]["post_id"] == "explicit-id"


def test_record_usage_post_id_none_when_no_contextvar(capture_record_cost):
    # default: nincs generálás-scope beállítva -> post_id None (mint eddig)
    assert ct.current_post_id.get() is None
    ct.record_claude_usage(_Msg(_Usage(10, 5)), "claude-sonnet-4-6", kind="relevance_scoring")
    assert capture_record_cost[0]["post_id"] is None


# ── visual_text_provider ─────────────────────────────────────────────────
class _FakeAsyncMessages:
    def __init__(self, text, usage=None):
        self._text = text
        self._usage = usage
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        block = type("B", (), {"text": self._text})()
        return type("M", (), {"content": [block], "usage": self._usage})()


class _FakeAsyncClient:
    def __init__(self, text, usage=None):
        self.messages = _FakeAsyncMessages(text, usage)


@pytest.mark.asyncio
async def test_anthropic_provider_calls_haiku_and_records(monkeypatch):
    from src.integrations.visuals import visual_text_provider as vtp

    fake = _FakeAsyncClient('{"main_text":"HELLO"}', usage=_Usage(10, 5))
    monkeypatch.setattr(vtp, "_anthropic_client", lambda: fake)
    recorded = []
    monkeypatch.setattr(vtp, "record_claude_usage",
                        lambda msg, model, **kw: recorded.append((model, kw.get("kind"))))

    prov = vtp.AnthropicProvider()
    out = await prov.complete("SYS", "USER", max_tokens=300)

    assert out == '{"main_text":"HELLO"}'
    assert fake.messages.calls[0]["model"] == vtp.HAIKU_MODEL
    assert fake.messages.calls[0]["system"] == "SYS"
    assert fake.messages.calls[0]["messages"] == [{"role": "user", "content": "USER"}]
    assert recorded == [(vtp.HAIKU_MODEL, "visual_text_extract")]  # cost-log + kind megtörtént


@pytest.mark.asyncio
async def test_openrouter_provider_raises_without_key():
    from src.integrations.visuals.visual_text_provider import OpenRouterProvider

    prov = OpenRouterProvider("deepseek/deepseek-chat", api_key=None)
    prov.api_key = None  # explicit: környezetből se legyen
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        await prov.complete("SYS", "USER", max_tokens=300)


@pytest.mark.asyncio
async def test_openrouter_provider_builds_payload_and_parses(monkeypatch):
    import httpx

    from src.integrations.visuals.visual_text_provider import OpenRouterProvider

    captured = {}

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"main_text":"OR"}'}}]}

    class _FakeHttpxClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)

    prov = OpenRouterProvider("deepseek/deepseek-chat", api_key="sk-test")
    out = await prov.complete("SYS", "USER", max_tokens=222)

    assert out == '{"main_text":"OR"}'
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"]["model"] == "deepseek/deepseek-chat"
    assert captured["json"]["max_tokens"] == 222
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert captured["json"]["response_format"] == {"type": "json_object"}


# ── extract_visual_text: default vs. cserélt szolgáltató ─────────────────
@pytest.mark.asyncio
async def test_extract_visual_text_default_path_unchanged(monkeypatch):
    from src.integrations.visuals import visual_generator as vg

    fake = _FakeAsyncClient('{"main_text":"AMD $10B","sub_text":"supply chain","stat":"$10B"}',
                            usage=_Usage(50, 20))
    monkeypatch.setattr(vg, "_client", lambda: fake)
    monkeypatch.setattr(vg, "record_claude_usage", lambda *a, **k: None)

    out = await vg.extract_visual_text("AMD committed $10 billion to Anthropic.")
    assert out["main_text"] == "AMD $10B"
    assert out["stat"] == "$10B"
    # default út a Haiku modellt hívta
    assert fake.messages.calls[0]["model"] == vg.HAIKU_MODEL


@pytest.mark.asyncio
async def test_extract_visual_text_routes_through_explicit_provider(monkeypatch):
    from src.integrations.visuals import visual_generator as vg

    class _Prov:
        name = "openrouter/test"
        seen = {}

        async def complete(self, system, user, *, max_tokens):
            _Prov.seen = {"system": system, "user": user, "max_tokens": max_tokens}
            return '{"main_text":"FROM PROVIDER","sub_text":"x","stat":null}'

    # ha a default utat hívná (nem a providert), ez a klienshiba kibukna:
    monkeypatch.setattr(vg, "_client", lambda: (_ for _ in ()).throw(AssertionError("nem a providert hívta")))

    out = await vg.extract_visual_text("some post text", provider=_Prov())
    assert out["main_text"] == "FROM PROVIDER"
    assert out["stat"] is None
    assert _Prov.seen["max_tokens"] == 300
    assert _Prov.seen["system"] == vg.EXTRACT_SYSTEM


@pytest.mark.asyncio
async def test_extract_visual_text_provider_error_falls_back_to_heuristic(monkeypatch):
    from src.integrations.visuals import visual_generator as vg

    class _BadProv:
        name = "openrouter/bad"

        async def complete(self, *a, **k):
            raise RuntimeError("boom")

    out = await vg.extract_visual_text("First line here\nSecond line", provider=_BadProv())
    # heurisztikus fallback: első sor főszöveg (uppercase, 40 kar), második alszöveg
    assert out["main_text"] == "FIRST LINE HERE"
    assert out["sub_text"] == "Second line"
    assert out["stat"] is None
