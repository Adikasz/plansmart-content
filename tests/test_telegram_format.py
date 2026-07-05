"""Zero-network unit tests a Telegram approval-bot TISZTA builder-eire.

Csak a pure formázó/keyboard buildereket teszteljük — se get_bot(), se hálózat.
A modul importja biztonságos: minden kliens lustán épül, a conftest env-et állít.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bots.telegram_bot import (
    ApprovalCB,
    build_keyboard,
    format_approval_message,
)


# ── format_approval_message ────────────────────────────────────────────
def _scored_post(**over) -> dict:
    base = {
        "voice": "david",
        "platform": "linkedin",
        "score": 8,
        "content": "Hello",
        "feed_item_url": "https://example.com/x",
    }
    base.update(over)
    return base


def test_scored_post_shows_score_line():
    text = format_approval_message(_scored_post())
    assert "Score: 8/10" in text
    # A forrás-domain a feed_item_url netloc-jából jön.
    assert "example.com" in text
    # Nem a kézi-poszt ágon vagyunk.
    assert "Kézi poszt (/create)" not in text


def test_manual_post_shows_manual_label():
    # score None → kézi (/create) poszt: nincs Score/Forrás sor.
    text = format_approval_message(_scored_post(score=None))
    assert "Kézi poszt (/create)" in text
    assert "Score:" not in text


def test_content_html_is_escaped_not_raw():
    # A tartalomban lévő HTML-t html.escape-eli — nem kerülhet nyers markup a caption-be.
    # A '<script>' szándékosan olyan tag, ami a boilerplate-ben (fejléc '<b>...</b>') NEM
    # fordul elő, így a nyers hiánya bizonyítja az escape-elést.
    post = _scored_post(content="Deal <b>closed</b> & won <script>alert(1)</script>")
    text = format_approval_message(post)

    # A tartalom '<b>closed</b>' része escape-elve jelenik meg.
    assert "&lt;b&gt;closed&lt;/b&gt;" in text
    # A '<script>' tag escape-elve, nyersen SEHOL nem szerepel.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>" not in text
    assert "</script>" not in text
    # A '&' karakter is escape-elve (&amp;), nyers "won & won" nem marad.
    assert "&amp;" in text


def test_hashtags_appear_when_present():
    post = _scored_post(hashtags=["#ClaudeAPI", "#BuildInPublic"])
    text = format_approval_message(post)
    assert "#ClaudeAPI" in text
    assert "#BuildInPublic" in text
    # Space-szel összefűzve egy sorban.
    assert "#ClaudeAPI #BuildInPublic" in text


def test_no_hashtag_line_when_absent():
    # Üres/hiányzó hashtags → nincs # a caption-ben (a boilerplate nem tartalmaz #-et).
    text = format_approval_message(_scored_post(hashtags=[]))
    assert "#" not in text


def test_voice_and_platform_header_rendered():
    text = format_approval_message(_scored_post(voice="david", platform="linkedin"))
    # VOICE_DISPLAY: david → ("DÁVID", "🔨"); PLATFORM_DISPLAY: linkedin → "LinkedIn".
    assert "DÁVID" in text
    assert "LinkedIn" in text


# ── build_keyboard ─────────────────────────────────────────────────────
def _flatten(kb: InlineKeyboardMarkup) -> list[InlineKeyboardButton]:
    return [btn for row in kb.inline_keyboard for btn in row]


def test_build_keyboard_has_exactly_four_buttons():
    kb = build_keyboard("post-123")
    assert isinstance(kb, InlineKeyboardMarkup)
    buttons = _flatten(kb)
    assert len(buttons) == 4


def test_build_keyboard_actions_and_postid_roundtrip():
    post_id = "post-123"
    kb = build_keyboard(post_id)
    buttons = _flatten(kb)

    decoded = [ApprovalCB.unpack(b.callback_data) for b in buttons]
    actions = [cb.action for cb in decoded]

    assert actions == ["approve", "edit", "regenerate", "skip"]
    # A post_id minden gombon oda-vissza megmarad.
    for cb in decoded:
        assert cb.post_id == post_id


# ── ApprovalCB pack/unpack ─────────────────────────────────────────────
def test_approvalcb_pack_unpack_roundtrip():
    packed = ApprovalCB(action="approve", post_id="abc").pack()
    assert isinstance(packed, str)
    # aiogram CallbackData prefix = "appr".
    assert packed.startswith("appr")

    cb = ApprovalCB.unpack(packed)
    assert cb.action == "approve"
    assert cb.post_id == "abc"
