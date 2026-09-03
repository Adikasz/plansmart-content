"""Ádám konszolidált digest worker (Fázis 23) — 2 naponta PONTOSAN EGY üzenet.

Ez váltja fel Ádámnál a három korábbi, egymástól független értesítési utat:
  • morning_post_worker  — napi reggeli poszt a 3-hangú ciklusban
  • breaking_news_worker — ő volt a "news reactor" (napi max 3)
  • video_idea_worker    — heti reakció-videó ötlet

Élesben ez 7 nap alatt 19 poszt + 1 videó-ötlet volt CSAK Ádámnak. Innentől: minden
2. reggel egy üzenet, a köztes időben felgyűlt jelöltek LEGJOBBIKÁBÓL.

Dávid és PlanSmart érintetlen: a kizárás hang-szintű (core/strategy/cadence.py), a
morning/breaking/video worker rájuk változatlanul fut.

Menete:
  1. Ablak: az utolsó SIKERES digest kiküldése óta (missed run ellen robusztus), ha nincs
     ilyen → DIGEST_WINDOW_HOURS; felülről DIGEST_MAX_LOOKBACK_HOURS-ra vágva (egy hosszú
     kiesés ne rántson be hetekkel korábbi, elavult híreket).
  2. Jelöltgyűjtés HÁROM sávból, feed_item_id szerint EGY poolba olvasztva:
       post       — feed_items.get_recent_top (status=filtered, score>=6, még nem használt)
       breaking   — feed_items.get_breaking_candidates + a breaking worker _qualifies()-a
       video_idea — feed_items.get_video_idea_candidates + adam voice_fit
     Egy hír több sávban is szerepelhet — a `kinds` halmaza jelöli, hogy melyekben.
  3. Rangsor: score csökkenő, holtversenyben frissebb (published_at, fallback fetched_at).
  4. A győztes jelöltre a "kind" prioritás dönti el, MILYEN tartalom készül:
       video_idea (ha jár — heti cooldown) > breaking > post
     A videó-ötlet azért elsőbbség, mert a heti ritmusát különben a mindig-jelen-lévő
     post-sáv teljesen kiszorítaná (minden videó-jelölt egyben post-jelölt is).
  5. Ha egy generálás skip-el / üres, a következő kind, majd a következő jelölt jön.
  6. Ha a POOL üres vagy mind kiesett: seed-alapú fallback a content_strategy motorból —
     pontosan az az attempt-lánc, amit a morning_post_worker használ (educational /
     case_study / workshop_promo).
  7. PONTOSAN EGY Telegram üzenet megy ki (POSTS csatorna), majd a futás leáll.

A "2 naponta" NEM páros/páratlan nap-ellenőrzés: a job MINDEN reggel elindul, és maga a
worker őrzi a minimális óraközt (adam_digest_min_hours, alap 47h) az utolsó sikeres
kiküldéstől. Ez év-/hónapforduló-biztos, ÉS egy kimaradt reggel után magától helyreáll
(a következő reggel kiküldi), amit egy fix páros/páratlan kapcsoló nem tudna.

Önálló futtatás (dry-run, nem küld/ír):
    python -m src.core.workers.adam_digest_worker
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from dotenv import load_dotenv

from src.ai.generators.video_idea_generator import generate_video_idea
from src.core.config.settings import get_settings
from src.core.storage import feed_items as feed_store
from src.core.storage import posts as posts_store
from src.core.storage import video_ideas as video_store
from src.core.storage.db import get_client, has_service_key
from src.core.strategy import content_strategy
from src.core.workers import breaking_news_worker as bnw
from src.core.workers import morning_post_worker as mpw
from src.core.workers import video_idea_worker as viw
from src.core.workers.generator_worker import GENERATORS, _optimize_post
from src.integrations.bots import telegram_bot as tb
from src.integrations.bots import video_idea_bot as vb
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

DIGEST_VOICE = "adam"

# Ha nincs korábbi digest, ekkora ablakból gyűjtünk (a 2 napos kadencia természetes ablaka).
DIGEST_WINDOW_HOURS = 48
# Hosszú kiesés (leállás, hibás deploy) után se rántsunk be ennél régebbi hírt.
DIGEST_MAX_LOOKBACK_HOURS = 168  # 7 nap
# Videó-ötlet a digesten belül is heti ritmusú marad.
VIDEO_IDEA_COOLDOWN_DAYS = 7
CANDIDATE_LIMIT = 40
# Hány jelöltig próbálkozunk, ha a generálás skip-el (Claude-hívás/jelölt — költség-korlát).
MAX_GENERATION_ATTEMPTS = 4

KIND_LABEL = {
    "post": "hír-reakció poszt",
    "breaking": "breaking reakció",
    "video_idea": "videó-ötlet",
    "seed": "stratégiai poszt",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    """ISO string → aware UTC datetime (a breaking worker parse-olójával azonos szemantika)."""
    return bnw._parse_dt(value)


def _recency(row: dict[str, Any]) -> datetime:
    """A jelölt frissessége: published_at, fallback fetched_at, fallback nagyon-régi."""
    dt = _parse_dt(row.get("published_at")) or _parse_dt(row.get("fetched_at"))
    return dt or datetime(1970, 1, 1, tzinfo=timezone.utc)


# ── Kadencia-kapu ─────────────────────────────────────────────────────
def last_digest_sent_at(client: Any) -> datetime | None:
    """Az utolsó SIKERES digest-kiküldés ideje — vagy None, ha még sosem ment ki.

    Két forrásból, mert a digest kétféle objektumot hozhat létre:
      • posts sor, `metadata.digest = True` jelölővel (poszt-típusú győztes)
      • video_ideas sor (videó-ötlet győztes) — ennek nincs metadata oszlopa

    Így NEM kell külön állapot-tábla/migráció, és a Railway ephemeral FS sem játszik
    (lásd a Fázis 15 portrait-counter tanulságát: lokális fájl-állapot minden redeploykor
    elveszik). A video_ideas oldali fallback a "biztonságos" irányba téved: egy kézi
    /create_video legfeljebb KÉSLELTETI a következő digestet, sosem okoz dupla kiküldést.
    """
    stamps: list[datetime] = []

    try:
        for row in posts_store.recent_sent_for_voice(DIGEST_VOICE, client=client):
            if (row.get("metadata") or {}).get("digest"):
                dt = _parse_dt(row.get("sent_at"))
                if dt:
                    stamps.append(dt)  # sent_at szerint DESC — az első találat a legfrissebb
                    break
    except Exception as exc:  # noqa: BLE001 — a kapu sosem dönthet crash-sel
        logger.warning("[digest] posts last-sent lekérdezés hiba: %s", str(exc)[:120])

    try:
        dt = _parse_dt(video_store.latest_created_at(DIGEST_VOICE, client=client))
        if dt:
            stamps.append(dt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[digest] video_ideas last-created lekérdezés hiba: %s", str(exc)[:120])

    return max(stamps) if stamps else None


def _window_start(last_sent: datetime | None, now: datetime) -> datetime:
    """A jelölt-ablak kezdete: az utolsó digest óta, de max. DIGEST_MAX_LOOKBACK_HOURS-ig."""
    default = now - timedelta(hours=DIGEST_WINDOW_HOURS)
    floor = now - timedelta(hours=DIGEST_MAX_LOOKBACK_HOURS)
    start = last_sent or default
    return max(start, floor)


# ── Jelöltgyűjtés ─────────────────────────────────────────────────────
def _add(pool: dict[str, dict[str, Any]], row: dict[str, Any], kind: str) -> None:
    entry = pool.get(row["id"])
    if entry is None:
        pool[row["id"]] = {"row": row, "kinds": {kind}}
    else:
        entry["kinds"].add(kind)


def collect_candidates(
    client: Any, since_iso: str, now: datetime, window_hours: int
) -> list[dict[str, Any]]:
    """A három sáv jelöltjei EGY rangsorolt listában (score DESC, majd frissesség DESC)."""
    pool: dict[str, dict[str, Any]] = {}

    for row in feed_store.get_recent_top(
        since_iso, min_score=mpw.MORNING_NEWS_MIN_SCORE, limit=CANDIDATE_LIMIT, client=client
    ):
        _add(pool, row, "post")

    for row in feed_store.get_breaking_candidates(
        min_score=bnw.BREAKING_MIN_SCORE,
        limit=CANDIDATE_LIMIT,
        window_hours=window_hours,
        client=client,
    ):
        if bnw._qualifies(row, now):
            _add(pool, row, "breaking")

    for row in feed_store.get_video_idea_candidates(
        min_score=viw.VIDEO_IDEA_MIN_SCORE,
        window_days=viw.VIDEO_IDEA_WINDOW_DAYS,
        limit=CANDIDATE_LIMIT,
        client=client,
    ):
        if viw._adam_fit(row):
            _add(pool, row, "video_idea")

    candidates = list(pool.values())
    candidates.sort(key=lambda c: ((c["row"].get("score") or 0), _recency(c["row"])), reverse=True)
    return candidates


def _kind_order(kinds: set[str], video_allowed: bool) -> list[str]:
    """Melyik tartalom-típust próbáljuk erre a jelöltre, milyen sorrendben."""
    order: list[str] = []
    if "video_idea" in kinds and video_allowed:
        order.append("video_idea")
    if "breaking" in kinds:
        order.append("breaking")
    if "post" in kinds:
        order.append("post")
    return order


def _video_idea_allowed(client: Any, now: datetime) -> bool:
    """Jár-e videó-ötlet? (a heti kadenciát a digesten belül is megtartjuk)."""
    try:
        last = _parse_dt(video_store.latest_created_at(DIGEST_VOICE, client=client))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[digest] videó-cooldown lekérdezés hiba: %s", str(exc)[:120])
        return False
    if last is None:
        return True
    return (now - last) >= timedelta(days=VIDEO_IDEA_COOLDOWN_DAYS)


# ── Tartalom-előállítás jelöltenként/típusonként ──────────────────────
async def _build_post(row: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """Poszt-típusú (post | breaking) tartalom a meglévő generátorokkal — vagy None, ha skip."""
    item = feed_store.row_to_item(row)
    result = await GENERATORS[DIGEST_VOICE](item)
    if not result:
        logger.info("[digest] a generátor skip-elte (%s): %s", kind, (row.get("title") or "")[:60])
        return None

    post = tb._result_to_post(result, DIGEST_VOICE, "linkedin")
    post.update(
        {
            "feed_item_id": item.id,
            "score": item.score,
            "feed_item_url": item.url,
            "title": item.title,
            "strategy_type": "ai_news",
        }
    )
    if kind == "breaking":
        post["is_breaking"] = True
    if not (post.get("content") or "").strip():
        return None

    hook_bias = bnw.BREAKING_HOOK_BIAS if kind == "breaking" else None
    return await _optimize_post(post, "ai_news", hook_bias=hook_bias)


async def _build_seed_post(client: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Fallback: seed-alapú poszt a content_strategy motorból (a morning worker láncával).

    Visszaad (post, resolved_type). Ugyanaz az attempt-sorrend, amit Ádám a reggeli
    ciklusban kapott volna — csak a hír-ág nélkül (azt már a pool lefedte).
    """
    counts = posts_store.weekly_content_type_counts(
        DIGEST_VOICE, mpw._week_start_iso(), client=client
    )
    ctype = content_strategy.next_content_type(DIGEST_VOICE, counts)
    attempts = [a for a in mpw._build_attempts(DIGEST_VOICE, ctype, None, client) if a[0] == "seed"]
    result, resolved, seed_key, _item = await mpw._generate_from_attempts(DIGEST_VOICE, attempts)
    if not result:
        return None, None

    post = tb._result_to_post(result, DIGEST_VOICE, "linkedin")
    post.update({"title": seed_key, "strategy_type": resolved, "seed_key": seed_key})
    if resolved == "workshop_promo":
        post["workshop_mentioned"] = True
    if not (post.get("content") or "").strip():
        return None, None
    post = await _optimize_post(post, cast(str, resolved))
    return post, resolved


