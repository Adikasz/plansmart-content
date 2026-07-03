"""Phase 14 eval loop — ENGLISH posts, ship-gate 9.0.

Per scenario:
  1) 5-variant A/B hook test (A=production, B=contrarian, C=data, D=narrative, E=pain),
     scored by the English TextEvaluator → pick the winner (highest overall).
  2) improve_post loop on the winner (target_score=9.0, max_iterations=5) → push toward 9.0.

Then: aggregate average final score per voice. If a voice is < 9.0 after the standard loop,
run ONE additional targeted round on that voice's posts (diminishing-returns rule), then stop.

Outputs:
  • data/text_eval_report_phase14_en.html
  • data/text_eval_phase14_results.json  (raw records, for the prompt-baking step)

Run:
    python scripts/run_phase14_eval.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# A/B variánsokat NEM akarjuk generálás közben auto-improve-olni (dupla költség + torzítja az
# összehasonlítást) — az improve_post-ot mi hívjuk explicit a győztesre. Ezt az import ELŐTT kell
# beállítani, mert a base_generator import-időben olvassa az AUTO_IMPROVE-ot.
os.environ["TEXT_AUTO_IMPROVE"] = "false"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.optimization.text_ab_test import generate_variants  # noqa: E402
from src.optimization.text_evaluator import TextEvaluator  # noqa: E402
from src.optimization.text_improver import improve_post  # noqa: E402

logger = logging.getLogger(__name__)
DATASET = PROJECT_ROOT / "data" / "text_eval_dataset.json"
RESULTS_JSON = PROJECT_ROOT / "data" / "text_eval_phase14_results.json"
REPORT_HTML = PROJECT_ROOT / "data" / "text_eval_report_phase14_en.html"

TARGET = 9.0
MAX_ITER = 5
CONCURRENCY = 2  # egyszerre ennyi scenario (rate-limit védelem; A/B 5 variánsa amúgy is konkurens)


async def _process(scenario: dict, sem: asyncio.Semaphore, evaluator: TextEvaluator) -> dict:
    voice, ctype = scenario["voice"], scenario["content_type"]
    label = scenario.get("label", ctype)
    async with sem:
        logger.info("[%s/%s] A/B hook test…", voice, scenario["id"])
        variants = await generate_variants(scenario, voice, ctype, evaluator=evaluator)
        scored = [v for v in variants if v.get("scores") and not v["scores"].get("error")]
        if not scored:
            logger.warning("[%s/%s] minden variáns skip/hiba", voice, scenario["id"])
            return {"id": scenario["id"], "voice": voice, "label": label, "content_type": ctype,
                    "error": "all variants failed", "winning_hook": None,
                    "baseline_score": 0.0, "final_score": 0.0, "final_post": "", "char_count": 0}
        winner = scored[0]
        baseline = winner["scores"].get("overall_score", 0.0)
        logger.info("[%s/%s] winner=%s (%.1f) → improve…", voice, scenario["id"],
                    winner["strategy"], baseline)
        improved = await improve_post(winner["post"], voice, ctype, target_score=TARGET,
                                      max_iterations=MAX_ITER, evaluator=evaluator)
        fs = improved["final_scores"]
        return {
            "id": scenario["id"], "voice": voice, "label": label, "content_type": ctype,
            "winning_hook": winner["strategy"],
            "hook_ranking": [(v["strategy"], (v.get("scores") or {}).get("overall_score")) for v in variants],
            "baseline_score": baseline,
            "final_score": improved["final_score"],
            "final_post": improved["final_post"],
            "final_scores": fs,
            "char_count": len(improved["final_post"]),
            "iterations": [round(i["scores"].get("overall_score", 0), 1) for i in improved["iterations"]],
        }


async def _targeted_round(rec: dict, evaluator: TextEvaluator) -> dict:
    """Egy extra célzott kör egy alulteljesítő poszton (a leggyengébb dimenzióra fókuszálva)."""
    fs = rec.get("final_scores") or {}
    dims = {k: v for k, v in fs.items() if isinstance(v, (int, float)) and k != "overall_score"}
    weakest = min(dims, key=dims.get) if dims else "hook_strength"
    logger.info("[%s/%s] targeted round (weakest=%s) …", rec["voice"], rec["id"], weakest)
    seed = (rec["final_post"] + f"\n\n[FOCUS: the weakest dimension is '{weakest}'. Fix that above all "
            "while keeping everything else at 9+.]")
    improved = await improve_post(seed, rec["voice"], rec["content_type"], target_score=TARGET,
                                  max_iterations=2, evaluator=evaluator)
    if improved["final_score"] >= rec["final_score"]:
        rec = {**rec, "final_score": improved["final_score"], "final_post": improved["final_post"],
               "final_scores": improved["final_scores"], "char_count": len(improved["final_post"]),
               "targeted": True, "targeted_weakness": weakest}
    else:
        rec = {**rec, "targeted": True, "targeted_weakness": weakest, "targeted_no_gain": True}
    return rec


def _avg_per_voice(records: list[dict]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for r in records:
        by.setdefault(r["voice"], []).append(r.get("final_score", 0.0))
    return {v: round(sum(s) / len(s), 2) for v, s in by.items() if s}


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    scenarios = json.loads(DATASET.read_text(encoding="utf-8"))
    evaluator = TextEvaluator()
    sem = asyncio.Semaphore(CONCURRENCY)

    print(f"Phase 14 eval — {len(scenarios)} scenarios, target {TARGET}, max_iter {MAX_ITER}\n")
    records = await asyncio.gather(*(_process(s, sem, evaluator) for s in scenarios))
    records = list(records)

    avg = _avg_per_voice(records)
    print("\n=== Average final score per voice (standard loop) ===")
    for v, a in avg.items():
        print(f"  {v:10} {a}")

    # Célzott extra kör az alulteljesítő voice-okra (diminishing-returns: pontosan 1 kör).
    weak_voices = [v for v, a in avg.items() if a < TARGET]
    if weak_voices:
        print(f"\nVoices below {TARGET}: {weak_voices} → one targeted round each…")
        idx = {i: r for i, r in enumerate(records)}
        tasks = {i: _targeted_round(r, evaluator) for i, r in idx.items() if r["voice"] in weak_voices}
        done = await asyncio.gather(*tasks.values())
        for i, newr in zip(tasks.keys(), done):
            records[i] = newr
        avg = _avg_per_voice(records)
        print("\n=== Average final score per voice (after targeted round) ===")
        for v, a in avg.items():
            print(f"  {v:10} {a}  {'✅' if a >= TARGET else '⚠️ below target (stopped, diminishing returns)'}")

    RESULTS_JSON.write_text(json.dumps({"avg_per_voice": avg, "records": records},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    _write_html(records, avg)
    print(f"\nResults JSON: {RESULTS_JSON}")
    print(f"HTML report:  {REPORT_HTML}")

    # Rövid összegzés a konzolra (voice, hook, score, char).
    print("\n=== Per-scenario summary ===")
    for r in records:
        print(f"  {r['voice']:10} {r['label']:18} hook={str(r.get('winning_hook')):10} "
              f"base={r.get('baseline_score')}→final={r.get('final_score')}  {r.get('char_count')} chars")
    return 0


def _esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def _write_html(records: list[dict], avg: dict[str, float]) -> None:
    voice_cards = "".join(
        f"<div class='v'><h2>{v}</h2><div class='big {'ok' if a >= TARGET else 'warn'}'>{a}</div>"
        f"<div class='sub'>avg final / 10</div></div>" for v, a in avg.items()
    )
    rows = ""
    for r in sorted(records, key=lambda x: (x["voice"], x["label"])):
        fs = r.get("final_scores") or {}
        dims = " · ".join(f"{k.replace('_',' ')}: {fs.get(k)}" for k in
                          ("hook_strength", "english_native_quality", "concrete_value", "engagement_potential")
                          if k in fs)
        rows += (
            f"<div class='card'><div class='hd'><b>{r['voice']}</b> · {r['label']} · "
            f"hook: <b>{r.get('winning_hook')}</b> · base {r.get('baseline_score')} → "
            f"<b class='{'ok' if r.get('final_score',0)>=TARGET else 'warn'}'>final {r.get('final_score')}</b> · "
            f"{r.get('char_count')} chars · iters {r.get('iterations')}"
            f"{' · <i>targeted</i>' if r.get('targeted') else ''}</div>"
            f"<div class='dims'>{dims}</div><div class='post'>{_esc(r.get('final_post',''))}</div></div>")
    html = (
        "<!doctype html><meta charset='utf-8'><title>Phase 14 EN eval</title>"
        "<style>body{background:#04060a;color:#cdd5de;font-family:Inter,system-ui,sans-serif;padding:32px;max-width:1000px;margin:auto}"
        "h1{font-weight:800}.voices{display:flex;gap:18px;margin:20px 0}"
        ".v{background:#0b0f16;border:1px solid #1b2430;border-radius:12px;padding:16px 26px;text-align:center}"
        ".big{font-size:40px;font-weight:800}.big.ok{color:#2dd4bf}.big.warn{color:#f5b342}.sub{font-size:12px;color:#7c8896}"
        ".card{background:#0b0f16;border:1px solid #1b2430;border-radius:12px;padding:16px;margin-bottom:14px}"
        ".hd{font-size:14px;margin-bottom:6px}.dims{font-size:12px;color:#8aa;margin-bottom:10px}"
        ".post{white-space:pre-wrap;font-size:14px;line-height:1.55;background:#070a0f;padding:14px;border-radius:8px}"
        "b.ok{color:#2dd4bf}b.warn{color:#f5b342}</style>"
        "<h1>Phase 14 — English rewrite, ship-gate 9.0</h1>"
        f"<div class='voices'>{voice_cards}</div>{rows}"
    )
    REPORT_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
