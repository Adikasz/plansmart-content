"""Phase 7.6 teljes flow teszt — 1 ÁLTALÁNOS feed item → 3 voice poszt → 3 vizuál.

Olyan hírt választ, amit MIND a 3 hang tud használni (nem csak fejlesztői tartalom):
  score >= 7  ÉS  a topic/cím tartalmaz "business" / "strategy" / "automation" jelet.
Több jelöltet végigpróbál, és azt választja, amelyikre mind a 3 hang generál.

Lépések:
  1. plansmart.live font scrape (live).
  2. Általános, magas score-ú feed_item kiválasztása.
  3. Mind a 3 hang posztja (Dávid / Ádám / PlanSmart).
  4. Mindháromhoz Muapi prompt (Claude Sonnet) + Muapi kép.
  5. visual_url + Muapi költség mentése (costs tábla), összköltség.
  6. (Opcionális) Telegram approval küldés.
  7. data/visual_preview.html — a 3 vizuál egymás mellett.

Futtatás:
    python scripts/test_visual.py                 # generál + HTML preview
    python scripts/test_visual.py --telegram      # + elküldi Telegramra
    python scripts/test_visual.py --no-image      # csak a 3 promptot mutatja (kép nélkül)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))  # a test_voice rangsoroló importjához

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from scrape_brand_font import scrape as scrape_font  # noqa: E402
from test_voice import _rank_candidates  # noqa: E402  (score>=7 jelöltek, on-the-fly pontozással)
from src.ai.generators.adam_generator import generate_adam  # noqa: E402
from src.ai.generators.base_generator import generate as generate_post  # noqa: E402
from src.ai.generators.david_generator import generate_david  # noqa: E402
from src.ai.generators.plansmart_generator import generate_plansmart  # noqa: E402
from src.core.storage import posts as posts_store  # noqa: E402
from src.core.storage.db import get_client, has_service_key  # noqa: E402
from src.core.strategy import content_strategy  # noqa: E402
from src.integrations.visuals import muapi_client, visual_generator  # noqa: E402

logger = logging.getLogger("test_visual")

VOICES = ["david", "adam", "plansmart"]
GEN = {"david": generate_david, "adam": generate_adam, "plansmart": generate_plansmart}
DISPLAY = {"david": "DÁVID (builder)", "adam": "ÁDÁM (strategist)", "plansmart": "PLANSMART (brand)"}
VOICE_PROMPTS = {"david": "prompts/voice_david.md", "adam": "prompts/voice_adam.md", "plansmart": "prompts/voice_plansmart.md"}
# Ha egy hang skip-eli a hírt (pl. PlanSmart soha nem reagál hírre), a saját content_type-jából generálunk.
FALLBACK_TYPE = {"david": "educational", "adam": "educational", "plansmart": "case_study"}
PREVIEW_PATH = ROOT / "data" / "visual_preview.html"

# Általános (nem tisztán fejlesztői) jelek — ezeket mind a 3 hang tudja kezelni.
GENERAL_TOPICS = {"business", "strategy", "automation"}
MAX_ATTEMPTS = 5  # ennyi jelöltet próbálunk, amíg mind a 3 hang generál


def _is_general(item) -> bool:
    """Topic/cím/tartalom tartalmaz-e business/strategy/automation jelet."""
    tags = {str(t).lower() for t in (item.tags or [])}
    if any(any(k in tag for k in GENERAL_TOPICS) for tag in tags):
        return True
    text = f"{item.title or ''} {item.content or ''}".lower()
    return any(k in text for k in GENERAL_TOPICS)


def _general_candidates(client) -> list:
    """score>=7 jelöltek, az általános (business/strategy/automation) elemeket előre rangsorolva."""
    candidates = _rank_candidates(client)
    general = [c for c in candidates if _is_general(c)]
    rest = [c for c in candidates if c not in general]
    logger.info("Jelöltek: %d összesen, ebből %d általános (business/strategy/automation).",
                len(candidates), len(general))
    return general + rest  # általánosak elöl, de fallback a többire


def _post_dict(p: dict, item) -> dict:
    return {
        "voice": p["voice"], "platform": "linkedin",
        "content": p["content"], "content_type": "post",
        "hashtags": p.get("hashtags") or [],
        "feed_item_id": item.id, "score": item.score, "feed_item_url": item.url,
    }


async def _gen_post(voice: str, item) -> dict:
    """Csak a poszt szöveg generálása (a jelölt-kiválasztáshoz, kép nélkül)."""
    out: dict = {"voice": voice, "content": None, "hashtags": [], "post_id": None,
                 "prompt": None, "image_url": None, "cost_usd": None, "model": None, "error": None}
    try:
        gen = await GEN[voice](item)
    except Exception as exc:
        out["error"] = f"gen hiba: {str(exc)[:80]}"
        return out
    if not gen:
        out["error"] = "skip (a modell szerint nem illik ehhez a hanghoz)"
        return out
    li = gen.get("linkedin") or {}
    if not li.get("content"):
        out["error"] = "nincs LinkedIn tartalom"
        return out
    out["content"] = li["content"]
    out["hashtags"] = li.get("hashtags") or []
    return out


async def _select(candidates: list):
    """Végigpróbál max MAX_ATTEMPTS jelöltet; az elsőt adja vissza, amire mind a 3 hang generál."""
    best = None
    for item in candidates[:MAX_ATTEMPTS]:
        posts = list(await asyncio.gather(*[_gen_post(v, item) for v in VOICES]))
        n_ok = sum(1 for p in posts if p["content"])
        logger.info("  próba: %-22s score=%s ált=%s → %d/3 hang",
                    (item.source_name or "")[:22], item.score, _is_general(item), n_ok)
        if best is None or n_ok > best[2]:
            best = (item, posts, n_ok)
        if n_ok == 3:
            return item, posts
    return best[0], best[1]


async def _fallback_seed_post(voice: str) -> dict | None:
    """Ha a hang skip-elte a hírt: a saját content_type-jából (seed YAML) generálunk."""
    ctype = FALLBACK_TYPE.get(voice, "educational")
    seed = content_strategy.get_seed(ctype, voice, set())
    if seed is None:
        return None
    try:
        gen = await generate_post(seed, VOICE_PROMPTS[voice])
    except Exception as exc:
        logger.warning("[%s] fallback gen hiba: %s", voice, str(exc)[:80])
        return None
    li = (gen or {}).get("linkedin") or {}
    if not li.get("content"):
        return None
    return {
        "content": li["content"], "hashtags": li.get("hashtags") or [],
        "seed_note": f"{ctype} seed: „{seed['seed_key']}” (a hang nem reagál hírre)", "error": None,
    }


async def _add_visual(item, p: dict, make_image: bool) -> dict:
    """Egy poszthoz: DB insert → Muapi prompt → (opcionális) kép + költség mentés."""
    if not p["content"]:
        return p
    post = _post_dict(p, item)
    p["post_id"] = posts_store.insert_post(post)
    post["id"] = p["post_id"]
    p["prompt"] = await visual_generator.write_muapi_prompt(post)
    if not make_image:
        return p
    try:
        res = await muapi_client.generate(p["prompt"], model=muapi_client.DEFAULT_MODEL)
        p["image_url"], p["model"], p["cost_usd"] = res.image_url, res.model, res.cost_usd
        posts_store.save_visual(p["post_id"], res.image_url)
        posts_store.record_cost(p["post_id"], res.model, res.cost_usd, res.request_id)
    except muapi_client.MuapiError as exc:
        p["error"] = f"Muapi: {exc}"
    return p


def _write_preview(item, results: list[dict]) -> None:
    PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    cols = []
    for r in results:
        img = (
            f'<img src="{escape(r["image_url"])}" alt="{r["voice"]}">'
            if r.get("image_url")
            else f'<div class="noimg">{escape(r.get("error") or "nincs kép")}</div>'
        )
        cols.append(
            f'<div class="col"><h2>{escape(DISPLAY[r["voice"]])}</h2>{img}'
            f'<pre class="prompt">{escape(r.get("prompt") or "—")}</pre></div>'
        )
    html = (
        "<!doctype html><meta charset='utf-8'><title>PlanSmart visual preview</title>"
        "<style>body{background:#04060a;color:#e8eaed;font-family:Inter,system-ui,sans-serif;margin:24px}"
        "h1{font-weight:600}.row{display:flex;gap:16px;align-items:flex-start}"
        ".col{flex:1;min-width:0}.col img,.noimg{width:100%;aspect-ratio:1;border-radius:12px;"
        "object-fit:cover;background:#0b0f17;display:flex;align-items:center;justify-content:center}"
        ".noimg{color:#8a93a3;font-size:13px;padding:16px;text-align:center}"
        ".prompt{white-space:pre-wrap;font:12px/1.5 'Fragment Mono',ui-monospace,monospace;"
        "color:#aab2c0;background:#0b0f17;padding:12px;border-radius:8px;margin-top:8px}</style>"
        f"<h1>PlanSmart — 3 voice, 1 feed item</h1><p style='color:#8a93a3'>{escape(item.title or item.url)}</p>"
        f"<div class='row'>{''.join(cols)}</div>"
    )
    PREVIEW_PATH.write_text(html, encoding="utf-8")


async def _send_telegram(item, results: list[dict]) -> None:
    from src.integrations.bots.telegram_bot import POSTS_CHAT_ID, get_bot, send_for_approval

    if not POSTS_CHAT_ID:
        logger.warning("TELEGRAM_POSTS_CHAT_ID nincs beállítva — Telegram küldés kihagyva.")
        return
    bot = get_bot()
    try:
        for r in results:
            if not r.get("content"):
                continue
            post = {
                "id": r["post_id"], "voice": r["voice"], "platform": "linkedin",
                "content": r["content"], "hashtags": r.get("hashtags") or [],
                "feed_item_url": item.url, "score": item.score, "visual_url": r.get("image_url"),
            }
            await send_for_approval(post, POSTS_CHAT_ID, bot)
        logger.info("Telegram: posztok elküldve a(z) %s csatornára.", POSTS_CHAT_ID)
    finally:
        await bot.session.close()


async def run(make_image: bool, telegram: bool) -> int:
    # 1) Font
    logger.info("plansmart.live font scrape ...")
    try:
        font = scrape_font()
        logger.info("FONT: elsődleges=%s | rangsor=%s",
                    font["primary_font"], [f for f, _ in font["families_ranked"]])
    except Exception as exc:
        logger.warning("Font scrape kihagyva: %s", str(exc)[:100])

    # 2) Általános feed item + 3 hang kiválasztása
    client = get_client(use_service_key=has_service_key())
    candidates = _general_candidates(client)
    if not candidates:
        logger.error("Nem találtam score>=7 feed_item-et.")
        return 1
    logger.info("\nJelölt-kiválasztás (mind a 3 hang generáljon):")
    item, results = await _select(candidates)
    logger.info("\nKIVÁLASZTOTT ITEM (score=%s, általános=%s): %s\n  %s",
                item.score, _is_general(item), item.title, item.url)

    # Fallback: amelyik hang skip-elte a hírt (pl. PlanSmart), a saját content_type-jából generál.
    for p in results:
        if not p["content"]:
            logger.info("  [%s] a hír skip-elve — fallback a saját content_type-ra…", p["voice"])
            fb = await _fallback_seed_post(p["voice"])
            if fb:
                p.update(fb)

    # 3-5) Vizuálok (prompt + kép) párhuzamosan
    results = list(await asyncio.gather(*[_add_visual(item, p, make_image) for p in results]))

    # Kimenet
    logger.info("\n%s\n  3 POSZT + VIZUÁL PROMPT + KÉP\n%s", "#" * 70, "#" * 70)
    total_cost = 0.0
    n_images = 0
    for r in results:
        logger.info("\n=== %s ===", DISPLAY[r["voice"]])
        if not r.get("content"):
            logger.info("  (kihagyva: %s)", r.get("error"))
            continue
        if r.get("seed_note"):
            logger.info("[fallback] %s", r["seed_note"])
        logger.info("POSZT:\n%s", r["content"])
        if r.get("hashtags"):
            logger.info("Hashtags: %s", " ".join(r["hashtags"]))
        logger.info("\nVIZUÁL PROMPT:\n%s", r.get("prompt"))
        if r.get("image_url"):
            logger.info("IMAGE : %s  (model=%s, cost=$%s)", r["image_url"], r.get("model"), r.get("cost_usd"))
            total_cost += float(r.get("cost_usd") or 0)
            n_images += 1
        elif r.get("error"):
            logger.info("IMAGE : (hiba) %s", r["error"])

    if make_image:
        logger.info("\n%s\nÖSSZES MUAPI KÖLTSÉG: $%.4f  (%d kép)\n%s", "=" * 70, total_cost, n_images, "=" * 70)

    # 7) Preview + 6) Telegram
    _write_preview(item, results)
    logger.info("Preview (egymás mellett): %s", PREVIEW_PATH)
    try:
        webbrowser.open(PREVIEW_PATH.as_uri())
    except Exception:
        pass
    if telegram:
        await _send_telegram(item, results)
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(
        description="Phase 7.6 teszt — általános hír, 3 hang, kép + összköltség (alapból Telegram nélkül)."
    )
    tg = ap.add_mutually_exclusive_group()
    tg.add_argument("--telegram", action="store_true", help="elküldi a posztokat a Telegram approval csatornára")
    tg.add_argument("--no-telegram", action="store_true", help="(alapértelmezett) nem küld Telegramra")
    ap.add_argument("--no-image", action="store_true", help="csak promptok, Muapi kép nélkül")
    args = ap.parse_args()

    return asyncio.run(run(make_image=not args.no_image, telegram=args.telegram))


if __name__ == "__main__":
    raise SystemExit(main())
