"""Iteratív szöveg-javító — addig írja át a posztot, amíg eléri a cél-pontszámot.

Loop: értékel (TextEvaluator) → ha >= target, kész → különben Claude Sonnet átírja a
feedback + a viral hook könyvtár + a voice prompt alapján → újra-értékel. Max N iteráció.

Önálló teszt:
    python -m src.optimization.text_improver
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.optimization.text_evaluator import TextEvaluator

logger = logging.getLogger(__name__)
load_dotenv(override=False)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1200
PROJECT_ROOT = Path(__file__).resolve().parents[2]

VOICE_PROMPTS = {
    "david": "prompts/voice_david.md",
    "adam": "prompts/voice_adam.md",
    "plansmart": "prompts/voice_plansmart.md",
}


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()


@lru_cache(maxsize=8)
def _read(rel: str) -> str:
    p = PROJECT_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


@lru_cache(maxsize=1)
def _hooks() -> str:
    return _read("prompts/viral_hooks_library.md")


def _rewrite_system(voice: str) -> str:
    voice_md = _read(VOICE_PROMPTS.get(voice, ""))
    return (
        "Te egy magyar LinkedIn copywriter vagy. A feladatod egy meglévő poszt ÁTÍRÁSA úgy, hogy "
        "erősebb legyen: ütős hook (lásd a hook-könyvtárat), emberi hang, konkrét szám/példa, "
        "nyelvileg hibátlan magyar, az adott voice-ban. NE találj ki hamis adatot — ha nincs konkrét "
        "szám a forrásban, használj hihető, a tartalomból következő konkrétumot, de ne hazudj tényt.\n\n"
        "Kerüld: AI-tell fordulatok (fontos megérteni, kulcsfontosságú, kihasználva, jelentős, "
        "innovatív), buzzword (forradalom, game changer, diszruptív), általános mennyiség (sokat, "
        "rengeteg, számos), engagement-bait ('Egyetértesz?'), külső link.\n\n"
        f"=== VOICE PROMPT ===\n{voice_md}\n\n=== HOOK KÖNYVTÁR ===\n{_hooks()}\n\n"
        "KIZÁRÓLAG a kész, átírt magyar posztot add vissza — sem magyarázat, sem JSON, sem idézőjel, "
        "sem cím. Csak a poszt szövege (a hashtagekkel a végén, ha kellenek)."
    )


async def _rewrite(post: str, voice: str, content_type: str, scores: dict) -> str:
    user = (
        f"CONTENT TYPE: {content_type}\n\n"
        f"JELENLEGI POSZT (overall {scores.get('overall_score')}):\n{post}\n\n"
        f"AZ ÉRTÉKELŐ VISSZAJELZÉSE:\n{scores.get('feedback', '')}\n\n"
        f"KONKRÉT PROBLÉMÁK:\n- " + "\n- ".join(scores.get("anti_patterns") or ["—"]) + "\n\n"
    )
    if scores.get("rewrite_suggestion"):
        user += f"AZ ÉRTÉKELŐ ÁTÍRÁSI JAVASLATA (inspiráció, nem kötelező):\n{scores['rewrite_suggestion']}\n\n"
    user += "Írd át a posztot most, a fenti hibákat javítva. Csak a kész posztot add vissza."

    msg = await _client().messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=_rewrite_system(voice),
        messages=[{"role": "user", "content": user}],
    )
    text = (msg.content[0].text if msg.content else "").strip()
    # Esetleges körítés levágása (idézőjel-keret).
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1].strip()
    return text


async def improve_post(
    raw_post: str,
    voice: str,
    content_type: str,
    target_score: float = 8.0,
    max_iterations: int = 3,
    evaluator: TextEvaluator | None = None,
) -> dict[str, Any]:
    """Iteratívan javítja a posztot a cél-pontszámig vagy a max iterációig.

    Visszaad: {"final_post", "iterations":[{post,scores,feedback}], "final_score", "improvement"}.
    """
    evaluator = evaluator or TextEvaluator()
    current = raw_post
    scores = await evaluator.evaluate_post(current, voice, content_type)
    initial_score = scores.get("overall_score", 0.0)
    iterations: list[dict] = [{"iter": 0, "post": current, "scores": scores}]

    it = 0
    while scores.get("overall_score", 0.0) < target_score and it < max_iterations:
        it += 1
        try:
            rewritten = await _rewrite(current, voice, content_type, scores)
        except Exception as exc:
            logger.warning("[improve] %s átírás hiba (it %d): %s", voice, it, str(exc)[:120])
            break
        if not rewritten:
            break
        new_scores = await evaluator.evaluate_post(rewritten, voice, content_type)
        iterations.append({"iter": it, "post": rewritten, "scores": new_scores})
        # Csak akkor fogadjuk el, ha javított (különben tartjuk a jobbat).
        if new_scores.get("overall_score", 0.0) >= scores.get("overall_score", 0.0):
            current, scores = rewritten, new_scores
        else:
            logger.info("[improve] %s it %d nem javított (%.1f→%.1f) — előző marad",
                        voice, it, scores.get("overall_score"), new_scores.get("overall_score"))
            # tovább próbálkozunk a jobbik alapról
            continue

    # A legjobb iterációt választjuk (nem feltétlen az utolsót).
    best = max(iterations, key=lambda x: x["scores"].get("overall_score", 0.0))
    return {
        "final_post": best["post"],
        "iterations": iterations,
        "final_score": best["scores"].get("overall_score", 0.0),
        "initial_score": initial_score,
        "improvement": round(best["scores"].get("overall_score", 0.0) - initial_score, 2),
        "final_scores": best["scores"],
    }


async def _demo() -> int:
    import json

    sample = ("Az AI forradalom korában fontos megérteni, hogy a magyar KKV-knak rengeteg "
              "lehetőséget biztosít az automatizáció. Egyetértesz?")
    out = await improve_post(sample, "adam", "ai_news", target_score=8.0, max_iterations=2)
    print("initial:", out["initial_score"], "→ final:", out["final_score"], f"(Δ{out['improvement']})")
    print("\nFINAL POST:\n", out["final_post"])
    print("\niterations:", [round(i["scores"].get("overall_score", 0), 1) for i in out["iterations"]])
    return 0


if __name__ == "__main__":
    import asyncio
    import os
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    raise SystemExit(asyncio.run(_demo()))
