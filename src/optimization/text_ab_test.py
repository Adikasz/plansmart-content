"""Szöveg A/B teszt — 5 variáns különböző hook-stratégiával, értékelve, rangsorolva.

Variánsok:
  A — jelenlegi produkciós prompt (hook-bias nélkül)
  B — CONTRARIAN hook bias
  C — DATA hook bias
  D — NARRATIVE hook bias
  E — PAIN POINT hook bias

Mindegyiket a TextEvaluator pontozza; a győztes a legmagasabb overall_score.

Önálló teszt:
    python -m src.optimization.text_ab_test
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from dotenv import load_dotenv

from src.generators.base_generator import generate as generate_post
from src.optimization.text_evaluator import TextEvaluator
from src.storage.models import FeedItem
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

VOICE_PROMPTS = {
    "david": "prompts/voice_david.md",
    "adam": "prompts/voice_adam.md",
    "plansmart": "prompts/voice_plansmart.md",
}

# Variáns → hook stratégia (A = produkciós, bias nélkül).
HOOK_STRATEGY = {"A": None, "B": "contrarian", "C": "data", "D": "narrative", "E": "pain"}
HOOK_LABEL = {"A": "production", "B": "contrarian", "C": "data", "D": "narrative", "E": "pain"}
# Phase 14: angol hook-direktívák (a "EN:" hook példák a viral_hooks_library.md-ből).
HOOK_DIRECTIVE = {
    "contrarian": "HOOK STRATEGY — CONTRARIAN: the first line challenges a widely held belief on "
                  "the topic (e.g. \"Don't automate. Think first.\").",
    "data": "HOOK STRATEGY — DATA: open with a concrete, surprising number/ratio that implies a "
            "story (e.g. \"I talked to 68 SME owners this year. One thing was the same for all of them.\").",
    "narrative": "HOOK STRATEGY — NARRATIVE: start in the MIDDLE of a story, with a concrete time "
                 "(e.g. \"Yesterday a client of ours snapped. They were right.\").",
    "pain": "HOOK STRATEGY — PAIN POINT: open with an honest, vulnerable admission or the audience's "
            "pain (e.g. \"We shipped our first AI project 2 weeks late. Here's what I learned.\").",
}


def _to_feed_item(feed: dict) -> FeedItem:
    return FeedItem(
        id=feed.get("id") or "scenario", source_name=feed.get("source") or "", source_priority=1,
        url=feed.get("url") or "", title=feed.get("title"), content=feed.get("summary"),
        score=feed.get("score"), tags=feed.get("tags") or [],
    )


def _manual(scenario: dict, hook: str | None) -> dict:
    """manual_instruction payload — opcionális hook-bias direktívával."""
    voice, ctype = scenario["voice"], scenario["content_type"]
    if scenario.get("kind") == "ai_news":
        f = scenario["feed"]
        base = (f"React to this AI news in your own voice (don't repeat it, bring your own angle): "
                f"TITLE: {f.get('title')}\nSUMMARY: {(f.get('summary') or '')[:600]}")
    else:
        base = scenario.get("instruction", "")
    if hook:
        base += "\n\n" + HOOK_DIRECTIVE[hook]
    return {"type": "manual_instruction", "instruction": base, "content_type": ctype,
            "voice": voice, "platform": "linkedin"}


def _linkedin(result: dict | None) -> str:
    if not result:
        return ""
    li = result.get("linkedin") or {}
    content = li.get("content", "")
    tags = li.get("hashtags") or []
    return f"{content}\n\n{' '.join(tags)}".strip() if tags else content


async def _one_variant(key: str, scenario: dict, evaluator: TextEvaluator) -> dict:
    voice, ctype = scenario["voice"], scenario["content_type"]
    hook = HOOK_STRATEGY[key]
    out: dict[str, Any] = {"variant": key, "strategy": HOOK_LABEL[key]}
    try:
        if key == "A" and scenario.get("kind") == "ai_news":
            # Produkciós út: a valós FeedItem. Ha a hang skip-eli (pl. Ádám + dev-hír),
            # fallback a hook-bias nélküli manual instruction-re, hogy legyen baseline.
            result = await generate_post(_to_feed_item(scenario["feed"]), VOICE_PROMPTS[voice])
            if not _linkedin(result):
                out["note"] = "produkciós skip → no-hook fallback baseline"
                result = await generate_post(_manual(scenario, None), VOICE_PROMPTS[voice])
        else:
            result = await generate_post(_manual(scenario, hook), VOICE_PROMPTS[voice])
    except Exception as exc:
        logger.warning("[ab-text] %s/%s gen hiba: %s", voice, key, str(exc)[:120])
        return {**out, "post": "", "scores": None, "error": str(exc)[:160]}
    post = _linkedin(result)
    if not post:
        return {**out, "post": "", "scores": None, "error": "voice skip / üres"}
    out["post"] = post
    out["scores"] = await evaluator.evaluate_post(post, voice, ctype)
    return out


async def generate_variants(
    scenario: dict, voice: str, content_type: str, variant_count: int = 5,
    evaluator: TextEvaluator | None = None,
) -> list[dict]:
    """N variáns (A..E) generálása + értékelése. Visszaad: overall_score szerint csökkenő lista."""
    scenario = {**scenario, "voice": voice, "content_type": content_type}
    evaluator = evaluator or TextEvaluator()
    keys = list(HOOK_STRATEGY)[: max(1, min(variant_count, len(HOOK_STRATEGY)))]
    variants = await asyncio.gather(*(_one_variant(k, scenario, evaluator) for k in keys))
    return sorted(
        variants,
        key=lambda v: (v.get("scores") or {}).get("overall_score", 0.0) if v.get("scores") else 0.0,
        reverse=True,
    )


async def _demo() -> int:
    scenario = {"kind": "seed", "instruction": "Oktató poszt: 5 jel, hogy automatizálni kellene egy folyamatot."}
    ranked = await generate_variants(scenario, "adam", "educational")
    for v in ranked:
        s = v.get("scores") or {}
        print(f"  {v['variant']} ({v['strategy']:10}) overall={s.get('overall_score','—')}")
    print("GYŐZTES:", ranked[0]["variant"], "—", (ranked[0].get("post") or "")[:120])
    return 0


if __name__ == "__main__":
    setup_logging()
    raise SystemExit(asyncio.run(_demo()))
