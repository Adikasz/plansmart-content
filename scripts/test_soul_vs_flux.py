"""Phase 12.6 — base-model A/B: Flux-2-Pro vs Midjourney-v8 (SOUL helyett).

A SOUL a Higgsfield modellje, NEM elérhető Muapin (450 modell, egy sem soul/higgsfield).
A felhasználó a Midjourney-v8-at választotta a legközelebbi editorial/film-grain alternatívaként.

Minden hanghoz UGYANAZ a szöveg-mentes prompt → generálás MINDKÉT modellel → ugyanaz a
magyar PIL overlay → VisualEvaluator pontozás. Side-by-side + ajánlás.

    python -m scripts.test_soul_vs_flux
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.optimization.visual_eval import SCORE_KEYS, VisualEvaluator
from src.visuals import muapi_client
from src.visuals import visual_generator as vg
from src.visuals.text_overlay import GENERATED_DIR, TextOverlayComposer
from src.visuals.uploader import ensure_bucket, upload_visual

logger = logging.getLogger("test_soul_vs_flux")
load_dotenv(override=False)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MODELS = {"flux": "flux-2-pro", "mj": "midjourney-v8"}

# Ugyanazok a posztok, mint a Phase 12.5 E2E (konzisztens összevetés).
POSTS = {
    "david": "Tegnap átírtam a queue logikát: a poll loop 16 percről 3,5 másodpercre esett, "
             "0 új alkalmazottal. Event-driven architektúra, itt a diff lényege.",
    "adam": "73% a magyar KKV-knak heti 4+ órát tölt ismétlődő manuális munkával. A felét egy "
            "hétvége alatt automatizálni lehetne. Ez nem technológiai, hanem döntési kérdés.",
    "plansmart": "Egy 8 fős mozgásterápiás stúdiónak építettük meg az automatizált foglalási "
                 "rendszerét. A napi 2 óra adminisztráció 0 percre csökkent.",
}


async def _gen_and_score(voice, content, prompt, vt, model_key, model, composer, evaluator, do_upload):
    out = {"voice": voice, "model_key": model_key, "model": model}
    try:
        res = await muapi_client.generate(prompt, model=model, aspect_ratio="1:1")
        out["base_url"] = res.image_url
        out["cost"] = res.cost_usd or muapi_client.MODEL_PRICING_USD.get(model, 0.0)
    except Exception as exc:
        logger.warning("[%s/%s] generálás hiba: %s", voice, model_key, str(exc)[:120])
        out.update({"base_url": None, "cost": 0.0, "scores": None, "error": str(exc)[:160]})
        return out
    path = str(GENERATED_DIR / f"cmp_{model_key}_{voice}.png")
    composed = await asyncio.to_thread(
        composer.compose, res.image_url, vt["main_text"], vt.get("sub_text"), vt.get("stat"), voice, path
    )
    out["composed_local"] = composed
    out["composed_url"] = (await asyncio.to_thread(upload_visual, composed, f"cmp_{model_key}_{voice}.png")
                           if do_upload else composed)
    out["scores"] = await evaluator.evaluate_image(composed, content, voice)
    logger.info("[%s/%s] overall=%s flags=%s | $%.4f", voice, model_key,
                out["scores"].get("overall_score"), out["scores"].get("anti_pattern_flags"), out["cost"])
    return out


async def _voice(voice, content, composer, evaluator, do_upload):
    vt = await vg.extract_visual_text(content)
    prompt = vg.build_textfree_prompt({"voice": voice, "content": content, "hashtags": []}, vt)
    results = await asyncio.gather(*(
        _gen_and_score(voice, content, prompt, vt, mk, m, composer, evaluator, do_upload)
        for mk, m in MODELS.items()
    ))
    return {"voice": voice, "prompt": prompt, "visual_text": vt, "by_model": {r["model_key"]: r for r in results}}


def _avg(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 2) if v else None


async def amain(do_upload: bool) -> int:
    if do_upload:
        ensure_bucket()
    composer = TextOverlayComposer()
    evaluator = VisualEvaluator()
    voices = await asyncio.gather(*(_voice(v, c, composer, evaluator, do_upload) for v, c in POSTS.items()))

    agg = {mk: {"overall": [], "hu": [], "cost": 0.0} for mk in MODELS}
    for vd in voices:
        for mk, r in vd["by_model"].items():
            s = r.get("scores") or {}
            if s and not s.get("error"):
                agg[mk]["overall"].append(s.get("overall_score"))
                agg[mk]["hu"].append(s.get("hungarian_text_quality"))
            agg[mk]["cost"] += r.get("cost", 0.0)

    print("\n" + "=" * 84 + "\n  BASE-MODEL A/B — Flux-2-Pro vs Midjourney-v8 (SOUL helyett)\n" + "=" * 84)
    for vd in voices:
        v = vd["voice"]
        print(f"\n=== {v.upper()} ===  (overlay: '{vd['visual_text']['main_text']}')")
        for mk in MODELS:
            r = vd["by_model"][mk]
            s = r.get("scores") or {}
            print(f"  [{mk:4}] {MODELS[mk]:14} overall={s.get('overall_score','—'):<4} "
                  f"HU={s.get('hungarian_text_quality','—'):<3} ${r.get('cost',0):.3f}")
            print(f"         BASE     : {r.get('base_url')}")
            print(f"         COMPOSED : {r.get('composed_url')}")
            if s.get("anti_pattern_flags"):
                print(f"         flags    : {s['anti_pattern_flags']}")
            if s.get("feedback"):
                print(f"         feedback : {s['feedback'][:150]}")

    print("\n" + "-" * 84 + "\n  ÖSSZEGZÉS\n" + "-" * 84)
    summary = {}
    for mk in MODELS:
        ov, hu = _avg(agg[mk]["overall"]), _avg(agg[mk]["hu"])
        summary[mk] = {"overall": ov, "hu": hu, "cost": round(agg[mk]["cost"], 4)}
        print(f"  {MODELS[mk]:14}  overall={ov}  HU={hu}  össz-költség=${agg[mk]['cost']:.4f}")
    fo, mo = summary["flux"]["overall"] or 0, summary["mj"]["overall"] or 0
    delta = round(mo - fo, 2)
    print(f"\n  Phase 12.5 baseline (Flux+overlay): ~6.7-6.8 overall")
    print(f"  Flux most: {fo} | Midjourney: {mo} | Δ(MJ-Flux): {'+' if delta>=0 else ''}{delta}")
    verdict = ("MJ EGYÉRTELMŰ GYŐZTES (>+1.0) → váltás javasolt" if delta > 1.0
               else "VEGYES/NEM EGYÉRTELMŰ → marad Flux, MJ opcióként dokumentálva")
    print(f"  Ítélet: {verdict}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "soul_vs_flux_results.json").write_text(
        json.dumps({"summary": summary, "delta": delta, "voices": voices}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n  Mentve: {DATA_DIR / 'soul_vs_flux_results.json'}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()
    return asyncio.run(amain(do_upload=not args.no_upload))


if __name__ == "__main__":
    raise SystemExit(main())
