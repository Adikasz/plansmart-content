"""Zero-network tests for src.core.storage.metrics.

collect() itself is glue over several storage-layer queries -- monkeypatched here so the
test exercises the ASSEMBLY logic (voice defaults, action-name mapping, missing-table
fallback) rather than re-testing the underlying Supabase wrappers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.storage import metrics


def test_today_start_iso_is_midnight_utc():
    now = datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc)
    assert metrics.today_start_iso(now) == "2026-07-21T00:00:00+00:00"


def test_week_start_iso_is_monday_midnight():
    # 2026-07-21 is a Tuesday -> the week started Monday 2026-07-20.
    now = datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc)
    assert metrics.week_start_iso(now) == "2026-07-20T00:00:00+00:00"


def test_week_start_iso_on_monday_returns_same_day():
    now = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
    assert metrics.week_start_iso(now) == "2026-07-20T00:00:00+00:00"


def test_collect_assembles_all_three_sections(monkeypatch):
    monkeypatch.setattr(
        "src.core.storage.posts.generated_counts_by_voice_since",
        lambda since, client=None: {"david": 3, "adam": 2},  # plansmart deliberately absent
    )
    monkeypatch.setattr(
        "src.core.storage.posts.approval_action_counts_since",
        lambda since, client=None: {"approve": 4, "skip": 1, "edited": 2, "regenerate": 1},
    )
    monkeypatch.setattr(
        "src.core.storage.posts.published_count_since", lambda since, client=None: 3
    )
    monkeypatch.setattr("src.core.storage.posts.breaking_count_since", lambda since, client=None: 1)
    monkeypatch.setattr(
        "src.core.storage.prospects.added_count_since", lambda since, client=None: 7
    )
    monkeypatch.setattr(
        "src.core.storage.prospects.approved_to_send_count_since", lambda since, client=None: 5
    )
    monkeypatch.setattr("src.core.storage.prospects.sent_count_since", lambda since, client=None: 2)
    monkeypatch.setattr(
        "src.core.storage.prospect_interactions.table_ready", lambda client=None: True
    )
    monkeypatch.setattr(
        "src.core.storage.prospect_interactions.replied_count_since", lambda since, client=None: 1
    )
    monkeypatch.setattr(
        "src.core.storage.posts.cost_summary_since",
        lambda since, client=None: {"claude_api": 1.23, "muapi_image": 0.45, "total": 1.68},
    )

    result = metrics.collect("2026-07-21T00:00:00+00:00")

    assert result["content"]["generated_total"] == 5
    # A hiányzó plansmart nem dobhat KeyError-t -- 0-nak kell számítania.
    assert result["content"]["by_voice"] == {"david": 3, "adam": 2, "plansmart": 0}
    assert result["content"]["approved"] == 4
    assert result["content"]["rejected"] == 1
    assert result["content"]["edited"] == 2
    assert result["content"]["published"] == 3
    assert result["content"]["breaking"] == 1

    assert result["outreach"]["researched"] == 7
    assert result["outreach"]["approved_to_send"] == 5
    assert result["outreach"]["sent"] == 2
    assert result["outreach"]["replied"] == 1
    assert result["outreach"]["interactions_ready"] is True

    assert result["costs"]["total"] == 1.68


def test_collect_handles_missing_prospect_interactions_table(monkeypatch):
    monkeypatch.setattr(
        "src.core.storage.posts.generated_counts_by_voice_since", lambda since, client=None: {}
    )
    monkeypatch.setattr(
        "src.core.storage.posts.approval_action_counts_since", lambda since, client=None: {}
    )
    monkeypatch.setattr(
        "src.core.storage.posts.published_count_since", lambda since, client=None: 0
    )
    monkeypatch.setattr("src.core.storage.posts.breaking_count_since", lambda since, client=None: 0)
    monkeypatch.setattr(
        "src.core.storage.prospects.added_count_since", lambda since, client=None: 0
    )
    monkeypatch.setattr(
        "src.core.storage.prospects.approved_to_send_count_since", lambda since, client=None: 0
    )
    monkeypatch.setattr("src.core.storage.prospects.sent_count_since", lambda since, client=None: 0)
    monkeypatch.setattr(
        "src.core.storage.prospect_interactions.table_ready", lambda client=None: False
    )
    monkeypatch.setattr(
        "src.core.storage.posts.cost_summary_since", lambda since, client=None: {"total": 0.0}
    )

    result = metrics.collect("2026-07-21T00:00:00+00:00")

    # Migráció előtt a "Válaszolt" sor None-t kap (a bot réteg ebből rajzolja a "—" placeholdert),
    # nem 0-t -- a kettő eltérő jelentésű (nincs adat vs. van adat, de nulla).
    assert result["outreach"]["replied"] is None
    assert result["outreach"]["interactions_ready"] is False
