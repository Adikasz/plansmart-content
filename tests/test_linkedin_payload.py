"""Zero-network unit tesztek a LinkedIn publisher tiszta függvényeire.

Csak pure logikát tesztelünk: compose_text / build_ugc_payload / _fake_share_urn.
Se hálózat, se Supabase, se httpx — semmit sem mockolunk, mert nincs rá szükség.
"""
from __future__ import annotations

import re

from src.publishers.linkedin_publisher import (
    _fake_share_urn,
    build_ugc_payload,
    compose_text,
)

SHARE_KEY = "com.linkedin.ugc.ShareContent"
VIS_KEY = "com.linkedin.ugc.MemberNetworkVisibility"
URN_RE = re.compile(r"^urn:li:share:\d+$")


# ── compose_text ──────────────────────────────────────────────────────
def test_compose_text_with_hashtags_joins_with_double_newline():
    result = compose_text("Hello world", ["#ai", "#build"])
    assert result == "Hello world\n\n#ai #build"


def test_compose_text_single_hashtag():
    assert compose_text("Body", ["#solo"]) == "Body\n\n#solo"


def test_compose_text_none_hashtags_returns_stripped_content():
    assert compose_text("Just the body", None) == "Just the body"


def test_compose_text_empty_hashtag_list_returns_stripped_content():
    # Üres lista -> nincs tag-blokk, csak a tiszta törzs.
    assert compose_text("Just the body", []) == "Just the body"


def test_compose_text_default_arg_is_none():
    # hashtags paraméter default None — hívható argumentum nélkül is.
    assert compose_text("Only content") == "Only content"


def test_compose_text_strips_leading_and_trailing_whitespace_no_tags():
    assert compose_text("   padded   ") == "padded"
    assert compose_text("\n\tmixed ws\n") == "mixed ws"


def test_compose_text_strips_content_before_appending_tags():
    # A törzset strippeljük, a tag-blokk pontosan a törzs után két sortöréssel jön.
    result = compose_text("   spaced content   ", ["#tag"])
    assert result == "spaced content\n\n#tag"


def test_compose_text_empty_content_with_tags_strips_leading_newlines():
    # Üres törzs + tag -> a végső .strip() leszedi a vezető "\n\n"-t, marad a tag.
    assert compose_text("", ["#a", "#b"]) == "#a #b"


def test_compose_text_empty_everything_returns_empty_string():
    assert compose_text("", []) == ""
    assert compose_text("", None) == ""


def test_compose_text_none_content_handled_as_empty():
    # (content or "") miatt a None nem robban, üres törzsként viselkedik.
    assert compose_text(None, None) == ""
    assert compose_text(None, ["#x"]) == "#x"


# ── build_ugc_payload ─────────────────────────────────────────────────
def test_build_ugc_payload_author_and_lifecycle():
    urn = "urn:li:person:ABC123"
    payload = build_ugc_payload(urn, "Some content", ["#x"])
    assert payload["author"] == urn
    assert payload["lifecycleState"] == "PUBLISHED"


def test_build_ugc_payload_share_media_category_none():
    payload = build_ugc_payload("urn:li:person:X", "content", None)
    share = payload["specificContent"][SHARE_KEY]
    assert share["shareMediaCategory"] == "NONE"


def test_build_ugc_payload_visibility_public():
    payload = build_ugc_payload("urn:li:organization:99", "content", None)
    assert payload["visibility"][VIS_KEY] == "PUBLIC"


def test_build_ugc_payload_share_commentary_matches_compose_text():
    content = "  Buildlog: átírtam a queue-t  "
    hashtags = ["#buildinpublic", "#ai"]
    payload = build_ugc_payload("urn:li:person:Y", content, hashtags)
    text = payload["specificContent"][SHARE_KEY]["shareCommentary"]["text"]
    assert text == compose_text(content, hashtags)
    # És konkrétan: stripped törzs + két sortörés + tagek.
    assert text == "Buildlog: átírtam a queue-t\n\n#buildinpublic #ai"


def test_build_ugc_payload_commentary_no_hashtags_is_plain_body():
    payload = build_ugc_payload("urn:li:person:Z", "  plain body  ", None)
    text = payload["specificContent"][SHARE_KEY]["shareCommentary"]["text"]
    assert text == "plain body"


def test_build_ugc_payload_organization_author_preserved():
    org_urn = "urn:li:organization:12345"
    payload = build_ugc_payload(org_urn, "PlanSmart hír", [])
    assert payload["author"] == org_urn


def test_build_ugc_payload_full_structure_shape():
    payload = build_ugc_payload("urn:li:person:P", "hi", ["#t"])
    # A pontos beágyazott kulcsstruktúra megléte.
    assert set(payload.keys()) == {
        "author",
        "lifecycleState",
        "specificContent",
        "visibility",
    }
    assert set(payload["specificContent"].keys()) == {SHARE_KEY}
    assert set(payload["specificContent"][SHARE_KEY].keys()) == {
        "shareCommentary",
        "shareMediaCategory",
    }
    assert set(payload["visibility"].keys()) == {VIS_KEY}


# ── _fake_share_urn ───────────────────────────────────────────────────
def test_fake_share_urn_matches_pattern():
    urn = _fake_share_urn("urn:li:person:A", "hello text")
    assert URN_RE.match(urn), f"URN nem illeszkedik: {urn!r}"


def test_fake_share_urn_deterministic_same_inputs():
    a = _fake_share_urn("urn:li:person:A", "same text")
    b = _fake_share_urn("urn:li:person:A", "same text")
    assert a == b


def test_fake_share_urn_differs_on_different_author():
    a = _fake_share_urn("urn:li:person:A", "text")
    b = _fake_share_urn("urn:li:person:B", "text")
    assert a != b


def test_fake_share_urn_differs_on_different_text():
    a = _fake_share_urn("urn:li:person:A", "text one")
    b = _fake_share_urn("urn:li:person:A", "text two")
    assert a != b


def test_fake_share_urn_numeric_suffix_only():
    urn = _fake_share_urn("urn:li:person:A", "some text")
    suffix = urn.rsplit(":", 1)[1]
    assert suffix.isdigit()
    # Modulo 10**19 miatt legfeljebb 19 jegyű.
    assert 1 <= len(suffix) <= 19
