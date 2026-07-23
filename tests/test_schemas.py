"""Boundary-schema tests for the four LLM/API `schemas.py` modules.

Zero-network: pure Pydantic model validation, no external clients touched.
Covers generators.validate_generated / HookVariant, filters.validate_score,
publishers.LinkedInUGCResponse.post_urn and visuals.MuapiResult.from_generation.
"""
from __future__ import annotations

import types

import pytest
from pydantic import ValidationError

from src.core.filters.schemas import RelevanceScore, validate_score
from src.ai.generators.schemas import (
    GeneratedPost,
    HookVariant,
    validate_generated,
)
from src.integrations.publishers.schemas import LinkedInUGCResponse
from src.integrations.visuals.schemas import MuapiResult


# ── generators.validate_generated ──────────────────────────────────────
def test_validate_generated_linkedin_content():
    post, err = validate_generated({"linkedin": {"content": "hi"}})
    assert err is None
    assert isinstance(post, GeneratedPost)
    assert post.linkedin is not None
    assert post.linkedin.content == "hi"
    # default skip is False, and hashtags default to an empty list
    assert post.skip is False
    assert post.linkedin.hashtags == []


def test_validate_generated_skip_true():
    post, err = validate_generated({"skip": True, "reason": "x"})
    assert err is None
    assert post is not None
    assert post.skip is True
    assert post.reason == "x"
    assert post.linkedin is None


def test_validate_generated_preserves_unknown_extra_keys():
    post, err = validate_generated(
        {"linkedin": {"content": "hi"}, "brand_new_field": {"nested": 42}}
    )
    assert err is None
    assert post is not None
    # extra="allow" keeps unknown top-level keys accessible and serializable
    assert post.brand_new_field == {"nested": 42}
    assert post.model_dump()["brand_new_field"] == {"nested": 42}


def test_validate_generated_extra_keys_preserved_on_nested_content():
    post, err = validate_generated(
        {"linkedin": {"content": "hi", "cta": "book a call"}}
    )
    assert err is None
    assert post is not None
    # LinkedInContent also has extra="allow"
    assert post.linkedin.cta == "book a call"


def test_validate_generated_hashtags_must_be_list():
    post, err = validate_generated({"linkedin": {"hashtags": "notalist"}})
    assert post is None
    assert isinstance(err, str)
    assert err  # non-empty error string
    # error should reference the offending field
    assert "hashtags" in err


def test_validate_generated_hashtags_accepts_list():
    post, err = validate_generated(
        {"linkedin": {"content": "hi", "hashtags": ["#ai", "#build"]}}
    )
    assert err is None
    assert post is not None
    assert post.linkedin.hashtags == ["#ai", "#build"]


def test_validate_generated_never_raises_on_garbage():
    # A structurally wrong payload (linkedin must be a mapping, not an int) must
    # return an error tuple, never propagate an exception.
    post, err = validate_generated({"linkedin": 123})
    assert post is None
    assert isinstance(err, str)
    assert err


def test_validate_generated_empty_dict_is_valid_default():
    post, err = validate_generated({})
    assert err is None
    assert post is not None
    assert post.skip is False
    assert post.linkedin is None


# ── generators HookVariant ─────────────────────────────────────────────
def test_hookvariant_requires_text():
    with pytest.raises(ValidationError):
        HookVariant.model_validate({"type": "A", "score": 0.9})


def test_hookvariant_with_text_ok():
    hv = HookVariant.model_validate({"text": "3-2-1 go", "type": "B"})
    assert hv.text == "3-2-1 go"
    assert hv.type == "B"
    assert hv.score is None


# ── filters.validate_score ─────────────────────────────────────────────
def test_validate_score_ok():
    score, err = validate_score({"score": 8, "reason": "r"})
    assert err is None
    assert isinstance(score, RelevanceScore)
    assert score.score == 8
    assert score.reason == "r"
    # untouched fields fall back to their declared defaults
    assert score.urgency == "low"
    assert score.voice_fit == {}
    assert score.topics == []


def test_validate_score_above_max_rejected():
    score, err = validate_score({"score": 11})
    assert score is None
    assert isinstance(err, str)
    assert err


def test_validate_score_below_min_rejected():
    score, err = validate_score({"score": -1})
    assert score is None
    assert isinstance(err, str)
    assert err


def test_validate_score_bounds_inclusive():
    lo, lo_err = validate_score({"score": 0})
    hi, hi_err = validate_score({"score": 10})
    assert lo_err is None and lo.score == 0
    assert hi_err is None and hi.score == 10


def test_validate_score_coerces_numeric_string():
    score, err = validate_score({"score": "7"})
    assert err is None
    assert score is not None
    assert score.score == 7
    assert isinstance(score.score, int)


def test_validate_score_never_raises():
    # score is required; missing it yields an error tuple, not an exception.
    score, err = validate_score({"reason": "no score here"})
    assert score is None
    assert isinstance(err, str)
    assert err


# ── publishers.LinkedInUGCResponse.post_urn ────────────────────────────
def test_post_urn_from_body_id():
    assert (
        LinkedInUGCResponse.post_urn({"id": "urn:li:share:1"}, None)
        == "urn:li:share:1"
    )


def test_post_urn_header_wins_over_body():
    assert LinkedInUGCResponse.post_urn({"id": "x"}, "urn:hdr") == "urn:hdr"


def test_post_urn_none_body_none_header():
    assert LinkedInUGCResponse.post_urn(None, None) is None


def test_post_urn_empty_body_none_header():
    assert LinkedInUGCResponse.post_urn({}, None) is None


def test_post_urn_header_default_arg_omitted():
    # header_id defaults to None, so a single-arg call reads the body.
    assert LinkedInUGCResponse.post_urn({"id": "urn:li:share:9"}) == "urn:li:share:9"


# ── visuals.MuapiResult.from_generation ────────────────────────────────
def test_muapi_from_generation_valid():
    obj = types.SimpleNamespace(
        image_url="https://r2.example/img.png",
        model="flux-2-pro",
        request_id="r1",
        cost_usd=0.032,
    )
    result = MuapiResult.from_generation(obj)
    assert str(result.image_url).startswith("https")
    assert "r2.example" in str(result.image_url)
    assert result.model == "flux-2-pro"
    assert result.request_id == "r1"
    assert result.cost_usd == 0.032


def test_muapi_from_generation_optional_fields_default():
    # request_id / cost_usd read via getattr with a None fallback.
    obj = types.SimpleNamespace(
        image_url="https://r2.example/x.png", model="flux-2-pro"
    )
    result = MuapiResult.from_generation(obj)
    assert result.request_id is None
    assert result.cost_usd is None


def test_muapi_negative_cost_rejected():
    obj = types.SimpleNamespace(
        image_url="https://r2.example/img.png",
        model="flux-2-pro",
        request_id="r1",
        cost_usd=-0.01,
    )
    with pytest.raises(ValidationError):
        MuapiResult.from_generation(obj)


def test_muapi_zero_cost_allowed():
    obj = types.SimpleNamespace(
        image_url="https://r2.example/img.png",
        model="flux-2-pro",
        cost_usd=0.0,
    )
    result = MuapiResult.from_generation(obj)
    assert result.cost_usd == 0.0


def test_muapi_rejects_non_http_url():
    obj = types.SimpleNamespace(image_url="not-a-url", model="flux-2-pro")
    with pytest.raises(ValidationError):
        MuapiResult.from_generation(obj)
