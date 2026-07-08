"""Phase 19 / B2 — bejövő komment/DM osztályozó (Claude Haiku — olcsó + gyors).

Egy LinkedIn kommentet vagy DM-et sorol be, hogy a bot eldönthesse, érdemes-e
egyáltalán választ javasolni, és ha igen, milyet:

  • spam_or_troll          → nincs javaslat ("Nem érdemes válaszolni")
  • competitor_pitch       → nincs javaslat ("Konkurens a saját szolgáltatásáról ír, hagyd")
  • appreciative_only      → opcionális rövid köszönet vagy skip (a user dönt)
  • question_or_engagement → teljes válasz-javaslat
  • lead_signal            → válasz + (DM-nél) workshop/Calendly follow-up lehetőség

A besoroláson felül eldönti a bejövő szöveg NYELVÉT (hu | en) — a válaszban a
kommentelő nyelvét tükrözzük (a PlanSmart angol kontentje így automatikusan
angol választ kap).

A projekt SDK-mintáján fut (anthropic==0.28.0-kompatibilis messages.create), a
relevance_scorer Haiku-hívásával összhangban.

Önálló teszt:
    python -m src.outreach.reaction_classifier
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.utils.json_repair import _repair_and_parse
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

MODEL = "claude-haiku-4-5-20251001"  # olcsó/gyors — reakció-besorolás (ADR-008 filter modell)
MAX_TOKENS = 300

CLASSIFICATIONS = (
    "spam_or_troll",
    "competitor_pitch",
    "appreciative_only",
    "question_or_engagement",
    "lead_signal",
)
# Ezeknél NEM generálunk választ — csak jelezzük a usernek, hogy hagyja.
SKIP_CLASSIFICATIONS = {"spam_or_troll", "competitor_pitch"}
SKIP_MESSAGES = {
    "spam_or_troll": "🚫 Spam vagy trollkodás — nem érdemes válaszolni.",
    "competitor_pitch": "🤝 Konkurens a saját szolgáltatásáról ír — hagyd, ne válaszolj.",
}

SYSTEM_PROMPT = (
    "You are a triage assistant for PlanSmart, a Hungarian AI-automation agency. You classify an "
    "incoming LinkedIn comment or direct message so the team knows whether (and how) to reply.\n\n"
    "Return EXACTLY ONE classification from this set:\n"
    "- spam_or_troll: generic spam, bots, insults, bait, off-topic promotion of unrelated junk.\n"
    "- competitor_pitch: someone pitching their OWN competing AI-automation service/agency at us.\n"
    "- appreciative_only: pure praise or emoji with no question ('Great post!', '🔥', 'Egyetértek').\n"
    "- question_or_engagement: a genuine question or a substantive point that invites a reply.\n"
    "- lead_signal: mentions their own business problem, asks about our services/pricing, or hints "
    "at buying intent (a potential client).\n\n"
    "Also detect the language of the incoming text: 'hu' for Hungarian, 'en' for anything else.\n"
    "Be conservative: only 'lead_signal' when there is a real business/buying signal, not mere interest.\n\n"
    "Respond with ONLY this JSON object, nothing else:\n"
    '{"classification":"<one of the set>","language":"hu|en","reason":"<max 12 words>"}'
)


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()  # az ANTHROPIC_API_KEY-t a környezetből olvassa


def _user(incoming_text: str, reaction_type: str, context_text: str = "") -> str:
    parts = [f"CHANNEL: {reaction_type}  (comment = public LinkedIn comment, dm = direct message)"]
    if context_text.strip():
        label = "OUR POST THEY COMMENTED ON" if reaction_type == "comment" else "PRIOR DM CONTEXT"
        parts.append(f"{label}:\n{context_text.strip()}")
    parts.append(f"INCOMING {reaction_type.upper()} TO CLASSIFY:\n{incoming_text.strip()}")
    parts.append("Classify it now.")
    return "\n\n".join(parts)


def _coerce(data: dict[str, Any] | None) -> dict[str, str]:
    """A modell kimenetét biztonságos, ismert értékekre normalizálja (soha nem bukik)."""
    data = data or {}
    classification = str(data.get("classification") or "").strip().lower()
    if classification not in CLASSIFICATIONS:
        classification = "question_or_engagement"  # biztonságos default: inkább válaszolunk
    language = str(data.get("language") or "").strip().lower()
    if language not in ("hu", "en"):
        language = "hu"
    reason = str(data.get("reason") or "").strip()[:120]
    return {"classification": classification, "language": language, "reason": reason}


async def classify_reaction(
    incoming_text: str, reaction_type: str, context_text: str = ""
) -> dict[str, Any]:
    """Egy komment/DM besorolása. Visszaad:
    {classification, language, reason, skip: bool, skip_message: str | None}.

    Hiba/parse-hiba esetén sem bukik: a legrosszabb esetben question_or_engagement.
    """
    if reaction_type not in ("comment", "dm"):
        raise ValueError(f"Ismeretlen reaction_type: {reaction_type!r} (comment | dm)")

    try:
        msg = await _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _user(incoming_text, reaction_type, context_text)}],
        )
        parsed = _repair_and_parse(msg.content[0].text if msg.content else "")
    except Exception as exc:  # noqa: BLE001 — a besorolás sosem buktathatja meg a flow-t
        logger.warning("[reaction-classify] hiba (%s) — question_or_engagement default", str(exc)[:120])
        parsed = None

    result = _coerce(parsed if isinstance(parsed, dict) else None)
    skip = result["classification"] in SKIP_CLASSIFICATIONS
    result["skip"] = skip
    result["skip_message"] = SKIP_MESSAGES.get(result["classification"]) if skip else None
    return result


async def _demo() -> int:
    import json

    samples = [
        ("comment", "How do you handle rate limits in n8n?", ""),
        ("dm", "Hi, we're a 30-person logistics firm, do you work with our size?", ""),
        ("comment", "Check out my services! Best AI agency, DM me for a deal", ""),
    ]
    for rtype, text, ctx in samples:
        out = await classify_reaction(text, rtype, ctx)
        print(json.dumps({"input": text, **out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    setup_logging()
    raise SystemExit(asyncio.run(_demo()))
