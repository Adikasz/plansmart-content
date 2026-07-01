"""Phase 6 teszt: a 3 voice generátor összehasonlítása UGYANAZON a feed item-en.

Kiválaszt egy score>=7 elemet (ha a DB-ben nincs pontozott elem, on-the-fly
pontoz Haiku-val), majd lefuttatja mindhárom generátort, és kiírja az outputokat
összehasonlításhoz.

Futtatás:
    python scripts/test_voice.py                              # 3 hang összehasonlítás
    python scripts/test_voice.py --voice david --type educational   # egy hang + content_type
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from src.filters.relevance_scorer import score_item  # noqa: E402
from src.generators.adam_generator import generate_adam  # noqa: E402
from src.generators.base_generator import generate as generate_post  # noqa: E402
from src.generators.david_generator import generate_david  # noqa: E402
from src.generators.plansmart_generator import generate_plansmart  # noqa: E402
from src.storage.db import get_client, has_service_key  # noqa: E402
from src.storage.models import FeedItem  # noqa: E402
from src.strategy import content_strategy  # noqa: E402

logger = logging.getLogger("test_voice")

VOICE_PROMPTS = {
    "david": "prompts/voice_david.md",
    "adam": "prompts/voice_adam.md",
    "plansmart": "prompts/voice_plansmart.md",
}
GENERATORS = {"david": generate_david, "adam": generate_adam, "plansmart": generate_plansmart}

# Tiltott marketing-buzzword-ök (a voice promptok szerint) — substringként keresve.
BANNED = [
    "forradalm", "game changer", "game-changer", "paradigmavált",
    "future of work", "next level", "áttörés", "breakthrough", "disruption",
]
PREMIUM = ["Anthropic Blog", "OpenAI Blog", "Cursor Blog", "LangChain Blog", "Google DeepMind"]
# SME-relevancia jelek — ezekre PlanSmart is szívesebben generál (nem enterprise/bank).
SME_SIGNALS = [
    "automation", "workflow", "agent", "productivity", "small business", "sme",
    "kkv", "pricing", "cost", "save", "team", "affordable", "adopt", "business",
]
MAX_CANDIDATES = 3  # ennyi itemet probalunk vegig, amig mind a 3 hang general


def _row_to_item(row: dict) -> FeedItem:
    return FeedItem(
        id=row["id"],
        source_name=row.get("source_name") or "",
        source_priority=row.get("source_priority") or 3,
        url=row.get("url") or "",
        title=row.get("title"),
        content=row.get("content"),
        author=row.get("author"),
        published_at=row.get("published_at"),
        score=row.get("score"),
        tags=row.get("topics") or [],
        raw_data=row.get("raw_data") or {},
        source_type=row.get("source_type") or "rss",
    )


def _sme_brandability(item: FeedItem) -> int:
    text = f"{item.title or ''} {item.content or ''}".lower()
    return sum(text.count(sig) for sig in SME_SIGNALS)


def _rank_candidates(client) -> list[FeedItem]:
    """score>=7 jelöltek, SME-relevancia szerint rangsorolva (PlanSmart-barát elöl)."""
    rows = client.table("feed_items").select("*").gte("score", 7).limit(15).execute().data or []
    items = [_row_to_item(r) for r in rows]

    if not items:
        logger.info("Nincs score>=7 a DB-ben — on-the-fly pontozas (Haiku) premium forrasokbol...\n")
        pool = client.table("feed_items").select("*").in_("source_name", PREMIUM).limit(15).execute().data or []
        for r in pool:
            item = _row_to_item(r)
            try:
                item.score = score_item(item).score
            except Exception as exc:
                logger.warning("  scoring hiba: %s", str(exc)[:60])
                continue
            if item.score is not None and item.score >= 7:
                items.append(item)

    items.sort(key=lambda x: (_sme_brandability(x), x.score or 0), reverse=True)
    return items


def _print_voice(name: str, result: dict | None) -> None:
    logger.info("\n" + "=" * 78)
    logger.info("  %s", name)
    logger.info("=" * 78)
    if result is None:
        logger.info("  (SKIP — a modell szerint a hír nem illik ehhez a hanghoz)")
        return
    li = result.get("linkedin") or {}
    logger.info("[LinkedIn]\n%s", li.get("content", "(nincs content)"))
    if li.get("hashtags"):
        logger.info("\nHashtags: %s", " ".join(li["hashtags"]))
    if li.get("best_time"):
        logger.info("Best time: %s", li["best_time"])
    tw = result.get("twitter")
    if tw:
        tweets = tw.get("tweets", [])
        logger.info("\n[Twitter — %s, %d tweet]", tw.get("type", "?"), len(tweets))
        for i, t in enumerate(tweets, 1):
            logger.info("  (%d) %s", i, t)
    else:
        logger.info("\n[Twitter] (nincs — várt viselkedés PlanSmart-nál)")
    if result.get("notes"):
        logger.info("\nNotes: %s", result["notes"])


def _flatten(result: dict | None) -> str:
    if not result:
        return ""
    parts: list[str] = []
    li = result.get("linkedin") or {}
    parts.append(li.get("content", ""))
    parts += li.get("hashtags", [])
    tw = result.get("twitter") or {}
    parts += tw.get("tweets", [])
    return " ".join(parts)


async def _generate_all(item: FeedItem):
    raw = await asyncio.gather(
        generate_david(item), generate_adam(item), generate_plansmart(item),
        return_exceptions=True,
    )
    clean = [None if isinstance(r, Exception) else r for r in raw]
    return clean, list(raw)


async def _find_and_generate(candidates: list[FeedItem]):
    """Végigpróbálja a jelölteket, amíg mind a 3 hang generál; egyébként az első próbát adja vissza."""
    fallback = None
    for cand in candidates[:MAX_CANDIDATES]:
        logger.info("Probalom: %-18s score=%s sme=%d ...", cand.source_name, cand.score, _sme_brandability(cand))
        clean, raw = await _generate_all(cand)
        if fallback is None:
            fallback = (cand, clean, raw)
        if all(x is not None for x in clean):
            return cand, clean, raw
    return fallback


async def _generate_typed(voice: str, ctype: str) -> dict | None:
    """Egy hang + content_type generálás (a content_strategy seed alapján)."""
    if ctype == "ai_news":
        client = get_client(use_service_key=has_service_key())
        cands = _rank_candidates(client)
        if not cands:
            logger.error("Nincs score>=7 feed item az ai_news teszthez.")
            return None
        item = cands[0]
        logger.info("ai_news forrás: %s — %s", item.source_name, item.title)
        return await GENERATORS[voice](item)

    seed = content_strategy.get_seed(ctype, voice, set())
    if seed is None:
        logger.error("Nincs '%s' típusú seed a(z) %s hanghoz (voice_fit?).", ctype, voice)
        return None
    logger.info("content_type=%s | seed='%s'\nInstrukció:\n%s\n", ctype, seed["seed_key"], seed["instruction"])
    return await generate_post(seed, VOICE_PROMPTS[voice])


def _run_typed(voice: str, ctype: str) -> int:
    name = f"{voice.upper()} / {ctype}"
    result = asyncio.run(_generate_typed(voice, ctype))
    _print_voice(name, result)
    if result is None:
        logger.info("\n(Eredmény: None — parse hiba VAGY skip. Lásd a logot fentebb.)")
        return 1
    logger.info("\n[OK] Érvényes JSON generálva, parse sikeres.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="Voice generátor teszt (3-hang összehasonlítás VAGY 1 hang + content_type).")
    ap.add_argument("--voice", choices=list(VOICE_PROMPTS), help="egy konkrét hang tesztelése")
    ap.add_argument("--type", dest="ctype", choices=content_strategy.CONTENT_TYPES,
                    help="content_type (educational | workshop_promo | case_study | ai_news)")
    args = ap.parse_args()

    if args.voice:
        ctype = args.ctype or "educational"
        return _run_typed(args.voice, ctype)

    client = get_client(use_service_key=has_service_key())
    candidates = _rank_candidates(client)
    if not candidates:
        logger.error("Nem talaltam score>=7 elemet a teszthez.")
        return 1

    item, clean, raw = asyncio.run(_find_and_generate(candidates))

    logger.info("\nKIVALASZTOTT ITEM (score=%s, sme-brandability=%d)", item.score, _sme_brandability(item))
    logger.info("  forras : %s", item.source_name)
    logger.info("  cim    : %s", item.title)
    logger.info("  url    : %s", item.url)

    names = ["DÁVID (builder)", "ÁDÁM (strategist)", "PLANSMART (brand)"]
    for name, res, rawres in zip(names, clean, raw):
        if isinstance(rawres, Exception):
            logger.info("\n=== %s ===\n  HIBA: %s", name, repr(rawres)[:200])
        else:
            _print_voice(name, res)

    # Acceptance ellenorzesek
    logger.info("\n" + "#" * 78)
    logger.info("  ACCEPTANCE ELLENORZESEK")
    logger.info("#" * 78)

    li_texts = [(c.get("linkedin") or {}).get("content", "") for c in clean if c]
    distinct = len(set(li_texts)) == len(li_texts) and len(li_texts) >= 2
    logger.info("Mind kulonbozo output: %s (%d nem-skip)", "IGEN" if distinct else "NEM", len(li_texts))

    any_buzz = False
    for name, c in zip(names, clean):
        hits = [b for b in BANNED if b in _flatten(c).lower()]
        if hits:
            any_buzz = True
            logger.info("  [BUZZWORD] %s: %s", name, hits)
    logger.info("Buzzword-mentes: %s", "NEM" if any_buzz else "IGEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
