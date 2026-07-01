"""Vizuál-minőség értékelő — Claude Sonnet vision a PlanSmart brand kritériumok szerint.

A VisualEvaluator letölti a képet (Muapi CDN), base64-ben átadja a Sonnet vision modellnek,
és JSON pontszámokat kér: brand alignment, magyar szöveg minőség, scroll-stopping,
professzionalizmus, anti-pattern jelzők, összpontszám + szöveges visszajelzés.

Az anthropic 0.28.0 SDK image source-ként base64-et vár (URL source-t nem támogat),
ezért a képet httpx-szel letöltjük és base64-eljük.

Önálló teszt:
    python -m src.optimization.visual_eval <image_url> [voice]
"""
from __future__ import annotations

import asyncio
import base64
import logging
from functools import lru_cache
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.generators.base_generator import _repair_and_parse

logger = logging.getLogger(__name__)
load_dotenv(override=False)

MODEL = "claude-sonnet-4-6"  # vision-képes minőségi modell
MAX_TOKENS = 900
FETCH_TIMEOUT_S = 30.0

VOICE_EXPECTATION = {
    "david": "Dávid — technikai builder: terminal/kód/rendszerdiagram hangulat, hűvös teal/kék "
             "accent, monospace részletek. Fejlesztői hitelesség.",
    "adam": "Ádám — üzleti stratéga: financial-dashboard minimalizmus, meleg arany vagy lágy zöld "
            "metrika-accent, before/after keretezés. Tulaj-tulajnak hangulat.",
    "plansmart": "PlanSmart — hivatalos brand: prémium SaaS, letisztult semleges accentek, látható de "
                 "nem domináns PlanSmart wordmark, megbízható és hivatalos.",
}

SCORE_KEYS = ("brand_alignment_score", "hungarian_text_quality", "scroll_stopping_score", "professional_score")

SYSTEM_PROMPT = (
    "You are a senior brand art director evaluating a generated social-media visual for the "
    "PlanSmart brand (Hungarian B2B AI-automation company). You judge strictly and consistently "
    "on a 1-10 scale. You ALWAYS respond with a single JSON object, no prose around it.\n\n"
    "PlanSmart brand DNA the image SHOULD meet:\n"
    "• Dark near-black #04060a background\n"
    "• Bold display typography (Bebas Neue / Neue Machina character)\n"
    "• Hungarian overlay text that is readable AND grammatically correct (watch for garbled/"
    "hallucinated letters, wrong accents, nonsense words — common in AI image text)\n"
    "• Exactly ONE clear focal element (a number, stat, or short statement) — not cluttered\n"
    "• Cinematic / 'Soul Cinema' aesthetic: lighting, slight film grain, high contrast\n"
    "• A subtle (visible but not dominant) PlanSmart watermark\n\n"
    "Anti-patterns the image SHOULD NOT have (flag any present):\n"
    "• stock-photo feel (handshakes, generic offices, smiling people)\n"
    "• generic AI-art look (melted shapes, nonsense UI, over-rendered)\n"
    "• multiple competing elements / clutter\n"
    "• garbled or non-Hungarian text\n\n"
    "Scoring keys (all integers 1-10):\n"
    "  brand_alignment_score   — dark bg + display type + watermark + overall fit\n"
    "  hungarian_text_quality  — is the Hungarian text correct, readable, well-set?\n"
    "  scroll_stopping_score   — would it stop the thumb in a LinkedIn feed?\n"
    "  professional_score      — does it look like a real, premium company made it?\n"
    "Then:\n"
    "  anti_pattern_flags      — array of short strings for any anti-patterns present (empty if none)\n"
    "  overall_score           — float 1-10, holistic (not just the average)\n"
    "  feedback                — one or two sentences, concrete, what to improve\n\n"
    'Respond ONLY with: {"brand_alignment_score":int,"hungarian_text_quality":int,'
    '"scroll_stopping_score":int,"professional_score":int,"anti_pattern_flags":[...],'
    '"overall_score":float,"feedback":"..."}'
)


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()


