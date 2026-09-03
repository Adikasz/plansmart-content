"""Zero-network tests for src.core.workers.video_idea_worker."""

from __future__ import annotations

import pytest

from src.core.strategy import cadence
from src.core.workers import video_idea_worker as worker


@pytest.fixture(autouse=True)
def _no_digest_voices(monkeypatch):
    """Fazis 23 ota Adam digest-hang, ezert a run_video_idea_check kapuja alapbol azonnal
    visszater. Az ITTENI tesztek a kapun BELULI logikat ellenorzik, ezert a kizarast
    kikapcsoljuk; magat a kaput a tests/test_adam_digest_worker.py fedi le."""
    monkeypatch.setattr(cadence, "digest_voices", lambda: set())


# ── _adam_fit ─────────────────────────────────────────────────────────────
def test_adam_fit_true():
    assert worker._adam_fit({"voice_fit": {"adam": True, "david": False}}) is True


def test_adam_fit_false():
    assert worker._adam_fit({"voice_fit": {"adam": False}}) is False


def test_adam_fit_missing_voice_fit():
    assert worker._adam_fit({}) is False


def test_adam_fit_none_voice_fit():
    assert worker._adam_fit({"voice_fit": None}) is False


# ── run_video_idea_check ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_video_idea_check_dry_run_does_not_write_or_send(monkeypatch):
    monkeypatch.setattr(worker, "get_client", lambda use_service_key=False: object())

    async def fake_generate(row, voice):
        return {"hook": "h", "talking_points": ["a"], "fabrication_risk": False}

    monkeypatch.setattr(worker, "generate_video_idea", fake_generate)

    insert_called = []
    monkeypatch.setattr(
        worker.video_store,
        "insert_video_idea",
        lambda idea, client=None: insert_called.append(idea),
    )
    send_called = []

    async def fake_send(*a, **k):
        send_called.append(True)

    monkeypatch.setattr(worker.vb, "send_video_idea_for_approval", fake_send)

    result = await worker.run_video_idea_check(
        dry_run=True,
        send=False,
        item={"id": "f1", "title": "t", "voice_fit": {"adam": True}},
    )
    assert result["produced"] is not None
    assert result["produced"]["feed_item_id"] == "f1"
    assert insert_called == []  # dry_run: SOSEM ír DB-t
    assert send_called == []  # dry_run: SOSEM küld Telegramra


@pytest.mark.asyncio
async def test_run_video_idea_check_filters_out_non_adam_fit_items():
    result = await run_with_item({"id": "f1", "title": "t", "voice_fit": {"adam": False}})
    assert result["qualified"] == 0
    assert result["produced"] is None


@pytest.mark.asyncio
async def test_run_video_idea_check_skips_when_model_says_skip(monkeypatch):
    monkeypatch.setattr(worker, "get_client", lambda use_service_key=False: object())

    async def fake_generate(row, voice):
        return {"skip": True, "reason": "too dry"}

    monkeypatch.setattr(worker, "generate_video_idea", fake_generate)

    result = await worker.run_video_idea_check(
        dry_run=True,
        send=False,
        item={"id": "f1", "title": "t", "voice_fit": {"adam": True}},
    )
    assert result["produced"] is None
    assert result["tried"] == 1


@pytest.mark.asyncio
async def test_run_video_idea_check_skips_when_generate_returns_none(monkeypatch):
    monkeypatch.setattr(worker, "get_client", lambda use_service_key=False: object())

    async def fake_generate(row, voice):
        return None

    monkeypatch.setattr(worker, "generate_video_idea", fake_generate)

    result = await worker.run_video_idea_check(
        dry_run=True,
        send=False,
        item={"id": "f1", "title": "t", "voice_fit": {"adam": True}},
    )
    assert result["produced"] is None


async def run_with_item(item):
    from unittest.mock import patch

    with patch.object(worker, "get_client", lambda use_service_key=False: object()):
        return await worker.run_video_idea_check(dry_run=True, send=False, item=item)


@pytest.mark.asyncio
async def test_run_video_idea_check_handles_duplicate_race_gracefully(monkeypatch):
    # dry_run=False, hogy az insert_video_idea agat tenylegesen elerje (dry_run=True korabban
    # visszater, mielott az insert-re sor kerulne).
    monkeypatch.setattr(worker, "get_client", lambda use_service_key=False: object())
    monkeypatch.setattr(
        worker.video_store, "has_video_idea_for_feed_item", lambda fid, client=None: False
    )

    async def fake_generate(row, voice):
        return {"hook": "h", "talking_points": ["a"], "fabrication_risk": False}

    monkeypatch.setattr(worker, "generate_video_idea", fake_generate)

    def raise_duplicate(idea, client=None):
        raise worker.video_store.DuplicateVideoIdeaError(idea.get("feed_item_id"))

    monkeypatch.setattr(worker.video_store, "insert_video_idea", raise_duplicate)

    async def fake_send(*a, **k):
        raise AssertionError(
            "nem szabadna Telegramra kuldeni, ha az insert dedup-race miatt bukott"
        )

    monkeypatch.setattr(worker.vb, "send_video_idea_for_approval", fake_send)

    # nem szabad felrobbannia -- a DuplicateVideoIdeaError-t kecsesen kell kezelnie.
    result = await worker.run_video_idea_check(
        dry_run=False,
        send=True,
        item={"id": "f1", "title": "t", "voice_fit": {"adam": True}},
    )
    assert result["produced"] is None
