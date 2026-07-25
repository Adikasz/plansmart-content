"""Zero-network tests for src.core.storage.prospect_interactions (pure bucketing logic).

current_stage()/bucket_label() take an in-memory interaction list -- no Supabase client
needed, so these exercise the actual stage-computation algorithm directly.
"""

from __future__ import annotations

from src.core.storage import prospect_interactions as store


def test_current_stage_empty_history_is_none():
    assert store.current_stage("p1", []) is None


def test_current_stage_picks_latest_by_date():
    rows = [
        {"interaction_type": "connection_sent", "interaction_date": "2026-07-01T00:00:00Z"},
        {"interaction_type": "connection_accepted", "interaction_date": "2026-07-05T00:00:00Z"},
        {"interaction_type": "replied", "interaction_date": "2026-07-10T00:00:00Z"},
    ]
    assert store.current_stage("p1", rows) == "replied"


def test_current_stage_skips_note_rows():
    # A 'note' sor sosem stage-váltás -- a legutóbbi VALÓDI stage-et kell visszaadnia,
    # akkor is, ha a jegyzet a legfrissebb dátumú sor.
    rows = [
        {"interaction_type": "connection_accepted", "interaction_date": "2026-07-05T00:00:00Z"},
        {"interaction_type": "note", "interaction_date": "2026-07-20T00:00:00Z"},
    ]
    assert store.current_stage("p1", rows) == "connection_accepted"


def test_current_stage_only_notes_is_none():
    rows = [{"interaction_type": "note", "interaction_date": "2026-07-20T00:00:00Z"}]
    assert store.current_stage("p1", rows) is None


def test_bucket_label_maps_went_cold_and_not_interested_to_same_bucket():
    assert store.bucket_label("went_cold") == "Lezárva (nem érdekli)"
    assert store.bucket_label("not_interested") == "Lezárva (nem érdekli)"


def test_bucket_label_none_defaults_to_sent_bucket():
    assert store.bucket_label(None) == "Küldve, nincs válasz"


def test_bucket_label_covers_every_stage_type():
    for stage in store._STAGE_TYPES:
        label = store.bucket_label(stage)
        assert label in store.BUCKET_ORDER


def test_log_interaction_rejects_unknown_type():
    import pytest

    with pytest.raises(ValueError):
        store.log_interaction("p1", "bogus_type", client=object())