def _media_type(url: str, content_type: str | None) -> str:
    if content_type and content_type.startswith("image/"):
        return content_type.split(";")[0].strip()
    lower = url.lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/png"


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


class VisualEvaluator:
    """Generált vizuálokat pontoz a PlanSmart brand kritériumok szerint (Sonnet vision)."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client

    async def _fetch_b64(self, image_url: str) -> tuple[str, str]:
        # Lokális fájl (komponált overlay) — közvetlen olvasás, nincs HTTP.
        if not str(image_url).startswith("http"):
            from pathlib import Path

            data = await asyncio.to_thread(Path(image_url).read_bytes)
            return base64.standard_b64encode(data).decode("ascii"), _media_type(image_url, None)
        own = self._http is None
        client = self._http or httpx.AsyncClient()
        try:
            resp = await client.get(image_url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
            resp.raise_for_status()
            media = _media_type(image_url, resp.headers.get("content-type"))
            return base64.standard_b64encode(resp.content).decode("ascii"), media
        finally:
            if own:
                await client.aclose()

    async def evaluate_image(self, image_url: str, post_content: str, voice: str) -> dict[str, Any]:
        """Egy kép értékelése. Hiba esetén overall_score=0 + a hiba a feedbackben."""
        try:
            b64, media = await self._fetch_b64(image_url)
        except Exception as exc:
            logger.warning("[visual-eval] kép letöltés hiba: %s", str(exc)[:120])
            return self._error_result(f"kép letöltés hiba: {str(exc)[:100]}")

        expectation = VOICE_EXPECTATION.get(voice, "")
        user_text = (
            f"VOICE: {voice}\nVOICE STYLE ELVÁRÁS: {expectation}\n\n"
            f"A POSZT SZÖVEGE (amihez a vizuál készült):\n{post_content[:1200]}\n\n"
            "Értékeld a fenti képet a brand kritériumok szerint. Csak a JSON-t add vissza."
        )
        user_blocks = [
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
            {"type": "text", "text": user_text},
        ]

        try:
            msg = await _client().messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_blocks}],
            )
            data = _repair_and_parse(msg.content[0].text if msg.content else "")
        except Exception as exc:
            logger.warning("[visual-eval] vision hívás hiba: %s", str(exc)[:120])
            return self._error_result(f"vision hívás hiba: {str(exc)[:100]}")

        if not data:
            return self._error_result("a modell nem adott értelmezhető JSON-t")
        return self._normalize(data)

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        scores = {k: _clamp(data.get(k), 1, 10, 5) for k in SCORE_KEYS}
        flags = [str(f) for f in (data.get("anti_pattern_flags") or []) if str(f).strip()]
        try:
            overall = float(data.get("overall_score"))
        except (TypeError, ValueError):
            overall = sum(scores.values()) / len(scores)
        overall = max(1.0, min(10.0, round(overall, 2)))
        return {
            **scores,
            "anti_pattern_flags": flags,
            "overall_score": overall,
            "feedback": str(data.get("feedback") or "").strip(),
        }

    @staticmethod
    def _error_result(reason: str) -> dict[str, Any]:
        return {
            "brand_alignment_score": 0, "hungarian_text_quality": 0,
            "scroll_stopping_score": 0, "professional_score": 0,
            "anti_pattern_flags": ["eval_error"], "overall_score": 0.0,
            "feedback": reason, "error": True,
        }


async def _demo(image_url: str, voice: str) -> int:
    import json

    ev = VisualEvaluator()
    out = await ev.evaluate_image(image_url, "Teszt poszt — automatizálás magyar KKV-knak.", voice)
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
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    vc = sys.argv[2] if len(sys.argv) > 2 else "david"
    if not url:
        print("Használat: python -m src.optimization.visual_eval <image_url> [voice]")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_demo(url, vc)))
