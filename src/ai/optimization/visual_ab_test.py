"""Vizuál prompt A/B teszt — 4 stratégia, generálás, értékelés, győztes kiválasztása.

A 4 variáns ugyanazt a magyar overlay-szöveget használja (extract_visual_text), de eltérő
vizuális megközelítéssel:
  A — jelenlegi produkciós prompt (visual_generator.write_muapi_prompt)
  B — minimalistább (kevesebb elem, nagyobb tipográfia, sok negatív tér)
  C — filmesebb (erősebb film grain, atmoszféra, mélység)
  D — adat-fókuszú (a szám/stat dominál, teal glow)

Minden variánshoz: Muapi kép → VisualEvaluator pontszám → költség. Győztes: overall_score.

Önálló teszt (1 poszt, 4 variáns, valódi képek):
    python -m src.ai.optimization.visual_ab_test
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from dotenv import load_dotenv

from src.ai.optimization.visual_eval import VisualEvaluator
from src.integrations.visuals import muapi_client
from src.integrations.visuals import visual_generator as vg
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

VARIANT_LABELS = {
    "A": "produkciós (current)",
    "B": "minimalista (kevesebb elem, nagyobb type)",
    "C": "filmes (több grain, atmoszféra)",
    "D": "adat-fókuszú (szám dominál)",
}
DEFAULT_VARIANTS = ["A", "B", "C", "D"]

_WATERMARK = (
    "Do NOT render any logo or watermark — the real PlanSmart logo is composited later via PIL."
)
_NO_PEOPLE = "No people. No stock-photo clichés. No marketing buzzwords."


def _voice_style(voice: str) -> str:
    return vg.VOICE_STYLE_LINE.get(voice, "clean, minimalist, premium")


def _stat_block(stat: str | None, style: str) -> str:
    return f'\n{style}: "{stat}"\n' if stat else ""


def build_variant_prompt(variant: str, voice: str, vt: dict[str, Any]) -> str | None:
    """A megadott variáns image-prompt-ja a magyar overlay-szövegből + voice stílusból."""
    main, sub, stat = vt.get("main_text", ""), vt.get("sub_text", ""), vt.get("stat")
    style = _voice_style(voice)

    if variant == "B":  # minimalista
        return (
            "Dark #04060a near-black background, vast negative space. "
            "Bebas Neue / Neue Machina display typography, MASSIVE.\n\n"
            f'ONE focal line (huge, centered, white, Hungarian, all caps):\n"{main}"\n'
            f"{_stat_block(stat, 'Tiny stat detail (monospace, low-key)')}"
            f'Optional tiny caption (Hungarian, small, grey, bottom): "{sub}"\n\n'
            f"{_WATERMARK}\nMinimalist, high contrast, a single idea. Cinematic but restrained.\n"
            f"{_NO_PEOPLE} Absolutely no clutter — one element only.\n"
            f"Voice style: {style}."
        )
    if variant == "C":  # filmes
        return (
            "Dark #04060a cinematic background with volumetric lighting, atmospheric haze, "
            "pronounced film grain and subtle lens vignette. Moody, depth-of-field bokeh.\n\n"
            f'MAIN TEXT (large, centered, white, Hungarian, all caps):\n"{main}"\n'
            f"{_stat_block(stat, 'STAT (glowing accent)')}"
            f'SUBTEXT (smaller, grey, Hungarian):\n"{sub}"\n\n'
            f"{_WATERMARK}\nStrong 'Soul Cinema' aesthetic: dramatic light, grain, high contrast, "
            f"premium movie-poster feel.\n{_NO_PEOPLE} Single focal point.\n"
            f"Voice style: {style}."
        )
    if variant == "D":  # adat-fókuszú
        hero = stat or main
        return (
            "Dark #04060a background, faint technical grid. The NUMBER is the hero.\n\n"
            f'GIANT METRIC (dominant, centered, teal glow, monospace+display mix):\n"{hero}"\n\n'
            f'MAIN LABEL (Hungarian, all caps, above or below the number):\n"{main}"\n'
            f'SUBTEXT (small, grey, Hungarian):\n"{sub}"\n\n'
            f"{_WATERMARK}\nData-visual energy: the metric dominates the frame, everything else "
            f"supports it. Cinematic lighting, slight grain, high contrast.\n{_NO_PEOPLE} One focal number.\n"
            f"Voice style: {style}."
        )
    # A — produkciós (a generator által ténylegesen használt prompt)
    return None  # jelzi a hívónak, hogy az async produkciós buildert kell hívni


async def build_prompt(variant: str, voice: str, vt: dict[str, Any], post_content: str) -> str:
    """Variáns prompt — A-hoz az async produkciós buildert, B/C/D-hez a sablonokat használja."""
    if variant == "A":
        post = {"voice": voice, "content": post_content, "hashtags": []}
        return await vg.write_muapi_prompt(post, visual_text=vt)
    # B/C/D-re a template-builder mindig str-t ad (None-t csak az 'A'-ra, amit fentebb lekezeltünk).
    return cast(str, build_variant_prompt(variant, voice, vt))


async def _run_variant(
    variant: str, voice: str, vt: dict[str, Any], post_content: str, evaluator: VisualEvaluator
) -> dict[str, Any]:
    """Egy variáns: prompt → kép → értékelés → költség. Hiba esetén error mezővel tér vissza."""
    out: dict[str, Any] = {"variant": variant, "label": VARIANT_LABELS.get(variant, variant)}
    try:
        prompt = await build_prompt(variant, voice, vt, post_content)
        out["prompt"] = prompt
        res = await muapi_client.generate(
            prompt, model=muapi_client.DEFAULT_MODEL, aspect_ratio="1:1"
        )
        out["image_url"] = res.image_url
        out["cost"] = res.cost_usd or 0.0
    except Exception as exc:
        logger.warning("[ab] %s/%s generálás hiba: %s", voice, variant, str(exc)[:120])
        out.update({"image_url": None, "cost": 0.0, "scores": None, "error": str(exc)[:160]})
        return out

    out["scores"] = await evaluator.evaluate_image(out["image_url"], post_content, voice)
    return out


async def ab_test_prompts(
    post_content: str,
    voice: str,
    variant_count: int = 4,
    evaluator: VisualEvaluator | None = None,
    visual_text: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """N variáns (A..) generálása + értékelése egy poszthoz. Visszaad: variánsok + győztes + költség.

    A variánsokat konkurensen futtatjuk. A győztes a legmagasabb overall_score.
    """
    variants = DEFAULT_VARIANTS[: max(1, min(variant_count, len(DEFAULT_VARIANTS)))]
    evaluator = evaluator or VisualEvaluator()
    vt = visual_text or await vg.extract_visual_text(post_content)

    results = await asyncio.gather(
        *(_run_variant(v, voice, vt, post_content, evaluator) for v in variants)
    )

    scored = [r for r in results if r.get("scores") and not r["scores"].get("error")]
    winner = max(scored, key=lambda r: r["scores"]["overall_score"], default=None)
    total_cost = sum(r.get("cost") or 0.0 for r in results)

    return {
        "voice": voice,
        "visual_text": vt,
        "variants": results,
        "winner_variant": winner["variant"] if winner else None,
        "winner_score": winner["scores"]["overall_score"] if winner else None,
        "total_cost": round(total_cost, 4),
    }


async def _demo() -> int:
    import json

    content = (
        "73% a magyar KKV-knak heti 4+ órát ismétlődő manuális munkával tölt. "
        "A felét automatizálni lehetne egy hétvége alatt."
    )
    out = await ab_test_prompts(content, "adam")
    for r in out["variants"]:
        s = r.get("scores") or {}
        print(
            f"  {r['variant']}: overall={s.get('overall_score')} url={r.get('image_url')} ${r.get('cost')}"
        )
    print(
        f"GYŐZTES: {out['winner_variant']} ({out['winner_score']}) | költség ${out['total_cost']}"
    )
    print(json.dumps(out["winner_variant"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    setup_logging()
    raise SystemExit(asyncio.run(_demo()))
