"""Közös generátor logika — voice prompt + Claude Sonnet + szigorú JSON parse.

A generate() betölti a voice prompt markdownt (system), elküldi a feed_item-et
JSON-ként (user), és visszaadja a parse-olt dict-et — vagy None-t, ha a modell
{"skip": true}-pal jelez (a hír nem illik az adott hanghoz).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlock
from dotenv import load_dotenv

from src.ai.generators.schemas import validate_generated
from src.core.config.settings import get_settings
from src.core.storage.cost_tracking import record_claude_usage
from src.core.storage.models import FeedItem
from src.utils.anthropic_cache import cached_system

# A JSON parse/repair a src.utils.json_repair-ben lakik; itt re-exportáljuk, hogy a történeti
# `from src.ai.generators.base_generator import _repair_and_parse` importok (optimization, outreach,
# visuals — 5 modul) érintetlenül maradjanak.
from src.utils.json_repair import _escape_inner_quotes as _escape_inner_quotes  # noqa: F401
from src.utils.json_repair import _repair_and_parse as _repair_and_parse  # noqa: F401
from src.utils.json_repair import _strip_fences as _strip_fences  # noqa: F401

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

MODEL = "claude-sonnet-4-6"  # ADR-008: voice generálás minőségi modellje
MAX_TOKENS = 2000  # 1500 -> 2000: a hosszabb (pl. educational) tartalom ne vágódjon el JSON közben
HOOK_MAX_TOKENS = 700  # a 3 hook-variáns + scoring kompakt válasza
SUMMARY_CHAR_CAP = 2000

# 3-2-1 hook framework — 3 variáns (különböző A-E típus), majd Claude pontoz és kiválaszt 1-et.
HOOK_TYPES_REF = (
    "A) CONTRARIAN — közhiedelem megkérdőjelezése. "
    "B) CURIOSITY GAP — eredmény említése, módszer elrejtése. "
    "C) DATA/SPECIFIC NUMBER — meglepő statisztikával nyit. "
    "D) PERSONAL STORY — sebezhető pillanat, tanulság. "
    "E) PRACTICAL PROMISE — konkrét eredmény + időkeret."
)
HOOK_SYSTEM = (
    "Te egy magyar LinkedIn copywriter vagy. Egy posztból generálsz 3 KÜLÖNBÖZŐ típusú "
    "horgot (hook = az első 1-2 sor, max 140 karakter, kérdőjel nélkül a végén), majd "
    "pontozod őket 1-10 skálán (mennyire scroll-stopping), és kiválasztod a legjobbat.\n\n"
    f"Hook típusok:\n{HOOK_TYPES_REF}\n\n"
    "KIZÁRÓLAG ezt a JSON-t add vissza:\n"
    '{"variants":[{"type":"A-E","text":"horog","score":1-10},...3 db...],'
    '"best_index":0-2}'
)


@lru_cache(maxsize=8)
def _load_prompt(voice_prompt_path: str) -> str:
    path = Path(voice_prompt_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()  # az ANTHROPIC_API_KEY-t a környezetből olvassa


def _build_payload(feed_item: FeedItem | dict[str, Any]) -> str:
    """A modellnek küldött user JSON — FeedItem-ből VAGY manual_instruction dict-ből."""
    if isinstance(feed_item, dict) and feed_item.get("type") == "manual_instruction":
        return json.dumps(
            {
                "type": "manual_instruction",
                "instruction": feed_item.get("instruction", ""),
                "content_type": feed_item.get(
                    "content_type"
                ),  # educational | case_study | workshop_promo | ai_news
                "voice": feed_item.get("voice"),
                "platform": feed_item.get("platform"),
            },
            ensure_ascii=False,
        )
    # A dict-ág mindig visszatér fentebb → a fallthrough garantáltan FeedItem (cast = runtime no-op).
    feed_item = cast(FeedItem, feed_item)
    return json.dumps(
        {
            "title": feed_item.title or "",
            "summary": (feed_item.content or "")[:SUMMARY_CHAR_CAP],
            "url": feed_item.url,
            "source": feed_item.source_name,
            "score": feed_item.score,
            "tags": feed_item.tags,
        },
        ensure_ascii=False,
    )


_DASH_RE = re.compile(r"\s*[—–]\s*")


def _normalize_dashes(text: str) -> str:
    """Em/en dash -> hyphen — a modell időnként em-dash-t ír a promptok tiltása ellenére is,
    ez a bevett ai_signature_risk AI-jelzés (text_evaluator._EM_DASH_RE), és mechanikusan
    kizárható ahelyett, hogy a rewrite-loopra bíznánk. ' - ' ha szóköz vette körül, különben
    sima '-' (pl. 'word—word' -> 'word-word', nem 'word - word')."""
    return _DASH_RE.sub(lambda m: " - " if len(m.group(0)) > 1 else "-", text)


AUTO_IMPROVE = get_settings().text_auto_improve
# Phase 14: az angol-natív minőségnek kevesebb a mentsége mint a magyar adaptációnak → 9.0 gate.
SHIP_THRESHOLD = get_settings().text_ship_threshold


async def generate(
    feed_item: FeedItem | dict[str, Any],
    voice_prompt_path: str,
    with_hook_variants: bool = False,
    auto_improve: bool | None = None,
) -> dict[str, Any] | None:
    """Egy voice-specifikus poszt generálása FeedItem-ből VAGY manual_instruction dict-ből.

    Manual: {"type": "manual_instruction", "instruction": "...", "voice": ..., "platform": ...}.
    None-t ad vissza, ha a modell skip-et jelez.

    with_hook_variants=True: a poszt megírása után 3-2-1 hook framework — 3 variánst
    generál (különböző A-E típus), Claude pontozza és kiválasztja a legjobbat.

    auto_improve (Phase 13): a generálás után TextEvaluator+improve_post fut; ha a pontszám a
    SHIP_THRESHOLD (alap 7.5) alatt van, a jobb átírt verzió kerül a linkedin.content-be.
    Alapból a TEXT_AUTO_IMPROVE env vezérli (alap: ki — extra Sonnet hívások / latencia miatt).
    """
    if auto_improve is None:
        auto_improve = AUTO_IMPROVE
    system = _load_prompt(voice_prompt_path)
    payload = _build_payload(feed_item)

    msg = await _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=cached_system(system),  # ~3.7k tokenes voice-prompt → prompt-caching (voice-onként)
        messages=cast("list[MessageParam]", [{"role": "user", "content": payload}]),
    )
    record_claude_usage(msg, MODEL, kind="voice_generation")
    raw_text = cast(TextBlock, msg.content[0]).text if msg.content else ""
    stem = Path(voice_prompt_path).stem

    data = _repair_and_parse(raw_text)
    if data is None:
        # Nem menthető JSON: logoljuk a nyers választ és None-t adunk (a hívó skip-ként kezeli).
        truncated = msg.stop_reason == "max_tokens"
        logger.error(
            "JSON parse SIKERTELEN [%s]%s — nyers válasz (első 800 kar):\n%s",
            stem,
            " [max_tokens-nél elvágva]" if truncated else "",
            (raw_text or "")[:800],
        )
        return None

    if data.get("skip") is True:
        logger.info("Skip [%s]: %s", stem, data.get("reason"))
        return None

    if with_hook_variants:
        post_text = ((data.get("linkedin") or {}).get("content") or "").strip()
        if post_text:
            hv = await generate_hook_variants(post_text)
            if hv:
                data["hook_variants"] = hv

    if auto_improve:
        await _auto_improve(data, feed_item, stem)

    li_final = data.get("linkedin") or {}
    if li_final.get("content"):
        li_final["content"] = _normalize_dashes(li_final["content"])
        data["linkedin"] = li_final

    # Best-effort séma-validáció a LLM-határon (log-only, visszafelé kompatibilis):
    # a hívó továbbra is a nyers dict-tel dolgozik, de a séma-eltérés naplózhatóvá válik.
    _, _schema_err = validate_generated(data)
    if _schema_err:
        logger.warning(
            "[schema] %s: a generált poszt eltér a GeneratedPost sémától — %s", stem, _schema_err
        )
    return data


async def _auto_improve(
    data: dict[str, Any], feed_item: FeedItem | dict[str, Any], stem: str
) -> None:
    """Phase 13 ship-gate + Phase 16 fabrikáció hard gate.

    - Ha a poszt pontszáma < SHIP_THRESHOLD, a jobb átírt verziót használjuk.
    - Ha fabrication_risk igaz, az improve_post akkor is átírat legalább egyszer, ha a pontszám már
      elérte a küszöböt (hard gate — az improve_post._needs_more kényszeríti), és a fabrikáció-mentes
      verziót fogadjuk el akkor is, ha a pontszáma nem nőtt.

    Lazy import (a text_improver → text_evaluator → base_generator kör elkerülésére).
    """
    li = data.get("linkedin") or {}
    post_text = (li.get("content") or "").strip()
    if not post_text:
        return
    voice = stem.replace("voice_", "")
    ctype = (feed_item.get("content_type") if isinstance(feed_item, dict) else None) or "ai_news"
    # Sourced-e a konkrétum? CSAK a valódi /create manual_instruction számít forrásnak (a szerző
    # maga írta le a konkrét, valós dolgot). A stratégia-seed (educational/workshop topic) is
    # type=manual_instruction, DE van seed_key-e — az csak egy TÉMA, nem valós forrás, tehát NEM
    # engedi a kitalált konkrétumot (a fabrikáció-gate rá is vonatkozik). A case_study seedet az
    # evaluator content_type=='case_study' ága kezeli (a case_studies.yml a forrás).
    has_manual_source = (
        isinstance(feed_item, dict)
        and feed_item.get("type") == "manual_instruction"
        and not feed_item.get("seed_key")
    )
    try:
        from src.ai.optimization.text_improver import improve_post

        result = await improve_post(
            post_text,
            voice,
            ctype,
            target_score=SHIP_THRESHOLD,
            max_iterations=3,
            has_manual_source=has_manual_source,
        )
    except Exception as exc:
        logger.warning("[auto-improve] %s hiba: %s", stem, str(exc)[:120])
        return
    init_scores = result.get("initial_scores") or {}
    final_scores = result.get("final_scores") or {}
    init_fab = bool(init_scores.get("fabrication_risk"))
    final_fab = bool(final_scores.get("fabrication_risk"))
    data["text_quality"] = {
        "initial": result["initial_score"],
        "final": result["final_score"],
        "improvement": result["improvement"],
        "fabrication_initial": init_fab,
        "fabrication_final": final_fab,
        "fabrication_reason": final_scores.get("fabrication_reason")
        or init_scores.get("fabrication_reason")
        or "",
    }
    # Elfogadjuk az átírt verziót, ha jobb pontszám VAGY ha eltüntette a fabrikációt.
    fixed_fabrication = init_fab and not final_fab
    if result["final_score"] > result["initial_score"] or fixed_fabrication:
        li["content"] = result["final_post"]
        data["linkedin"] = li
        logger.info(
            "[auto-improve] %s %.1f → %.1f (fab %s→%s)",
            stem,
            result["initial_score"],
            result["final_score"],
            init_fab,
            final_fab,
        )
    if final_fab:
        logger.warning(
            "[auto-improve] %s: fabrikáció-kockázat MEGMARADT az átírás után — %s",
            stem,
            final_scores.get("fabrication_reason", ""),
        )


async def generate_hook_variants(post_text: str) -> dict[str, Any] | None:
    """3-2-1 hook framework: 3 különböző típusú horog + Claude pontozás → legjobb kiválasztása.

    Visszaad: {"variants":[{"type","text","score"}, x3], "best_index":int, "best":{...}}
    vagy None hiba esetén (a hívó best-effort kezeli — a hookok opcionálisak).
    """
    try:
        msg = await _client().messages.create(
            model=MODEL,
            max_tokens=HOOK_MAX_TOKENS,
            system=HOOK_SYSTEM,
            messages=cast(
                "list[MessageParam]", [{"role": "user", "content": f"POSZT:\n{post_text}"}]
            ),
        )
        record_claude_usage(msg, MODEL, kind="hook_variants")
        data = _repair_and_parse(cast(TextBlock, msg.content[0]).text if msg.content else "")
    except Exception as exc:
        logger.warning("[hook] variáns generálás hiba: %s", str(exc)[:120])
        return None

    variants = [v for v in (data or {}).get("variants", []) if (v or {}).get("text")]
    if not variants:
        return None
    # best_index a modelltől; fallback a legmagasabb score-ra.
    bi = (data or {}).get("best_index")
    try:
        best_index = int(bi) if bi is not None else None
    except (TypeError, ValueError):
        best_index = None
    if best_index is None or not (0 <= best_index < len(variants)):
        best_index = max(range(len(variants)), key=lambda i: _safe_score(variants[i]))
    return {"variants": variants, "best_index": best_index, "best": variants[best_index]}


def _safe_score(variant: dict[str, Any]) -> int:
    try:
        return int(variant.get("score", 0))
    except (TypeError, ValueError):
        return 0
