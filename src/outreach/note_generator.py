"""Phase 18 / Part 3 — személyre szabott kapcsolat-üzenet generátor (Claude Sonnet).

Minden prospecthez rövid (LinkedIn ~300 kar) kapcsolatkérő üzenetet ír:
  • konkrét, ŐSZINTE utalás — DE csak MEGERŐSÍTETT, kvalitatív kontextusból
    (iparág/domain, konkrét megnevezett kihívás/téma, vagy a személy SAJÁT idézete),
  • NEM sales, NEM pitch — tiszta „szeretnék kapcsolódni, mert X" hangnem,
  • SOHA nem említi a PlanSmart szolgáltatásait (az korai — a beszélgetésre marad),
  • az alapító (Dávid vagy Ádám) hangján, aki küldeni fogja.

Hardening (Phase 18.1 — a Bengyel-eset tanulsága): a jegyzet NEM állíthat konkrét
számot (árbevétel, létszám, flottaméret) tényként, és NEM állíthatja a személy
JELENLEGI pozícióját/cégvezetői szerepét — a titkok elavulnak (Bengyel már EX-FoxPost
CEO). Csak kvalitatív, megerősített utalás megy bele; a relevance_notes-ban szereplő
számokat/címeket ellenőrizetlennek kezeljük.

Nyelv: hu_sme_owner (magyar KKV tulaj) → magyar; a többi (intl / specialist / peer) → angol.
Alapító: üzleti tulajokhoz Ádám (stratéga), technikai/AI körhöz Dávid (builder).

A projekt SDK-mintáján fut (anthropic==0.28.0-kompatibilis messages.create), NEM httpx —
ez sima szöveggenerálás, nem kell hozzá server-tool (szemben a prospect_research-csel).

Önálló teszt:
    python -m src.outreach.note_generator
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.storage import prospects as store
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 400
CHAR_LIMIT = 300  # LinkedIn kapcsolatkérő üzenet karakter-limit

# Melyik alapító küldi (voice) kategóriánként.
VOICE_FOR_CATEGORY = {
    "hu_sme_owner": "adam",     # üzleti tulaj → stratéga, tulaj-tulajnak
    "intl_sme_owner": "adam",
    "ai_specialist": "david",   # technikai → builder
    "industry_peer": "david",
}

# Az alapító hangjának TÖMÖR leírása a rövid üzenethez (NEM a teljes voice_*.md — az posztokra van).
VOICE_DESC = {
    "adam": ("You are Ádám, co-founder of PlanSmart — a business strategist. You write owner-to-owner, "
             "peer to peer, never top-down or salesy. Pragmatic, warm, concrete. You connect because "
             "you find the person's business or thinking genuinely interesting."),
    "david": ("You are Dávid, co-founder of PlanSmart — a hands-on builder/engineer. You write directly "
              "and concretely, engineer to engineer, no marketing fluff. You connect because their work, "
              "tooling, or ideas genuinely interest you."),
}


def default_voice_for(category: str) -> str:
    return VOICE_FOR_CATEGORY.get(category, "adam")


def note_language_for(prospect: dict[str, Any]) -> str:
    """hu_sme_owner vagy magyarországi cím → magyar üzenet; egyébként angol."""
    if prospect.get("category") == "hu_sme_owner":
        return "hu"
    country = (prospect.get("country") or "").strip().lower()
    if country in ("hu", "hungary", "magyarország", "magyarorszag"):
        return "hu"
    return "en"


def _system(voice: str, language: str) -> str:
    lang_line = ("Write the note in HUNGARIAN (natural, native, informal-professional 'ön'-less warmth "
                 "as peers)." if language == "hu" else "Write the note in ENGLISH.")
    return (
        f"{VOICE_DESC.get(voice, VOICE_DESC['adam'])}\n\n"
        "TASK: Write ONE short LinkedIn connection-request note to the person described.\n"
        "The PERSON notes below come from a web-research pass and MAY contain unverified or stale "
        "specifics — treat every number and every 'current title' claim in them as NOT confirmed.\n"
        f"HARD RULES:\n"
        f"- Maximum {CHAR_LIMIT} characters (LinkedIn's limit). Shorter is better.\n"
        "- Reference something SPECIFIC and genuine, but ONLY from these SAFE, confirmed sources: "
        "their industry/domain, a specific named challenge or theme, or their OWN words (a direct "
        "quote). No generic 'I'd love to connect and grow my network'.\n"
        "- DO NOT state any hard number as fact — no revenue, headcount/employee count, fleet size, "
        "funding, or growth figure — EVEN IF such a number appears in the notes. Leave numbers out.\n"
        "- DO NOT assert their CURRENT job title or that they currently own/lead/run the company "
        "(titles go stale — they may have moved on). If you mention the company, frame it around what "
        "they BUILT or their domain, in past or neutral phrasing — never 'as the current CEO/owner of X'.\n"
        "- NOT salesy, NOT a pitch. Pure 'I'd like to connect because X' tone.\n"
        "- NEVER mention PlanSmart, its services, automation offerings, or any product. Do not sell "
        "anything — this is just a genuine connection request.\n"
        "- No links, no call-to-action, no 'let's hop on a call'.\n"
        f"- {lang_line}\n"
        "Respond with ONLY the note text — no quotes, no preamble, no explanation."
    )


def _user(prospect: dict[str, Any]) -> str:
    fields = [
        f"Name: {prospect.get('name', '')}",
        f"Title: {prospect.get('title') or '-'}",
        f"Company: {prospect.get('company') or '-'}",
        f"Location: {', '.join(x for x in (prospect.get('city'), prospect.get('country')) if x) or '-'}",
        f"Why relevant: {prospect.get('relevance_notes') or '-'}",
    ]
    return "PERSON:\n" + "\n".join(fields) + "\n\nWrite the connection note now."


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()


def _clean_note(text: str) -> str:
    t = (text or "").strip()
    if len(t) >= 2 and t[0] in "\"'“”" and t[-1] in "\"'“”":
        t = t[1:-1].strip()
    return t


async def _one_call(system: str, user: str) -> str:
    msg = await _client().messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return _clean_note(msg.content[0].text if msg.content else "")


async def generate_note(
    prospect: dict[str, Any], voice: str | None = None, persist: bool = True
) -> dict[str, Any]:
    """Egy prospecthez kapcsolat-üzenet. Visszaad: {note, language, voice, char_count, over_limit}.

    Ha az első üzenet túllépi a limitet, egy rövidítő újrapróbát kérünk. persist=True esetén
    best-effort mentés (save_note → status='note_drafted'), ha a tábla létezik és van id.
    """
    voice = voice or default_voice_for(prospect.get("category", ""))
    language = note_language_for(prospect)
    system = _system(voice, language)

    note = await _one_call(system, _user(prospect))
    if len(note) > CHAR_LIMIT:
        shorten = (_user(prospect) + f"\n\nThe previous version was too long. Rewrite it UNDER "
                   f"{CHAR_LIMIT} characters, keeping the specific reference.")
        retried = await _one_call(system, shorten)
        if retried:
            note = retried

    result = {
        "note": note,
        "language": language,
        "voice": voice,
        "char_count": len(note),
        "over_limit": len(note) > CHAR_LIMIT,
    }

    if persist and prospect.get("id"):
        try:
            if store.table_ready():
                store.save_note(prospect["id"], note, voice, language)
                result["saved"] = True
        except Exception as exc:  # noqa: BLE001 — a mentés sosem buktathatja meg a generálást
            logger.warning("[note-gen] mentés hiba (%s)", exc)
    return result


async def _demo() -> int:
    import json

    sample = {
        "name": "Teszt Elek", "title": "ügyvezető", "company": "Példa Logisztika Kft.",
        "city": "Budapest", "country": "HU", "category": "hu_sme_owner",
        "relevance_notes": "Középméretű fuvarozó cég, nemrég posztolt a diszpécser-adminisztráció terheiről.",
    }
    out = await generate_note(sample, persist=False)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    setup_logging()
    raise SystemExit(asyncio.run(_demo()))
