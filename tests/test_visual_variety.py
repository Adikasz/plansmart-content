"""Zero-network tests for src.integrations.visuals.visual_variety.

Covers the deterministic no-immediate-repeat rotation:
  • _next_template / _next_mood wrap within the voice's allowed set / palette,
  • _decide baseline (empty state) + next-step (differs from last),
  • next_variant end-to-end with an IN-MEMORY store injected via monkeypatch
    on the module globals _sb_get / _sb_upsert (NEVER hits Supabase).

Mirrors the module's own _demo() pattern for the store fakes.
"""

from __future__ import annotations

import pytest

import src.integrations.visuals.layout_templates as lt
import src.integrations.visuals.visual_variety as vv

VOICES = ("david", "adam", "plansmart")


# ── In-memory store injection (mirror of _demo) ────────────────────────
def _install_store(monkeypatch) -> dict:
    """Patch the module globals with in-memory fakes; return the backing store."""
    store: dict[str, dict] = {}

    def fake_get(v):
        return dict(store[v]) if v in store else None

    def fake_upsert(v, t, m):
        store[v] = {"last_template": t, "last_mood": m}

    monkeypatch.setattr(vv, "_sb_get", fake_get)
    monkeypatch.setattr(vv, "_sb_upsert", fake_upsert)
    return store


# ── _next_template ─────────────────────────────────────────────────────
@pytest.mark.parametrize("voice", VOICES)
def test_next_template_none_returns_first(voice):
    assert vv._next_template(voice, None) == lt.templates_for(voice)[0]


def test_next_template_outside_set_returns_first_david():
    # QUOTE_STYLE is a valid global template but NOT in david's restricted 2-set.
    assert lt.QUOTE_STYLE not in lt.templates_for("david")
    assert vv._next_template("david", lt.QUOTE_STYLE) == lt.templates_for("david")[0]


def test_next_template_unknown_name_returns_first():
    assert vv._next_template("adam", "NOT_A_REAL_TEMPLATE") == lt.templates_for("adam")[0]


def test_next_template_david_cycle():
    # david: STAT_CARD -> SPLIT_COMPARISON -> STAT_CARD (2-element wrap).
    assert vv._next_template("david", lt.STAT_CARD) == lt.SPLIT_COMPARISON
    assert vv._next_template("david", lt.SPLIT_COMPARISON) == lt.STAT_CARD


def test_next_template_wraps_full_set_adam():
    templates = lt.templates_for("adam")
    assert len(templates) == 4
    for i, t in enumerate(templates):
        expected = templates[(i + 1) % len(templates)]
        assert vv._next_template("adam", t) == expected
    # explicit wrap: last -> first
    assert vv._next_template("adam", templates[-1]) == templates[0]


# ── _next_mood ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("voice", VOICES)
def test_next_mood_none_returns_first(voice):
    assert vv._next_mood(voice, None) == lt.mood_keys(voice)[0]


@pytest.mark.parametrize("voice", VOICES)
def test_next_mood_unknown_returns_first(voice):
    assert vv._next_mood(voice, "not-a-mood") == lt.mood_keys(voice)[0]


@pytest.mark.parametrize("voice", VOICES)
def test_next_mood_wraps(voice):
    keys = lt.mood_keys(voice)
    for i, k in enumerate(keys):
        expected = keys[(i + 1) % len(keys)]
        assert vv._next_mood(voice, k) == expected
    # explicit wrap: last -> first
    assert vv._next_mood(voice, keys[-1]) == keys[0]


# ── _decide ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("voice", VOICES)
@pytest.mark.parametrize("last", [None, {}])
def test_decide_baseline_on_empty_state(voice, last):
    template, mood = vv._decide(voice, last)
    assert template == lt.templates_for(voice)[0]
    assert mood == lt.DEFAULT_MOOD[voice]


