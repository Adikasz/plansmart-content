"""Vizuál eval v2 — szöveg-mentes alapkép + PIL overlay, a KOMPONÁLT végeredményt pontozza.

Ugyanazon a 10-poszt dataseten fut, mint a Phase 12 eval, de:
  • az alapképet SZÖVEG NÉLKÜL generáljuk (build_textfree_prompt),
  • a magyar szöveget PIL-lel ráírjuk (TextOverlayComposer),
  • a KOMPONÁLT képet pontozzuk (VisualEvaluator),
  • összevetjük a Phase 12 baseline-nal (data/visual_eval_results_latest.json).

    python -m scripts.run_visual_eval_v2            # teljes (dataset mérete szerint)
    python -m scripts.run_visual_eval_v2 --limit 2  # olcsó füstteszt
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

from src.ai.optimization.visual_eval import SCORE_KEYS, VisualEvaluator
from src.integrations.visuals import muapi_client
from src.integrations.visuals import visual_generator as vg
from src.integrations.visuals.text_overlay import GENERATED_DIR, TextOverlayComposer

logger = logging.getLogger("run_visual_eval_v2")
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATASET_FILE = DATA_DIR / "eval_dataset.json"
BASELINE_FILE = DATA_DIR / "visual_eval_results_latest.json"
REPORT_FILE = DATA_DIR / "visual_eval_v2_report.html"
LATEST_V2 = DATA_DIR / "visual_eval_v2_results_latest.json"


def _avg(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 2) if v else None


async def _one(entry, composer, evaluator, sem):
    async with sem:
        voice, content = entry["voice"], entry["content"]
        post = {"voice": voice, "content": content, "hashtags": []}
        vt = await vg.extract_visual_text(content)
        prompt = vg.build_textfree_prompt(post, vt)
        try:
            res = await muapi_client.generate(prompt, model=muapi_client.DEFAULT_MODEL, aspect_ratio="1:1")
        except Exception as exc:
            logger.warning("[v2] %s base hiba: %s", entry["post_id"], str(exc)[:100])
            return {**entry, "error": str(exc)[:120], "scores": None}
        out = str(GENERATED_DIR / f"eval_v2_{entry['post_id']}.png")
        composed = await asyncio.to_thread(
            composer.compose, res.image_url, vt.get("main_text", ""), vt.get("sub_text"), vt.get("stat"), voice, out
        )
        scores = await evaluator.evaluate_image(composed, content, voice)
        logger.info("[v2] %s [%s] overall=%s HU=%s | $%.4f",
                    entry["post_id"], voice, scores.get("overall_score"),
                    scores.get("hungarian_text_quality"), res.cost_usd or 0.0)
        return {**entry, "base_image_url": res.image_url, "composed_path": composed,
                "visual_text": vt, "scores": scores, "cost": res.cost_usd or 0.0}


def _aggregate(posts):
    per_voice = defaultdict(lambda: defaultdict(list))
    sub = defaultdict(list)
    overall = []
    for p in posts:
        s = p.get("scores")
        if not s or s.get("error"):
            continue
        overall.append(s["overall_score"])
        for k in SCORE_KEYS:
            sub[k].append(s.get(k))
            per_voice[p["voice"]][k].append(s.get(k))
        per_voice[p["voice"]]["overall_score"].append(s["overall_score"])
    return {
        "overall_avg": _avg(overall),
        "subscore_avg": {k: _avg(v) for k, v in sub.items()},
        "per_voice_avg": {voice: {k: _avg(v) for k, v in vm.items()} for voice, vm in per_voice.items()},
    }


def _baseline() -> dict:
    if not BASELINE_FILE.exists():
        return {}
    b = json.loads(BASELINE_FILE.read_text(encoding="utf-8")).get("aggregate", {})
    pv = b.get("per_variant_avg", {})
    subs = b.get("per_variant_subscores", {})
    overall = _avg(list(pv.values()))
    hu = _avg([(subs.get(v, {}) or {}).get("hungarian_text_quality") for v in subs])
    return {"overall": overall, "hungarian_text_quality": hu, "winner": b.get("winner_overall"),
            "per_voice": b.get("per_voice_avg", {})}


def build_html(results, agg, base) -> str:
    rows = ""
    for p in results:
        s = p.get("scores") or {}
        if p.get("error") or not p.get("composed_path"):
            rows += f'<section class="post"><h3>{html.escape(p["post_id"])} [{html.escape(p["voice"])}] — hiba</h3></section>'
            continue
        composed = Path(p["composed_path"]).resolve().as_uri()
        base_u = html.escape(p.get("base_image_url", ""))
        subs = " · ".join(f'{k.replace("_score","").replace("_"," ")}: <b>{s.get(k,"—")}</b>' for k in SCORE_KEYS)
        vt = p.get("visual_text") or {}
        rows += (
            f'<section class="post"><h3>{html.escape(p["post_id"])} '
            f'<span class="meta">[{html.escape(p["voice"])} / {html.escape(str(p.get("content_type")))}]</span> '
            f'<span class="badge">{s.get("overall_score","—")}</span></h3>'
            f'<p class="ov">overlay: <b>{html.escape(vt.get("main_text",""))}</b> / '
            f'{html.escape(vt.get("sub_text") or "")}{" / stat:"+html.escape(str(vt.get("stat"))) if vt.get("stat") else ""}</p>'
            f'<div class="cards">'
            f'<div class="card"><h4>BASE (text-free Muapi)</h4><img src="{base_u}" loading="lazy"></div>'
            f'<div class="card win"><h4>COMPOSED (PIL overlay)</h4><img src="{html.escape(composed)}" loading="lazy"></div>'
            f'</div><p class="subs">{subs}</p>'
            f'<p class="fb">{html.escape(s.get("feedback",""))}</p></section>'
        )
    cmp_rows = ""
    for label, key in (("Overall", "overall_avg"), ("Magyar szöveg minőség", "hungarian_text_quality")):
        v2 = agg["overall_avg"] if key == "overall_avg" else agg["subscore_avg"].get("hungarian_text_quality")
        b = base.get("overall" if key == "overall_avg" else "hungarian_text_quality")
        delta = round((v2 or 0) - (b or 0), 2) if (v2 is not None and b is not None) else "—"
        cmp_rows += f'<tr><td>{html.escape(label)}</td><td>{b}</td><td><b>{v2}</b></td><td>{"+" if isinstance(delta,(int,float)) and delta>=0 else ""}{delta}</td></tr>'
    return f"""<!doctype html><html lang="hu"><head><meta charset="utf-8"><title>Vizuál eval v2</title>
