"""Vizuál eval futtató — A/B teszt 4 variánssal minden eval-poszton, riport + HTML.

Lépések:
  1. data/eval_dataset.json betöltése
  2. posztonként ab_test_prompts (4 variáns: A/B/C/D) — kép + értékelés
  3. per-variant és per-voice átlagpontszámok
  4. győztes variáns (összes + hangonként)
  5. data/visual_eval_results_{timestamp}.json + data/visual_eval_results_latest.json
  6. data/visual_eval_report.html (side-by-side, győztes kiemelve, költség)

Becsült költség: posztok × 4 × $0.032 (10 poszt → ~$1.30).

    python -m scripts.run_visual_eval                 # teljes (a dataset mérete szerint)
    python -m scripts.run_visual_eval --limit 1       # olcsó füstteszt (1 poszt × 4)
    python -m scripts.run_visual_eval --variants A,B   # kevesebb variáns
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from src.optimization import visual_ab_test as ab
from src.optimization.visual_eval import SCORE_KEYS, VisualEvaluator

logger = logging.getLogger("run_visual_eval")
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATASET_FILE = DATA_DIR / "eval_dataset.json"
REPORT_FILE = DATA_DIR / "visual_eval_report.html"
LATEST_FILE = DATA_DIR / "visual_eval_results_latest.json"


def _avg(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _aggregate(posts: list[dict]) -> dict:
    """per-variant és per-voice átlag overall_score + sub-score átlagok."""
    per_variant: dict[str, list[float]] = defaultdict(list)
    per_variant_sub: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    per_voice: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for post in posts:
        voice = post["voice"]
        for v in post["variants"]:
            s = v.get("scores")
            if not s or s.get("error"):
                continue
            per_variant[v["variant"]].append(s["overall_score"])
            per_voice[voice][v["variant"]].append(s["overall_score"])
            for k in SCORE_KEYS:
                per_variant_sub[v["variant"]][k].append(s.get(k))

    variant_avg = {k: _avg(v) for k, v in per_variant.items()}
    variant_sub_avg = {k: {sk: _avg(sv) for sk, sv in subs.items()} for k, subs in per_variant_sub.items()}
    voice_avg = {voice: {k: _avg(v) for k, v in vm.items()} for voice, vm in per_voice.items()}

    def _best(d: dict[str, float | None]) -> str | None:
        scored = {k: v for k, v in d.items() if v is not None}
        return max(scored, key=scored.get) if scored else None

    winner_overall = _best(variant_avg)
    winner_per_voice = {voice: _best(vm) for voice, vm in voice_avg.items()}
    return {
        "per_variant_avg": variant_avg,
        "per_variant_subscores": variant_sub_avg,
        "per_voice_avg": voice_avg,
        "winner_overall": winner_overall,
        "winner_per_voice": winner_per_voice,
    }


async def run(dataset: list[dict], variants: list[str], concurrency: int) -> dict:
    evaluator = VisualEvaluator()
    sem = asyncio.Semaphore(concurrency)

    async def _one(entry: dict) -> dict:
        async with sem:
            res = await ab.ab_test_prompts(
                entry["content"], entry["voice"], variant_count=len(variants), evaluator=evaluator,
            )
        res.update({
            "post_id": entry["post_id"], "content_type": entry.get("content_type"),
            "content": entry["content"], "current_visual_url": entry.get("current_visual_url"),
            "expected_visual_elements": entry.get("expected_visual_elements", []),
        })
        done = sum(1 for v in res["variants"] if v.get("image_url"))
        logger.info("[eval] %s [%s] győztes=%s (%s) | %d/%d kép | $%.4f",
                    entry["post_id"], entry["voice"], res.get("winner_variant"),
                    res.get("winner_score"), done, len(res["variants"]), res["total_cost"])
        return res

    posts = await asyncio.gather(*(_one(e) for e in dataset))
    agg = _aggregate(list(posts))
    total_cost = round(sum(p["total_cost"] for p in posts), 4)
    n_images = sum(1 for p in posts for v in p["variants"] if v.get("image_url"))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_size": len(dataset), "variants": variants,
        "images_generated": n_images, "total_cost": total_cost,
        "aggregate": agg, "posts": list(posts),
    }


# ── HTML riport ────────────────────────────────────────────────────────
def _score_badge(score: float | None) -> str:
    if score is None:
        return '<span class="badge na">—</span>'
    cls = "high" if score >= 8 else "mid" if score >= 6.5 else "low"
    return f'<span class="badge {cls}">{score:.1f}</span>'


def _variant_card(v: dict, is_winner: bool) -> str:
    s = v.get("scores") or {}
    label = html.escape(f"{v['variant']} — {v.get('label', '')}")
    if v.get("error") or not v.get("image_url"):
        body = f'<div class="err">hiba: {html.escape(str(v.get("error") or "nincs kép"))}</div>'
        return f'<div class="card {"winner" if is_winner else ""}"><h4>{label}</h4>{body}</div>'
    subs = "".join(
        f'<li>{k.replace("_score","").replace("_"," ")}: <b>{s.get(k,"—")}</b></li>' for k in SCORE_KEYS
    )
    flags = "".join(f'<span class="flag">{html.escape(f)}</span>' for f in (s.get("anti_pattern_flags") or []))
    crown = "👑 " if is_winner else ""
    return (
        f'<div class="card {"winner" if is_winner else ""}">'
        f'<h4>{crown}{label} {_score_badge(s.get("overall_score"))}</h4>'
        f'<img src="{html.escape(v["image_url"])}" loading="lazy" />'
        f'<ul class="subs">{subs}</ul>'
        f'<div class="flags">{flags}</div>'
        f'<p class="fb">{html.escape(s.get("feedback",""))}</p>'
        f'<p class="cost">${v.get("cost",0):.4f}</p>'
        f'</div>'
    )


def _post_section(post: dict) -> str:
    winner = post.get("winner_variant")
    cards = "".join(_variant_card(v, v["variant"] == winner) for v in post["variants"])
    orig = ""
    if post.get("current_visual_url"):
        orig = (f'<div class="card orig"><h4>Eredeti (produkció)</h4>'
                f'<img src="{html.escape(post["current_visual_url"])}" loading="lazy" /></div>')
    expected = ", ".join(html.escape(e) for e in post.get("expected_visual_elements", []))
    return (
        f'<section class="post"><h3>{html.escape(post["post_id"])} '
        f'<span class="meta">[{html.escape(post["voice"])} / {html.escape(str(post.get("content_type")))}]</span></h3>'
        f'<p class="content">{html.escape(post["content"][:280])}…</p>'
        f'<p class="expected"><b>Elvárt elemek:</b> {expected}</p>'
        f'<div class="cards">{orig}{cards}</div></section>'
    )


def _bar(label: str, score: float | None, is_winner: bool) -> str:
    pct = (score or 0) * 10
    cls = "winner" if is_winner else ""
    txt = f"{score:.2f}" if score is not None else "—"
    return (f'<div class="barrow"><span class="blabel {cls}">{html.escape(label)}</span>'
            f'<span class="bar"><span class="fill {cls}" style="width:{pct:.0f}%"></span></span>'
            f'<span class="bval">{txt}</span></div>')


def build_html(results: dict) -> str:
    agg = results["aggregate"]
    va = agg["per_variant_avg"]
    wo = agg["winner_overall"]
    variant_bars = "".join(
        _bar(f"{k} — {ab.VARIANT_LABELS.get(k, '')}", va.get(k), k == wo)
        for k in ab.DEFAULT_VARIANTS if k in va
    )
    voice_rows = ""
    for voice, vm in agg["per_voice_avg"].items():
        wv = agg["winner_per_voice"].get(voice)
        cells = " ".join(f'{k}:{_score_badge(vm.get(k))}' for k in ab.DEFAULT_VARIANTS if k in vm)
        voice_rows += f'<tr><td><b>{html.escape(voice)}</b></td><td>{cells}</td><td>👑 {wv}</td></tr>'
    posts_html = "".join(_post_section(p) for p in results["posts"])

    return f"""<!doctype html><html lang="hu"><head><meta charset="utf-8">
