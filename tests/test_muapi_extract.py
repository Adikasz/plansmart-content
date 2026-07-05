"""Offline unit tests for the pure extractor helpers in src.visuals.muapi_client.

These cover _extract_request_id, _extract_image_url, _extract_cost and
_is_placeholder — all pure dict-in / value-out helpers, no network involved.
"""
from src.visuals.muapi_client import (
    MODEL_PRICING_USD,
    _extract_cost,
    _extract_image_url,
    _extract_request_id,
    _is_placeholder,
)


# --------------------------------------------------------------------------- #
# _extract_request_id
# --------------------------------------------------------------------------- #
def test_request_id_snake_case():
    assert _extract_request_id({"request_id": "a"}) == "a"


def test_request_id_camel_case():
    assert _extract_request_id({"requestId": "b"}) == "b"


def test_request_id_plain_id():
    assert _extract_request_id({"id": "c"}) == "c"


def test_request_id_prediction_id():
    assert _extract_request_id({"prediction_id": "d"}) == "d"


def test_request_id_nested_under_data():
    assert _extract_request_id({"data": {"request_id": "e"}}) == "e"


def test_request_id_missing_returns_none():
    assert _extract_request_id({}) is None


def test_request_id_coerced_to_str():
    # Non-string truthy ids are stringified.
    assert _extract_request_id({"id": 123}) == "123"


# --------------------------------------------------------------------------- #
# _extract_image_url
# --------------------------------------------------------------------------- #
def test_image_url_direct_field():
    assert _extract_image_url({"image_url": "https://x/i.png"}) == "https://x/i.png"


def test_image_url_from_outputs_list():
    assert _extract_image_url({"outputs": ["https://x/o.png"]}) == "https://x/o.png"


def test_image_url_nested_data_images_dict():
    data = {"data": {"images": [{"url": "https://x/n.png"}]}}
    assert _extract_image_url(data) == "https://x/n.png"


def test_image_url_non_http_value_ignored():
    # A value that is not an http(s) URL must not be returned.
    assert _extract_image_url({"image_url": "not-a-real-url"}) is None
    assert _extract_image_url({"url": "ftp://x/i.png"}) is None


def test_image_url_missing_returns_none():
    assert _extract_image_url({}) is None


# --------------------------------------------------------------------------- #
# _extract_cost
# --------------------------------------------------------------------------- #
def test_cost_from_nested_amount_usd():
    assert _extract_cost({"cost": {"amount_usd": 0.04}}, "flux-2-pro") == 0.04


def test_cost_from_cost_usd_field():
    assert _extract_cost({"cost_usd": 0.05}, "flux-2-pro") == 0.05


def test_cost_falls_back_to_model_pricing():
    result = _extract_cost({}, "flux-2-pro")
    assert result == MODEL_PRICING_USD["flux-2-pro"]
    assert result == 0.032


def test_cost_nested_under_data():
    assert _extract_cost({"data": {"cost_usd": 0.07}}, "flux-2-pro") == 0.07


def test_cost_unknown_model_no_data_returns_none():
    # No cost anywhere and an unknown model -> pricing dict has no entry -> None.
    assert _extract_cost({}, "no-such-model") is None


# --------------------------------------------------------------------------- #
# _is_placeholder
# --------------------------------------------------------------------------- #
def test_is_placeholder_webassets_marker_true():
    assert _is_placeholder("https://api.muapi.ai/webassets/example.png") is True


def test_is_placeholder_normal_url_false():
    assert _is_placeholder("https://cdn.muapi.ai/r2/real-image.png") is False


def test_is_placeholder_none_false():
    assert _is_placeholder(None) is False
