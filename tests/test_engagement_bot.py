"""Zero-network tests for src.integrations.bots.engagement_bot (arg parsing + Telegram text formatting)."""
from __future__ import annotations

import pytest

from src.integrations.bots import engagement_bot as bot
from src.core.storage import engagement_report as report


# ── _parse_kv_args ───────────────────────────────────────────────────────
def test_parse_kv_args_basic():
    post_id, kv = bot._parse_kv_args("abc123 views=450 likes=12 comments=3 shares=1")
    assert post_id == "abc123"
    assert kv == {"views": "450", "likes": "12", "comments": "3", "shares": "1"}


def test_parse_kv_args_post_id_only():
    post_id, kv = bot._parse_kv_args("abc123")
    assert post_id == "abc123"
    assert kv == {}


def test_parse_kv_args_empty_string():
    post_id, kv = bot._parse_kv_args("")
    assert post_id == ""
    assert kv == {}


def test_parse_kv_args_ignores_malformed_token():
    post_id, kv = bot._parse_kv_args("abc123 views=450 garbage")
    assert kv == {"views": "450"}


def test_parse_kv_args_case_insensitive_keys():
    _, kv = bot._parse_kv_args("abc123 VIEWS=10")
    assert kv == {"views": "10"}


# ── _parse_int ───────────────────────────────────────────────────────────
def test_parse_int_valid():
    assert bot._parse_int("450") == 450


def test_parse_int_none_passthrough():
    assert bot._parse_int(None) is None


def test_parse_int_empty_string_passthrough():
    assert bot._parse_int("") is None


def test_parse_int_invalid_raises():
    with pytest.raises(ValueError):
        bot._parse_int("not-a-number")


# ── _fmt_group_line ───────────────────────────────────────────────────────
def test_fmt_group_line_none_stats_shows_no_data():
    assert "nincs adat" in bot._fmt_group_line("david", None)


def test_fmt_group_line_zero_n_shows_no_data():
    assert "nincs adat" in bot._fmt_group_line("david", {"n": 0})


def test_fmt_group_line_below_threshold_shows_caveat():
    stats = {"n": 3, "avg_views": 10.0, "avg_likes": 1.0, "avg_comments": 0.0, "avg_shares": 0.0,
              "low_confidence": True}
    line = bot._fmt_group_line("david", stats)
    assert "kevés adat" in line
    assert "n=3" in line


def test_fmt_group_line_at_threshold_no_caveat():
    stats = {"n": 5, "avg_views": 10.0, "avg_likes": 1.0, "avg_comments": 0.0, "avg_shares": 0.0,
              "low_confidence": False}
    line = bot._fmt_group_line("david", stats)
    assert "kevés adat" not in line
    assert "n=5" in line


# ── format_report ─────────────────────────────────────────────────────────
def test_format_report_zero_posts_message():
    text = bot.format_report(report.build_report({}, []))
    assert "Még nincs logolt engagement adat" in text


def test_format_report_single_post_shows_low_confidence_and_advisory():
    posts_by_id = {"p1": {"voice": "david", "hook_type": "A", "is_breaking": False,
                            "metadata": {"strategy_type": "educational"}}}
    rows = [{"post_id": "p1", "views": 100, "likes": 10, "comments": 2, "shares": 1,
             "hours_since_post": 24, "is_final_snapshot": False, "measured_at": "2026-07-01T00:00:00+00:00"}]
    data = report.build_report(posts_by_id, rows)
    text = bot.format_report(data)
    assert "n=1" in text
    assert "kevés adat" in text
    assert "várj legalább 15-20 mintát" in text
    assert "david" in text


def test_format_report_includes_all_three_voices_even_when_empty():
    posts_by_id = {"p1": {"voice": "david", "hook_type": "A", "is_breaking": False,
                            "metadata": {"strategy_type": "educational"}}}
    rows = [{"post_id": "p1", "views": 10, "likes": None, "comments": None, "shares": None,
             "hours_since_post": 1, "is_final_snapshot": False, "measured_at": "x"}]
    data = report.build_report(posts_by_id, rows)
    text = bot.format_report(data)
    assert "adam" in text
    assert "plansmart" in text


def test_format_report_flags_missing_hook_type_footnote():
    posts_by_id = {"p1": {"voice": "david", "hook_type": None, "is_breaking": False, "metadata": {}}}
    rows = [{"post_id": "p1", "views": 10, "likes": None, "comments": None, "shares": None,
             "hours_since_post": 1, "is_final_snapshot": False, "measured_at": "x"}]
    data = report.build_report(posts_by_id, rows)
    text = bot.format_report(data)
    assert "hook_type nélkül" in text
