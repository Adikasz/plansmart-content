"""Generator worker — filtered hírekből voice-specifikus posztok + Telegram approval.

A 'filtered' (score>=threshold) hírekből generál posztokat a GENERATION_VOICES
hangokra (alapból david + adam), vizuált próbál (Muapi — graceful fail), és
elküldi a Telegram approval csatornára. Ciklusonként max MAX_POSTS_PER_RUN posztot.

A main.py a collector után 1 órával futtatja. Önállóan is fut:
    python -m src.workers.generator_worker
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from src.bots import telegram_bot as tb
from src.config.settings import get_settings
from src.generators.adam_generator import generate_adam
from src.generators.base_generator import generate as generate_post
from src.generators.david_generator import generate_david
from src.generators.plansmart_generator import generate_plansmart
from src.optimization.linkedin_optimizer import optimize_for_linkedin
from src.storage import feed_items as feed_store
from src.storage import posts as posts_store
from src.storage.db import get_client, has_service_key
from src.strategy import content_strategy
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

GENERATION_VOICES = get_settings().generation_voices
MAX_POSTS_PER_RUN = get_settings().max_posts_per_run
ITEM_LIMIT = get_settings().generator_item_limit
EDUCATIONAL_REUSE_DAYS = 30  # ennyi napon belül nem ismételünk educational topicot

QUALITY_EVAL_EVERY = get_settings().quality_eval_every  # N generált posztonként vizuál-eval
_gen_counter = 0
_quality_running = False

GENERATORS = {"david": generate_david, "adam": generate_adam, "plansmart": generate_plansmart}
VOICE_PROMPTS = {
    "david": "prompts/voice_david.md",
    "adam": "prompts/voice_adam.md",
    "plansmart": "prompts/voice_plansmart.md",
}


async def _optimize_post(post: dict, content_type: str, hook_bias: list[str] | None = None) -> dict:
    """LinkedIn-optimalizálás (Phase 7.7) a poszton — graceful degradation.

    A nyers tartalmat átstrukturálja a 2026 keretrendszerrel: a final_post lesz a poszt
    élő tartalma (content), a nyers a metadata.raw_content-be kerül, a diagnosztika az
    oszlopokba. LinkedIn platformra fut; X-re (twitter) kihagyjuk. Hiba → nyers marad.

    hook_bias: opcionális preferált hook típusok (pl. ["B","C"] breaking newshoz).
    """
    if post.get("platform") != "linkedin":
        return post
    raw = (post.get("content") or "").strip()
    if not raw:
        return post
    diag = await optimize_for_linkedin(
        raw_post=raw, voice=post.get("voice", ""), content_type=content_type,
        topic_context={"title": post.get("title"), "url": post.get("feed_item_url")},
        hook_bias=hook_bias,
    )
    post["raw_content"] = raw
    post["content"] = diag["final_post"]        # az optimalizált megy élesre + Telegramra
    post["final_content"] = diag["final_post"]
    post["hook_type"] = diag["hook_type"]
    post["hook_score"] = diag["hook_score"]
    post["structure_score"] = diag["structure_score"]
    post["estimated_engagement_tier"] = diag["estimated_engagement_tier"]
    post["optimizer_warnings"] = diag["warnings"]
    logger.info(
        "[optimizer] %s/%s hook=%s (%d/10) szerkezet=%d/10 tier=%s %d kar | %d warning",
        post.get("voice"), content_type, diag["hook_type"] or "—", diag["hook_score"],
        diag["structure_score"], diag["estimated_engagement_tier"],
        diag["character_count"], len(diag["warnings"]),
    )
    return post


async def _run_quality_bg() -> None:
    """Háttér vizuál-minőség ellenőrzés (Phase 12) — nem blokkolja a generálást."""
    global _quality_running
    if _quality_running:
        return
    _quality_running = True
    try:
        from src.optimization.quality_monitor import run_continuous_quality_check
        from src.optimization.text_quality_monitor import run_continuous_text_check

        await run_continuous_quality_check()   # vizuál minőség (Phase 12)
        await run_continuous_text_check()      # szöveg minőség (Phase 13)
    except Exception as exc:
        logger.warning("[quality] háttér-ellenőrzés hiba: %s", str(exc)[:100])
    finally:
        _quality_running = False


def _bump_and_maybe_quality() -> None:
    """Minden kiküldött poszt után: QUALITY_EVAL_EVERY-enként vizuál-eval (fire-and-forget)."""
    global _gen_counter
    _gen_counter += 1
    if QUALITY_EVAL_EVERY > 0 and _gen_counter % QUALITY_EVAL_EVERY == 0:
        try:
            asyncio.create_task(_run_quality_bg())
        except RuntimeError:
            pass  # nincs futó event loop (pl. szinkron teszt) — kihagyjuk


def _today_start_iso() -> str:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _week_start_iso() -> str:
    """A hét (hétfő) kezdete UTC-ben."""
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def run_generator_cycle(
    dry_run: bool = False,
    send: bool = True,
    max_posts: int = MAX_POSTS_PER_RUN,
    voices: list[str] | None = None,
    item_limit: int = ITEM_LIMIT,
    rows: list[dict] | None = None,
) -> dict:
    """Egy generátor ciklus.

    dry_run=True: generál (valós Claude), de NEM ír DB-t, NEM küld Telegramra.
    send=False:   ír DB-t, de NEM küld Telegramra (pl. lokális teszt).
    rows:         explicit feed_items sorok (pl. a filter dry-run kimenete); ha None,
                  a 'filtered' sorokat kéri le a DB-ből.
    """
    voices = voices or GENERATION_VOICES
    today = _today_start_iso()
    client = get_client(use_service_key=has_service_key())

    if rows is None:
        rows = feed_store.get_for_generation(limit=item_limit, client=client)
    generated: list[dict] = []
    skipped_existing, skipped_voice, errors = 0, 0, 0
    sent = 0
    bot = None

    for row in rows:
        if sent >= max_posts:
            break
        item = feed_store.row_to_item(row)
        produced_any = False

        for voice in voices:
            if sent >= max_posts:
                break
            gen = GENERATORS.get(voice)
            if gen is None:
                continue
            # Skip, ha ma már generáltunk ehhez a hírhez ehhez a hanghoz.
            if posts_store.has_recent_post(item.id, voice, today, client=client):
                skipped_existing += 1
                continue
            try:
                result = await gen(item)
            except Exception as exc:
                errors += 1
                logger.warning("[generator] %s/%s hiba: %s", voice, item.id, str(exc)[:90])
                continue
            if not result:  # a hang skip-elte ezt a hírt
                skipped_voice += 1
                continue

            post = tb._result_to_post(result, voice, "linkedin")
            post.update({"feed_item_id": item.id, "score": item.score,
                         "feed_item_url": item.url, "title": item.title})
            if not (post.get("content") or "").strip():
                skipped_voice += 1
                continue

            if dry_run:
                entry = {
                    "voice": voice, "feed_item_id": item.id, "title": item.title,
                    "score": item.score, "preview": post["content"][:140].replace("\n", " "),
                }
                generated.append(entry)
                sent += 1
                produced_any = True
                continue

            post = await _optimize_post(post, "ai_news")
            entry = {
                "voice": voice, "feed_item_id": item.id, "title": item.title,
                "score": item.score, "preview": post["content"][:140].replace("\n", " "),
                "hook_type": post.get("hook_type"), "tier": post.get("estimated_engagement_tier"),
            }
            post["id"] = posts_store.insert_post(post)
            await tb._attach_visual(post)  # Muapi blokk esetén csak warn, kép nélkül megy
            _bump_and_maybe_quality()
            if send:
                bot = bot or tb.get_bot()
                await tb.send_for_approval(post, tb.POSTS_CHAT_ID, bot)
            entry["post_id"] = post["id"]
            entry["visual"] = bool(post.get("visual_url"))
            generated.append(entry)
            sent += 1
            produced_any = True

        if produced_any and not dry_run:
            feed_store.mark_generated(item.id, client=client)

    summary = {
        "candidates": len(rows), "generated": len(generated), "sent_to_telegram": (0 if dry_run or not send else sent),
        "skipped_existing": skipped_existing, "skipped_voice_skip": skipped_voice, "errors": errors,
        "voices": voices, "max_posts": max_posts, "dry_run": dry_run, "posts": generated,
    }
    logger.info(
        "[generator]%s %d jelölt | %d poszt generálva | %d már megvolt | %d voice-skip | %d hiba",
        " [DRY]" if dry_run else "", len(rows), len(generated), skipped_existing, skipped_voice, errors,
    )
    return summary


# ── Stratégia-vezérelt ciklus (content_strategy) ───────────────────────
def _news_pool(client) -> list:
    """Generálásra váró 'filtered' hírek FeedItem listája (ai_news forrás)."""
    rows = feed_store.get_for_generation(limit=ITEM_LIMIT, client=client)
    return [feed_store.row_to_item(r) for r in rows]


def _resolve_content(account: str, ctype: str, client, used_news: set[str]):
    """Eldönti a content forrást: (feed_item, seed_instruction, seed_key, resolved_type).

    ai_news → friss feed_item; egyébként seed YAML. Ha a választott típushoz nincs
    elérhető tartalom (pl. minden educational topic elhasznált), ai_news-re esik vissza.
    """
    if ctype != "ai_news":
        since = _days_ago_iso(EDUCATIONAL_REUSE_DAYS) if ctype == "educational" else None
        used = posts_store.used_seed_keys(account, ctype, since, client=client)
        seed = content_strategy.get_seed(ctype, account, used)
        if seed is not None:
            return None, seed, seed["seed_key"], ctype
        logger.info("[strategy] %s: nincs szabad '%s' seed — ai_news fallback", account, ctype)

    today = _today_start_iso()
    for item in _news_pool(client):
        if item.id in used_news:
            continue
        if posts_store.has_recent_post(item.id, account, today, client=client):
            continue
        used_news.add(item.id)
        return item, None, item.id, "ai_news"
    return None, None, None, "ai_news"


async def run_strategy_cycle(
    dry_run: bool = False,
    send: bool = True,
    max_posts: int = MAX_POSTS_PER_RUN,
    accounts: list[str] | None = None,
) -> dict:
    """Stratégia-vezérelt generálás: fiókonként a legnagyobb hiányú content_type.

    Algoritmus (content_strategy.yml alapján):
      1. fiókonként kiszámoljuk a heti tényleges content_type eloszlást,
      2. a target - actual legnagyobb hiányú típust választjuk,
      3. a típushoz tartozó forrásból (feed_items / seed YAML) generálunk,
      4. elküldjük Telegram approve-ra (dry_run/send szerint).
    """
    accounts = accounts or content_strategy.accounts()
    week_start = _week_start_iso()
    client = get_client(use_service_key=has_service_key())

    generated: list[dict] = []
    plan: list[dict] = []
    used_news: set[str] = set()
    sent = 0
    bot = None

    for account in accounts:
        if sent >= max_posts:
            break
        counts = posts_store.weekly_content_type_counts(account, week_start, client=client)
        weekly_done = sum(counts.values())
        weekly_target = content_strategy.weekly_target(account)
        ctype = content_strategy.next_content_type(account, counts)
        rec = {"account": account, "next_type": ctype, "weekly_done": weekly_done,
               "weekly_target": weekly_target, "status": None}

        if weekly_target and weekly_done >= weekly_target:
            rec["status"] = "weekly_target_reached"
            plan.append(rec)
            continue

        feed_item, seed, seed_key, resolved = _resolve_content(account, ctype, client, used_news)
        rec["resolved_type"] = resolved
        if feed_item is None and seed is None:
            rec["status"] = "no_content_available"
            plan.append(rec)
            continue

        try:
            if feed_item is not None:
                result = await GENERATORS[account](feed_item)
                fi_id, fi_url, score = feed_item.id, feed_item.url, feed_item.score
            else:
                result = await generate_post(seed, VOICE_PROMPTS[account])
                fi_id, fi_url, score = None, "", None
        except Exception as exc:
            rec["status"] = f"error: {str(exc)[:60]}"
            plan.append(rec)
            continue

        if not result:
            rec["status"] = "voice_skip"
            plan.append(rec)
            continue

        post = tb._result_to_post(result, account, "linkedin")
        post.update({
            "feed_item_id": fi_id, "score": score, "feed_item_url": fi_url,
            "strategy_type": resolved, "seed_key": seed_key,
            "title": feed_item.title if feed_item is not None else seed_key,
        })
        if resolved == "workshop_promo":
            post["workshop_mentioned"] = True
        if not (post.get("content") or "").strip():
            rec["status"] = "empty"
            plan.append(rec)
            continue

        rec["status"] = "generated"

        if not dry_run:
            post = await _optimize_post(post, resolved)
            post["id"] = posts_store.insert_post(post)
            await tb._attach_visual(post)
            _bump_and_maybe_quality()
            if send:
                bot = bot or tb.get_bot()
                await tb.send_for_approval(post, tb.POSTS_CHAT_ID, bot)
        entry = {"account": account, "content_type": resolved, "seed_key": seed_key,
                 "preview": post["content"][:140].replace("\n", " "),
                 "hook_type": post.get("hook_type"), "tier": post.get("estimated_engagement_tier")}
        if not dry_run:
            entry["post_id"] = post["id"]
        generated.append(entry)
        sent += 1
        plan.append(rec)

    summary = {
        "generated": len(generated), "sent_to_telegram": (0 if dry_run or not send else sent),
        "plan": plan, "posts": generated, "dry_run": dry_run,
    }
    logger.info("[strategy]%s %d poszt generálva | terv: %s",
                " [DRY]" if dry_run else "", len(generated),
                ", ".join(f"{p['account']}→{p.get('resolved_type', p['next_type'])}({p['status']})" for p in plan))
    return summary


def main() -> int:
    setup_logging()
    asyncio.run(run_strategy_cycle())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