<style>:root{{color-scheme:dark}}body{{font-family:system-ui,Segoe UI,sans-serif;background:#04060a;color:#e8edf2;margin:0;padding:24px}}
h1{{font-size:24px}}.summary{{background:#0a0f16;border:1px solid #1c2530;border-radius:10px;padding:18px;margin:16px 0}}
table{{border-collapse:collapse}}td,th{{padding:6px 16px;border-bottom:1px solid #1c2530;text-align:left}}
.post{{margin:24px 0;border-top:1px solid #1c2530;padding-top:10px}}.meta{{color:#7d8ea0;font-size:14px;font-weight:400}}
.cards{{display:flex;gap:14px}}.card{{width:320px;background:#0a0f16;border:1px solid #1c2530;border-radius:10px;padding:10px}}
.card.win{{border-color:#14b8a6;box-shadow:0 0 0 2px rgba(20,184,166,.3)}}.card img{{width:100%;border-radius:6px;display:block;background:#000}}
.card h4{{font-size:12px;margin:0 0 8px}}.subs{{font-size:13px;color:#9fb0c0}}.fb{{font-size:12px;color:#c7d2dd;font-style:italic}}
.ov{{font-size:13px;color:#9fb0c0}}.badge{{background:#0f3b33;color:#5eead4;padding:1px 10px;border-radius:10px;font-size:14px}}</style></head><body>
<h1>PlanSmart — Vizuál eval v2 (text overlay pipeline)</h1>
<div class="summary"><div>Generálva: <b>{html.escape(results[0].get("generated_at","") if results else "")}</b></div>
<h2>Phase 12 → 12.5 összevetés</h2>
<table><tr><th>Metrika</th><th>Phase 12 baseline</th><th>Phase 12.5</th><th>Δ</th></tr>{cmp_rows}</table>
<p>Hangonkénti overall: {html.escape(json.dumps({v:m.get("overall_score") for v,m in agg["per_voice_avg"].items()}, ensure_ascii=False))}</p>
</div>{rows}</body></html>"""


async def run(dataset, concurrency):
    composer = TextOverlayComposer()
    evaluator = VisualEvaluator()
    sem = asyncio.Semaphore(concurrency)
    posts = await asyncio.gather(*(_one(e, composer, evaluator, sem) for e in dataset))
    posts = list(posts)
    stamp = datetime.now().isoformat(timespec="seconds")
    for p in posts:
        p["generated_at"] = stamp
    return posts


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
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    if not DATASET_FILE.exists():
        logger.error("Hiányzik a dataset — futtasd: python -m scripts.build_eval_dataset")
        return 1
    dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    if args.limit:
        dataset = dataset[: args.limit]

    logger.info("Eval v2 indul: %d poszt (text-free base + overlay), ~$%.2f", len(dataset), len(dataset) * 0.032)
    posts = asyncio.run(run(dataset, args.concurrency))
    agg = _aggregate(posts)
    base = _baseline()
    total_cost = round(sum(p.get("cost", 0) for p in posts), 4)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_V2.write_text(json.dumps({"aggregate": agg, "baseline": base, "total_cost": total_cost,
                                     "posts": posts}, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_FILE.write_text(build_html(posts, agg, base), encoding="utf-8")

    hu12 = base.get("hungarian_text_quality")
    hu125 = agg["subscore_avg"].get("hungarian_text_quality")
    logger.info("\n%s\nPhase 12 → 12.5:", "=" * 60)
    logger.info("  overall:        %s → %s", base.get("overall"), agg["overall_avg"])
    logger.info("  HU szöveg:      %s → %s", hu12, hu125)
    logger.info("  per-voice 12.5: %s", {v: m.get("overall_score") for v, m in agg["per_voice_avg"].items()})
    logger.info("Költség: $%.4f | Riport: %s", total_cost, REPORT_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
