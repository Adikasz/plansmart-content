"""Video-ötlet generátor (Phase 22) — Ádám heti reakció-videó talking-point váza.

Ugyanaz a hang-DNS mint a voice_adam.md (személyiség, fabrikáció-tiltás, anti-AI-jelzés
szabályok), DE más kimeneti struktúra: nem egy LinkedIn poszt, hanem egy videó-forgatókönyv
váz Ádámnak, hogy TERMÉSZETESEN, kamera előtt tudjon beszélni róla -- nem szó szerint
felolvasandó szkript (a hook kivétel, lásd lent).

Fabrikáció-ellenőrzés: a src.ai.optimization.text_evaluator.TextEvaluator-t hasznosítja újra
(nem egy párhuzamos, bespoke ellenőrzőt) -- a hook+talking_points+closing+caption egy
összefűzött szöveg-blobba kerül, és CSAK a fabrication_risk/fabrication_reason mezőt vesszük
figyelembe a válaszból (a többi pontszám LinkedIn-poszt-alakra van hangolva -- hossz, hook-erő
stb. -- ezek zajt adnának egy vázlatpont-struktúrán). Ha fabrikációt jelez, EGY célzott
újrapróbálkozás fut (nem a teljes improve_post() loop, ami LinkedIn-poszt-alakú kimenetet
várna és szétesne a struktúrán) -- lásd _generate_once fabrication_reason paramétere.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import TextBlock
from dotenv import load_dotenv

from src.ai.generators.base_generator import _repair_and_parse
from src.ai.optimization.text_evaluator import TextEvaluator
from src.core.storage.cost_tracking import record_claude_usage
from src.utils.anthropic_cache import cached_system

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Egyelőre csak Ádám -- a /create_video parancs explicit hibát ad a többire (lásd Phase 22 spec).
VOICE_PROMPTS = {"adam": "prompts/voice_adam.md"}

MODEL = "claude-sonnet-4-6"  # ADR-008: minőségi modell (összhangban base_generator-ral)
MAX_TOKENS = 1200
SUMMARY_CHAR_CAP = 2000
DURATION_MIN_S, DURATION_MAX_S = 60, 400  # szanity-clamp a modell becslésére (cél: 120-240s)

VIDEO_STRUCTURE_INSTRUCTIONS = """

---

VIDEO-ÖTLET MÓD (ÚJ KIMENETI FORMÁTUM — ez FELÜLÍRJA a fenti "What you output" JSON sémát):

A fenti személyiség / hang / fabrikáció-tiltás szabályok VÁLTOZATLANOK. De itt NEM egy
LinkedIn posztot írsz, hanem egy VÁZLATOT egy 2-4 perces reakció-videóhoz, amit a beszélő
TERMÉSZETESEN fog elmondani kamera előtt -- NEM egy szó szerint felolvasandó szkript.

Struktúra:
1. HOOK (5-10 mp): az ELSŐ 1-2 mondat, amit a beszélő MOND -- ez az egy rész lehet közel szó
   szerinti, mert egy erős hook precizitást igényel. Rövid, max 1-2 mondat.
2. TALKING POINTS (3-5 pont): TERMÉSZETES NYELVŰ útmutató arról, MIT kell elmondani -- NE
   "Mondd: 'Ez azért fontos mert...'", HANEM pl. "Magyarázd el miért fontos ez egy 20 fős
   cégnek, nem csak a nagyvállalatoknak." Ezek vázlatpontok, NEM szó szerinti szöveg.
3. CLOSING THOUGHT: egy pont, laza lezárás, NINCS kemény CTA, NINCS "link a kommentben" (a
   LinkedIn natív videó ezt úgysem támogatja).
4. SUGGESTED CAPTION: a TÉNYLEGES szöveg, amit a videó feltöltésekor beírnak -- rövid (2-4
   mondat), kontextust ad, 2-3 releváns hashtag. EZ a rész VALÓDI, publikált szöveg lesz, tehát
   a teljes LinkedIn-optimalizálási szabályok (hook-erő, nincs AI-jelzés nyelvezet, nincs
   banned buzzword) UGYANÚGY vonatkoznak rá, mint egy rendes posztra.
