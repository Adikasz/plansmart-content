"""Zero-network tests for src.generators.video_idea_generator (pure logic + mocked Claude/eval)."""
from __future__ import annotations

import pytest

from src.generators import video_idea_generator as gen


# ── _build_payload ────────────────────────────────────────────────────
def test_build_payload_includes_core_fields():
    import json

    feed_item = {
        "title": "Anthropic ships agent skills", "content": "Some long body text here.",
        "source_name": "Anthropic Blog", "url": "https://example.com/a", "topics": ["agents", "claude"],
    }
    payload = json.loads(gen._build_payload(feed_item))
    assert payload["title"] == "Anthropic ships agent skills"
    assert payload["source"] == "Anthropic Blog"
    assert payload["tags"] == ["agents", "claude"]


def test_build_payload_handles_missing_fields_gracefully():
    import json

    payload = json.loads(gen._build_payload({}))
    assert payload["title"] == ""
    assert payload["tags"] == []


def test_build_payload_truncates_long_content():
    feed_item = {"content": "x" * 5000}
    import json

    payload = json.loads(gen._build_payload(feed_item))
    assert len(payload["summary"]) == gen.SUMMARY_CHAR_CAP


# ── _fabrication_check_text ─────────────────────────────────────────────
def test_fabrication_check_text_includes_all_four_fields():
    data = {
        "hook": "Hook line.", "talking_points": ["point one", "point two"],
        "closing_thought": "Closing.", "suggested_caption": "Caption #tag",
    }
    text = gen._fabrication_check_text(data)
    assert "Hook line." in text
    assert "- point one" in text
    assert "- point two" in text
    assert "Closing." in text
    assert "Caption #tag" in text


def test_fabrication_check_text_handles_missing_fields():
    text = gen._fabrication_check_text({})
    assert "HOOK:" in text
    assert "TALKING POINTS:" in text


# ── _clamp_duration ──────────────────────────────────────────────────────
def test_clamp_duration_within_range_unchanged():
    data = {"estimated_duration_seconds": 180}
    gen._clamp_duration(data)
    assert data["estimated_duration_seconds"] == 180


def test_clamp_duration_clamps_too_high():
    data = {"estimated_duration_seconds": 9999}
    gen._clamp_duration(data)
    assert data["estimated_duration_seconds"] == gen.DURATION_MAX_S


def test_clamp_duration_clamps_too_low():
    data = {"estimated_duration_seconds": 1}
    gen._clamp_duration(data)
    assert data["estimated_duration_seconds"] == gen.DURATION_MIN_S


def test_clamp_duration_defaults_when_missing():
    data = {}
    gen._clamp_duration(data)
    assert data["estimated_duration_seconds"] == 180


def test_clamp_duration_defaults_when_non_numeric():
    data = {"estimated_duration_seconds": "not-a-number"}
    gen._clamp_duration(data)
    assert data["estimated_duration_seconds"] == 180


# ── generate_video_idea: voice validation ────────────────────────────────
@pytest.mark.asyncio
async def test_generate_video_idea_rejects_unsupported_voice():
    with pytest.raises(ValueError):
        await gen.generate_video_idea({"title": "x"}, voice="david")


# ── generate_video_idea: end-to-end with mocked Claude + evaluator ──────
class _FakeMsg:
    def __init__(self, text: str):
        self.content = [type("Block", (), {"text": text})()]
        self.stop_reason = "end_turn"
        self.usage = None


@pytest.mark.asyncio
async def test_generate_video_idea_skip_passthrough(monkeypatch):
    async def fake_create(*_a, **_k):
        return _FakeMsg('{"skip": true, "reason": "too dry"}')

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    result = await gen.generate_video_idea({"title": "x"}, voice="adam")
    assert result == {"skip": True, "reason": "too dry"}


