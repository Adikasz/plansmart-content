"""Szöveg-eval futtató — baseline + iteratív javítás + A/B variánsok, riport + HTML.

Szcenáriónként: 5 hook-variáns (A-E) generálás + pontozás, majd a baseline (A) iteratív
javítása (improve_post). Győztes = a legmagasabb overall a variánsok + iterációk közül.

    python -m scripts.run_text_eval                # teljes (dataset szerint)
    python -m scripts.run_text_eval --limit 1      # olcsó füstteszt
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from src.optimization.text_ab_test import HOOK_LABEL, generate_variants
from src.optimization.text_evaluator import SCORE_KEYS, TextEvaluator
from src.optimization.text_improver import improve_post

logger = logging.getLogger("run_text_eval")
load_dotenv(override=False)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATASET = DATA_DIR / "text_eval_dataset.json"
REPORT = DATA_DIR / "text_eval_report.html"
LATEST = DATA_DIR / "text_eval_results_latest.json"


def _avg(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 2) if v else None


async def _scenario(entry, evaluator, sem, target, max_iter):
    async with sem:
        voice, ctype = entry["voice"], entry["content_type"]
        variants = await generate_variants(entry, voice, ctype, variant_count=5, evaluator=evaluator)
        baseline = next((v for v in variants if v["variant"] == "A"), variants[0] if variants else None)
        base_post = (baseline or {}).get("post", "")
        base_score = ((baseline or {}).get("scores") or {}).get("overall_score", 0.0)

        improved = await improve_post(base_post, voice, ctype, target_score=target,
                                      max_iterations=max_iter, evaluator=evaluator) if base_post else None

        # Győztes a variánsok + javító-iterációk közül.
        pool = [{"source": f"variant-{v['variant']}", "post": v.get("post", ""),
                 "score": (v.get("scores") or {}).get("overall_score", 0.0),
                 "variant": v["variant"]} for v in variants if v.get("scores")]
        if improved:
            for it in improved["iterations"]:
                pool.append({"source": f"improve-it{it['iter']}", "post": it["post"],
                             "score": it["scores"].get("overall_score", 0.0), "variant": "improve"})
        winner = max(pool, key=lambda x: x["score"], default=None)
        logger.info("[text] %s [%s/%s] baseline=%.1f → winner=%.1f (%s)",
                    entry["id"], voice, ctype, base_score, winner["score"] if winner else 0,
                    winner["source"] if winner else "—")
        return {"id": entry["id"], "voice": voice, "content_type": ctype, "label": entry.get("label"),
                "variants": variants, "baseline_score": base_score, "improved": improved,
                "winner": winner}


def _aggregate(scenarios):
    base = [s["baseline_score"] for s in scenarios if s["baseline_score"]]
    improved = [s["improved"]["final_score"] for s in scenarios if s.get("improved")]
    winners = [s["winner"]["score"] for s in scenarios if s.get("winner")]
    # hook-győztes hangonként (mely VARIÁNS nyert a variánsok közül)
    hook_by_voice = defaultdict(Counter)
    for s in scenarios:
        ranked = [v for v in s["variants"] if v.get("scores")]
        if ranked:
            top = max(ranked, key=lambda v: v["scores"]["overall_score"])
            hook_by_voice[s["voice"]][HOOK_LABEL.get(top["variant"], top["variant"])] += 1
    # leggyakoribb anti-patternek
    flags = Counter()
    for s in scenarios:
        for v in s["variants"]:
            for f in ((v.get("scores") or {}).get("anti_patterns") or []):
                flags[f] += 1
    # top győztes posztok
    top_posts = sorted(
        [{"id": s["id"], "voice": s["voice"], "score": s["winner"]["score"],
          "source": s["winner"]["source"], "post": s["winner"]["post"]}
         for s in scenarios if s.get("winner")],
        key=lambda x: x["score"], reverse=True)[:5]
    return {
        "avg_baseline": _avg(base), "avg_improved": _avg(improved), "avg_winner": _avg(winners),
        "hook_winners_by_voice": {k: dict(v) for k, v in hook_by_voice.items()},
        "top_anti_patterns": flags.most_common(12), "top_posts": top_posts,
    }


# ── HTML ───────────────────────────────────────────────────────────────
def _badge(score):
    if score is None:
        return '<span class="b na">—</span>'
    c = "hi" if score >= 8 else "mid" if score >= 6.5 else "lo"
    return f'<span class="b {c}">{score:.1f}</span>'


def _post_block(title, post, scores, is_winner=False):
    s = scores or {}
    subs = " · ".join(f'{k.split("_")[0]}:{s.get(k,"—")}' for k in SCORE_KEYS)
    flags = "".join(f'<span class="fl">{html.escape(f)}</span>' for f in (s.get("anti_patterns") or []))
    crown = "👑 " if is_winner else ""
    return (f'<div class="pb {"win" if is_winner else ""}"><h4>{crown}{html.escape(title)} '
            f'{_badge(s.get("overall_score"))}</h4><pre>{html.escape((post or "")[:900])}</pre>'
            f'<div class="subs">{html.escape(subs)}</div><div class="flags">{flags}</div>'
            f'<p class="fb">{html.escape(s.get("feedback",""))}</p></div>')


def build_html(results):
    agg = results["aggregate"]
    secs = ""
    for s in results["scenarios"]:
        w = s.get("winner") or {}
        cards = ""
        for v in s["variants"]:
            cards += _post_block(f"{v['variant']} — {v['strategy']}", v.get("post"), v.get("scores"),
                                 is_winner=(w.get("source") == f"variant-{v['variant']}"))
        iters = ""
        if s.get("improved"):
            for it in s["improved"]["iterations"]:
                iters += _post_block(f"improve it{it['iter']}", it["post"], it["scores"],
                                     is_winner=(w.get("source") == f"improve-it{it['iter']}"))
        secs += (f'<section><h3>{html.escape(s["id"])} '
                 f'<span class="meta">[{html.escape(s["voice"])}/{html.escape(s["content_type"])}] '
                 f'baseline {_badge(s["baseline_score"])} → winner {_badge((w or {}).get("score"))}</span></h3>'
                 f'<div class="row"><div class="col"><b>A/B variánsok</b>{cards}</div>'
                 f'<div class="col"><b>Iteratív javítás</b>{iters or "<i>—</i>"}</div></div></section>')
    hooks = "".join(f'<tr><td>{html.escape(v)}</td><td>{html.escape(json.dumps(c, ensure_ascii=False))}</td></tr>'
                    for v, c in agg["hook_winners_by_voice"].items())
    aps = "".join(f'<li>{html.escape(f)} <b>×{n}</b></li>' for f, n in agg["top_anti_patterns"])
    tops = "".join(f'<li>{_badge(p["score"])} <b>{html.escape(p["voice"])}</b> ({html.escape(p["source"])}): '
                   f'{html.escape(p["post"][:140])}…</li>' for p in agg["top_posts"][:3])
    return f"""<!doctype html><html lang=hu><head><meta charset=utf-8><title>Szöveg eval</title>