# ── Kiküldés ──────────────────────────────────────────────────────────
def _header(kind: str, row: dict[str, Any] | None) -> str:
    label = KIND_LABEL.get(kind, kind)
    lines = [f"📬 Ádám digest — {mpw.hungarian_date()}", f"🏷️ Típus: {label}"]
    if row is not None:
        lines.append(f"📰 Forrás: {row.get('source_name') or 'ismeretlen forrás'}")
    return "\n".join(lines) + "\n\n"


async def _send_post(
    post: dict[str, Any], kind: str, row: dict[str, Any] | None, client: Any, send: bool
) -> dict[str, Any]:
    """Poszt-típusú digest kiküldése — pontosan EGY Telegram üzenet a POSTS csatornára."""
    metadata = dict(post.get("metadata") or {})
    metadata.update({"digest": True, "digest_kind": kind})
    post["metadata"] = metadata

    post["sent_at"] = posts_store._now()
    post["id"] = posts_store.insert_post(post, client=client)
    await tb._attach_visual(post)
    if send:
        await tb.send_for_approval(post, tb.POSTS_CHAT_ID, header=_header(kind, row))
        posts_store.mark_sent(post["id"], post["sent_at"], client=client)
    if row is not None:
        feed_store.mark_generated(row["id"], client=client)
        if kind == "breaking":
            feed_store.mark_breaking(row["id"], client=client)
    return {"post_id": post["id"], "visual_url": post.get("visual_url")}