@pytest.mark.asyncio
async def test_generate_video_idea_happy_path_no_fabrication(monkeypatch):
    idea_json = (
        '{"title": "t", "hook": "hi", "talking_points": ["a", "b", "c"], '
        '"closing_thought": "c", "suggested_caption": "cap #tag", '
        '"estimated_duration_seconds": 150}'
    )

    async def fake_create(*_a, **_k):
        return _FakeMsg(idea_json)

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    class FakeEvaluator:
        async def evaluate_post(self, *_a, **_k):
            return {"fabrication_risk": False, "fabrication_reason": ""}

    monkeypatch.setattr(gen, "TextEvaluator", FakeEvaluator)

    result = await gen.generate_video_idea({"title": "x"}, voice="adam")
    assert result["fabrication_risk"] is False
    assert result["hook"] == "hi"
    assert result["estimated_duration_seconds"] == 150


@pytest.mark.asyncio
async def test_generate_video_idea_retries_once_on_fabrication(monkeypatch):
    idea_json = (
        '{"title": "t", "hook": "hi", "talking_points": ["a"], '
        '"closing_thought": "c", "suggested_caption": "cap", "estimated_duration_seconds": 150}'
    )
    fixed_json = (
        '{"title": "t2", "hook": "hi fixed", "talking_points": ["a"], '
        '"closing_thought": "c", "suggested_caption": "cap", "estimated_duration_seconds": 150}'
    )
    calls = {"n": 0}

    async def fake_create(*_a, **_k):
        calls["n"] += 1
        return _FakeMsg(idea_json if calls["n"] == 1 else fixed_json)

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    eval_calls = {"n": 0}

    class FakeEvaluator:
        async def evaluate_post(self, *_a, **_k):
            eval_calls["n"] += 1
            if eval_calls["n"] == 1:
                return {"fabrication_risk": True, "fabrication_reason": "invented client story"}
            return {"fabrication_risk": False, "fabrication_reason": ""}

    monkeypatch.setattr(gen, "TextEvaluator", FakeEvaluator)

    result = await gen.generate_video_idea({"title": "x"}, voice="adam")
    assert calls["n"] == 2  # egy eredeti + egy javító újrapróbálkozás
    assert result["hook"] == "hi fixed"
    assert result["fabrication_risk"] is False


@pytest.mark.asyncio
async def test_generate_video_idea_reports_unfixed_fabrication_honestly(monkeypatch):
    idea_json = (
        '{"title": "t", "hook": "hi", "talking_points": ["a"], '
        '"closing_thought": "c", "suggested_caption": "cap", "estimated_duration_seconds": 150}'
    )

    async def fake_create(*_a, **_k):
        return _FakeMsg(idea_json)

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    class FakeEvaluator:
        async def evaluate_post(self, *_a, **_k):
            return {"fabrication_risk": True, "fabrication_reason": "still fabricated"}

    monkeypatch.setattr(gen, "TextEvaluator", FakeEvaluator)

    result = await gen.generate_video_idea({"title": "x"}, voice="adam")
    # a javító próbálkozás is fabrikált maradt -- SOSEM hazudunk a fabrication_risk-ről, a
    # végleges (javított) verzió kerül vissza a jelzéssel, nem törli/rejti el a hívó elől.
    assert result["fabrication_risk"] is True
    assert result["fabrication_reason"] == "still fabricated"


@pytest.mark.asyncio
async def test_generate_video_idea_returns_none_on_unparseable_json(monkeypatch):
    async def fake_create(*_a, **_k):
        return _FakeMsg("not json at all")

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    result = await gen.generate_video_idea({"title": "x"}, voice="adam")
    assert result is None


# ── _is_valid_shape ───────────────────────────────────────────────────────
def test_is_valid_shape_accepts_well_formed_idea():
    assert gen._is_valid_shape({"hook": "h", "talking_points": ["a", "b"]}) is True


def test_is_valid_shape_rejects_missing_hook():
    assert gen._is_valid_shape({"talking_points": ["a"]}) is False