<title>PlanSmart — Vizuál eval riport</title>
<style>
:root{{color-scheme:dark}}
body{{font-family:system-ui,Segoe UI,sans-serif;background:#04060a;color:#e8edf2;margin:0;padding:24px}}
h1{{font-size:24px}} h2{{margin-top:32px;border-bottom:1px solid #1c2530;padding-bottom:6px}}
.summary{{background:#0a0f16;border:1px solid #1c2530;border-radius:10px;padding:18px;margin:16px 0}}
.barrow{{display:flex;align-items:center;gap:10px;margin:6px 0}}
.blabel{{width:300px;font-size:13px;color:#9fb0c0}} .blabel.winner{{color:#5eead4;font-weight:700}}
.bar{{flex:1;height:14px;background:#10161f;border-radius:7px;overflow:hidden}}
.fill{{display:block;height:100%;background:#3b5566}} .fill.winner{{background:#14b8a6}}
.bval{{width:46px;text-align:right;font-variant-numeric:tabular-nums}}
table{{border-collapse:collapse;margin-top:10px}} td{{padding:6px 12px;border-bottom:1px solid #1c2530}}
.post{{margin:28px 0;padding-top:8px;border-top:1px solid #1c2530}}
.post h3 .meta{{color:#7d8ea0;font-weight:400;font-size:14px}}
.content{{color:#b9c6d3;font-size:13px;max-width:1100px}} .expected{{color:#8aa;font-size:12px}}
.cards{{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}}
.card{{width:230px;background:#0a0f16;border:1px solid #1c2530;border-radius:10px;padding:10px}}
.card.winner{{border-color:#14b8a6;box-shadow:0 0 0 2px rgba(20,184,166,.3)}}
.card.orig{{border-style:dashed;opacity:.9}}
.card h4{{font-size:13px;margin:0 0 8px}} .card img{{width:100%;border-radius:6px;display:block;background:#000}}
.subs{{list-style:none;padding:0;margin:8px 0;font-size:12px;color:#9fb0c0}} .subs li{{margin:2px 0}}
.flags{{display:flex;gap:4px;flex-wrap:wrap;margin:4px 0}}
.flag{{background:#3a1620;color:#ff9aa8;font-size:11px;padding:1px 6px;border-radius:4px}}
.fb{{font-size:12px;color:#c7d2dd;font-style:italic}} .cost{{font-size:11px;color:#6b7a89;text-align:right;margin:2px 0 0}}
.err{{color:#ff9aa8;font-size:12px}}
.badge{{font-size:12px;padding:1px 8px;border-radius:10px;font-weight:700}}
.badge.high{{background:#0f3b33;color:#5eead4}} .badge.mid{{background:#3a3416;color:#e8d36a}}
.badge.low{{background:#3a1620;color:#ff9aa8}} .badge.na{{background:#1c2530;color:#7d8ea0}}
</style></head><body>
<h1>PlanSmart — Vizuál eval riport</h1>
<div class="summary">
  <div>Generálva: <b>{html.escape(results["generated_at"])}</b> · poszt: <b>{results["dataset_size"]}</b>
  · kép: <b>{results["images_generated"]}</b> · költség: <b>${results["total_cost"]:.4f}</b></div>
  <h2>Variáns átlagok (overall, 1-10)</h2>
  {variant_bars}
  <div style="margin-top:10px">Összgyőztes: <b style="color:#5eead4">{wo} — {html.escape(ab.VARIANT_LABELS.get(wo or '', ''))}</b></div>
  <h2>Hangonkénti győztes</h2>
  <table>{voice_rows}</table>
</div>
<h2>Posztok — side-by-side</h2>
{posts_html}
</body></html>"""


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="Vizuál eval futtató (A/B teszt + HTML riport).")
    ap.add_argument("--limit", type=int, default=0, help="csak az első N poszt (0 = mind)")
    ap.add_argument("--variants", type=str, default="A,B,C,D")
    ap.add_argument("--concurrency", type=int, default=2, help="párhuzamos posztok száma")
    args = ap.parse_args()

    if not DATASET_FILE.exists():
        logger.error("Hiányzik a dataset: %s — futtasd előbb: python -m scripts.build_eval_dataset", DATASET_FILE)
        return 1
    dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    if args.limit:
        dataset = dataset[: args.limit]
    variants = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    ab.DEFAULT_VARIANTS = [v for v in ["A", "B", "C", "D"] if v in variants] or ["A", "B", "C", "D"]

    logger.info("Eval indul: %d poszt × %d variáns (becsült ~$%.2f)",
                len(dataset), len(ab.DEFAULT_VARIANTS), len(dataset) * len(ab.DEFAULT_VARIANTS) * 0.032)
    results = asyncio.run(run(dataset, ab.DEFAULT_VARIANTS, args.concurrency))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = results["generated_at"].replace(":", "").replace("-", "").replace("T", "_")
    ts_file = DATA_DIR / f"visual_eval_results_{stamp}.json"
    ts_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_FILE.write_text(build_html(results), encoding="utf-8")

    agg = results["aggregate"]
    logger.info("\n%s\nÖSSZGYŐZTES: %s | per-voice: %s", "=" * 60, agg["winner_overall"], agg["winner_per_voice"])
    logger.info("Variáns átlagok: %s", agg["per_variant_avg"])
    logger.info("Költség: $%.4f | Riport: %s | JSON: %s", results["total_cost"], REPORT_FILE, ts_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
