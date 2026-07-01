"""Szöveg-minőség értékelő — magyar LinkedIn posztok pontozása Claude Sonnettel.

A 2026-os algoritmus + engagement kritériumok szerint pontoz: hook erő, emberi érzet,
magyar nyelvhelyesség, konkrét érték, voice-konzisztencia, engagement potenciál — plusz
anti-pattern lista és (gyenge poszt esetén) teljes átírási javaslat.

Önálló teszt:
    python -m src.optimization.text_evaluator
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.generators.base_generator import _repair_and_parse

logger = logging.getLogger(__name__)
load_dotenv(override=False)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1400

SCORE_KEYS = (
    "hook_strength", "human_feel", "hungarian_quality",
    "concrete_value", "voice_consistency", "engagement_potential",
)

# Az értékelőnek átadott voice-elvárás.
VOICE_EXPECTATION = {
    "david": "Dávid — builder: közvetlen, technikai, konkrét; saját build-tapasztalat, kódrészletek, "
             "buildlog hangulat. SOHA marketing-buzzword.",
    "adam": "Ádám — stratéga: üzleti, érvelő, tulaj-tulajnak; ROI/idő számok, döntéshozói nézőpont. "
            "SOHA technikai jargon, SOHA fentről-lefelé.",
    "plansmart": "PlanSmart — brand: 'mi' forma, eredmény-orientált, számszerűsített, anonim "
                 "ügyféleredmény. SOHA személyes vélemény vagy építői részlet.",
}

# AI-tell-tale kifejezések, amiket flag-elni kell (a feladatból + bővítve).
AI_TELLS = [
    "fontos megérteni", "kihasználva", "lehetőséget biztosítva", "kulcsfontosságú", "jelentős",
    "innovatív", "a mai rohanó világban", "nem szabad elfelejteni", "összességében",
    "ezáltal", "lehetővé teszi", "számos előnnyel",
]
BUZZWORDS = ["forradalom", "forradalmi", "game changer", "diszruptív", "diszrupció", "paradigmaváltás"]

SYSTEM_PROMPT = (
    "Te egy magyar LinkedIn copywriting szakértő vagy, aki a 2026-os algoritmus és engagement "
    "kritériumok szerint SZIGORÚAN pontoz egy posztot 1-10 skálán. Mindig EGYETLEN JSON objektummal "
    "válaszolsz, körülötte semmi szöveg.\n\n"
    "Pontozási kulcsok (egész 1-10):\n"
    "  hook_strength        — megállítja a görgetést? Az első ~140 karakter contrarian/data/"
    "narrative/pain/comparison hook-e, vagy lapos/általános?\n"
    "  human_feel           — embernek hangzik vagy AI-generáltnak? Az AI-tell és sablonos "
    "fordulatok rontják.\n"
    "  hungarian_quality    — nyelvhelyesség, szórend, természetes folyás; angolból tükörfordított "
    "szerkezetek rontják.\n"
    "  concrete_value       — konkrét szám/név/példa van, vagy általános ('sokat', 'rengeteg', "
    "'számos')? Üres közhely = alacsony.\n"
    "  voice_consistency    — a megadott voice-hoz illik?\n"
    "  engagement_potential — kommentelnének/mentenék? Valódi kérdés vagy insight, nem engagement-bait.\n\n"
    "Amit KERESS és flag-elj az anti_patterns-ben (konkrétan idézd a problémás részt):\n"
    f"  • AI-tell kifejezések: {', '.join(AI_TELLS)}\n"
    f"  • Buzzword: {', '.join(BUZZWORDS)}\n"
    "  • Általános mennyiség konkrét szám helyett ('sokat', 'rengeteg', 'számos', 'rengetegen')\n"
    "  • Hiányzó emberi jel (nincs 'tegnap', 'ma reggel', 'az ügyfelünk', 'mi csináltuk', konkrét időpont)\n"
    "  • Angolból tükörfordított szórend / esetlen mondat\n"
    "  • Üres business-közhely / klisé\n"
    "  • Rossz CTA: 'Egyetértesz?', engagement-bait, külső link, 'DM-ezz', 'foglalj időpontot'\n\n"
    "Az overall_score holisztikus (nem a részpontok átlaga). Ha overall < 7, a rewrite_suggestion "
    "egy TELJES, kész átírt poszt legyen (ugyanaz a voice, magyar, betűhű, hookkal). Ha >= 7, a "
    "rewrite_suggestion legyen üres string.\n\n"
    'Válasz CSAK ezzel a JSON-nal: {"hook_strength":int,"human_feel":int,"hungarian_quality":int,'
    '"concrete_value":int,"voice_consistency":int,"engagement_potential":int,"anti_patterns":[...],'
    '"overall_score":float,"rewrite_suggestion":"...","feedback":"..."}'
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _local_flags(text: str) -> list[str]:
    """Determinisztikus, kódból ellenőrizhető anti-pattern jelek (a modelltől függetlenül)."""
    low = (text or "").lower()
    flags = []
    for t in AI_TELLS:
        if t in low:
            flags.append(f"AI-tell: „{t}”")
    for b in BUZZWORDS:
        if b in low:
            flags.append(f"buzzword: „{b}”")
    for vague in ("sokat", "rengeteg", "számos", "rengetegen", "rengeteget"):
        if vague in low:
            flags.append(f"általános mennyiség: „{vague}” (konkrét szám kellene)")
    if "egyetértesz" in low:
        flags.append("engagement-bait CTA: „Egyetértesz?”")
    if "http://" in low or "https://" in low:
        flags.append("külső link a posztban (-60% reach)")
    return flags


class TextEvaluator:
    """Magyar LinkedIn posztokat pontoz a 2026 engagement-kritériumok szerint (Sonnet)."""

    async def evaluate_post(self, post_content: str, voice: str, content_type: str) -> dict[str, Any]:
        """Egy poszt értékelése. Hiba esetén overall_score=0 + a hiba a feedbackben."""
        expectation = VOICE_EXPECTATION.get(voice, "")
        user = (
            f"VOICE: {voice}\nVOICE ELVÁRÁS: {expectation}\nCONTENT TYPE: {content_type}\n\n"
            f"POSZT:\n{post_content}\n\nÉrtékeld a posztot. Csak a JSON-t add vissza."
        )
        try:
            msg = await _client().messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            )
            data = _repair_and_parse(msg.content[0].text if msg.content else "")
        except Exception as exc:
            logger.warning("[text-eval] hiba (%s): %s", voice, str(exc)[:120])
            return self._error(f"eval hiba: {str(exc)[:100]}")
        if not data:
            return self._error("a modell nem adott értelmezhető JSON-t")
        return self._normalize(data, post_content)

    @staticmethod
    def _normalize(data: dict[str, Any], post_content: str) -> dict[str, Any]:
        scores = {k: _clamp(data.get(k), 1, 10, 5) for k in SCORE_KEYS}
        flags = [str(f) for f in (data.get("anti_patterns") or []) if str(f).strip()]
        for lf in _local_flags(post_content):
            if lf not in flags:
                flags.append(lf)
        try:
            overall = float(data.get("overall_score"))
        except (TypeError, ValueError):
            overall = sum(scores.values()) / len(scores)
        overall = max(1.0, min(10.0, round(overall, 2)))
        return {
            **scores, "anti_patterns": flags, "overall_score": overall,
            "rewrite_suggestion": str(data.get("rewrite_suggestion") or "").strip(),
            "feedback": str(data.get("feedback") or "").strip(),
        }

    @staticmethod
    def _error(reason: str) -> dict[str, Any]:
        return {
            **{k: 0 for k in SCORE_KEYS}, "anti_patterns": ["eval_error"],
            "overall_score": 0.0, "rewrite_suggestion": "", "feedback": reason, "error": True,
        }


async def _demo() -> int:
    sample = ("Az AI forradalom korában fontos megérteni, hogy a vállalatok számára "
              "kulcsfontosságú a digitalizáció. Számos lehetőséget biztosít. Egyetértesz?")
    out = await TextEvaluator().evaluate_post(sample, "adam", "ai_news")
    print(json.dumps(out, ensure_ascii=False, indent=2))
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
