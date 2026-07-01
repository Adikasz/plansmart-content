"""Phase 12.6 — a két overlay bug javításának before/after tesztje.

BUG1: hosszú összetett szó (ADMINISZTRÁCIÓ) kilóg → shrink-to-fit + char-wrap.
BUG2: stat == main (vagy a main tartalmazza) → kétszer jelenik meg → stat elhagyva.

A BEFORE képek a SOUL/Flux összevetésből maradtak (régi, bugos kód):
  assets/generated/cmp_flux_{adam,plansmart}.png
Az AFTER ugyanazon a Flux alapképen + ugyanazzal a szöveggel készül (csak a kód változott),
így a különbség kizárólag a javítás. Mindkettőt pontozzuk (VisualEvaluator).

    python -m scripts.test_overlay_fixes
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.optimization.visual_eval import VisualEvaluator
from src.visuals.text_overlay import GENERATED_DIR, TextOverlayComposer

logger = logging.getLogger("test_overlay_fixes")
load_dotenv(override=False)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RESULTS = DATA_DIR / "soul_vs_flux_results.json"

# Eval-kontextus (a poszt szövege) hangonként.
CONTENT = {
    "adam": "73% a magyar KKV-knak heti 4+ órát tölt ismétlődő manuális munkával.",
    "plansmart": "Egy 8 fős mozgásterápiás stúdiónak építettük az automatizált foglalási rendszerét; "
                 "a napi 2 óra adminisztráció 0 percre csökkent.",
}
BUG = {"adam": "BUG2 (duplikált 73%)", "plansmart": "BUG1 (ADMINISZTRÁCIÓ kilóg)"}


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    cases = {vd["voice"]: vd for vd in data["voices"] if vd["voice"] in ("adam", "plansmart")}

    composer = TextOverlayComposer()
    evaluator = VisualEvaluator()

    print("=" * 80 + "\n  OVERLAY BUG-FIX before/after\n" + "=" * 80)
    for voice in ("plansmart", "adam"):
        vd = cases[voice]
        vt, base = vd["visual_text"], vd["by_model"]["flux"]["base_url"]
        before = GENERATED_DIR / f"cmp_flux_{voice}.png"          # régi, bugos
        after = GENERATED_DIR / f"fix_after_{voice}.png"          # új, javított
        # AFTER renderelése a JAVÍTOTT kóddal, ugyanazon a base-en + ugyanazzal a szöveggel
        await asyncio.to_thread(composer.compose, base, vt["main_text"], vt.get("sub_text"),
                                vt.get("stat"), voice, str(after))

        s_before = await evaluator.evaluate_image(str(before), CONTENT[voice], voice) if before.exists() else {}
        s_after = await evaluator.evaluate_image(str(after), CONTENT[voice], voice)

        print(f"\n=== {voice.upper()} — {BUG[voice]} ===")
        print(f"  overlay: main='{vt['main_text']}'  stat={vt.get('stat')!r}"
              f"  → stat a javítás után: {'ELHAGYVA (a main tartalmazza)' if _stat_dropped(vt) else vt.get('stat')!r}")
        print(f"  BEFORE  {before.name}: overall={s_before.get('overall_score','—')} | flags={s_before.get('anti_pattern_flags')}")
        print(f"  AFTER   {after.name}: overall={s_after.get('overall_score','—')} | flags={s_after.get('anti_pattern_flags')}")
        d = (s_after.get("overall_score") or 0) - (s_before.get("overall_score") or 0)
        print(f"  Δ overall: {'+' if d >= 0 else ''}{round(d, 2)}")
        print(f"  AFTER feedback: {s_after.get('feedback','')[:160]}")
    print(f"\nKépek: {GENERATED_DIR}\\fix_after_plansmart.png , fix_after_adam.png")
    return 0


def _stat_dropped(vt) -> bool:
    s = (vt.get("stat") or "").strip().lower()
    m = (vt.get("main_text") or "").strip().lower()
    return bool(s) and (s == m or s in m)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
