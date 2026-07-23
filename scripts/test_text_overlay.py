"""Phase 12.5 PART 1D — szöveg-overlay teszt 3 hangon.

Minden hanghoz: szöveg-mentes Muapi alapkép → magyar PIL overlay → mentés (+ feltöltés).
Megmutatja az alapkép URL-t és a komponált végső utat/URL-t.

    python -m scripts.test_text_overlay              # generál + overlay + feltöltés
    python -m scripts.test_text_overlay --no-upload  # csak lokális mentés
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.integrations.visuals import muapi_client
from src.integrations.visuals import visual_generator as vg
from src.integrations.visuals.text_overlay import GENERATED_DIR, TextOverlayComposer
from src.integrations.visuals.uploader import ensure_bucket, upload_visual

logger = logging.getLogger("test_text_overlay")
load_dotenv(override=False)

SAMPLES = {
    "david": {"main_text": "MEGTANULTAD A CLAUDE-OT", "sub_text": "Mi jön utána?", "stat": None},
    "adam": {"main_text": "73%", "sub_text": "heti 4+ óra manuális munka", "stat": "73%"},
    "plansmart": {"main_text": "16 PERC → 3,5 MP", "sub_text": "Ezt csináljuk KKV-knak", "stat": None},
}


async def _one(voice: str, composer: TextOverlayComposer, do_upload: bool) -> dict:
    vt = SAMPLES[voice]
    post = {"voice": voice, "content": "", "hashtags": []}
    prompt = vg.build_textfree_prompt(post, vt)
    res = await muapi_client.generate(prompt, model=muapi_client.DEFAULT_MODEL, aspect_ratio="1:1")
    out = str(GENERATED_DIR / f"test_overlay_{voice}.png")
    composed = await asyncio.to_thread(
        composer.compose, res.image_url, vt["main_text"], vt["sub_text"], vt["stat"], voice, out
    )
    url = await asyncio.to_thread(upload_visual, composed, f"test_overlay_{voice}.png") if do_upload else composed
    return {"voice": voice, "base_url": res.image_url, "composed": composed, "final": url, "cost": res.cost_usd or 0.0}


async def amain(do_upload: bool) -> int:
    if do_upload:
        ensure_bucket()
    composer = TextOverlayComposer()  # fontokat előkészíti (woff2→ttf, Bebas letöltés)
    results = await asyncio.gather(*(_one(v, composer, do_upload) for v in SAMPLES))

    print("\n" + "=" * 74 + "\n  SZÖVEG-OVERLAY TESZT — 3 hang\n" + "=" * 74)
    total = 0.0
    for r in results:
        total += r["cost"]
        s = SAMPLES[r["voice"]]
        print(f"\n=== {r['voice'].upper()} ===")
        print(f"  overlay: main='{s['main_text']}' sub='{s['sub_text']}' stat={s['stat']!r}")
        print(f"  BASE  (no-text Muapi): {r['base_url']}  (${r['cost']:.4f})")
        print(f"  FINAL (composed)     : {r['final']}")
        print(f"  local                : {r['composed']}")
    print(f"\nÖsszes Muapi költség: ${total:.4f}")
    print(f"Megnyitás: {GENERATED_DIR}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true", help="csak lokális mentés")
    args = ap.parse_args()
    return asyncio.run(amain(do_upload=not args.no_upload))


if __name__ == "__main__":
    raise SystemExit(main())