def test_is_valid_shape_rejects_empty_hook():
    assert gen._is_valid_shape({"hook": "   ", "talking_points": ["a"]}) is False


def test_is_valid_shape_rejects_non_string_hook():
    assert gen._is_valid_shape({"hook": 123, "talking_points": ["a"]}) is False


def test_is_valid_shape_rejects_missing_talking_points():
    assert gen._is_valid_shape({"hook": "h"}) is False


def test_is_valid_shape_rejects_empty_talking_points_list():
    assert gen._is_valid_shape({"hook": "h", "talking_points": []}) is False


def test_is_valid_shape_rejects_non_list_talking_points():
    assert gen._is_valid_shape({"hook": "h", "talking_points": "not a list"}) is False


def test_is_valid_shape_rejects_non_string_items_in_talking_points():
    assert gen._is_valid_shape({"hook": "h", "talking_points": ["a", {"nested": "dict"}]}) is False


@pytest.mark.asyncio
async def test_generate_video_idea_returns_none_when_shape_invalid(monkeypatch):
    # szintaktikailag ervenyes JSON, de hianyzik a kotelezo "hook" mezo -- ugyanugy kezelendo,
    # mint egy parse-hiba (None), hogy az insert_video_idea/format_video_idea_message SOSE
    # kapjon hianyos/rossz tipusu adatot (lasd a review talalatok 2. es 3. pontja).
    async def fake_create(*_a, **_k):
        return _FakeMsg('{"talking_points": ["a", "b"]}')

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    result = await gen.generate_video_idea({"title": "x"}, voice="adam")
    assert result is None


@pytest.mark.asyncio
async def test_generate_video_idea_returns_none_when_talking_points_not_list_of_strings(monkeypatch):
    async def fake_create(*_a, **_k):
        return _FakeMsg('{"hook": "h", "talking_points": [{"weird": "shape"}]}')

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    result = await gen.generate_video_idea({"title": "x"}, voice="adam")
    assert result is None


# ── retry sends the model its own previous draft (review fix) ───────────
@pytest.mark.asyncio
async def test_fabrication_retry_includes_previous_draft_in_prompt(monkeypatch):
    idea_json = (
        '{"title": "t", "hook": "original hook mentioning a fake client", '
        '"talking_points": ["a"], "closing_thought": "c", "suggested_caption": "cap", '
        '"estimated_duration_seconds": 150}'
    )
    fixed_json = (
        '{"title": "t2", "hook": "fixed hook", "talking_points": ["a"], '
        '"closing_thought": "c", "suggested_caption": "cap", "estimated_duration_seconds": 150}'
    )
    captured_user_contents = []

    async def fake_create(*_a, **kwargs):
        captured_user_contents.append(kwargs["messages"][0]["content"])
        return _FakeMsg(idea_json if len(captured_user_contents) == 1 else fixed_json)

    monkeypatch.setattr(gen, "_client", lambda: type("C", (), {"messages": type("M", (), {"create": staticmethod(fake_create)})()})())

    eval_calls = {"n": 0}

    class FakeEvaluator:
        async def evaluate_post(self, *_a, **_k):
            eval_calls["n"] += 1
            if eval_calls["n"] == 1:
                return {"fabrication_risk": True, "fabrication_reason": "invented client story"}
            return {"fabrication_risk": False, "fabrication_reason": ""}

    monkeypatch.setattr(gen, "TextEvaluator", FakeEvaluator)

    await gen.generate_video_idea({"title": "x"}, voice="adam")

    assert len(captured_user_contents) == 2
    retry_prompt = captured_user_contents[1]
    # a javito hivasnak LATNIA kell az eredeti draftot (a review talalat szerint korabban nem
    # latta, csak a nyers feed_item payloadot -- ez a regresszios teszt ezt vedi).
    assert "original hook mentioning a fake client" in retry_prompt
    assert "invented client story" in retry_prompt
