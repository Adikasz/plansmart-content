"""Zero-network unit tests for src/utils/json_repair.py.

Covers repair_and_parse (alias _repair_and_parse), _strip_fences/strip_fences,
_escape_inner_quotes/escape_inner_quotes, and the base_generator re-export identity.
Pure logic, no network / no external clients.
"""
from __future__ import annotations

import json

from src.utils.json_repair import (
    _escape_inner_quotes,
    _repair_and_parse,
    _strip_fences,
    escape_inner_quotes,
    repair_and_parse,
    strip_fences,
)

# ── repair_and_parse: happy path & common model glitches ──────────────

def test_plain_valid_json_object():
    assert repair_and_parse('{"a": 1}') == {"a": 1}


def test_plain_valid_json_multi_key():
    assert repair_and_parse('{"a": 1, "b": "two", "c": [1, 2, 3]}') == {
        "a": 1,
        "b": "two",
        "c": [1, 2, 3],
    }


def test_fenced_json_block():
    assert repair_and_parse('```json\n{"a": 1}\n```') == {"a": 1}


def test_bare_fenced_block_without_lang_tag():
    # ``` ... ``` without the json language hint must still parse.
    assert repair_and_parse('```\n{"a": 1}\n```') == {"a": 1}


def test_trailing_comma_object():
    assert repair_and_parse('{"a": 1,}') == {"a": 1}


def test_trailing_comma_nested_array():
    assert repair_and_parse('{"a": [1, 2,], "b": 3,}') == {"a": [1, 2], "b": 3}


def test_prose_around_json_block_extraction():
    # First '{' .. last '}' block is extracted out of surrounding prose.
    assert repair_and_parse('noise {"a": 1} tail') == {"a": 1}


def test_prose_with_multiline_and_leading_commentary():
    text = 'Here is your JSON:\n{"post": "hello", "score": 9}\nHope that helps!'
    assert repair_and_parse(text) == {"post": "hello", "score": 9}


# ── repair_and_parse: inner unescaped quotes (the hard case) ──────────

def test_inner_unescaped_quotes_recovered():
    # Strict json.loads fails because the value contains bare double-quotes,
    # but the escape-inner-quotes repair step recovers it.
    raw = '{"text": "he said "hello" world"}'

    # sanity: this genuinely breaks strict parsing first
    try:
        json.loads(raw)
        broke = False
    except json.JSONDecodeError:
        broke = True
    assert broke, "raw input should break strict json.loads for a meaningful test"

    parsed = repair_and_parse(raw)
    assert parsed is not None
    assert parsed["text"] == 'he said "hello" world'


def test_inner_unescaped_quotes_with_other_keys():
    raw = '{"quote": "she called it "genius" today", "score": 8}'
    parsed = repair_and_parse(raw)
    assert parsed is not None
    assert parsed["quote"] == 'she called it "genius" today'
    assert parsed["score"] == 8


# ── repair_and_parse: failure / None cases ────────────────────────────

def test_top_level_array_returns_none():
    assert repair_and_parse('[1, 2]') is None


def test_empty_string_returns_none():
    assert repair_and_parse('') is None


def test_whitespace_only_returns_none():
    assert repair_and_parse('   \n\t  ') is None


def test_unparseable_garbage_returns_none():
    assert repair_and_parse('this is not json at all') is None


def test_none_input_returns_none():
    # _strip_fences guards against None via `text or ""`.
    assert repair_and_parse(None) is None


# ── _strip_fences / strip_fences ──────────────────────────────────────

def test_strip_fences_removes_json_fence():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_fences_removes_bare_fence():
    assert strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_fences_leaves_unfenced_text():
    assert strip_fences('{"a": 1}') == '{"a": 1}'


def test_strip_fences_alias_identity():
    assert strip_fences is _strip_fences


# ── _escape_inner_quotes / escape_inner_quotes ────────────────────────

def test_escape_inner_quotes_makes_parseable():
    src = '{"q": "say "hi" now"}'
    escaped = escape_inner_quotes(src)
    # after escaping, the structural JSON is valid
    obj = json.loads(escaped)
    assert obj["q"] == 'say "hi" now'


def test_escape_inner_quotes_leaves_clean_string_untouched():
    # No content quotes -> unchanged, still valid JSON.
    src = '{"q": "plain value"}'
    assert escape_inner_quotes(src) == src
    assert json.loads(escape_inner_quotes(src)) == {"q": "plain value"}


def test_escape_inner_quotes_preserves_existing_escapes():
    # Already-escaped inner quote must not be double-escaped.
    src = '{"q": "already \\"safe\\" text"}'
    escaped = escape_inner_quotes(src)
    assert json.loads(escaped) == {"q": 'already "safe" text'}


def test_escape_inner_quotes_alias_identity():
    assert escape_inner_quotes is _escape_inner_quotes


# ── public/private alias identity within json_repair ──────────────────

def test_repair_and_parse_alias_identity():
    assert repair_and_parse is _repair_and_parse


# ── cross-module re-export identity (base_generator) ──────────────────

def test_base_generator_reexport_is_same_object():
    from src.ai.generators.base_generator import _repair_and_parse as bg_repair
    from src.utils.json_repair import repair_and_parse as util_repair

    assert bg_repair is util_repair
