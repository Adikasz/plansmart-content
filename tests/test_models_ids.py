"""Zero-network unit tests for src/utils/ids.py and src/core/storage/models.py.

Covers:
- make_id: sha256 hex prefix, determinism, length, lowercase.
- Re-export identity of make_id across the two modules.
- utcnow_iso: ISO-8601, fromisoformat-parseable, timezone-aware; _utcnow_iso alias.
- FeedItem defaults + score typing.
- FeedItem.to_row shape / mapping.
"""
from __future__ import annotations

import hashlib
import string
from datetime import datetime

from src.core.storage.models import make_id as make_id_from_models
from src.utils.ids import _utcnow_iso, make_id, utcnow_iso


# ── make_id ────────────────────────────────────────────────────────────
def test_make_id_matches_sha256_prefix():
    url = "https://example.com/some/post?a=1"
    expected = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    assert make_id(url) == expected


def test_make_id_length_is_16():
    assert len(make_id("https://example.com/x")) == 16


def test_make_id_deterministic_same_url_same_id():
    url = "https://news.ycombinator.com/item?id=42"
    assert make_id(url) == make_id(url)


def test_make_id_different_url_different_id():
    a = make_id("https://example.com/a")
    b = make_id("https://example.com/b")
    assert a != b


def test_make_id_is_lowercase_hex():
    result = make_id("https://Example.com/MixedCase?Q=Z")
    hex_lower = set(string.hexdigits.lower())  # 0-9 a-f (plus already-lower)
    assert all(c in "0123456789abcdef" for c in result)
    # explicit lowercase invariant (sha256 hexdigest is already lowercase)
    assert result == result.lower()
    assert hex_lower  # sanity: charset non-empty


def test_make_id_empty_string_is_stable_16_hex():
    result = make_id("")
    assert result == hashlib.sha256(b"").hexdigest()[:16]
    assert len(result) == 16


# ── re-export identity ─────────────────────────────────────────────────
def test_make_id_reexport_is_same_function_object():
    # The models module must re-export the exact same callable, not a copy/wrapper.
    assert make_id_from_models is make_id


# ── utcnow_iso ─────────────────────────────────────────────────────────
def test_utcnow_iso_returns_str():
    value = utcnow_iso()
    assert isinstance(value, str)
    assert value  # non-empty


def test_utcnow_iso_parseable_by_fromisoformat():
    parsed = datetime.fromisoformat(utcnow_iso())
    assert isinstance(parsed, datetime)


def test_utcnow_iso_is_timezone_aware_with_offset():
    parsed = datetime.fromisoformat(utcnow_iso())
    # timezone-aware => tzinfo present and a concrete UTC offset resolvable
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


def test_private_alias_is_same_callable():
    assert _utcnow_iso is utcnow_iso


# ── FeedItem defaults ──────────────────────────────────────────────────
def test_feed_item_default_tags_is_empty_list(make_feed_item):
    item = make_feed_item()
    assert item.tags == []


def test_feed_item_default_raw_data_is_empty_dict(make_feed_item):
    item = make_feed_item()
    assert item.raw_data == {}


def test_feed_item_default_source_type_is_rss(make_feed_item):
    item = make_feed_item()
    assert item.source_type == "rss"


def test_feed_item_default_fetched_at_is_nonempty_iso_str(make_feed_item):
    item = make_feed_item()
    assert isinstance(item.fetched_at, str)
    assert item.fetched_at
    # must be a real ISO-8601 timestamp
    parsed = datetime.fromisoformat(item.fetched_at)
    assert parsed.tzinfo is not None


def test_feed_item_score_accepts_int(make_feed_item):
    item = make_feed_item(score=7)
    assert item.score == 7


def test_feed_item_score_accepts_none(make_feed_item):
    item = make_feed_item(score=None)
    assert item.score is None


def test_feed_item_default_mutables_are_independent(make_feed_item):
    # default_factory must not share a single list/dict across instances.
    a = make_feed_item()
    b = make_feed_item()
    a.tags.append("x")
    a.raw_data["k"] = "v"
    assert b.tags == []
    assert b.raw_data == {}


# ── FeedItem.to_row ────────────────────────────────────────────────────
def test_to_row_has_expected_keys(make_feed_item):
    item = make_feed_item()
    row = item.to_row()
    expected_keys = {
        "id",
        "source_type",
        "source_name",
        "source_priority",
        "url",
        "title",
        "content",
        "author",
        "published_at",
        "fetched_at",
        "topics",
        "raw_data",
        "status",
    }
    assert expected_keys.issubset(row.keys())


def test_to_row_topics_equals_tags(make_feed_item):
    item = make_feed_item(tags=["ai", "llm"])
    row = item.to_row()
    assert row["topics"] == ["ai", "llm"]
    assert row["topics"] == item.tags


def test_to_row_status_is_new(make_feed_item):
    row = make_feed_item().to_row()
    assert row["status"] == "new"


def test_to_row_carries_core_field_values(make_feed_item):
    item = make_feed_item(
        id="abc123",
        source_name="Src",
        source_priority=3,
        url="https://example.com/y",
        title="T",
        content="C",
    )
    row = item.to_row()
    assert row["id"] == "abc123"
    assert row["source_type"] == "rss"
    assert row["source_name"] == "Src"
    assert row["source_priority"] == 3
    assert row["url"] == "https://example.com/y"
    assert row["title"] == "T"
    assert row["content"] == "C"
    assert row["fetched_at"] == item.fetched_at
    assert row["raw_data"] == item.raw_data


def test_to_row_returns_plain_dict(make_feed_item):
    assert isinstance(make_feed_item().to_row(), dict)
