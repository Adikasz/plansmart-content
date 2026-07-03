"""Phase 17 — vizuál-változatosság teszt: 4 egymást követő poszt / voice (12 kép).

Végigfuttatja a TELJES éles vizuál-pipeline-t (compose_visual: Muapi szöveg-mentes alapkép →
PIL template+mood overlay → feltöltés), voice-onként 4 poszttal, és igazolja:
  • nincs azonnali template/mood ismétlés egy voice sorozatán belül,
  • mind a 4 template megjelenik (nem 2 dominál),
  • visual_eval minőség-sanity: egy kép se essen 1.0-nál többel a voice Phase 12.6 baseline-je alá.
Végül önálló HTML összehasonlító oldal (base64 képek, voice szerint csoportosítva, címkézve).

Futtatás (VALÓS Muapi költség, ~12 * $0.032):
    python scripts/test_visual_variety.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
load_dotenv(override=False)

from src.optimization.visual_eval import VisualEvaluator
from src.visuals import layout_templates as lt
from src.visuals import visual_generator, visual_variety

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Phase 12.6 validált baseline-ek (a promptból) + 1.0 pont tűrés.
BASELINE = {"david": 8.5, "adam": 7.2, "plansmart": 7.5}
REGRESSION_TOLERANCE = 1.0

# 4 rövid, ANGOL, NEM-fabrikált (általános/konceptuális) poszt voice-onként.
POSTS = {
    "david": [
        "Bigger models don't fix broken agent loops. Better error handling does. We cut our own "
        "content pipeline's poll loop from 16 minutes to 3.5 seconds by going event-driven.",
        "Most 'AI agents' are one API call in a trench coat. A real agent decides at each step — "
        "that's where error handling and observability actually start to matter.",
        "Observability first, model second. When a 12-step agent returns garbage, you need to know "
        "which step went bad, not just that the final answer was wrong.",
        "Checkpoint long agent chains. If step 8 of 12 fails, you shouldn't rerun steps 1 through 7. "
        "Persist intermediate state and resume from the failure point.",
    ],
    "adam": [
        "'Is it the right time for AI' is the wrong question. The real one: which three processes "
        "cost you the most time right now, and what does that time actually cost?",
        "73% of the value from automation comes from the boring, repeatable work — not the flashy "
        "use cases. Start where the hours actually go.",
        "Owners don't want AI. They want the person drowning in reports to stop burning out. AI is "
        "just the tool that gets there.",
        "Run the math once a month: your three longest manual processes, hours per week, times your "
        "loaded cost. The timing question answers itself.",
    ],
    "plansmart": [
        "One well-chosen automation beats five disconnected AI tools. Scope the whole process, from "
        "trigger to outcome, not a single step.",
        "A stack of tools digitizes moments. A system automates the process. The difference shows up "
        "directly in the ROI you can calculate in an afternoon.",
        "The clearest wins start with one question: which single process, if it ran itself, would "
        "give you the most time back?",
        "Automating one process end to end removes the coordination tax — the quiet hours spent "
        "keeping five separate tools in sync.",
    ],
}


def _reset_variety_state() -> None:
    """Tiszta rotáció-kiindulás: fallback JSON törlése + Supabase sorok törlése (best-effort)."""
    try:
        if visual_variety.STATE_FILE.exists():
            visual_variety.STATE_FILE.unlink()
    except Exception:
        pass
    try:
        from src.storage.db import get_client, has_service_key, table_exists

        client = get_client(use_service_key=has_service_key())
        if table_exists(client, visual_variety.STATE_TABLE):
            for v in ("david", "adam", "plansmart"):
                client.table(visual_variety.STATE_TABLE).delete().eq("voice", v).execute()
            print("[reset] Supabase visual_variety_state sorok törölve.")
        else:
            print("[reset] Supabase tábla még nincs — a rotáció JSON fallbacket használ (a teszthez OK).")
    except Exception as exc:
        print(f"[reset] Supabase reset kihagyva ({str(exc)[:80]}) — JSON fallback.")


async def _gen_voice(voice: str) -> list[dict]:
    """A voice 4 egymást követő posztja (SORBAN — a rotáció az előzőtől függ)."""
    out = []
    for i, content in enumerate(POSTS[voice], 1):
        post = {"id": None, "voice": voice, "platform": "linkedin", "content": content,
                "hashtags": [], "portrait": False}  # portrét kikapcsoljuk: tiszta template-variancia
        res = await visual_generator.compose_visual(post)
        out.append({"voice": voice, "idx": i, "content": content,
                    "template": res["template"], "mood": res["mood"],
                    "image_url": res["image_url"], "local_path": res["local_path"],
                    "visual_text": res["visual_text"], "cost_usd": res.get("cost_usd") or 0.0})
        print(f"  {voice} {i}/4 → template={res['template']:20s} mood={res['mood']:18s} ${res.get('cost_usd') or 0:.4f}")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(PROJECT_ROOT / "assets" / "generated" / "variety_report.html"))
    args = ap.parse_args()

    print("=" * 74)
    print("PHASE 17 — VISUAL VARIETY TEST (12 images, real Muapi)")
    print("=" * 74)
    _reset_variety_state()

    # Voice-onként SORBAN (rotáció), a 3 voice PÁRHUZAMOSAN (független state).
    per_voice = await asyncio.gather(*[_gen_voice(v) for v in ("david", "adam", "plansmart")])
    items = [x for chain in per_voice for x in chain]

    # ── Ellenőrzés 1: nincs azonnali ismétlés + mind a 4 template ──────
    print("\n" + "=" * 74 + "\nROTATION CHECK\n" + "=" * 74)
    rotation_ok = True
    for voice, chain in zip(("david", "adam", "plansmart"), per_voice):
        combos = [(c["template"], c["mood"]) for c in chain]
        templates = [c["template"] for c in chain]
        allowed = set(lt.templates_for(voice))  # Phase 17.1: david=2, adam/plansmart=4
        immediate_repeat = any(combos[i] == combos[i - 1] for i in range(1, len(combos)))
        all_allowed = set(templates) == allowed
        rotation_ok = rotation_ok and not immediate_repeat and all_allowed
        print(f"\n{voice} (allowed: {len(allowed)}):")
        for c in chain:
            print(f"  {c['idx']}. {c['template']:20s} | {c['mood']}")
        print(f"  no immediate repeat: {not immediate_repeat} | all allowed templates used: {all_allowed} "
              f"({len(set(templates))}/{len(allowed)} distinct)")

    # ── Ellenőrzés 2: minőség-sanity (visual_eval) ─────────────────────
    print("\n" + "=" * 74 + "\nQUALITY EVAL (vs Phase 12.6 baseline)\n" + "=" * 74)
    ev = VisualEvaluator()
    sem = asyncio.Semaphore(4)

    async def _eval(it):
        async with sem:
            r = await ev.evaluate_image(it["local_path"], it["content"], it["voice"])
        it["eval"] = r
        base = BASELINE[it["voice"]]
        it["regression"] = r.get("overall_score", 0) < base - REGRESSION_TOLERANCE
        return it

    await asyncio.gather(*[_eval(it) for it in items])
    regressions = [it for it in items if it["regression"]]
    for voice in ("david", "adam", "plansmart"):
        base = BASELINE[voice]
        print(f"\n{voice} (baseline {base}, flag if < {base - REGRESSION_TOLERANCE}):")
        for it in [x for x in items if x["voice"] == voice]:
            o = it["eval"].get("overall_score", 0)
            flag = "  ⚠ REGRESSION" if it["regression"] else ""
            print(f"  {it['template']:20s} {it['mood']:18s} overall={o}{flag}")

    total_cost = sum(it["cost_usd"] for it in items)

    # ── Manifest (image → voice/template/mood/eval), hogy a mapping ne vesszen el ──
    import json

    manifest = [{"voice": it["voice"], "idx": it["idx"], "template": it["template"],
                 "mood": it["mood"], "local_path": it["local_path"], "image_url": it["image_url"],
                 "overall": it.get("eval", {}).get("overall_score"),
                 "regression": it.get("regression")} for it in items]
    (PROJECT_ROOT / "assets" / "generated" / "variety_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── HTML riport (self-contained, base64 képek) ─────────────────────
    _write_html(args.report, items)

    print("\n" + "=" * 74 + "\nSUMMARY\n" + "=" * 74)
    print(f"images generated: {len(items)}")
    print(f"rotation OK (no immediate repeats, all allowed templates each voice): {rotation_ok}")
    print(f"quality regressions (> {REGRESSION_TOLERANCE} below baseline): {len(regressions)}")
    for it in regressions:
        print(f"   ⚠ {it['voice']} {it['template']} {it['mood']} — "
              f"{it['eval'].get('overall_score')} vs base {BASELINE[it['voice']]}")
    print(f"Muapi cost (reported): ${total_cost:.4f}  (+ 12 Sonnet-vision evals, not billed here)")
    print(f"HTML report: {args.report}")
    return 0


def _b64(path: str) -> str:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except Exception:
        return ""


def _write_html(path: str, items: list[dict]) -> None:
    cards_by_voice: dict[str, list[str]] = {"david": [], "adam": [], "plansmart": []}
    for it in items:
        ev = it.get("eval", {})
        o = ev.get("overall_score", 0)
        base = BASELINE[it["voice"]]
        reg = it.get("regression")
        badge = (f'<span class="reg">▼ {o} (base {base})</span>' if reg
                 else f'<span class="ok">{o} / 10</span>')
        subs = (f"brand {ev.get('brand_alignment_score','?')} · text {ev.get('hungarian_text_quality','?')} · "
                f"scroll {ev.get('scroll_stopping_score','?')} · pro {ev.get('professional_score','?')}")
        img = _b64(it["local_path"])
        cards_by_voice[it["voice"]].append(f"""
      <figure class="card">
        <img src="data:image/png;base64,{img}" alt="{it['template']}"/>
        <figcaption>
          <div class="tpl">{it['idx']}. {it['template']}</div>
          <div class="mood">mood: {it['mood']}</div>
          <div class="score">{badge}</div>
          <div class="subs">{subs}</div>
        </figcaption>
      </figure>""")
    sections = ""
    for voice in ("david", "adam", "plansmart"):
        sections += f'<h2>{voice} <span class="base">baseline {BASELINE[voice]}</span></h2>\n<div class="grid">{"".join(cards_by_voice[voice])}</div>\n'
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Phase 17 — Visual Variety</title>
<style>
  body{{background:#0a0c10;color:#e6ecf2;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:32px}}
  h1{{font-weight:800;letter-spacing:-.02em}} h2{{margin-top:40px;text-transform:capitalize;border-bottom:1px solid #1e2530;padding-bottom:8px}}
  .base{{font-size:13px;color:#7e8a99;font-weight:400}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:18px;margin-top:16px}}
  .card{{margin:0;background:#11151b;border:1px solid #1e2530;border-radius:12px;overflow:hidden}}
  .card img{{width:100%;display:block;aspect-ratio:1/1;object-fit:cover}}
  figcaption{{padding:10px 12px}} .tpl{{font-weight:700}} .mood{{color:#8a97a6;font-size:13px}}
  .score{{margin-top:6px}} .ok{{color:#2dd4bf;font-weight:700}} .reg{{color:#f87171;font-weight:700}}
  .subs{{color:#6b7787;font-size:12px;margin-top:4px}}
</style></head><body>
<h1>Phase 17 — Visual Variety (4 templates × curated moods)</h1>
<p style="color:#8a97a6">12 images, real pipeline. Each voice: 4 consecutive posts, template+mood rotation (no immediate repeats).</p>
{sections}
</body></html>"""
    Path(path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
