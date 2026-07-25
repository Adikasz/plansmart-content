"""Zero-network unit tests for src.core.filters.keyword_filter.

Minden config INLINE dict — a config/scoring.yml-t NEM olvassuk (kivéve a
config=None fallback smoke-tesztet, ami lokális fájl, nem hálózat).
"""

from __future__ import annotations

from src.core.filters.keyword_filter import (
    BOOST_POINTS,
    KeywordResult,
    keyword_filter,
)


# ── Word boundary (szó-határ) ──────────────────────────────────────────
def test_newspaper_does_not_match_paper(make_feed_item):
    """A 'paper' skip_keyword NEM matchel a 'newspaper' szóra (szó-határ)."""
    item = make_feed_item(
        title="Latest newspaper headlines",
        content="Just a newspaper article, nothing else here.",
        url="https://example.com/newspapers",
    )
    result = keyword_filter(item, {"skip_keywords": ["paper"]})
    assert result.decision == "pass"
    assert result.boost == 0


def test_standalone_paper_is_skipped(make_feed_item):
    """A standalone 'paper' szó IGENIS skip-et vált ki."""
    item = make_feed_item(
        title="A new research paper dropped",
        content="Neutral body.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, {"skip_keywords": ["paper"]})
    assert result.decision == "skip"
    assert "paper" in result.reason


def test_word_boundary_is_case_insensitive(make_feed_item):
    """Nagybetűs 'PAPER' is skip (kis/nagybetű-független illesztés)."""
    item = make_feed_item(
        title="PAPER of the year",
        content="Neutral body.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, {"skip_keywords": ["paper"]})
    assert result.decision == "skip"


def test_case_insensitive_newspaper_still_not_matched(make_feed_item):
    """A 'Newspaper' (nagy kezdőbetű) sem matchel a 'paper' kulcsszóra."""
    item = make_feed_item(
        title="The Newspaper Guild",
        content="Neutral body.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, {"skip_keywords": ["paper"]})
    assert result.decision == "pass"


# ── Precedence (skip > boost) ──────────────────────────────────────────
PRECEDENCE_CFG = {"skip_keywords": ["crypto"], "boost_keywords": ["ai"]}


def test_precedence_skip_only(make_feed_item):
    """Csak skip kulcsszó -> 'skip', boost 0."""
    item = make_feed_item(
        title="crypto markets tumble",
        content="Neutral body.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, PRECEDENCE_CFG)
    assert result.decision == "skip"
    assert result.boost == 0
    assert "crypto" in result.reason


def test_precedence_boost_only(make_feed_item):
    """Csak boost kulcsszó -> 'pass', boost == BOOST_POINTS (2)."""
    item = make_feed_item(
        title="ai breakthrough announced",
        content="Neutral body.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, PRECEDENCE_CFG)
    assert result.decision == "pass"
    assert result.boost == BOOST_POINTS == 2
    assert "ai" in result.reason


def test_precedence_both_skip_wins(make_feed_item):
    """Mindkettő jelen -> skip nyer (a boost-ot meg sem nézi)."""
    item = make_feed_item(
        title="ai in crypto trading",
        content="Neutral body.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, PRECEDENCE_CFG)
    assert result.decision == "skip"
    assert result.boost == 0
    assert "crypto" in result.reason


def test_precedence_neither(make_feed_item):
    """Se skip, se boost -> 'pass', boost 0."""
    item = make_feed_item(
        title="a completely unrelated topic",
        content="Neutral body about gardening.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, PRECEDENCE_CFG)
    assert result.decision == "pass"
    assert result.boost == 0


# ── Kereses title + content + url mezokben ─────────────────────────────
def test_matches_keyword_in_url(make_feed_item):
    """A kulcsszó a URL-ben is talál (nem csak title-ben)."""
    item = make_feed_item(
        title="Neutral headline",
        content="Neutral body.",
        url="https://example.com/crypto/latest",
    )
    result = keyword_filter(item, {"skip_keywords": ["crypto"]})
    assert result.decision == "skip"


def test_matches_keyword_in_content(make_feed_item):
    """A kulcsszó a content-ben is talál (nem csak title-ben)."""
    item = make_feed_item(
        title="Neutral headline",
        content="Here we discuss ai models at length.",
        url="https://example.com/post",
    )
    result = keyword_filter(item, {"boost_keywords": ["ai"]})
    assert result.decision == "pass"
    assert result.boost == BOOST_POINTS


def test_none_fields_do_not_crash(make_feed_item):
    """None title/content nem dob (a text join kiszűri a None-t)."""
    item = make_feed_item(
        title=None,
        content=None,
        url="https://example.com/crypto",
    )
    result = keyword_filter(item, {"skip_keywords": ["crypto"]})
    assert result.decision == "skip"


# ── Ures / hianyzo config kulcsok ──────────────────────────────────────
def test_empty_config_passes(make_feed_item):
    """Üres config dict -> mindig 'pass', boost 0 (nincs kulcsszó lista)."""
    item = make_feed_item(title="ai crypto paper", content="x", url="https://x")
    result = keyword_filter(item, {})
    assert result.decision == "pass"
    assert result.boost == 0


def test_null_keyword_lists_pass(make_feed_item):
    """A `None` értékű kulcsszó-listák sem dobnak (cfg.get(...) or [])."""
    item = make_feed_item(title="crypto", content="ai", url="https://x")
    result = keyword_filter(item, {"skip_keywords": None, "boost_keywords": None})
    assert result.decision == "pass"
    assert result.boost == 0


# ── config=None fallback (lokalis scoring.yml, nem halozat) ─────────────
def test_config_none_falls_back_to_scoring_yml(make_feed_item):
    """config=None esetén a load_scoring_config()-ra esik vissza — KeywordResult, hiba nélkül."""
    item = make_feed_item(
        title="An ordinary neutral headline",
        content="Neutral body with no special keywords.",
        url="https://example.com/post",
    )
    result = keyword_filter(item)  # config kihagyva -> None ág
    assert isinstance(result, KeywordResult)
    assert result.decision in ("skip", "pass")
    assert isinstance(result.boost, int)
