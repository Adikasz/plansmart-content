"""Zero-network tests for src.integrations.bots.video_idea_bot (formatting + candidate-picking logic)."""
from __future__ import annotations

import pytest

from src.integrations.bots import video_idea_bot as vb


# ── format_video_idea_message ────────────────────────────────────────────
def test_format_includes_all_sections():
    idea = {
        "hook": "This changes everything for small teams.",
        "talking_points": ["point one", "point two", "point three"],
        "closing_thought": "Something to think about.",
        "suggested_caption": "Caption text #ai #automation",
        "estimated_duration_seconds": 180,
    }
    text = vb.format_video_idea_message(idea, "Anthropic ships new agent framework")
    assert "🎥 <b>Heti videó ötlet — Ádám</b>" in text
    assert "Anthropic ships new agent framework" in text
    assert "This changes everything" in text
    assert "- point one" in text
    assert "- point two" in text
    assert "Something to think about." in text
    assert "Caption text #ai #automation" in text
    assert "~180s" in text


def test_format_escapes_html():
    idea = {"hook": "<script>alert(1)</script>", "talking_points": [], "closing_thought": "",
             "suggested_caption": ""}
    text = vb.format_video_idea_message(idea, "title")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_shows_fabrication_warning_when_flagged():
    idea = {
        "hook": "h", "talking_points": [], "closing_thought": "", "suggested_caption": "",
        "fabrication_risk": True, "fabrication_reason": "invented a client story",
    }
    text = vb.format_video_idea_message(idea, "title")
    assert "Fabrikáció-kockázat" in text
    assert "invented a client story" in text


def test_format_no_fabrication_line_when_not_flagged():
    idea = {"hook": "h", "talking_points": [], "closing_thought": "", "suggested_caption": "",
             "fabrication_risk": False}
    text = vb.format_video_idea_message(idea, "title")
    assert "Fabrikáció-kockázat" not in text


def test_format_handles_missing_duration():
    idea = {"hook": "h", "talking_points": [], "closing_thought": "", "suggested_caption": ""}
    text = vb.format_video_idea_message(idea, "title")
    assert "~?s" not in text  # ne generáljon értelmetlen "~?s"-t
    assert "Becsült hossz:</b> ?" in text


# ── _kb (keyboard) ────────────────────────────────────────────────────────
def test_keyboard_has_four_buttons_with_correct_actions():
    kb = vb._kb("vid123")
    row = kb.inline_keyboard[0]
    assert len(row) == 4
    actions = [vb.VideoIdeaCB.unpack(btn.callback_data).action for btn in row]
    assert actions == ["approve", "edit", "regenerate", "skip"]


def test_keyboard_callback_data_roundtrips_video_id():
    kb = vb._kb("vid123")
    for btn in kb.inline_keyboard[0]:
        cb = vb.VideoIdeaCB.unpack(btn.callback_data)
        assert cb.video_id == "vid123"


def test_video_idea_cb_prefix_is_distinct_from_approval_cb():
    # kritikus: a VideoIdeaCB-nek SAJÁT prefixe kell legyen, hogy a routing ne keveredjen a
    # telegram_bot.py ApprovalCB-jével (lásd a research report ajánlását -- ez a teszt védi
    # ezt a döntést egy jövőbeli véletlen prefix-ütközés ellen).
    from src.integrations.bots.telegram_bot import ApprovalCB

    packed = vb.VideoIdeaCB(action="approve", video_id="x").pack()
    assert packed.split(":")[0] != ApprovalCB(action="approve", post_id="x").pack().split(":")[0]


# ── _pick_and_generate ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pick_and_generate_forced_feed_id_not_found(monkeypatch):
    monkeypatch.setattr(vb.feed_store, "get_by_id", lambda item_id, client=None: None)
    idea, row, error = await vb._pick_and_generate("adam", client=object(), forced_feed_id="nope")
    assert idea is None
    assert "Nincs ilyen feed_item" in error


@pytest.mark.asyncio
async def test_pick_and_generate_forced_feed_id_skip_surfaces_reason(monkeypatch):
    monkeypatch.setattr(vb.feed_store, "get_by_id", lambda item_id, client=None: {"id": "f1", "title": "t"})

    async def fake_generate(row, voice):
        return {"skip": True, "reason": "too technical"}

    monkeypatch.setattr(vb, "generate_video_idea", fake_generate)
    idea, row, error = await vb._pick_and_generate("adam", client=object(), forced_feed_id="f1")
    assert idea is None
    assert "too technical" in error


