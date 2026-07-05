"""Zero-network unit tests for src/visuals/layout_templates.py — pure constants/lookups.

Covers: templates_for (voice-specific + fallback), moods_for/mood_keys, accent_for/mood_bg
(with unknown-key fallback to first mood), template_spec/layout_of/template_bg (unknown ->
STAT_CARD spec), is_template, and DEFAULT_MOOD consistency.
"""
from __future__ import annotations

import pytest

from src.visuals.layout_templates import (
    DEFAULT_MOOD,
    MINIMAL_TYPOGRAPHIC,
    QUOTE_STYLE,
    SPLIT_COMPARISON,
    STAT_CARD,
    TEMPLATES,
    accent_for,
    is_template,
    layout_of,
    mood_bg,
    mood_keys,
    moods_for,
    template_bg,
    template_spec,
    templates_for,
)


# ── templates_for ──────────────────────────────────────────────────────
def test_templates_for_david_is_the_narrowed_two_set():
    assert templates_for("david") == (STAT_CARD, SPLIT_COMPARISON)


def test_templates_for_david_has_exactly_two():
    assert len(templates_for("david")) == 2


def test_templates_for_adam_is_all_four():
    result = templates_for("adam")
    assert result == TEMPLATES
    assert len(result) == 4


def test_templates_for_plansmart_has_four():
    result = templates_for("plansmart")
    assert len(result) == 4
    # plansmart isn't in VOICE_TEMPLATES, so it falls back to the full set
    assert result == TEMPLATES


def test_templates_for_unknown_falls_back_to_all_templates():
    assert templates_for("totally-unknown-voice") == TEMPLATES


def test_templates_constant_fixed_order():
    # The rotation walks this order — assert the documented fixed sequence.
    assert TEMPLATES == (STAT_CARD, QUOTE_STYLE, SPLIT_COMPARISON, MINIMAL_TYPOGRAPHIC)


# ── moods_for / mood_keys ──────────────────────────────────────────────
@pytest.mark.parametrize("voice", ["david", "adam", "plansmart"])
def test_moods_for_each_voice_has_three(voice):
    assert len(moods_for(voice)) == 3


@pytest.mark.parametrize("voice", ["david", "adam", "plansmart"])
def test_mood_keys_each_voice_has_three(voice):
    keys = mood_keys(voice)
    assert len(keys) == 3
    assert all(isinstance(k, str) for k in keys)


def test_mood_keys_matches_keys_in_moods_for():
    assert mood_keys("david") == [m["key"] for m in moods_for("david")]


def test_moods_for_unknown_voice_falls_back_to_plansmart():
    assert moods_for("nope") == moods_for("plansmart")


def test_mood_keys_unknown_voice_falls_back_to_plansmart():
    assert mood_keys("nope") == mood_keys("plansmart")


# ── accent_for ─────────────────────────────────────────────────────────
def test_accent_for_known_mood_returns_that_moods_accent():
    # david[0] is "teal" -> (45, 212, 191)
    assert accent_for("david", "teal") == (45, 212, 191)


@pytest.mark.parametrize("voice", ["david", "adam", "plansmart"])
def test_accent_for_returns_three_int_tuple(voice):
    key = mood_keys(voice)[1]  # a non-default key to exercise the loop
    accent = accent_for(voice, key)
    assert isinstance(accent, tuple)
    assert len(accent) == 3
    assert all(isinstance(c, int) for c in accent)


def test_accent_for_unknown_mood_key_falls_back_to_first_mood():
    first_accent = moods_for("david")[0]["accent"]
    assert accent_for("david", "no-such-mood") == tuple(first_accent)


def test_accent_for_unknown_voice_and_key_uses_plansmart_first_mood():
    first = tuple(moods_for("plansmart")[0]["accent"])
    assert accent_for("ghost-voice", "ghost-mood") == first


# ── mood_bg ────────────────────────────────────────────────────────────
def test_mood_bg_known_mood_returns_that_bg():
    expected = moods_for("adam")[2]["bg"]  # "soft_cool_blue"
    assert mood_bg("adam", "soft_cool_blue") == expected


def test_mood_bg_unknown_mood_key_falls_back_to_first_mood():
    first_bg = moods_for("plansmart")[0]["bg"]
    assert mood_bg("plansmart", "no-such-mood") == first_bg


def test_mood_bg_returns_string():
    assert isinstance(mood_bg("david", "teal"), str)


# ── template_spec / layout_of / template_bg ────────────────────────────
def test_template_spec_unknown_returns_stat_card_spec():
    assert template_spec("unknown") == template_spec(STAT_CARD)


def test_template_spec_known_returns_own_spec():
    spec = template_spec(SPLIT_COMPARISON)
    assert spec["layout"] == "split"


def test_layout_of_stat_card():
    assert layout_of(STAT_CARD) == "stat_card"


@pytest.mark.parametrize(
    "template,expected_layout",
    [
        (STAT_CARD, "stat_card"),
        (QUOTE_STYLE, "quote"),
        (SPLIT_COMPARISON, "split"),
        (MINIMAL_TYPOGRAPHIC, "minimal"),
    ],
)
def test_layout_of_each_template(template, expected_layout):
    assert layout_of(template) == expected_layout


def test_layout_of_unknown_reads_stat_card_layout():
    assert layout_of("mystery") == "stat_card"


def test_template_bg_reads_bg_from_spec():
    assert template_bg(STAT_CARD) == template_spec(STAT_CARD)["bg"]
    assert isinstance(template_bg(MINIMAL_TYPOGRAPHIC), str)


def test_template_bg_unknown_falls_back_to_stat_card_bg():
    assert template_bg("mystery") == template_spec(STAT_CARD)["bg"]


# ── is_template ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "name", [STAT_CARD, QUOTE_STYLE, SPLIT_COMPARISON, MINIMAL_TYPOGRAPHIC]
)
def test_is_template_true_for_known(name):
    assert is_template(name) is True


@pytest.mark.parametrize("name", ["nope", "", "stat_card", "STATCARD"])
def test_is_template_false_for_unknown(name):
    # note: is_template checks the TEMPLATE constant names, not the layout values,
    # so the lowercase layout "stat_card" is NOT a template name.
    assert is_template(name) is False


# ── DEFAULT_MOOD consistency ───────────────────────────────────────────
@pytest.mark.parametrize("voice", ["david", "adam", "plansmart"])
def test_default_mood_is_first_mood_key(voice):
    assert DEFAULT_MOOD[voice] == moods_for(voice)[0]["key"]


def test_default_mood_has_all_three_voices():
    assert set(DEFAULT_MOOD.keys()) == {"david", "adam", "plansmart"}
