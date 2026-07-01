"""Phase 7.7 teszt — LinkedIn optimalizálás teljes pipeline 3 hangon.

Egy téma, 3 hang (Dávid / Ádám / PlanSmart):
  1. nyers poszt generálás (base_generator) + 3-2-1 hook variánsok,
  2. LinkedIn-optimalizálás (optimize_for_linkedin),
  3. before/after + hook variánsok + diagnosztika kiírása egymás mellett.

NEM ír DB-t, NEM küld Telegramra, NEM generál képet — csak szöveg + diagnosztika.

    python -m scripts.test_optimization
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from src.generators.base_generator import generate as generate_post
from src.optimization.linkedin_optimizer import optimize_for_linkedin

logger = logging.getLogger("test_optimization")
load_dotenv(override=False)

TOPIC = "Megtanultad a Claude-ot. Mi jön utána?"

VOICE_PROMPTS = {
    "david": "prompts/voice_david.md",
    "adam": "prompts/voice_adam.md",
    "plansmart": "prompts/voice_plansmart.md",
}

# Hangonkénti, az adott voice-ra szabott instrukció, hogy egyik se skip-eljen.
INSTRUCTIONS = {
    "david": (
        f"Téma: „{TOPIC}”\n"
        "Builder/educational poszt. Te már túl vagy a Claude megtanulásán — most a "
        "kérdés, hogy mit építesz vele a saját workflow-dba. Írd meg konkrétan, technikai "
        "mélységgel: mi a következő lépés, ha valaki már tudja használni a Claude-ot, de "
        "még nem épített rá rendszert. Saját tapasztalat, konkrét példák."
    ),
    "adam": (
        f"Téma: „{TOPIC}”\n"
        "Stratégiai/educational poszt cégtulajoknak. A csapatod megtanulta a Claude-ot — "
        "de a tudás önmagában nem ROI. Írd meg üzleti szemmel: mi jön a tanulás után, "
        "hogyan lesz a képességből tényleges üzleti eredmény egy 10-50 fős cégnél. "
        "Tulaj-tulajnak, konkrét döntési helyzetekkel."
    ),
    "plansmart": (
        f"Téma: „{TOPIC}”\n"
        "Céges (brand) educational poszt. Sok cég eljut oda, hogy a csapat megtanulja az "
        "AI-eszközöket — de utána megáll a folyamat. Írd meg „mi” formában, érték-vezérelten: "
        "mi a következő lépés a tanulás után, hogyan segít a PlanSmart a tudásból működő "
        "rendszert csinálni. Eredmény-orientált, NEM nyomulós."
    ),
}


def _instruction(voice: str) -> dict:
    return {
        "type": "manual_instruction",
        "instruction": INSTRUCTIONS[voice],
        "content_type": "educational",
        "voice": voice,
        "platform": "linkedin",
    }


def _raw_linkedin(result: dict) -> str:
    li = result.get("linkedin") or {}
    content = li.get("content", "")
    tags = li.get("hashtags") or []
    return f"{content}\n\n{' '.join(tags)}".strip() if tags else content


async def _run_voice(voice: str) -> dict:
    """Egy hang: nyers generálás + hook variánsok + optimalizálás."""
    result = await generate_post(_instruction(voice), VOICE_PROMPTS[voice], with_hook_variants=True)
    if not result:
        return {"voice": voice, "skipped": True}
    raw = _raw_linkedin(result)
    diag = await optimize_for_linkedin(raw, voice, "educational", {"topic": TOPIC})
    return {
        "voice": voice, "skipped": False, "raw": raw,
        "hook_variants": result.get("hook_variants"), "diag": diag,
    }


HR = "=" * 78
SUB = "-" * 78


def _print_voice(r: dict) -> None:
    voice = r["voice"].upper()
    print(f"\n{HR}\n  {voice}\n{HR}")
    if r["skipped"]:
        print("  (a hang skip-elte ezt a témát)")
        return

    print("\n[NYERS POSZT — BEFORE]")
    print(r["raw"])

    hv = r.get("hook_variants")
    print("\n[HOOK VARIÁNSOK — 3-2-1 framework]")
    if hv and hv.get("variants"):
        best_i = hv.get("best_index")
        for i, v in enumerate(hv["variants"]):
            mark = "  ★ LEGJOBB" if i == best_i else ""
            print(f"  {v.get('type', '?')}) [{v.get('score', '?')}/10] {v.get('text', '')}{mark}")
    else:
        print("  (nem készült hook variáns)")

    d = r["diag"]
    print("\n[OPTIMALIZÁLT POSZT — AFTER]")
    print(d["final_post"])

    print("\n[DIAGNOSZTIKA]")
    print(f"  hook_type ............ {d['hook_type'] or '—'}")
    print(f"  hook_score ........... {d['hook_score']}/10")
    print(f"  structure_score ...... {d['structure_score']}/10")
    print(f"  character_count ...... {d['character_count']} (sweet spot: 1300-1900)")
    print(f"  engagement_tier ...... {d['estimated_engagement_tier'].upper()}")
    if d["warnings"]:
        print("  warnings:")
        for w in d["warnings"]:
            print(f"    ⚠ {w}")
    else:
        print("  warnings ............. nincs ✓")


def _print_summary(results: list[dict]) -> None:
    print(f"\n{HR}\n  ÖSSZEGZÉS (3 hang egymás mellett)\n{HR}")
    header = f"  {'HANG':<11}{'HOOK':<6}{'HOOK/10':<9}{'SZERK/10':<10}{'KAR':<7}{'TIER':<8}WARN"
    print(header)
    print(SUB)
    for r in results:
        if r["skipped"]:
            print(f"  {r['voice'].upper():<11}— skip —")
            continue
        d = r["diag"]
        print(
            f"  {r['voice'].upper():<11}{(d['hook_type'] or '—'):<6}{str(d['hook_score'])+'/10':<9}"
            f"{str(d['structure_score'])+'/10':<10}{str(d['character_count']):<7}"
            f"{d['estimated_engagement_tier'].upper():<8}{len(d['warnings'])}"
        )


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    print(f"TÉMA: „{TOPIC}”")
    results = await asyncio.gather(*(_run_voice(v) for v in ("david", "adam", "plansmart")))
    for r in results:
        _print_voice(r)
    _print_summary(list(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
