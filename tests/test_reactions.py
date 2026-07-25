"""Zero-network tesztek a reakció-asszisztens (Phase 19) TISZTA logikájára.

Se Anthropic, se Supabase, se Telegram, se hálózat — csak a pure builderek:
osztályozó-normalizálás, generátor prompt-építés (Calendly-szivárgás komment vs DM),
storage row-alak (fake Supabase), és a Telegram formázók/keyboard.
"""

from __future__ import annotations

from src.ai.outreach.reaction_classifier import (
    CLASSIFICATIONS,
    SKIP_CLASSIFICATIONS,
    SKIP_MESSAGES,
    _coerce,
    _user,
)
from src.ai.outreach.reaction_generator import (
    VOICE_DESC,
    _system,
    _workshop_line,
    build_user_prompt,
)
from src.core.storage import reactions as store
from src.integrations.bots.reactions_bot import (
    CLASSIFICATION_LABEL,
    ReactionCB,
    _has_active_flow,
    _kb,
    _parse_voice,
    format_final,
    format_suggestion,
)

CALENDLY = "https://calendly.com/plansmart/intro"


# ── classifier ─────────────────────────────────────────────────────────
def test_skip_messages_cover_exactly_the_skip_classifications():
    assert set(SKIP_MESSAGES) == SKIP_CLASSIFICATIONS
    assert SKIP_CLASSIFICATIONS <= set(CLASSIFICATIONS)


def test_coerce_unknown_classification_defaults_to_engagement():
    out = _coerce({"classification": "banana", "language": "de"})
    # Ismeretlen besorolás → biztonságos default (inkább válaszolunk).
    assert out["classification"] == "question_or_engagement"
    # Nem hu/en nyelv → hu fallback.
    assert out["language"] == "hu"


def test_coerce_keeps_valid_values():
    out = _coerce(
        {"classification": "lead_signal", "language": "en", "reason": "asks about pricing"}
    )
    assert out["classification"] == "lead_signal"
    assert out["language"] == "en"
    assert "pricing" in out["reason"]


def test_coerce_handles_none():
    out = _coerce(None)
    assert out["classification"] in CLASSIFICATIONS
    assert out["language"] in ("hu", "en")


def test_classifier_user_prompt_contains_incoming_and_channel():
    p = _user("How do you handle rate limits?", "comment", context_text="Our n8n post")
    assert "How do you handle rate limits?" in p
    assert "comment" in p
    # Kommentnél a kontextus a posztként van címkézve.
    assert "POST THEY COMMENTED ON" in p
    assert "Our n8n post" in p


# ── generator: prompt-építés (a Calendly-szivárgás a kulcs) ────────────
def test_comment_prompt_never_leaks_calendly():
    p = build_user_prompt(
        "comment",
        "question_or_engagement",
        "How do you handle rate limits in n8n?",
        context_text="",
        is_first_dm=False,
        calendly_url=CALENDLY,
        workshop_line="n8n workshop",
    )
    assert CALENDLY not in p  # publikus kommentben SOSEM megy link
    assert "1-3 sentences" in p


def test_first_dm_lead_prompt_includes_calendly_and_workshop():
    p = build_user_prompt(
        "dm",
        "lead_signal",
        "We're a 30-person logistics firm, do you work with our size?",
        context_text="",
        is_first_dm=True,
        calendly_url=CALENDLY,
        workshop_line="AI alapok workshop",
    )
    assert CALENDLY in p  # first-message lead → foglalási link
    assert "AI alapok workshop" in p  # workshop-említés


def test_non_first_dm_does_not_push_workshop_or_calendly():
    p = build_user_prompt(
        "dm",
        "question_or_engagement",
        "thanks, and what about error handling?",
        context_text="earlier we discussed n8n",
        is_first_dm=False,
        calendly_url=CALENDLY,
        workshop_line="AI alapok workshop",
    )
    assert CALENDLY not in p
    assert "do NOT push a workshop" in p