<style>:root{{color-scheme:dark}}body{{font-family:system-ui,Segoe UI,sans-serif;background:#04060a;color:#e8edf2;margin:0;padding:24px}}
h1{{font-size:24px}}.sum{{background:#0a0f16;border:1px solid #1c2530;border-radius:10px;padding:16px;margin:14px 0}}
table{{border-collapse:collapse}}td{{padding:4px 12px;border-bottom:1px solid #1c2530}}
section{{border-top:1px solid #1c2530;margin-top:22px;padding-top:8px}}.meta{{color:#7d8ea0;font-size:14px;font-weight:400}}
.row{{display:flex;gap:18px}}.col{{flex:1}}
.pb{{background:#0a0f16;border:1px solid #1c2530;border-radius:8px;padding:8px 10px;margin:8px 0}}
.pb.win{{border-color:#14b8a6;box-shadow:0 0 0 2px rgba(20,184,166,.3)}}
.pb h4{{font-size:13px;margin:0 0 6px}}pre{{white-space:pre-wrap;font-family:inherit;font-size:12px;color:#c7d2dd;margin:0}}
.subs{{font-size:11px;color:#9fb0c0;margin-top:6px}}.flags{{display:flex;gap:4px;flex-wrap:wrap;margin:4px 0}}
.fl{{background:#3a1620;color:#ff9aa8;font-size:10px;padding:1px 5px;border-radius:4px}}
.fb{{font-size:11px;color:#a9b6c2;font-style:italic;margin:2px 0 0}}
.b{{font-size:12px;padding:1px 8px;border-radius:10px;font-weight:700}}.b.hi{{background:#0f3b33;color:#5eead4}}
.b.mid{{background:#3a3416;color:#e8d36a}}.b.lo{{background:#3a1620;color:#ff9aa8}}.b.na{{background:#1c2530;color:#7d8ea0}}
ul{{margin:6px 0}}</style></head><body>
<h1>PlanSmart — Szöveg-minőség eval</h1>
<div class=sum><div>Generálva: <b>{html.escape(results["generated_at"])}</b> · szcenárió: <b>{len(results["scenarios"])}</b></div>
<h2>Átlagok</h2>
<div>Baseline (produkciós A): <b>{agg["avg_baseline"]}</b> → Javított (improve): <b>{agg["avg_improved"]}</b>
 → Győztes (A/B+iter): <b>{agg["avg_winner"]}</b></div>
<h2>Nyertes hook hangonként</h2><table>{hooks}</table>
<h2>Top 3 győztes hook</h2><ul>{tops}</ul>
<h2>Leggyakoribb anti-patternek</h2><ul>{aps}</ul></div>
{secs}</body></html>"""


async def run(dataset, concurrency, target, max_iter):
    evaluator = TextEvaluator()
    sem = asyncio.Semaphore(concurrency)
    scenarios = await asyncio.gather(*(_scenario(e, evaluator, sem, target, max_iter) for e in dataset))
    scenarios = list(scenarios)
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "scenarios": scenarios, "aggregate": _aggregate(scenarios)}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--target", type=float, default=8.0)
    ap.add_argument("--max-iter", type=int, default=2)
    args = ap.parse_args()

    if not DATASET.exists():
        logger.error("Hiányzik a dataset — futtasd: python -m scripts.build_text_eval_dataset")
        return 1
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.limit:
        dataset = dataset[: args.limit]
    logger.info("Szöveg-eval: %d szcenárió × (5 variáns + max %d javító iteráció)", len(dataset), args.max_iter)
    results = asyncio.run(run(dataset, args.concurrency, args.target, args.max_iter))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = results["generated_at"].replace(":", "").replace("-", "").replace("T", "_")
    (DATA_DIR / f"text_eval_results_{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT.write_text(build_html(results), encoding="utf-8")

    agg = results["aggregate"]
    logger.info("\n%s\nÁTLAG baseline=%s → improved=%s → winner=%s", "=" * 60,
                agg["avg_baseline"], agg["avg_improved"], agg["avg_winner"])
    logger.info("Nyertes hook/voice: %s", agg["hook_winners_by_voice"])
    logger.info("Top anti-patternek: %s", agg["top_anti_patterns"][:6])
    logger.info("Riport: %s", REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