@pytest.mark.asyncio
async def test_pick_and_generate_forced_feed_id_success(monkeypatch):
    monkeypatch.setattr(vb.feed_store, "get_by_id", lambda item_id, client=None: {"id": "f1", "title": "t"})

    async def fake_generate(row, voice):
        return {"hook": "h", "talking_points": []}

    monkeypatch.setattr(vb, "generate_video_idea", fake_generate)
    idea, row, error = await vb._pick_and_generate("adam", client=object(), forced_feed_id="f1")
    assert error is None
    assert idea["hook"] == "h"
    assert row["id"] == "f1"


@pytest.mark.asyncio
async def test_pick_and_generate_auto_pick_filters_by_voice_fit(monkeypatch):
    candidates = [
        {"id": "f1", "title": "no fit", "voice_fit": {"adam": False}},
        {"id": "f2", "title": "fits", "voice_fit": {"adam": True}},
    ]
    monkeypatch.setattr(vb.feed_store, "get_video_idea_candidates", lambda **kw: candidates)
    monkeypatch.setattr(vb.video_store, "has_video_idea_for_feed_item", lambda fid, client=None: False)

    seen_ids = []

    async def fake_generate(row, voice):
        seen_ids.append(row["id"])
        return {"hook": "h"}

    monkeypatch.setattr(vb, "generate_video_idea", fake_generate)
    idea, row, error = await vb._pick_and_generate("adam", client=object(), forced_feed_id=None)
    assert seen_ids == ["f2"]  # f1 kimarad -- nincs adam voice_fit
    assert row["id"] == "f2"


@pytest.mark.asyncio
async def test_pick_and_generate_auto_pick_skips_already_used_feed_items(monkeypatch):
    candidates = [
        {"id": "f1", "title": "used", "voice_fit": {"adam": True}},
        {"id": "f2", "title": "fresh", "voice_fit": {"adam": True}},
    ]
    monkeypatch.setattr(vb.feed_store, "get_video_idea_candidates", lambda **kw: candidates)
    monkeypatch.setattr(vb.video_store, "has_video_idea_for_feed_item",
                        lambda fid, client=None: fid == "f1")

    async def fake_generate(row, voice):
        return {"hook": "h"}

    monkeypatch.setattr(vb, "generate_video_idea", fake_generate)
    idea, row, error = await vb._pick_and_generate("adam", client=object(), forced_feed_id=None)
    assert row["id"] == "f2"


@pytest.mark.asyncio
async def test_pick_and_generate_no_candidates_returns_clear_message(monkeypatch):
    monkeypatch.setattr(vb.feed_store, "get_video_idea_candidates", lambda **kw: [])
    idea, row, error = await vb._pick_and_generate("adam", client=object(), forced_feed_id=None)
    assert idea is None
    assert "Nincs megfelelő" in error


# ── create_video_cmd: duplicate-race handling (review fix) ──────────────
class _FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, *_a, **_k):
        self.answers.append(text)


class _FakeCommand:
    def __init__(self, args):
        self.args = args


@pytest.mark.asyncio
async def test_create_video_cmd_handles_duplicate_race_gracefully(monkeypatch):
    monkeypatch.setattr(vb.video_store, "table_ready", lambda client=None: True)
    monkeypatch.setattr(vb, "get_client", lambda use_service_key=False: object())
    monkeypatch.setattr(
        vb, "_pick_and_generate",
        lambda voice, client, forced_feed_id: _async_result(
            ({"hook": "h", "talking_points": ["a"]}, {"id": "f1", "title": "t"}, None)
        ),
    )

    def raise_duplicate(idea, client=None):
        raise vb.video_store.DuplicateVideoIdeaError("f1")

    monkeypatch.setattr(vb.video_store, "insert_video_idea", raise_duplicate)

    message = _FakeMessage()
    await vb.create_video_cmd(message, _FakeCommand("adam"), bot=object())

    assert any("már készült" in a for a in message.answers)
    assert not any(a.startswith("❌") for a in message.answers)  # nem generikus hibaként jelenik meg


async def _async_result(value):
    return value
