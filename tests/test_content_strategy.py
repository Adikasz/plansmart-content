"""Zero-network tests for src.strategy.content_strategy.

Reads the checked-in config/content_strategy.yml + prompts/*.yml from disk (allowed,
no network). Uses accounts()/target_distribution() to get REAL account names so the
tests do not hardcode an account that might disappear from config.
"""
from __future__ import annotations

import pytest

from src.strategy.content_strategy import (
    CONTENT_TYPES,
    _voice_ok,
    accounts,
    distribution_report,
    get_seed,
    next_content_type,
    target_distribution,
    weekly_target,
)


# ── helpers ────────────────────────────────────────────────────────────
def _real_account() -> str:
    accs = accounts()
    assert accs, "config must define at least one account"
    return accs[0]


# ── next_content_type ──────────────────────────────────────────────────
def test_next_content_type_empty_counts_returns_target_member():
    account = _real_account()
    target = target_distribution(account)
    assert target, "real account must have a target distribution"

    result = next_content_type(account, {})
    assert result in CONTENT_TYPES
    # With no posts yet, the pick must be a type the account actually targets.
    assert result in target


def test_next_content_type_overrepresented_type_shifts_away():
    account = _real_account()
    target = target_distribution(account)
    # Behaviour is only meaningful when the target offers alternatives.
    assert len(target) >= 2

    first = next_content_type(account, {})
    # Pile many posts of the currently-recommended type: its gap goes negative,
    # so the recommendation must move to a different (under-represented) type.
    counts = {first: 50}
    shifted = next_content_type(account, counts)

    assert shifted in target
    assert shifted != first


def test_next_content_type_unknown_account_returns_ai_news():
    assert next_content_type("does_not_exist", {}) == "ai_news"


def test_next_content_type_always_in_content_types_for_real_accounts():
    for account in accounts():
        assert next_content_type(account, {}) in CONTENT_TYPES


# ── _voice_ok ──────────────────────────────────────────────────────────
def test_voice_ok_all_matches_any_voice():
    assert _voice_ok("all", "david") is True
    assert _voice_ok("all", "adam") is True
    assert _voice_ok(["all"], "plansmart") is True


def test_voice_ok_matching_string_and_list():
    assert _voice_ok("david", "david") is True
    assert _voice_ok(["david", "adam"], "adam") is True
    assert _voice_ok(["david", "adam"], "david") is True


def test_voice_ok_case_insensitive():
    assert _voice_ok("David", "david") is True
    assert _voice_ok("david", "DAVID") is True
    assert _voice_ok(["Adam", "Plansmart"], "adam") is True


def test_voice_ok_non_matching_returns_false():
    assert _voice_ok("adam", "david") is False
    assert _voice_ok(["adam", "plansmart"], "david") is False


def test_voice_ok_empty_or_none_returns_false():
    assert _voice_ok(None, "david") is False
    assert _voice_ok([], "david") is False
    assert _voice_ok("", "david") is False


# ── distribution_report ────────────────────────────────────────────────
def test_distribution_report_has_all_keys():
    account = _real_account()
    report = distribution_report(account, {})
    expected = {
        "account",
        "target",
        "actual",
        "counts",
        "total_posts",
        "weekly_target",
        "next_type",
    }
    assert expected.issubset(report.keys())


def test_distribution_report_values_are_consistent():
    account = _real_account()
    target = target_distribution(account)
    # Use a real target type so counts/actual reflect it.
    some_type = next(iter(target))
    counts = {some_type: 3}

    report = distribution_report(account, counts)

    assert report["account"] == account
    assert report["target"] == target
    assert report["total_posts"] == 3
    assert report["weekly_target"] == weekly_target(account)
    assert report["next_type"] == next_content_type(account, counts)
    # counts in the report is restricted to the account's target keys.
    assert set(report["counts"]).issubset(set(target))
    assert report["counts"][some_type] == 3
    # actual is a per-target fraction; the over-represented type is at 1.0 here
    # (only one type has posts) — but only if it is the sole counted type.
    assert set(report["actual"]) == set(target)
    assert report["actual"][some_type] == pytest.approx(1.0)


# ── get_seed ───────────────────────────────────────────────────────────
def test_get_seed_ai_news_returns_none():
    assert get_seed("ai_news", "david", set()) is None


def test_get_seed_case_study_returns_manual_instruction():
    # case_studies.yml: every entry lists plansmart in voice_fit -> guaranteed match.
    seed = get_seed("case_study", "plansmart", set())
    assert isinstance(seed, dict)
    assert seed["type"] == "manual_instruction"
    assert seed["content_type"] == "case_study"
    assert seed["voice"] == "plansmart"
    assert seed["platform"] == "linkedin"
    assert seed["seed_key"]  # non-empty
    assert "instruction" in seed and seed["instruction"]


def test_get_seed_workshop_all_voice_matches():
    # workshop_topics.yml first entry has voice_fit [all] -> matches any voice.
    seed = get_seed("workshop_promo", "david", set())
    assert isinstance(seed, dict)
    assert seed["content_type"] == "workshop_promo"
    assert seed["type"] == "manual_instruction"
    assert seed["voice"] == "david"
    assert seed["platform"] == "linkedin"


def test_get_seed_instruction_embeds_source_details():
    seed = get_seed("case_study", "plansmart", set())
    assert seed is not None
    # The seed_key for a case_study is the client name, and it must appear in the
    # instruction text handed to the generator.
    assert seed["seed_key"] in seed["instruction"]


def test_get_seed_dedup_skips_used_key():
    seed1 = get_seed("case_study", "plansmart", set())
    assert seed1 is not None

    # Re-requesting with the first seed_key marked used must NOT return it again:
    # either a different seed or None.
    seed2 = get_seed("case_study", "plansmart", {seed1["seed_key"]})
    assert seed2 is None or seed2["seed_key"] != seed1["seed_key"]

    # case_studies.yml has multiple plansmart-fit entries, so we expect a distinct one.
    assert seed2 is not None
    assert seed2["seed_key"] != seed1["seed_key"]


def test_get_seed_all_keys_used_returns_none():
    # Exhaust every possible plansmart case_study key -> must yield None.
    keys_seen: set[str] = set()
    while True:
        seed = get_seed("case_study", "plansmart", keys_seen)
        if seed is None:
            break
        assert seed["seed_key"] not in keys_seen
        keys_seen.add(seed["seed_key"])
    assert get_seed("case_study", "plansmart", keys_seen) is None
    assert keys_seen  # at least one was found before exhaustion
