"""Phase 13.5 teszt — Hunglish (anglicizált magyar) mérés valós posztokon.

1) 5 legutóbbi poszt a `posts` táblából (Supabase); ha nem elérhető, beépített minta.
2) Mindegyiket pontozza a ÚJ `hungarian_nativeness` dimenzión (+ a determinisztikus
   Hunglish-jargon kapás megmutatja a konkrét talált mintákat).
3) A 2 legrosszabb (legalacsonyabb nativeness) posztot átírja az improve_post loop-pal
   (ami már a magyar natívsági segédletet is kapja), és mutatja az előtte/utána-t.

Futtatás:
    python scripts/test_hunglish_scoring.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.optimization.text_evaluator import TextEvaluator, _hunglish_flags  # noqa: E402
from src.ai.optimization.text_improver import improve_post  # noqa: E402

logger = logging.getLogger(__name__)

# Beépített minta (fallback), ha nincs Supabase — szándékosan Hunglish-es példák.
SAMPLE_POSTS = [
    {"voice": "adam", "content_type": "ai_news",
     "content": "A mai AI mindset-tel a magyar KKV-k tudják leverage-elni az új eszközöket. "
                "Az insights, amiket a data-ból nyerünk, egy igazi game changer. Ha jól csinálod a "
                "workflow-t, könnyen tudsz scale-elni és az onboarding is gördülékeny lesz."},
    {"voice": "david", "content_type": "educational",
     "content": "Tegnap este átírtam a queue logikát. A deep dive a kódban megérte: a fő takeaway, "
                "hogy a régi megoldás nem tudott scale-elni. Itt vannak az action items amiket "
                "csináltam, hogy jobb legyen a performance."},
    {"voice": "plansmart", "content_type": "case_study",
     "content": "Egy 30 fős ügyfelünknél bevezettük az új folyamatot. A napi 4 óra adminisztráció "
                "35 percre csökkent. A csapat visszajelzése kifejezetten pozitív volt."},
]


def _fetch_recent(limit: int = 5) -> list[dict]:
    try:
        from src.core.storage.db import get_client, has_service_key

        c = get_client(use_service_key=has_service_key())
        r = (c.table("posts")
             .select("voice,content,edited_content,final_content,content_type,generated_at")
             .order("generated_at", desc=True).limit(limit).execute())
        rows = r.data or []
        posts = []
        for x in rows:
            text = (x.get("final_content") or x.get("edited_content") or x.get("content") or "").strip()
            if text:
                posts.append({"voice": x.get("voice") or "david",
                              "content_type": x.get("content_type") or "ai_news", "content": text})
        if posts:
            logger.info("Supabase: %d valós poszt betöltve.", len(posts))
            return posts
    except Exception as exc:
        logger.warning("Supabase nem elérhető (%s) — beépített minta.", str(exc)[:120])
    return SAMPLE_POSTS


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    posts = _fetch_recent(5)
    evaluator = TextEvaluator()

    print("\n" + "=" * 70)
    print("HUNGLISH NATÍVSÁG-PONTOZÁS — 5 poszt")
    print("=" * 70)
    scored = []
    for i, p in enumerate(posts, 1):
        res = await evaluator.evaluate_post(p["content"], p["voice"], p["content_type"])
        hunglish = _hunglish_flags(p["content"])
        scored.append({**p, "scores": res, "hunglish": hunglish})
        print(f"\n[{i}] voice={p['voice']}  nativeness={res.get('hungarian_nativeness')}/10  "
              f"overall={res.get('overall_score')}")
        print(f"    szöveg: {p['content'][:90].replace(chr(10), ' ')}…")
        if hunglish:
            print("    Talált Hunglish jargon (determinisztikus):")
            for h in hunglish:
                print(f"      • {h}")
        native_flags = [f for f in res.get("anti_patterns", []) if "Hunglish" in f or "jargon" in f.lower()]
        struct = [f for f in res.get("anti_patterns", []) if f not in native_flags]
        if struct:
            print(f"    Egyéb anti-pattern: {', '.join(struct[:4])}")

    # A 2 legrosszabb (legalacsonyabb nativeness, majd overall) átírása.
    worst = sorted(scored, key=lambda s: (s["scores"].get("hungarian_nativeness", 10),
                                          s["scores"].get("overall_score", 10)))[:2]
    print("\n" + "=" * 70)
    print("A 2 LEGROSSZABB POSZT ÁTÍRÁSA (improve_post + natívsági segédlet)")
    print("=" * 70)
    improvements = []
    for s in worst:
        before_n = s["scores"].get("hungarian_nativeness")
        out = await improve_post(s["content"], s["voice"], s["content_type"],
                                 target_score=8.5, max_iterations=2)
        after = out["final_scores"]
        improvements.append({"voice": s["voice"], "before": s["content"], "after": out["final_post"],
                             "before_scores": s["scores"], "after_scores": after,
                             "before_hunglish": s["hunglish"], "after_hunglish": _hunglish_flags(out["final_post"])})
        print(f"\n── voice={s['voice']} ──")
        print(f"NATÍVSÁG: {before_n}/10 → {after.get('hungarian_nativeness')}/10   "
              f"OVERALL: {s['scores'].get('overall_score')} → {out['final_score']}")
        print(f"\nELŐTTE:\n{s['content']}")
        print(f"\nUTÁNA:\n{out['final_post']}")
        print(f"\nHunglish jargon előtte: {len(s['hunglish'])} → utána: {len(_hunglish_flags(out['final_post']))}")

    _write_html(scored, improvements)
    print(f"\nHTML riport: {PROJECT_ROOT / 'data' / 'hunglish_report.html'}")
    return 0


def _esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def _write_html(scored: list[dict], improvements: list[dict]) -> None:
    rows = ""
    for s in scored:
        hl = "".join(f"<li>{_esc(h)}</li>" for h in s["hunglish"]) or "<li>—</li>"
        rows += (f"<tr><td>{s['voice']}</td><td class='n'>{s['scores'].get('hungarian_nativeness')}</td>"
                 f"<td class='n'>{s['scores'].get('overall_score')}</td>"
                 f"<td>{_esc(s['content'][:160])}…</td><td><ul>{hl}</ul></td></tr>")
    cards = ""
    for im in improvements:
        cards += (
            f"<div class='card'><h3>{im['voice']} — natívság "
            f"{im['before_scores'].get('hungarian_nativeness')} → {im['after_scores'].get('hungarian_nativeness')}"
            f" | jargon {len(im['before_hunglish'])} → {len(im['after_hunglish'])}</h3>"
            f"<div class='ba'><div><b>ELŐTTE</b><p>{_esc(im['before'])}</p></div>"
            f"<div><b>UTÁNA</b><p>{_esc(im['after'])}</p></div></div></div>")
    html = (
        "<!doctype html><meta charset='utf-8'><title>Hunglish riport</title>"
        "<style>body{background:#04060a;color:#cdd5de;font-family:Inter,system-ui,sans-serif;padding:32px}"
        "table{border-collapse:collapse;width:100%;margin-bottom:32px}td,th{border:1px solid #1b2430;padding:8px;"
        "vertical-align:top;font-size:14px}.n{text-align:center;font-weight:700;color:#2dd4bf}"
        ".card{background:#0b0f16;border:1px solid #1b2430;border-radius:12px;padding:16px;margin-bottom:16px}"
        ".ba{display:flex;gap:20px}.ba>div{flex:1}.ba p{white-space:pre-wrap;font-size:14px;line-height:1.5}"
        "b{color:#2dd4bf}ul{margin:0;padding-left:16px;font-size:13px}h1{font-weight:800}</style>"
        "<h1>Hunglish natívság-riport (Phase 13.5)</h1>"
        "<h2>Pontozás</h2><table><tr><th>voice</th><th>nativeness</th><th>overall</th><th>szöveg</th>"
        f"<th>talált jargon</th></tr>{rows}</table>"
        f"<h2>Átírás — a 2 legrosszabb</h2>{cards}"
    )
    (PROJECT_ROOT / "data" / "hunglish_report.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
