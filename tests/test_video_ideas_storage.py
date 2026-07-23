"""Zero-network tests for src.storage.video_ideas (row-building + mutation logic)."""
from __future__ import annotations

import pytest
from postgrest.exceptions import APIError

from src.storage import video_ideas as store


class _CapturingQuery:
    """Minimal fake Supabase query chain that records .insert()/.update() payloads."""

    def __init__(self, get_response=None):
        self._get_response = get_response or []
        self.inserted = None
        self.updated = None

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, row):
        self.inserted = row
        return self

    def update(self, patch):
        self.updated = patch
        return self

    def upsert(self, row):
        self.inserted = row
        return self

    def execute(self):
        return type("Resp", (), {"data": list(self._get_response)})()


def test_insert_video_idea_generates_id_when_missing():
    client = _CapturingQuery()
    idea = {"voice": "adam", "hook": "h"}
    idea_id = store.insert_video_idea(idea, client=client)
    assert idea_id
    assert client.inserted["id"] == idea_id


def test_insert_video_idea_preserves_given_id():
    client = _CapturingQuery()
    idea = {"id": "fixed123", "voice": "adam", "hook": "h"}
    idea_id = store.insert_video_idea(idea, client=client)
    assert idea_id == "fixed123"
    assert client.inserted["id"] == "fixed123"


def test_insert_video_idea_defaults_talking_points_to_empty_list():
    client = _CapturingQuery()
    store.insert_video_idea({"voice": "adam", "hook": "h"}, client=client)
    assert client.inserted["talking_points"] == []


def test_insert_video_idea_defaults_status_to_drafted():
    client = _CapturingQuery()
    store.insert_video_idea({"voice": "adam", "hook": "h"}, client=client)
    assert client.inserted["status"] == "drafted"


def test_insert_video_idea_omits_fabrication_reason_when_absent():
    client = _CapturingQuery()
    store.insert_video_idea({"voice": "adam", "hook": "h"}, client=client)
    assert "fabrication_reason" not in client.inserted


def test_insert_video_idea_includes_fabrication_reason_when_present():
    client = _CapturingQuery()
    store.insert_video_idea(
        {"voice": "adam", "hook": "h", "fabrication_reason": "invented client"}, client=client
    )
    assert client.inserted["fabrication_reason"] == "invented client"


def test_has_video_idea_for_feed_item_false_for_empty_id():
    assert store.has_video_idea_for_feed_item("", client=_CapturingQuery()) is False
    assert store.has_video_idea_for_feed_item(None, client=_CapturingQuery()) is False


def test_has_video_idea_for_feed_item_true_when_row_exists():
    client = _CapturingQuery(get_response=[{"id": "existing"}])
    assert store.has_video_idea_for_feed_item("f1", client=client) is True


def test_has_video_idea_for_feed_item_false_when_no_row():
    client = _CapturingQuery(get_response=[])
    assert store.has_video_idea_for_feed_item("f1", client=client) is False


def test_update_status_skips_none_fields():
    client = _CapturingQuery()
    store.update_status("v1", "approved", client=client, approved_by=None, approved_at="2026-01-01")
    assert "approved_by" not in client.updated
    assert client.updated["approved_at"] == "2026-01-01"
    assert client.updated["status"] == "approved"


def test_mark_edited_increments_edit_count(monkeypatch):
    client = _CapturingQuery(get_response=[{"id": "v1", "edit_count": 2}])
    store.mark_edited("v1", "corrected notes", client=client)
    assert client.updated["edit_count"] == 3
    assert client.updated["edited_notes"] == "corrected notes"
    assert client.updated["status"] == "edited"


def test_mark_edited_starts_at_one_when_no_prior_count():
    client = _CapturingQuery(get_response=[{"id": "v1"}])
    store.mark_edited("v1", "notes", client=client)
    assert client.updated["edit_count"] == 1


def test_update_content_resets_status_to_drafted():
    client = _CapturingQuery()
    store.update_content("v1", {"hook": "new hook", "talking_points": ["x"]}, client=client)
    assert client.updated["status"] == "drafted"
    assert client.updated["hook"] == "new hook"


# ── DuplicateVideoIdeaError (TOCTOU dedup race, review finding) ─────────
class _RaisingQuery(_CapturingQuery):
    def __init__(self, exc):
        super().__init__()
        self._exc = exc

    def execute(self):
        raise self._exc


def test_insert_video_idea_raises_duplicate_error_on_unique_violation():
    exc = APIError({"code": "23505", "message": "duplicate key value violates unique constraint"})
    client = _RaisingQuery(exc)
    with pytest.raises(store.DuplicateVideoIdeaError) as excinfo:
        store.insert_video_idea({"voice": "adam", "hook": "h", "feed_item_id": "f1"}, client=client)
    assert excinfo.value.feed_item_id == "f1"


def test_insert_video_idea_reraises_other_api_errors_unchanged():
    exc = APIError({"code": "PGRST205", "message": "table not found"})
    client = _RaisingQuery(exc)
    with pytest.raises(APIError):
        store.insert_video_idea({"voice": "adam", "hook": "h"}, client=client)
