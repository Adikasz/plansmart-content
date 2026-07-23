"""Phase 12.6 — új (fotografikus) text-free prompt A/B a 12.5 baseline ellen.

A 3 hangra friss alapképet generál az ÚJ build_textfree_prompt-tal, ráteszi a magyar
overlay-t, és pontozza (VisualEvaluator). A régi (12.5) baseline a soul_vs_flux flux
eredményeiből jön (régi base URL-ek + cmp_flux_*.png composed + scorek).

    python -m scripts.test_phase126_prompt              # generál + overlay + upload + eval
    python -m scripts.test_phase126_prompt --no-upload
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.ai.optimization.visual_eval import VisualEvaluator
from src.integrations.visuals import muapi_client
from src.integrations.visuals import visual_generator as vg
from src.integrations.visuals.text_overlay import GENERATED_DIR, TextOverlayComposer
from src.integrations.visuals.uploader import ensure_bucket, upload_visual

logger = logging.getLogger("test_phase126_prompt")
load_dotenv(override=False)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OLD = DATA_DIR / "soul_vs_flux_results.json"   # 12.5 baseline (flux ág)

POSTS = {
    "david": "Tegnap átírtam a queue logikát: a poll loop 16 percről 3,5 másodpercre esett, "
             "0 új alkalmazottal. Event-driven architektúra, itt a diff lényege.",
    "adam": "73% a magyar KKV-knak heti 4+ órát tölt ismétlődő manuális munkával. A felét egy "
            "hétvége alatt automatizálni lehetne. Ez nem technológiai, hanem döntési kérdés.",
    "plansmart": "Egy 8 fős mozgásterápiás stúdiónak építettük meg az automatizált foglalási "
                 "rendszerét. A napi 2 óra adminisztráció 0 percre csökkent.",
}
BG_KEYS = ("generic", "ai-art", "ai art", "grain", "cinematic", "stock", "clutter",
           "competing", "grey", "gray", "mid-tone", "pseudo", "hud", "screen", "panel",
           "code", "holograph", "illustrat", "concept art", "3d", "render")


def _bg_flags(scores) -> list[str]:
    return [f for f in (scores.get("anti_pattern_flags") or [])
            if any(k in f.lower() for k in BG_KEYS)]


async def _one(voice, content, composer, evaluator, do_upload):
    vt = await vg.extract_visual_text(content)
    prompt = vg.build_textfree_prompt({"voice": voice, "content": content, "hashtags": []}, vt)
    res = await muapi_client.generate(prompt, model=muapi_client.DEFAULT_MODEL, aspect_ratio="1:1")
    path = str(GENERATED_DIR / f"p126_{voice}.png")
    composed = await asyncio.to_thread(
        composer.compose, res.image_url, vt["main_text"], vt.get("sub_text"), vt.get("stat"), voice, path)
    final = (await asyncio.to_thread(upload_visual, composed, f"p126_{voice}.png")) if do_upload else composed
    scores = await evaluator.evaluate_image(composed, content, voice)
    logger.info("[%s] NEW overall=%s | bg-flags=%s | $%.4f", voice, scores.get("overall_score"),
                _bg_flags(scores), res.cost_usd or 0.0)
    return {"voice": voice, "visual_text": vt, "prompt": prompt, "base_url": res.image_url,
            "composed": composed, "final": final, "scores": scores, "cost": res.cost_usd or 0.0}


def _old_baseline():
    """A 12.5 flux baseline: voice → {base_url, composed, overall, bg_flags}."""
    out = {}
    if not OLD.exists():
        return out
    d = json.loads(OLD.read_text(encoding="utf-8"))
    for vd in d.get("voices", []):
        fl = (vd.get("by_model") or {}).get("flux") or {}
        s = fl.get("scores") or {}
        out[vd["voice"]] = {"base_url": fl.get("base_url"),
                            "composed": str(GENERATED_DIR / f"cmp_flux_{vd['voice']}.png"),
                            "overall": s.get("overall_score"), "bg_flags": _bg_flags(s)}
    return out


async def amain(do_upload: bool) -> int:
    if do_upload:
        ensure_bucket()
    composer = TextOverlayComposer()
    evaluator = VisualEvaluator()
    results = await asyncio.gather(*(_one(v, c, composer, evaluator, do_upload) for v, c in POSTS.items()))
    old = _old_baseline()

    new_overalls = [r["scores"].get("overall_score") for r in results if r["scores"].get("overall_score")]
    new_avg = round(sum(new_overalls) / len(new_overalls), 2) if new_overalls else None
    old_overalls = [v["overall"] for v in old.values() if v.get("overall")]
    old_avg = round(sum(old_overalls) / len(old_overalls), 2) if old_overalls else None
    total_cost = round(sum(r["cost"] for r in results), 4)

    print("\n" + "=" * 86 + "\n  PHASE 12.6 — ÚJ fotografikus prompt vs 12.5 baseline\n" + "=" * 86)
    for r in results:
        v = r["voice"]; o = old.get(v, {})
        s = r["scores"]
        print(f"\n=== {v.upper()} ===")
        print(f"  OLD base (12.5): {o.get('base_url')}")
        print(f"  NEW base (12.6): {r['base_url']}")
        print(f"  OLD composed   : {o.get('composed')}")
        print(f"  NEW composed   : {r['final']}")
        print(f"  overall: {o.get('overall','—')} → {s.get('overall_score')}  "
              f"(Δ {round((s.get('overall_score') or 0)-(o.get('overall') or 0),2)})")
        print(f"  OLD bg-flags: {o.get('bg_flags')}")
        print(f"  NEW bg-flags: {_bg_flags(s)}")
        print(f"  NEW feedback: {s.get('feedback','')[:170]}")

    print("\n" + "-" * 86)
    print(f"  Baseline (12.5) átlag overall: {old_avg}  (referencia: 6.7-6.8)")
    print(f"  ÚJ (12.6) átlag overall:       {new_avg}")
    print(f"  Δ: {round((new_avg or 0)-(old_avg or 0),2)} | Muapi költség: ${total_cost:.4f}")
    gate = "✅ ELÉRI a 7.5 küszöböt → produkciós prompt frissítés" if (new_avg or 0) >= 7.5 \
        else "❌ 7.5 alatt → produkciós prompt MARAD, további irány kell"
    print(f"  Döntési kapu: {gate}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "phase126_prompt_results.json").write_text(
        json.dumps({"new_avg": new_avg, "old_avg": old_avg, "total_cost": total_cost,
                    "results": [{k: r[k] for k in ("voice", "base_url", "final", "scores", "prompt")} for r in results],
                    "old": old}, ensure_ascii=False, indent=2), encoding="utf-8")
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
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()
    return asyncio.run(amain(do_upload=not args.no_upload))


if __name__ == "__main__":
    raise SystemExit(main())
