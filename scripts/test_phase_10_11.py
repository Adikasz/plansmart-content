"""Phase 10 + 11 integrációs teszt — ütemező, reggeli poszt, breaking news, HU vizuálok.

Mit mutat:
  1. APScheduler: a 4 job ütemezése + a következő 24 óra futásai.
  2. extract_visual_text példák (magyar vizuál-szöveg kinyerés).
  3. Reggeli poszt szimuláció — 3 hang, content_strategy típus + hook + magyar vizuál.
  4. Breaking news szimuláció — Ádám, 🚨 prefix, hook bias (C/B), magyar vizuál.
  5. Health végpont válasz.

Alapból NEM küld Telegramra (csak megmutatja a célcsatornát). Valódi képeket generál
(Muapi); a --no-image kihagyja. A --send ténylegesen kiküldi (morning→POSTS, breaking→REACTIONS).

    python -m scripts.test_phase_10_11               # generál + képek, nem küld
    python -m scripts.test_phase_10_11 --no-image    # kép nélkül (gyors)
    python -m scripts.test_phase_10_11 --send        # Telegramra is kiküldi
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from src.generators.base_generator import generate as generate_post
from src.optimization.linkedin_optimizer import optimize_for_linkedin
from src.storage.models import FeedItem, make_id
from src.strategy import content_strategy
from src.visuals import muapi_client
from src.visuals import visual_generator as vg
from src.workers import breaking_news_worker as bnw
from src.workers import health
from src.workers import main as orch
from src.workers.generator_worker import GENERATORS, VOICE_PROMPTS
from src.workers.morning_post_worker import hungarian_date

logger = logging.getLogger("test_phase_10_11")
load_dotenv(override=False)

HR = "=" * 80
SUB = "-" * 80
MORNING_ACCOUNTS = ["david", "adam", "plansmart"]


def _linkedin(result: dict) -> tuple[str, list[str]]:
    li = result.get("linkedin") or {}
    return (li.get("content", ""), li.get("hashtags") or [])


async def _make_visual(voice: str, content: str, hashtags: list[str], make_image: bool) -> dict:
    """Magyar vizuál-szöveg kinyerés + Muapi prompt (+ opcionálisan valódi kép)."""
    post = {"voice": voice, "content": content, "hashtags": hashtags}
    vtext = await vg.extract_visual_text(f"{content}\n\n{' '.join(hashtags)}")
    vprompt = await vg.write_muapi_prompt(post, visual_text=vtext)
    out = {"visual_text": vtext, "visual_prompt": vprompt, "image_url": None, "cost": 0.0}
    if make_image:
        res = await muapi_client.generate(vprompt, model=muapi_client.DEFAULT_MODEL, aspect_ratio="1:1")
        out["image_url"], out["cost"] = res.image_url, res.cost_usd
    return out


# ── 1. Ütemező ─────────────────────────────────────────────────────────
def section_scheduler() -> None:
    print(f"\n{HR}\n  1) APSCHEDULER — ütemezés + következő 24 óra\n{HR}")
    orch._print_schedule()
    print(f"\n  Következő 24 óra futásai (időrendben):\n{SUB}")
    for job_id, when in orch._next_24h_runs():
        print(f"   {when:<24} → {job_id}")


# ── 2. extract_visual_text példák ──────────────────────────────────────
async def section_extract_examples() -> None:
    print(f"\n{HR}\n  2) extract_visual_text — magyar vizuál-szöveg kinyerés\n{HR}")
    samples = [
        "Megtanultad a Claude-ot. Mi jön utána?",
        "73% a magyar KKV-knak heti 4+ órát ismétlődő manuális munkával tölt, "
        "miközben ennek a fele automatizálható lenne.",
    ]
    results = await asyncio.gather(*(vg.extract_visual_text(s) for s in samples))
    for src, vt in zip(samples, results):
        print(f"\n  POSZT: {src[:70]}{'…' if len(src) > 70 else ''}")
        print(f"    main_text: {vt['main_text']!r}")
        print(f"    sub_text : {vt['sub_text']!r}")
        print(f"    stat     : {vt['stat']!r}")


# ── 3. Reggeli poszt szimuláció ────────────────────────────────────────
async def _morning_one(account: str, make_image: bool) -> dict:
    ctype = content_strategy.next_content_type(account, {})  # üres hét → legnagyobb target
    seed = content_strategy.get_seed(ctype, account, set())
    resolved = ctype
    fallback = False
    if seed is None:  # pl. ai_news → seed nincs → educational fallback
        seed = content_strategy.get_seed("educational", account, set())
        resolved, fallback = "educational", True
    if seed is None:
        return {"account": account, "skipped": True}

    result = await generate_post(seed, VOICE_PROMPTS[account])
    if not result:
        return {"account": account, "skipped": True}
    raw, tags = _linkedin(result)
    diag = await optimize_for_linkedin(raw, account, resolved, {"seed": seed.get("seed_key")})
    vis = await _make_visual(account, diag["final_post"], tags, make_image)
    return {
        "account": account, "skipped": False, "recommended": ctype, "resolved": resolved,
        "fallback": fallback, "seed_key": seed.get("seed_key"), "diag": diag, "vis": vis,
    }


async def section_morning(make_image: bool) -> list[dict]:
    print(f"\n{HR}\n  3) REGGELI POSZT — szimuláció (☀️ {hungarian_date()})\n{HR}")
    print(f"  Célcsatorna: TELEGRAM_POSTS_CHAT_ID\n")
    results = await asyncio.gather(*(_morning_one(a, make_image) for a in MORNING_ACCOUNTS))
    for r in results:
        acc = r["account"].upper()
        print(f"\n{SUB}\n  ☀️ {acc}")
        if r["skipped"]:
            print("    (nincs használható tartalom)")
            continue
        fb = "  (fallback→educational)" if r["fallback"] else ""
        print(f"    ajánlott típus: {r['recommended']} → felhasznált: {r['resolved']}{fb}  | seed: {r['seed_key']}")
        d, v = r["diag"], r["vis"]
        print(f"    hook: {d['hook_type'] or '—'} ({d['hook_score']}/10) | szerkezet: {d['structure_score']}/10 "
              f"| {d['character_count']} kar | tier: {d['estimated_engagement_tier'].upper()}")
        print(f"\n    HOOK (poszt első sora): {d['final_post'].splitlines()[0]}")
        print(f"\n    MAGYAR VIZUÁL SZÖVEG:")
        print(f"      main_text: {v['visual_text']['main_text']!r}")
        print(f"      sub_text : {v['visual_text']['sub_text']!r}")
        print(f"      stat     : {v['visual_text']['stat']!r}")
        print(f"    VIZUÁL PROMPT:\n      {v['visual_prompt'].replace(chr(10), chr(10)+'      ')}")
        print(f"    KÉP: {v['image_url'] or '(kihagyva --no-image)'}"
              + (f"  (${v['cost']:.4f})" if v['image_url'] else ""))
    return results


# ── 4. Breaking news szimuláció ────────────────────────────────────────
async def section_breaking(make_image: bool) -> dict:
    print(f"\n{HR}\n  4) BREAKING NEWS — szimuláció (Ádám, 🚨 prefix, hook bias C/B)\n{HR}")
    print(f"  Célcsatorna: TELEGRAM_REACTIONS_CHAT_ID\n")

    url = "https://example.com/openai-breaking-test"
    row = {
        "id": make_id(url), "source_name": "TechCrunch AI", "source_priority": 1,
        "url": url, "title": "OpenAI launches GPT-5.5 with agentic reasoning",
        "content": ("OpenAI announced GPT-5.5, a major step in autonomous agents. "
                    "Anthropic and Google are expected to respond. The model reasons over "
                    "long-horizon tasks and plans tool use natively."),
        "published_at": None, "fetched_at": bnw.datetime.now(bnw.timezone.utc).isoformat(),
        "score": 9, "topics": ["AI", "OpenAI", "agents"],
    }
    # Kritérium-ellenőrzés (a worker logikájával).
    now = bnw.datetime.now(bnw.timezone.utc)
    print(f"  Kritériumok: score={row['score']}>=8 ✓ | kulcsszó={bnw._has_keyword(row)} ✓ | "
          f"friss(4h)={bnw._is_fresh(row, now)} ✓")
    print(f"  Prefix:\n    " + bnw._breaking_prefix(row).replace("\n", "\n    ").rstrip())

    item = FeedItem(**{k: row[k] for k in
                       ("id", "source_name", "source_priority", "url", "title", "content",
                        "published_at", "fetched_at", "score")}, tags=row["topics"])
    result = await GENERATORS["adam"](item)
    if not result:
        print("\n  Ádám skip-elte a hírt (nem várt).")
        return {"skipped": True}
    raw, tags = _linkedin(result)
    diag = await optimize_for_linkedin(raw, "adam", "ai_news",
                                       {"title": item.title, "source": item.source_name},
                                       hook_bias=bnw.BREAKING_HOOK_BIAS)
    vis = await _make_visual("adam", diag["final_post"], tags, make_image)

    print(f"\n  hook: {diag['hook_type'] or '—'} ({diag['hook_score']}/10) | szerkezet: {diag['structure_score']}/10 "
          f"| {diag['character_count']} kar | tier: {diag['estimated_engagement_tier'].upper()}")
    print(f"\n  TELJES ÜZENET (ahogy a reactions csatornára menne):\n{SUB}")
    print(bnw._breaking_prefix(row) + diag["final_post"])
    print(SUB)
    print(f"  MAGYAR VIZUÁL SZÖVEG: main={vis['visual_text']['main_text']!r} | "
          f"sub={vis['visual_text']['sub_text']!r} | stat={vis['visual_text']['stat']!r}")
    print(f"  KÉP: {vis['image_url'] or '(kihagyva --no-image)'}"
          + (f"  (${vis['cost']:.4f})" if vis['image_url'] else ""))
    return {"skipped": False, "diag": diag, "vis": vis, "row": row}


# ── 5. Health végpont ──────────────────────────────────────────────────
async def section_health() -> None:
    print(f"\n{HR}\n  5) HEALTH végpont\n{HR}")
    health.STATE["scheduler_running"] = True
    health.record_run("collector")
    req = None
    resp_health = await health.handle_health(req)
    resp_status = await health.handle_status(req)
    print(f"  GET /health → {resp_health.status} {resp_health.text}")
    print(f"  GET /status → {resp_status.status}")
    print(f"    {resp_status.text}")


async def _maybe_send(morning: list[dict], breaking: dict) -> None:
    from src.workers.morning_post_worker import hungarian_date
    bot = orch.tb.get_bot()
    header = f"☀️ Reggeli poszt — {hungarian_date()}\n\n"
    for r in morning:
        if r.get("skipped"):
            continue
        post = {"voice": r["account"], "platform": "linkedin", "content": r["diag"]["final_post"],
                "hashtags": [], "visual_url": r["vis"]["image_url"], "score": None, "feed_item_url": ""}
        await orch.tb.send_for_approval(post, orch.tb.POSTS_CHAT_ID, bot, header=header)
    if not breaking.get("skipped"):
        post = {"voice": "adam", "platform": "linkedin", "content": breaking["diag"]["final_post"],
                "hashtags": [], "visual_url": breaking["vis"]["image_url"], "score": 9, "feed_item_url": ""}
        await orch.tb.send_for_approval(post, orch.tb.REACTIONS_CHAT_ID, bot,
                                        header=bnw._breaking_prefix(breaking["row"]))
    await bot.session.close()
    print("\n  ✓ Telegramra kiküldve (morning→POSTS, breaking→REACTIONS).")


async def amain(make_image: bool, send: bool) -> int:
    section_scheduler()
    await section_extract_examples()
    morning = await section_morning(make_image)
    breaking = await section_breaking(make_image)
    await section_health()

    total_cost = sum(r["vis"]["cost"] for r in morning if not r.get("skipped"))
    total_cost += breaking["vis"]["cost"] if not breaking.get("skipped") else 0.0
    n_img = sum(1 for r in morning if not r.get("skipped") and r["vis"]["image_url"])
    n_img += 1 if (not breaking.get("skipped") and breaking["vis"]["image_url"]) else 0
    print(f"\n{HR}\n  ÖSSZEGZÉS\n{HR}")
    print(f"  Reggeli posztok: {sum(1 for r in morning if not r.get('skipped'))}/3 | breaking: "
          f"{0 if breaking.get('skipped') else 1}")
    print(f"  Generált képek: {n_img} | Muapi költség: ${total_cost:.4f}")

    if send:
        await _maybe_send(morning, breaking)
    else:
        print("\n  (Telegram küldés kihagyva — add hozzá a --send flaget az élő kiküldéshez.)")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="Phase 10+11 integrációs teszt.")
    ap.add_argument("--no-image", action="store_true", help="ne generáljon Muapi képet (gyors)")
    ap.add_argument("--send", action="store_true", help="ténylegesen küldjön Telegramra")
    args = ap.parse_args()

    return asyncio.run(amain(make_image=not args.no_image, send=args.send))


if __name__ == "__main__":
    raise SystemExit(main())
