"""Phase 19 / B2 — komment/DM válasz-generátor (Claude Sonnet).

A besorolt (reaction_classifier) komment/DM-hez ír egy válasz-DRAFTOT a megfelelő
hangon. A user (Dávid/Ádám) szerkeszti és MANUÁLISAN küldi el — ez csak javaslat.

Szabályok:
  • Komment-válasz: rövid (1-3 mondat), a pontjukra reagál, a hang stílusában,
    természetes, NINCS Calendly-link publikus kommentben.
  • DM-válasz: lehet hosszabb. Ha ELSŐ üzenet egy új embertől ÉS lead_signal, akkor
    a DM first-message sablon: üdvözlés + rövid segítő válasz + workshop-említés +
    Calendly-link. Minden más DM (meglévő beszélgetés, laza kérdés): csak segítő
    válasz, workshop/Calendly nyomulás NÉLKÜL.
  • appreciative_only: 1 mondatos, őszinte köszönet (a user dönti el, elküldi-e).

Ugyanazok az anti-pattern szabályok mint a posztoknál: nincs nyomulós CTA, nincs
"forradalom"/"game changer" buzzword; a válasz a bejövő üzenet nyelvén (hu/en).

A projekt SDK-mintáján fut (anthropic==0.28.0-kompatibilis messages.create), a
note_generator Sonnet-hívásával összhangban.

Önálló teszt:
    python -m src.outreach.reaction_generator
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.config.settings import get_settings
from src.outreach.reaction_classifier import classify_reaction
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSHOP_TOPICS = PROJECT_ROOT / "prompts" / "workshop_topics.yml"

MODEL = "claude-sonnet-4-6"  # minőségi hang-generálás (a base_generator-ral összhangban)
MAX_TOKENS = 600

# A három hang TÖMÖR leírása a rövid reakció-válaszhoz (NEM a teljes voice_*.md).
VOICE_DESC = {
    "david": ("You are Dávid, co-founder of PlanSmart — a hands-on builder/engineer. You reply directly, "
              "concretely, engineer to engineer, no marketing fluff. Technical depth is welcome."),
    "adam": ("You are Ádám, co-founder of PlanSmart — a business strategist. You reply owner-to-owner, "
             "pragmatic and warm, never top-down or salesy. Business framing, not technical jargon."),
    "plansmart": ("You are replying as the PlanSmart brand account ('we' voice). Results-oriented, "
                  "professional but human, never corporate buzzword soup."),
}

# AI-tell / buzzword tiltólista (a voice_david.md BANNED phrases + magyar megfelelők).
BANNED_LINE = (
    "BANNED words/phrases (they instantly kill the human feel): leverage, revolutionize, game changer, "
    "forradalom, seamless, disruptive, cutting-edge, unlock your potential, synergy, supercharge, "
    "paradigm shift, áttörés, korszakalkotó; stiff connectors (furthermore, moreover, in conclusion); "
    "'Agree?'-style engagement-bait."
)


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()


@lru_cache(maxsize=1)
def _workshop_topics() -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(WORKSHOP_TOPICS.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError) as exc:
        logger.warning("[reaction-gen] workshop_topics.yml nem olvasható (%s)", exc)
        return []
    return data.get("workshop_topics", []) or []


def _workshop_line(voice: str) -> str | None:
    """A voice-hoz illő legrelevánsabb workshop rövid leírása (a DM-sablonhoz)."""
    for w in _workshop_topics():
        fit = w.get("voice_fit") or []
        if "all" in fit or voice in fit:
            topic = w.get("topic", "")
            fmt = w.get("format", "")
            return f"{topic} ({fmt})".strip(" ()") if topic else None
    return None


def _system(voice: str, language: str) -> str:
    desc = VOICE_DESC.get(voice, VOICE_DESC["adam"])
    lang_line = ("Write the reply in HUNGARIAN (natural, native, warm-professional)."
                 if language == "hu" else "Write the reply in ENGLISH.")
    return (
        f"{desc}\n\n"
        "You are drafting a reply to an incoming LinkedIn message. It is a DRAFT — a human will edit "
        "and send it manually. Sound like a real person, not a brand bot.\n"
        f"- {lang_line}\n"
        "- Address their specific point; don't be generic.\n"
        "- No pushy call-to-action, no 'book a call now', no hard sell.\n"
        f"- {BANNED_LINE}\n"
        "Respond with ONLY the reply text — no quotes, no preamble, no explanation."
    )


def build_user_prompt(
    reaction_type: str,
    classification: str,
    incoming_text: str,
    context_text: str,
    is_first_dm: bool,
    calendly_url: str,
    workshop_line: str | None,
) -> str:
    """A feladat-specifikus user prompt (komment vs DM; first-message lead sablon).

    Külön függvény, hogy a zero-network teszt ellenőrizhesse: kommentben SOSEM
    kerül Calendly-link a promptba, DM-first-lead esetén viszont IGEN.
    """
    parts: list[str] = []
    if context_text.strip():
        label = "OUR POST THEY COMMENTED ON" if reaction_type == "comment" else "PRIOR DM CONTEXT"
        parts.append(f"{label}:\n{context_text.strip()}")
    parts.append(f"INCOMING {reaction_type.upper()}:\n{incoming_text.strip()}")

    use_template = reaction_type == "dm" and is_first_dm and classification == "lead_signal"

    if reaction_type == "comment":
        parts.append(
            "TASK: Write a SHORT public comment reply — 1-3 sentences. Natural, conversational. "
            "Answer their point directly. Do NOT include any link, Calendly, or 'let's talk in DMs' — "
            "this is a public comment thread."
        )
        if classification == "appreciative_only":
            parts.append("They only expressed appreciation — a warm one-sentence thank-you is enough.")
    elif use_template:
        wl = workshop_line or "a free intro workshop for SME owners"
        link_line = (f"end with the Calendly link so they can book a slot: {calendly_url}"
                     if calendly_url.strip()
                     else "offer to share a booking link in a follow-up (no link is configured yet)")
        parts.append(
            "TASK: This is a FIRST DM from a new potential client showing buying intent. Write a warm "
            "first-message reply that: (1) briefly welcomes them and thanks them for reaching out, "
            "(2) gives a genuinely useful short answer to their question, "
            f"(3) casually mentions the relevant workshop — {wl}, "
            f"(4) {link_line}. Keep it human and low-pressure, not a sales script."
        )
    else:
        parts.append(
            "TASK: Write a helpful DM reply. Longer is fine. Just answer their question helpfully and "
            "genuinely. This is an ongoing/casual conversation — do NOT push a workshop or Calendly link."
        )
    parts.append("Write the reply now.")
    return "\n\n".join(parts)


def _clean(text: str) -> str:
    t = (text or "").strip()
    if len(t) >= 2 and t[0] in "\"'“”" and t[-1] in "\"'“”":
        t = t[1:-1].strip()
    return t


async def generate_reply(
    voice: str,
    reaction_type: str,
    incoming_text: str,
    classification: str,
    context_text: str = "",
    language: str = "hu",
    is_first_dm: bool = False,
) -> str:
    """Egy válasz-draft generálása. A hívó (bot/script) menti + jóváhagyatja."""
    calendly_url = get_settings().calendly_url or ""
    user = build_user_prompt(
        reaction_type, classification, incoming_text, context_text,
        is_first_dm, calendly_url, _workshop_line(voice),
    )
    msg = await _client().messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=_system(voice, language),
        messages=[{"role": "user", "content": user}],
    )
    return _clean(msg.content[0].text if msg.content else "")


async def build_reaction(
    voice: str,
    reaction_type: str,
    incoming_text: str,
    context_text: str = "",
    is_first_dm: bool = False,
) -> dict[str, Any]:
    """Teljes pipeline: osztályoz (Haiku) → ha nem skip, választ ír (Sonnet).

    Visszaad: {classification, language, reason, skip, skip_message, reply}. A `reply`
    None, ha skip. Se DB, se Telegram — a hívó dönt a mentésről és a UI-ról.
    """
    cls = await classify_reaction(incoming_text, reaction_type, context_text)
    if cls["skip"]:
        return {**cls, "reply": None}
    reply = await generate_reply(
        voice, reaction_type, incoming_text, cls["classification"],
        context_text=context_text, language=cls["language"], is_first_dm=is_first_dm,
    )
    return {**cls, "reply": reply}


async def _demo() -> int:
    import json

    out = await build_reaction(
        "adam", "dm",
        "Hi, we're a 30-person logistics firm, do you work with our size?",
        context_text="", is_first_dm=True,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    setup_logging()
    raise SystemExit(asyncio.run(_demo()))
