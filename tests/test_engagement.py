"""Zero-network tests for src.storage.engagement (validation + hours_since_post computation).

posts.get_post is monkeypatched so these exercise the actual date-math / auto-final logic in
log() without touching Supabase. The fake_supabase fixture stands in for the insert call --
its return value isn't asserted on (log()'s return dict is computed independently beforehand).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.storage import engagement


def test_log_raises_on_all_metrics_none():
    with pytest.raises(ValueError):
        engagement.log("p1")


def test_log_raises_on_missing_post(monkeypatch, fake_supabase):
    monkeypatch.setattr("src.storage.posts.get_post", lambda post_id, client=None: None)
    with pytest.raises(ValueError):
        engagement.log("nope", views=1, client=fake_supabase())


def test_log_missing_sent_at_warns_and_leaves_hours_none(monkeypatch, fake_supabase):
    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "david", "sent_at": None},
    )
    result = engagement.log("p1", views=100, client=fake_supabase())
    assert result["hours_since_post"] is None
    assert result["is_final_snapshot"] is False
    assert result["warning"] is not None
    assert "mark_posted" in result["warning"]


def test_log_computes_hours_since_post_from_sent_at(monkeypatch, fake_supabase):
    sent = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "adam", "sent_at": sent},
    )
    result = engagement.log("p1", views=100, client=fake_supabase())
    assert result["hours_since_post"] in (9, 10)  # kis időzítési tolerancia a teszt futása alatt
    assert result["is_final_snapshot"] is False
    assert result["warning"] is None


def test_log_auto_marks_final_at_48h_plus(monkeypatch, fake_supabase):
    sent = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "adam", "sent_at": sent},
    )
    result = engagement.log("p1", views=100, client=fake_supabase())
    assert result["is_final_snapshot"] is True


def test_log_not_final_below_48h(monkeypatch, fake_supabase):
    sent = (datetime.now(timezone.utc) - timedelta(hours=47)).isoformat()
    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "adam", "sent_at": sent},
    )
    result = engagement.log("p1", views=100, client=fake_supabase())
    assert result["is_final_snapshot"] is False


def test_log_manual_final_override_true(monkeypatch, fake_supabase):
    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "adam", "sent_at": None},
    )
    result = engagement.log("p1", views=100, is_final_snapshot=True, client=fake_supabase())
    assert result["is_final_snapshot"] is True


def test_log_manual_final_override_false_even_past_48h(monkeypatch, fake_supabase):
    sent = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "adam", "sent_at": sent},
    )
    result = engagement.log("p1", views=100, is_final_snapshot=False, client=fake_supabase())
    assert result["is_final_snapshot"] is False


def test_log_accepts_single_metric_only(monkeypatch, fake_supabase):
    # csak views megadva -- nem dobhat, a többinek None-nak kell maradnia a mentett sorban.
    captured = {}

    class _Query(fake_supabase().__class__):
        def insert(self, row):
            captured.update(row)
            return self

    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "david", "sent_at": None},
    )
    engagement.log("p1", views=100, client=_Query())
    assert captured["views"] == 100
    assert captured["likes"] is None
    assert captured["comments"] is None
    assert captured["shares"] is None


def test_log_future_sent_at_clamps_hours_to_zero(monkeypatch, fake_supabase):
    # óra-eltolódás / clock skew védelem: sosem lehet negatív hours_since_post.
    sent = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    monkeypatch.setattr(
        "src.storage.posts.get_post",
        lambda post_id, client=None: {"id": post_id, "voice": "adam", "sent_at": sent},
    )
    result = engagement.log("p1", views=100, client=fake_supabase())
    assert result["hours_since_post"] == 0