@pytest.mark.parametrize("voice", VOICES)
def test_decide_from_last_advances_and_never_repeats(voice):
    last_t = lt.templates_for(voice)[0]
    last_m = lt.DEFAULT_MOOD[voice]
    template, mood = vv._decide(voice, {"last_template": last_t, "last_mood": last_m})
    # Must be the deterministic next, and must differ from the previous pick.
    assert template == vv._next_template(voice, last_t)
    assert mood == vv._next_mood(voice, last_m)
    assert template != last_t
    assert mood != last_m


def test_decide_david_full_manual_trace():
    # Baseline then one advance, spelled out against the module's own _next_* helpers.
    t0, m0 = vv._decide("david", None)
    assert (t0, m0) == (lt.STAT_CARD, lt.DEFAULT_MOOD["david"])
    t1, m1 = vv._decide("david", {"last_template": t0, "last_mood": m0})
    assert (t1, m1) == (lt.SPLIT_COMPARISON, vv._next_mood("david", m0))


# ── next_variant (in-memory store) ─────────────────────────────────────
@pytest.mark.parametrize("voice", VOICES)
def test_next_variant_first_call_is_baseline(monkeypatch, voice):
    _install_store(monkeypatch)
    v = vv.next_variant(voice)
    assert set(v.keys()) == {"template", "mood", "accent"}
    assert v["template"] == lt.templates_for(voice)[0]
    assert v["template"] == lt.STAT_CARD  # templates_for[0] is STAT_CARD for every voice
    assert v["mood"] == lt.DEFAULT_MOOD[voice]
    assert v["accent"] == lt.accent_for(voice, lt.DEFAULT_MOOD[voice])
    assert isinstance(v["accent"], tuple) and len(v["accent"]) == 3


@pytest.mark.parametrize("voice", VOICES)
def test_next_variant_persists_state_to_store(monkeypatch, voice):
    store = _install_store(monkeypatch)
    v = vv.next_variant(voice)
    # The upsert fake must have recorded exactly what was returned.
    assert store[voice] == {"last_template": v["template"], "last_mood": v["mood"]}


@pytest.mark.parametrize("voice", VOICES)
def test_next_variant_no_immediate_repeat_and_full_coverage(monkeypatch, voice):
    _install_store(monkeypatch)
    allowed = lt.templates_for(voice)
    seq = [vv.next_variant(voice) for _ in range(2 * len(allowed))]

    combos = [(v["template"], v["mood"]) for v in seq]
    # No two CONSECUTIVE (template, mood) combos are identical.
    for i in range(1, len(combos)):
        assert combos[i] != combos[i - 1], f"immediate repeat at index {i} for {voice}: {combos[i]}"

    # Every allowed template appears at least once across the run.
    templates_used = {v["template"] for v in seq}
    assert templates_used == set(allowed)

    # Every emitted template stays inside the voice's allowed set.
    assert all(v["template"] in allowed for v in seq)

    # Every accent matches what accent_for computes for that mood (no drift).
    for v in seq:
        assert v["accent"] == lt.accent_for(voice, v["mood"])


def test_next_variant_david_exact_sequence(monkeypatch):
    _install_store(monkeypatch)
    seq = [vv.next_variant("david") for _ in range(4)]
    combos = [(v["template"], v["mood"]) for v in seq]
    assert combos == [
        (lt.STAT_CARD, "teal"),
        (lt.SPLIT_COMPARISON, "amber"),
        (lt.STAT_CARD, "cool_white_glow"),
        (lt.SPLIT_COMPARISON, "teal"),
    ]


def test_next_variant_voices_are_independent(monkeypatch):
    store = _install_store(monkeypatch)
    a = vv.next_variant("david")
    b = vv.next_variant("adam")
    # Separate voice keys in the store, each at its own baseline.
    assert set(store.keys()) == {"david", "adam"}
    assert a["template"] == lt.STAT_CARD and a["mood"] == lt.DEFAULT_MOOD["david"]
    assert b["template"] == lt.STAT_CARD and b["mood"] == lt.DEFAULT_MOOD["adam"]
