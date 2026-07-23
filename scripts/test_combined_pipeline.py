"""Phase 13.5 — kombinált end-to-end teszt: David poszt PORTRÉVAL, teljes lánccal.

Lánc: nyers generálás → magyar-natív átírás-ellenőrzés → (opt.) optimalizálás →
vizuál-szöveg kinyerés → szöveg-mentes Muapi alapkép → portré-kompozit → logó-watermark →
magyar szöveg-overlay → végleges kép + végleges szöveg.

Kimenet:
  • a végleges magyar poszt szövege (természetes, jargon nélkül),
  • a végleges komponált kép útja (logó + portré + magyar szöveg),
  • Hunglish natívság pontszám előtte/utána.

Futtatás:
    python scripts/test_combined_pipeline.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.generators.base_generator import generate as generate_post  # noqa: E402
from src.ai.optimization.text_evaluator import TextEvaluator, _hunglish_flags  # noqa: E402
from src.ai.optimization.text_improver import improve_post  # noqa: E402
from src.integrations.visuals import visual_generator  # noqa: E402

logger = logging.getLogger(__name__)

VOICE = "david"
CONTENT_TYPE = "educational"
INSTRUCTION = (
    "Content type: oktató (educational) poszt.\n"
    "Téma: hogyan érdemes egy KKV-nak az első AI-automatizálását bevezetni, kis lépésekben.\n"
    "Írj egy tanító, lépésről lépésre posztot a saját builder hangodon, konkrét példával."
)


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    manual = {"type": "manual_instruction", "instruction": INSTRUCTION,
              "content_type": CONTENT_TYPE, "voice": VOICE, "platform": "linkedin"}

    # 1) Nyers generálás (auto_improve KI — a natív átírást külön, láthatóan futtatjuk).
    print("1) Nyers generálás (David)…")
    raw = await generate_post(manual, "prompts/voice_david.md", auto_improve=False)
    if not raw:
        print("❌ A modell skip-elt / nem adott posztot.")
        return 1
    raw_text = (raw.get("linkedin") or {}).get("content", "").strip()
    before_hunglish = _hunglish_flags(raw_text)

    # 2-3) Magyar-natív ellenőrzés + átírás (a hungarian_native_guide-ot is megkapja).
    # Az improve_post első iterációjának pontszáma AZ „előtte" — így nem kell külön eval-hívás.
    print("2) Magyar-natív ellenőrzés + átírás + optimalizálás…")
    evaluator = TextEvaluator()
    improved = await improve_post(raw_text, VOICE, CONTENT_TYPE, target_score=8.5, max_iterations=2,
                                  evaluator=evaluator)
    before = improved["iterations"][0]["scores"]
    final_text = improved["final_post"]
    after = improved["final_scores"]
    after_hunglish = _hunglish_flags(final_text)
    print(f"   nativeness: {before.get('hungarian_nativeness')}/10 → {after.get('hungarian_nativeness')}/10  "
          f"(overall {before.get('overall_score')} → {improved['final_score']}, "
          f"jargon {len(before_hunglish)} → {len(after_hunglish)})")

    # 4-8) Vizuál: text-free Muapi base → portré-kompozit → logó → magyar overlay.
    print("3) Vizuál generálás portréval (Muapi base → portré → logó → overlay)…")
    post = {"id": None, "voice": VOICE, "platform": "linkedin",
            "content": final_text, "hashtags": raw.get("linkedin", {}).get("hashtags", []),
            "portrait": True}  # kényszerített portré
    try:
        vis = await visual_generator.compose_visual(post)
    except Exception as exc:
        print(f"❌ Vizuál hiba: {exc}")
        return 1

    print("\n" + "=" * 70)
    print("VÉGLEGES MAGYAR POSZT")
    print("=" * 70)
    print(final_text)
    print("\n" + "=" * 70)
    print("EREDMÉNY")
    print("=" * 70)
    print(f"Hunglish natívság: {before.get('hungarian_nativeness')}/10 → {after.get('hungarian_nativeness')}/10")
    print(f"Overall: {before.get('overall_score')} → {improved['final_score']}")
    print(f"Portré használva: {vis.get('portrait_used')}")
    print(f"Vizuál-szöveg overlay: main='{vis['visual_text'].get('main_text')}' / "
          f"sub='{vis['visual_text'].get('sub_text')}'")
    print(f"Muapi alapkép: {vis.get('base_image_url', '')[:80]}")
    print(f"Végleges komponált kép (lokális): {vis.get('local_path')}")
    print(f"Publikus URL (ha feltöltve): {vis.get('image_url')}")
    print(f"Muapi költség: ${vis.get('cost_usd')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