def test_first_dm_lead_without_calendly_configured_degrades_gracefully():
    p = build_user_prompt(
        "dm",
        "lead_signal",
        "do you work with our size?",
        context_text="",
        is_first_dm=True,
        calendly_url="",
        workshop_line=None,
    )
    # Nincs konfigurált link → nem hazudik linket, felajánlja a follow-upot.
    assert "no link is configured" in p


def test_system_prompt_has_banned_words_and_language_switch():
    hu = _system("david", "hu")
    en = _system("adam", "en")
    assert "game changer" in hu and "forradalom" in hu  # a tiltólista bent van
    assert "HUNGARIAN" in hu
    assert "ENGLISH" in en


def test_voice_desc_covers_all_three_voices():
    assert set(VOICE_DESC) == {"david", "adam", "plansmart"}


def test_workshop_line_resolves_for_each_voice():
    for v in ("david", "adam", "plansmart"):
        assert _workshop_line(v)  # a workshop_topics.yml-ben van 'all' fit → mindig van találat


# ── storage (fake Supabase, client= injektálva) ───────────────────────
def _sample_reaction(**over) -> dict:
    base = {
        "voice": "david",
        "type": "comment",
        "incoming_text": "How about rate limits?",
        "classification": "question_or_engagement",
        "language": "en",
        "our_reply_draft": "You can cap concurrency in the node settings.",
        "status": "drafted",
    }
    base.update(over)
    return base


def test_insert_reaction_returns_given_id(fake_supabase):
    rid = store.insert_reaction(
        {**_sample_reaction(), "id": "react12345678"}, client=fake_supabase()
    )
    assert rid == "react12345678"


def test_insert_reaction_generates_id_when_missing(fake_supabase):
    rid = store.insert_reaction(_sample_reaction(), client=fake_supabase())
    assert isinstance(rid, str) and len(rid) == 12


def test_get_returns_first_row(fake_supabase):
    row = {"id": "r1", "voice": "adam", "status": "drafted"}
    assert store.get("r1", client=fake_supabase([row])) == row


def test_by_status_returns_rows(fake_supabase):
    rows = [{"id": "r1", "status": "drafted"}, {"id": "r2", "status": "drafted"}]
    assert store.by_status("drafted", client=fake_supabase(rows)) == rows


# ── Telegram builderek ─────────────────────────────────────────────────
def test_kb_has_four_buttons_with_roundtrip_ids():
    kb = _kb("react-abc")
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == 4
    decoded = [ReactionCB.unpack(b.callback_data) for b in buttons]
    assert [d.action for d in decoded] == ["approve", "edit", "regenerate", "cancel"]
    assert all(d.rid == "react-abc" for d in decoded)


def test_reactioncb_prefix():
    packed = ReactionCB(action="approve", rid="x").pack()
    assert packed.startswith("react")


def test_parse_voice_valid_invalid_missing():
    assert _parse_voice("david") == "david"
    assert _parse_voice("plansmart extra tokens") == "plansmart"
    assert _parse_voice("boss") is None
    assert _parse_voice("") is None
    assert _parse_voice(None) is None


def test_format_suggestion_escapes_html_and_labels_classification():
    text = format_suggestion("lead_signal", "hu", "Kipróbálnád? <b>nem</b> markup")
    assert "🎯 Lead jelzés" in text
    assert "&lt;b&gt;nem&lt;/b&gt;" in text  # a nyers markup escape-elve
    assert "<b>nem</b>" not in text


def test_format_final_wraps_in_code_for_copy():
    text = format_final("Kész válasz & vége", "✅ Kész:")
    assert "<code>" in text and "</code>" in text
    assert "&amp;" in text  # & escape-elve a <code>-on belül


def test_classification_label_covers_all_classifications():
    for c in CLASSIFICATIONS:
        assert c in CLASSIFICATION_LABEL


def test_has_active_flow_reflects_flow_registry():
    from types import SimpleNamespace

    from src.integrations.bots import reactions_bot as rb

    msg = SimpleNamespace(chat=SimpleNamespace(id=111), from_user=SimpleNamespace(id=222))
    assert _has_active_flow(msg) is False
    rb._flows[(111, 222)] = {"kind": "comment"}
    try:
        assert _has_active_flow(msg) is True
    finally:
        rb._flows.pop((111, 222), None)
