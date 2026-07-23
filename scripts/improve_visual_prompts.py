"""Vizuál prompt iterációs elemző — a legrosszabb mintázatok + Sonnet javaslatok.

A legutóbbi eval eredményekből (data/visual_eval_results_latest.json):
  1. melyik hang teljesít konzisztensen gyengébben,
  2. melyik prompt-megközelítés (variáns) korrelál a magas pontszámmal,
  3. milyen visszajelzés-mintázatok ismétlődnek (anti-pattern flag-ek + feedback),
  4. Claude Sonnet 2 új prompt-variánst javasol a gyengeségekre + top 3 fejlesztés.

Költségmentes elemzés (nem generál képet). Az eredmény:
  data/visual_prompt_improvements.json

    python -m scripts.improve_visual_prompts
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.ai.generators.base_generator import _repair_and_parse

logger = logging.getLogger("improve_visual_prompts")
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
LATEST_FILE = DATA_DIR / "visual_eval_results_latest.json"
OUT_FILE = DATA_DIR / "visual_prompt_improvements.json"

MODEL = "claude-sonnet-4-6"
_STOP = {"the", "and", "for", "with", "that", "this", "are", "not", "but", "túl", "egy", "nem",
         "hogy", "ami", "lehet", "kicsit", "kép", "image", "text", "szöveg", "lenne", "több", "van"}


def _collect(results: dict) -> dict:
    posts = results["posts"]
    feedback, flags = [], []
    variant_scores: dict[str, list[float]] = {}
    voice_scores: dict[str, list[float]] = {}
    for p in posts:
        for v in p["variants"]:
            s = v.get("scores")
            if not s or s.get("error"):
                continue
            variant_scores.setdefault(v["variant"], []).append(s["overall_score"])
            voice_scores.setdefault(p["voice"], []).append(s["overall_score"])
            if s.get("feedback"):
                feedback.append(s["feedback"])
            flags.extend(s.get("anti_pattern_flags") or [])

    def _avg(d):
        return {k: round(sum(v) / len(v), 2) for k, v in d.items() if v}

    words = Counter()
    for fb in feedback:
        for w in re.findall(r"[\wáéíóöőúüűÁÉÍÓÖŐÚÜŰ]{4,}", fb.lower()):
            if w not in _STOP:
                words[w] += 1

    voice_avg = _avg(voice_scores)
    return {
        "variant_avg": _avg(variant_scores),
        "voice_avg": voice_avg,
        "weakest_voice": min(voice_avg, key=voice_avg.get) if voice_avg else None,
        "top_flags": flags and Counter(flags).most_common(8) or [],
        "recurring_feedback_terms": words.most_common(12),
        "sample_feedback": feedback[:24],
    }


async def _propose(analysis: dict) -> dict:
    system = (
        "You are a prompt engineer improving text-to-image prompts for the PlanSmart brand "
        "(dark #04060a, bold Bebas Neue/Neue Machina display type, Hungarian overlay text, ONE "
        "focal element, cinematic 'Soul Cinema' grain, subtle watermark; avoid clutter, stock-photo "
        "feel, generic AI art, garbled Hungarian text). You are given evaluation analytics. "
        "Return ONLY JSON: {\"diagnosis\":[3 short strings], \"top_3_improvements\":[3 short, "
        "concrete, actionable strings], \"new_variants\":[{\"key\":\"E\",\"name\":\"...\","
        "\"rationale\":\"...\",\"prompt_template\":\"...\"},{\"key\":\"F\",...}]}. "
        "The prompt_template must use {main_text},{sub_text},{stat},{voice_style} placeholders, "
        "keep the Hungarian overlay text verbatim, and directly address the weaknesses."
    )
    compact = {**analysis, "sample_feedback": analysis.get("sample_feedback", [])[:10]}
    user = (
        "EVAL ANALYTICS:\n" + json.dumps(compact, ensure_ascii=False, indent=2) +
        "\n\nPropose the improvements now. Focus especially on the weakest voice and the most "
        "recurring anti-patterns / feedback terms. Keep each prompt_template under ~120 words."
    )
    msg = await AsyncAnthropic().messages.create(
        model=MODEL, max_tokens=3000, system=system,
        messages=[{"role": "user", "content": user}],
    )
    data = _repair_and_parse(msg.content[0].text if msg.content else "")
    return data or {}


async def amain() -> int:
    if not LATEST_FILE.exists():
        logger.error("Hiányzik: %s — futtasd előbb a run_visual_eval-t.", LATEST_FILE)
        return 1
    results = json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    analysis = _collect(results)

    logger.info("Variáns átlagok: %s", analysis["variant_avg"])
    logger.info("Hang átlagok: %s | leggyengébb: %s", analysis["voice_avg"], analysis["weakest_voice"])
    logger.info("Top anti-pattern flag-ek: %s", analysis["top_flags"])
    logger.info("Ismétlődő feedback szavak: %s", analysis["recurring_feedback_terms"])

    proposal = await _propose(analysis)
    out = {"source": str(LATEST_FILE), "analysis": analysis, "proposal": proposal}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print("DIAGNÓZIS:")
    for d in proposal.get("diagnosis", []):
        print(f"  • {d}")
    print("\nTOP 3 FEJLESZTÉSI JAVASLAT:")
    for i, s in enumerate(proposal.get("top_3_improvements", []), 1):
        print(f"  {i}. {s}")
    print("\nÚJ JAVASOLT VARIÁNSOK:")
    for nv in proposal.get("new_variants", []):
        print(f"  [{nv.get('key')}] {nv.get('name')} — {nv.get('rationale','')[:100]}")
    print(f"\nMentve: {OUT_FILE}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    import asyncio
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
