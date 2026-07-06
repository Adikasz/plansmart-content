"""LinkedIn optimalizációs réteg — 2026 algoritmus-szabályok alkalmazása.

Egy nyers, voice-specifikus posztot átstrukturál a `prompts/linkedin_optimization.md`
keretrendszer szerint (mobile-first hook, 1300-1900 kar sweet spot, bullet pontok,
anti-pattern eltávolítás), és diagnosztikát ad vissza (hook típus + pontszámok).

A modell KIZÁRÓLAG JSON-t ad vissza; a base_generator robusztus parse-ját használjuk.

Önálló teszt:
    python -m src.optimization.linkedin_optimizer
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
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK_FILE = PROJECT_ROOT / "prompts" / "linkedin_optimization.md"

MODEL = "claude-sonnet-4-6"  # ADR-008: minőségi modell (voice gen + optimalizálás)
MAX_TOKENS = 2500

# A sweet spot a kar-tartomány — ezen kívül warning kerül a diagnosztikába.
CHAR_MIN, CHAR_MAX = 1300, 1900
HOOK_MOBILE_LIMIT = 140
VALID_HOOKS = {"A", "B", "C", "D", "E"}

TASK = f"""\
Megkapsz egy nyers, magyar nyelvű LinkedIn posztot. A feladatod: optimalizáld a fenti
2026-os LinkedIn algoritmus-keretrendszer szerint. Tartsd meg a poszt HANGJÁT és üzenetét
— ne írd át a tartalmat, csak a STRUKTÚRÁT és a megfogalmazást optimalizáld.

Lépések:
1. Elemezd a nyers posztot.
2. Válaszd ki a tartalomhoz legjobban illő hook típust (A/B/C/D/E).
3. Írd át a hookot, hogy mobile-first legyen (max {HOOK_MOBILE_LIMIT} karakter az első sor,
   kérdőjel nélkül a végén).
4. Strukturáld újra a törzset a {CHAR_MIN}-{CHAR_MAX} karakteres sweet spotra.
5. Tegyél bullet pontokat oda, ahol több konkrét pont van (scannability).
6. Távolíts el minden anti-patternt (engagement bait, buzzword, külső link, emoji-fal,
   "DM-ezz"/"foglalj időpontot" típusú nyomulás, fal-szerű bekezdés nélküli szöveg).
7. Adj megfelelő sortöréseket (üres sorok a blokkok közt).
8. A poszt VÉGÉRE 3-5 releváns hashtag (magyar + angol mix OK).
9. Pontozd az eredményt.

