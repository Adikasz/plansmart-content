"""Zero-network tests for src/config/settings.py.

Only the PURE `Settings.from_env(dict)` path + the `_as_*` helpers are exercised
with explicit dicts, so nothing depends on the ambient os.environ. `get_settings()`
is only checked for type + caching identity (its values come from the real env).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.settings import (
    Settings,
    _as_bool,
    _as_float,
    _as_int,
    get_settings,
)


# ── _as_bool ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("truthy", ["true", "1", "yes", "on", "ON", "Yes"])
def test_as_bool_truthy_values(truthy):
    assert _as_bool(truthy) is True


def test_as_bool_falsey_literals_return_false():
    # Non-empty, non-truthy strings are False regardless of the default arg.
    assert _as_bool("false") is False
    assert _as_bool("0") is False
    assert _as_bool("false", default=True) is False


def test_as_bool_empty_and_none_return_default():
    # "" and None fall back to the given default.
    assert _as_bool("", default=False) is False
    assert _as_bool("", default=True) is True
    assert _as_bool(None, default=False) is False
    assert _as_bool(None, default=True) is True


def test_as_bool_default_is_false_when_omitted():
    assert _as_bool(None) is False
    assert _as_bool("") is False


def test_as_bool_strips_whitespace():
    assert _as_bool("  true  ") is True
    assert _as_bool("   ") is False  # whitespace-only -> stripped to "" -> default


# ── _as_int ────────────────────────────────────────────────────────────
def test_as_int_parses_valid():
    assert _as_int("42", 0) == 42
    assert _as_int("  7 ", 0) == 7
    assert _as_int("-3", 0) == -3


def test_as_int_falls_back_on_invalid():
    assert _as_int(None, 99) == 99
    assert _as_int("", 99) == 99
    assert _as_int("abc", 99) == 99
    assert _as_int("1.5", 99) == 99  # not a valid int literal


# ── _as_float ──────────────────────────────────────────────────────────
def test_as_float_parses_valid():
    assert _as_float("7.5", 0.0) == 7.5
    assert _as_float("  3 ", 0.0) == 3.0
    assert _as_float("10", 0.0) == 10.0


def test_as_float_falls_back_on_invalid():
    assert _as_float(None, 1.25) == 1.25
    assert _as_float("", 1.25) == 1.25
    assert _as_float("abc", 1.25) == 1.25


# ── Settings.from_env defaults ─────────────────────────────────────────
def test_from_env_empty_defaults():
    s = Settings.from_env({})
    assert s.collector_interval_hours == 2
    assert s.text_ship_threshold == 9.0
    assert s.linkedin_mock is False
    assert s.breaking_news_enabled is True
    assert s.log_level == "INFO"
    assert s.timezone == "Europe/Budapest"


def test_from_env_empty_secrets_are_none():
    s = Settings.from_env({})
    assert s.anthropic_api_key is None
    assert s.supabase_url is None
    assert s.supabase_key is None
    assert s.supabase_service_key is None
    assert s.telegram_bot_token is None
    assert s.muapi_api_key is None
    assert s.sentry_dsn is None
    assert s.webhook_base_url is None
    assert s.railway_environment is None


def test_from_env_empty_more_defaults():
    s = Settings.from_env({})
    assert s.telegram_posts_chat_id == 0
    assert s.telegram_reactions_chat_id == 0
    assert s.port == 8080
    assert s.morning_post_time == "07:30"
    assert s.dry_run is False
    assert s.text_auto_improve is False
    assert s.max_breaking_per_day == 3
    assert s.filter_batch_limit == 50
    assert s.relevance_threshold == 6
    assert s.rss_timeout_sec == 15.0
    assert s.rss_max_concurrency == 10
    assert s.rss_max_items == 50
    assert s.rss_max_retries == 3


# ── Clamping / parsing ─────────────────────────────────────────────────
def test_collector_interval_clamped_to_min_one():
    assert Settings.from_env({"COLLECTOR_INTERVAL_HOURS": "0"}).collector_interval_hours == 1
    # A blank/invalid value uses the default (2), which is also >= 1.
    assert Settings.from_env({"COLLECTOR_INTERVAL_HOURS": "-5"}).collector_interval_hours == 1
    assert Settings.from_env({"COLLECTOR_INTERVAL_HOURS": "6"}).collector_interval_hours == 6


def test_linkedin_mock_parsed():
    assert Settings.from_env({"LINKEDIN_MOCK": "on"}).linkedin_mock is True
    assert Settings.from_env({"LINKEDIN_MOCK": "false"}).linkedin_mock is False


def test_text_ship_threshold_parsed():
    assert Settings.from_env({"TEXT_SHIP_THRESHOLD": "7.5"}).text_ship_threshold == 7.5


def test_timezone_falls_back_to_scheduler_tz():
    # No TIMEZONE key, only SCHEDULER_TZ -> that value wins over the hard default.
    s = Settings.from_env({"SCHEDULER_TZ": "America/New_York"})
    assert s.timezone == "America/New_York"


def test_timezone_prefers_timezone_over_scheduler_tz():
    s = Settings.from_env({"TIMEZONE": "UTC", "SCHEDULER_TZ": "America/New_York"})
    assert s.timezone == "UTC"


def test_breaking_news_can_be_disabled():
    assert Settings.from_env({"BREAKING_NEWS_ENABLED": "false"}).breaking_news_enabled is False


# ── Validation ─────────────────────────────────────────────────────────
def test_relevance_threshold_out_of_range_raises():
    with pytest.raises(ValidationError):
        Settings.from_env({"RELEVANCE_THRESHOLD": "99"})


def test_relevance_threshold_in_range_ok():
    assert Settings.from_env({"RELEVANCE_THRESHOLD": "10"}).relevance_threshold == 10
    assert Settings.from_env({"RELEVANCE_THRESHOLD": "0"}).relevance_threshold == 0


# ── Frozen model ───────────────────────────────────────────────────────
def test_settings_is_frozen():
    s = Settings.from_env({})
    with pytest.raises(ValidationError):
        s.log_level = "DEBUG"


# ── get_settings caching ───────────────────────────────────────────────
def test_get_settings_returns_settings_and_is_cached():
    s = get_settings()
    assert isinstance(s, Settings)
    assert get_settings() is get_settings()
    assert get_settings() is s