async def _send_video_idea(
    idea: dict[str, Any], row: dict[str, Any], client: Any, send: bool
) -> dict[str, Any] | None:
    idea["feed_item_id"] = row["id"]
    idea["voice"] = DIGEST_VOICE
    try:
        idea_id = video_store.insert_video_idea(idea, client=client)
    except video_store.DuplicateVideoIdeaError:
        # Verseny-helyzet: a Claude-hívás alatt (pl. egy egyidejű /create_video) létrejött
        # ugyanerre a hírre egy ötlet. Nem hiba — csak nincs mit kiküldeni.
        logger.info("[digest] már volt videó-ötlet ehhez a hírhez, kihagyva: %s", row["id"])
        return None
    if send:
        await vb.send_video_idea_for_approval(idea_id, idea, row)
    return {"video_id": idea_id}


# ── Fő ciklus ─────────────────────────────────────────────────────────
async def run_adam_digest(
    dry_run: bool = False, send: bool = True, force: bool = False
) -> dict[str, Any]:
    """Egy digest-ciklus. PONTOSAN EGY üzenetet küld ki, vagy egyet sem.

    dry_run=True: generál (valódi Claude-hívás), de NEM ír DB-t, NEM küld Telegramra.
    force=True: átlépi a minimális óraköz kapuját (kézi teszt / /adam_digest).
    """
    settings = get_settings()
    if not settings.adam_digest_enabled:
        logger.info("[digest] kikapcsolva (ADAM_DIGEST_ENABLED=false).")
        return {"enabled": False, "sent": 0, "skipped_reason": "disabled"}

    client = get_client(use_service_key=has_service_key())
    now = _now_utc()

    last_sent = last_digest_sent_at(client)
    hours_since = (now - last_sent).total_seconds() / 3600 if last_sent else None
    if not force and hours_since is not None and hours_since < settings.adam_digest_min_hours:
        logger.info(
            "[digest] kihagyva — %.1f órája ment az előző digest (< %d h).",
            hours_since,
            settings.adam_digest_min_hours,
        )
        return {
            "enabled": True,
            "sent": 0,
            "skipped_reason": "too_soon",
            "hours_since_last": round(hours_since, 1),
            "last_sent_at": last_sent.isoformat() if last_sent else None,
        }

    since = _window_start(last_sent, now)
    window_hours = max(1, int((now - since).total_seconds() // 3600))
    candidates = collect_candidates(client, since.isoformat(), now, window_hours)
    video_allowed = _video_idea_allowed(client, now)
    logger.info(
        "[digest] ablak: %s (%dh) | %d jelölt | videó-ötlet %s",
        since.strftime("%Y-%m-%d %H:%M"),
        window_hours,
        len(candidates),
        "engedélyezve" if video_allowed else "cooldownban",
    )

    ranked = [
        {
            "feed_item_id": c["row"]["id"],
            "title": c["row"].get("title"),
            "score": c["row"].get("score"),
            "kinds": sorted(c["kinds"]),
            "published_at": c["row"].get("published_at") or c["row"].get("fetched_at"),
        }
        for c in candidates[:10]
    ]

    attempts = 0
    for cand in candidates:
        if attempts >= MAX_GENERATION_ATTEMPTS:
            logger.info("[digest] generálási próbálkozás-limit (%d) elérve.", attempts)
            break
        row = cand["row"]
        for kind in _kind_order(cand["kinds"], video_allowed):
            if attempts >= MAX_GENERATION_ATTEMPTS:
                break
            # Dedup: ne készítsünk másodszor tartalmat ugyanahhoz a hírhez.
            if not dry_run:
                if kind == "video_idea":
                    if video_store.has_video_idea_for_feed_item(row["id"], client=client):
                        continue
                elif posts_store.has_post_for_feed_item(row["id"], client=client):
                    continue
            attempts += 1

            if kind == "video_idea":
                idea = await generate_video_idea_safe(row)
                if not idea:
                    continue
                winner = {
                    "kind": kind,
                    "feed_item_id": row["id"],
                    "title": row.get("title"),
                    "score": row.get("score"),
                    "hook": idea.get("hook"),
                }
                if not dry_run:
                    sent_info = await _send_video_idea(idea, row, client, send)
                    if sent_info is None:
                        continue
                    winner.update(sent_info)
                return _summary(since, candidates, ranked, winner, dry_run, send, last_sent)

            post = await _build_post(row, kind)
            if post is None:
                continue
            winner = {
                "kind": kind,
                "feed_item_id": row["id"],
                "title": row.get("title"),
                "score": row.get("score"),
                "hook_type": post.get("hook_type"),
                "tier": post.get("estimated_engagement_tier"),
                "content": post.get("content"),
            }
            if not dry_run:
                winner.update(await _send_post(post, kind, row, client, send))
            return _summary(since, candidates, ranked, winner, dry_run, send, last_sent)

    # A pool nem hozott ki semmit → seed-alapú stratégiai poszt (mint a reggeli fallback).
    post, resolved = await _build_seed_post(client)
    if post is not None:
        winner = {
            "kind": "seed",
            "strategy_type": resolved,
            "seed_key": post.get("seed_key"),
            "hook_type": post.get("hook_type"),
            "tier": post.get("estimated_engagement_tier"),
            "content": post.get("content"),
        }
        if not dry_run:
            winner.update(await _send_post(post, "seed", None, client, send))
        return _summary(since, candidates, ranked, winner, dry_run, send, last_sent)

    logger.info("[digest] nincs használható tartalom — üzenet nem megy ki.")
    return _summary(since, candidates, ranked, None, dry_run, send, last_sent)


async def generate_video_idea_safe(row: dict[str, Any]) -> dict[str, Any] | None:
    """A videó-ötlet generátor `{"skip": true}` válaszát None-ra fordítja."""
    idea = await generate_video_idea(row, voice=DIGEST_VOICE)
    if not idea or idea.get("skip"):
        logger.info(
            "[digest] videó-ötlet skip (%s): %s",
            (row.get("title") or "")[:60],
            (idea or {}).get("reason", "nincs indoklás"),
        )
        return None
    return idea


def _summary(
    since: datetime,
    candidates: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    winner: dict[str, Any] | None,
    dry_run: bool,
    send: bool,
    last_sent: datetime | None,
) -> dict[str, Any]:
    summary = {
        "enabled": True,
        "window_since": since.isoformat(),
        "last_sent_at": last_sent.isoformat() if last_sent else None,
        "candidates": len(candidates),
        "ranked_top": ranked,
        "winner": winner,
        "sent": (1 if (winner and not dry_run and send) else 0),
        "dry_run": dry_run,
    }
    logger.info(
        "[digest]%s %d jelölt → %s | kiküldve: %d",
        " [DRY]" if dry_run else "",
        len(candidates),
        f"{winner['kind']} ({(winner.get('title') or winner.get('seed_key') or '')[:60]})"
        if winner
        else "nincs győztes",
        summary["sent"],
    )
    return cast(dict[str, Any], summary)


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description="Ádám konszolidált digest (2 naponta 1 üzenet).")
    ap.add_argument("--force", action="store_true", help="a minimális óraköz kapujának átlépése")
    ap.add_argument("--live", action="store_true", help="ÉLES: DB-írás + Telegram kiküldés")
    args = ap.parse_args()
    summary = asyncio.run(run_adam_digest(dry_run=not args.live, send=args.live, force=args.force))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