KIZÁRÓLAG ezt a JSON-t add vissza, semmi mást:
{{
  "final_post": "a teljes optimalizált poszt szövege a hashtagekkel együtt",
  "hook_type": "A|B|C|D|E",
  "hook_score": 1-10 egész szám (mennyire scroll-stopping a hook),
  "structure_score": 1-10 egész szám (formula-követés, scannability, hossz),
  "warnings": ["maradék vagy nem javítható problémák listája, üres ha nincs"],
  "estimated_engagement_tier": "high|medium|low"
}}
"""


@lru_cache(maxsize=1)
def _framework() -> str:
    return FRAMEWORK_FILE.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()  # ANTHROPIC_API_KEY a környezetből


def _local_warnings(final_post: str) -> list[str]:
    """Determinisztikus, kódból ellenőrizhető figyelmeztetések (a modelltől függetlenül)."""
    warns: list[str] = []
    n = len(final_post)
    if n < CHAR_MIN:
        warns.append(f"Rövid ({n} kar) — a {CHAR_MIN}-{CHAR_MAX} sweet spot alatt.")
    elif n > CHAR_MAX:
        warns.append(f"Hosszú ({n} kar) — a {CHAR_MIN}-{CHAR_MAX} sweet spot felett.")
    first_line = final_post.strip().splitlines()[0] if final_post.strip() else ""
    if len(first_line) > HOOK_MOBILE_LIMIT:
        warns.append(f"A hook {len(first_line)} kar — mobilon ~{HOOK_MOBILE_LIMIT} fölött levágódik.")
    if first_line.rstrip().endswith("?"):
        warns.append("A hook kérdőjellel zár — bizonyítottan alacsonyabb engagement.")
    if "http://" in final_post or "https://" in final_post:
        warns.append("Külső link a posztban — -60% reach. Távolítsd el.")
    hashtag_count = final_post.count("#")
    if hashtag_count > 5:
        warns.append(f"{hashtag_count} hashtag — 5 fölött spam jelzés.")
    return warns


def _tier(hook_score: int, structure_score: int, warnings: list[str]) -> str:
    """Engagement tier a pontszámokból + warningok számából (fallback, ha a modell nem adja)."""
    avg = (hook_score + structure_score) / 2
    if avg >= 8 and len(warnings) == 0:
        return "high"
    if avg >= 6 and len(warnings) <= 1:
        return "medium"
    return "low"


async def optimize_for_linkedin(
    raw_post: str,
    voice: str,
    content_type: str,
    topic_context: dict | None = None,
    hook_bias: list[str] | None = None,
) -> dict[str, Any]:
    """Egy nyers posztot LinkedIn-optimalizál (2026 keretrendszer) + diagnosztika.

    Visszaad:
        {
          "final_post": str,
          "hook_type": str (A/B/C/D/E),
          "hook_score": int (1-10),
          "structure_score": int (1-10),
          "warnings": list[str],
          "character_count": int,
          "estimated_engagement_tier": "high" | "medium" | "low",
        }

    Hiba/parse-hiba esetén: a nyers poszttal tér vissza (graceful degradation),
    warning-gal jelezve, hogy az optimalizálás kimaradt.
    """
    topic_context = topic_context or {}
    system = _framework()
    bias_line = ""
    if hook_bias:
        bias_line = (
            f"\nHOOK BIAS: lehetőleg ezekből a hook típusokból válassz ({', '.join(hook_bias)}) — "
            "ez a tartalom (pl. breaking news) ezekkel teljesít a legjobban. Csak akkor térj el, "
            "ha egy másik típus egyértelműen erősebb.\n"
        )
    user = (
        f"{TASK}\n\n"
        f"VOICE: {voice}\n"
        f"CONTENT TYPE: {content_type}\n"
        f"TOPIC CONTEXT: {json.dumps(topic_context, ensure_ascii=False)}{bias_line}\n\n"
        f"NYERS POSZT:\n{raw_post}"
    )

    try:
        msg = await _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw_text = msg.content[0].text if msg.content else ""
        data = _repair_and_parse(raw_text)
    except Exception as exc:  # API/hálózati hiba
        logger.warning("[optimizer] hiba (%s) — nyers poszt megy tovább: %s", voice, str(exc)[:120])
        data = None

    if not data or not (data.get("final_post") or "").strip():
        logger.warning("[optimizer] nincs használható kimenet (%s) — fallback a nyers posztra.", voice)
        return {
            "final_post": raw_post,
            "hook_type": "",
            "hook_score": 0,
            "structure_score": 0,
            "warnings": ["Optimalizálás kimaradt (parse/API hiba) — nyers poszt."],
            "character_count": len(raw_post),
            "estimated_engagement_tier": "low",
        }

    final_post = str(data["final_post"]).strip()
    hook_type = str(data.get("hook_type", "")).strip().upper()[:1]
    if hook_type not in VALID_HOOKS:
        hook_type = ""
    hook_score = _clamp_int(data.get("hook_score"), 1, 10)
    structure_score = _clamp_int(data.get("structure_score"), 1, 10)

    warnings = list(data.get("warnings") or [])
    warnings += [w for w in _local_warnings(final_post) if w not in warnings]

    tier = str(data.get("estimated_engagement_tier", "")).lower()
    if tier not in {"high", "medium", "low"}:
        tier = _tier(hook_score, structure_score, warnings)

    return {
        "final_post": final_post,
        "hook_type": hook_type,
        "hook_score": hook_score,
        "structure_score": structure_score,
        "warnings": warnings,
        "character_count": len(final_post),
        "estimated_engagement_tier": tier,
    }


def _clamp_int(value: Any, lo: int, hi: int, default: int = 0) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


async def _demo() -> int:
    raw = (
        "Megtanultad a Claude-ot. Mi jön utána? Sokan most ismerkednek az AI-jal, "
        "de a kérdés az, hogy utána mit kezdesz vele a cégedben. Az AI önmagában nem "
        "csinál semmit, neked kell beépítened a folyamataidba. Egyetértesz? Komment! "
        "Foglalj időpontot: https://plansmart.live"
    )
    out = await optimize_for_linkedin(raw, "david", "educational", {"topic": "AI adoption"})
    logging.getLogger(__name__).info("%s", json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import asyncio

    setup_logging()
    raise SystemExit(asyncio.run(_demo()))