5. ESTIMATED_DURATION_SECONDS: becslés a talking point sűrűsége alapján, cél 120-240 másodperc.

Ha a hír NEM alkalmas reakció-videóra (pl. száraz technikai release note, nincs valódi
"reagálható" szög, vagy egyszerűen nem illik ehhez a hanghoz), adj vissza EZT és semmi mást:
{"skip": true, "reason": "..."}

Egyébként add vissza PONTOSAN ezt a JSON-t, semmi mást (nincs code fence, nincs magyarázat):
{
  "title": "rövid munkacím a videóhoz (belső használatra, NEM publikus)",
  "hook": "az első 1-2 mondat",
  "talking_points": ["pont 1", "pont 2", "pont 3"],
  "closing_thought": "egy mondat, laza lezárás",
  "suggested_caption": "a tényleges LinkedIn caption szövege, hashtagekkel",
  "estimated_duration_seconds": 180
}
"""

FABRICATION_FIX_TEMPLATE = """

---

FIGYELEM — JAVÍTÁS SZÜKSÉGES: az előző verziód fabrikáció-kockázatot tartalmazott: {reason}

Az előző verziód (JSON):
{previous_json}

Írd újra a teljes JSON-t úgy, hogy ez a konkrétum ELTŰNJÖN -- vagy cseréld le egy általános,
becsületesen keretezett megfigyelésre, vagy hagyd el a hamis specifikusságot és magyarázd el a
pontot általánosan. NE cserélj egy kitalált konkrétumot egy másik kitaláltra. A többi rész
(ami nem fabrikált) maradhat hasonló, csak a hibás rész javítandó.
"""


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()  # az ANTHROPIC_API_KEY-t a környezetből olvassa


@lru_cache(maxsize=4)
def _load_voice_prompt(voice: str) -> str:
    path = PROJECT_ROOT / VOICE_PROMPTS[voice]
    return path.read_text(encoding="utf-8")


def _build_system_prompt(voice: str) -> str:
    return _load_voice_prompt(voice) + VIDEO_STRUCTURE_INSTRUCTIONS


def _build_payload(feed_item: dict[str, Any]) -> str:
    payload = {
        "title": feed_item.get("title") or "",
        "summary": (feed_item.get("content") or "")[:SUMMARY_CHAR_CAP],
        "source": feed_item.get("source_name") or "",
        "url": feed_item.get("url") or "",
        "tags": feed_item.get("topics") or [],
    }
    return json.dumps(payload, ensure_ascii=False)


def _fabrication_check_text(data: dict[str, Any]) -> str:
    points = "\n".join(f"- {p}" for p in (data.get("talking_points") or []))
    return (
        f"HOOK: {data.get('hook', '')}\n\n"
        f"TALKING POINTS:\n{points}\n\n"
        f"CLOSING THOUGHT: {data.get('closing_thought', '')}\n\n"
        f"SUGGESTED CAPTION: {data.get('suggested_caption', '')}"
    )


def _clamp_duration(data: dict[str, Any]) -> None:
    try:
        d = int(data.get("estimated_duration_seconds") or 0)
    except (TypeError, ValueError):
        d = 0
    if d <= 0:
        d = 180  # ésszerű alapérték, ha a modell nem adott/hibás számot
    data["estimated_duration_seconds"] = max(DURATION_MIN_S, min(DURATION_MAX_S, d))


def _is_valid_shape(data: dict[str, Any]) -> bool:
    """A generált JSON tartalmazza-e a video_ideas séma KÖTELEZŐ mezőit a helyes típussal.

    Az LLM-válasz szintaktikailag lehet érvényes JSON (a _repair_and_parse ezt már
    biztosítja), de a KULCSOK/TÍPUSOK jelenlétét nem -- enélkül a hívó lánc (insert_video_idea
    idea["hook"]-ja, format_video_idea_message html.escape(p)-je a talking_points-on) hibázna.
    Érvénytelen alaknál a hívó UGYANÚGY kezeli, mint egy parse-hibát (None -> skip), a
    hívóknak (worker/bot) nem kell külön védekezniük emiatt.
    """
    if not isinstance(data.get("hook"), str) or not data["hook"].strip():
        return False
    points = data.get("talking_points")
    if (
        not isinstance(points, list)
        or not points
        or not all(isinstance(p, str) and p.strip() for p in points)
    ):
        return False
    return True


async def _generate_once(
    system: str,
    payload: str,
    fabrication_reason: str | None = None,
    previous_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    user_content = payload
    if fabrication_reason:
        previous_json = json.dumps(previous_data or {}, ensure_ascii=False, indent=2)
        user_content += FABRICATION_FIX_TEMPLATE.format(
            reason=fabrication_reason, previous_json=previous_json
        )

    msg = await _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=cached_system(system),  # ~3.8k tok, retry-ismételt
        messages=[{"role": "user", "content": user_content}],
    )
    record_claude_usage(msg, MODEL, kind="video_idea")
    raw_text = cast(TextBlock, msg.content[0]).text if msg.content else ""
    data = _repair_and_parse(raw_text)
    if data is None:
        truncated = msg.stop_reason == "max_tokens"
        logger.error(
            "[video-idea] JSON parse SIKERTELEN%s — nyers válasz (első 800 kar):\n%s",
            " [max_tokens-nél elvágva]" if truncated else "",
            (raw_text or "")[:800],
        )
        return None
    if not data.get("skip") and not _is_valid_shape(data):
        logger.error(
            "[video-idea] a modell válasza érvényes JSON, de hiányzik/hibás a "
            "kötelező mező (hook/talking_points) — kihagyva: %s",
            str(data)[:400],
        )
        return None
    return data


async def generate_video_idea(
    feed_item: dict[str, Any], voice: str = "adam"
) -> dict[str, Any] | None:
    """Egy feed_item-ből video-ötlet vázlat generálása a megadott hangon.

    Visszaad: a video_ideas séma mezőit tartalmazó dict (hook, talking_points, closing_thought,
    suggested_caption, estimated_duration_seconds, title, fabrication_risk, fabrication_reason)
    VAGY {"skip": True, "reason": "..."} ha a modell szerint a hír nem alkalmas reakció-videóra,
    VAGY None, ha a válasz nem parse-olható JSON-ná (a hívó skip-ként kezelje).

    voice: egyelőre KIZÁRÓLAG "adam" támogatott (ValueError másra).
    """
    if voice not in VOICE_PROMPTS:
        raise ValueError(
            f"Video-ötlet generálás egyelőre csak ezekre a hangokra támogatott: "
            f"{sorted(VOICE_PROMPTS)} (kapott: {voice!r})"
        )

    system = _build_system_prompt(voice)
    payload = _build_payload(feed_item)

    data = await _generate_once(system, payload)
    if data is None or data.get("skip"):
        return data

    _clamp_duration(data)
    evaluator = TextEvaluator()
    check_text = _fabrication_check_text(data)
    eval_result = await evaluator.evaluate_post(
        check_text, voice, "ai_news", has_manual_source=False
    )
    data["fabrication_risk"] = bool(eval_result.get("fabrication_risk"))
    data["fabrication_reason"] = eval_result.get("fabrication_reason") or ""

    if data["fabrication_risk"]:
        logger.warning(
            "[video-idea] fabrikáció-kockázat, javító újrapróbálkozás: %s",
            data["fabrication_reason"],
        )
        fixed = await _generate_once(
            system,
            payload,
            fabrication_reason=data["fabrication_reason"],
            previous_data=data,
        )
        if fixed and not fixed.get("skip"):
            _clamp_duration(fixed)
            check_text2 = _fabrication_check_text(fixed)
            eval_result2 = await evaluator.evaluate_post(
                check_text2, voice, "ai_news", has_manual_source=False
            )
            fixed["fabrication_risk"] = bool(eval_result2.get("fabrication_risk"))
            fixed["fabrication_reason"] = eval_result2.get("fabrication_reason") or ""
            data = fixed
            if data["fabrication_risk"]:
                logger.warning(
                    "[video-idea] fabrikáció-kockázat MEGMARADT a javítás után is — %s",
                    data["fabrication_reason"],
                )

    return data
