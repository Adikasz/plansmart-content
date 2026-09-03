"""Heti videó-ötlet detektor (Phase 22) — Ádám reakció-videó javaslat egy friss hírre.

Hetente egyszer fut (lásd main.py SCHEDULE: hétfő 08:05 Europe/Budapest -- NEM 08:00, mert az
egybeesne a collector job-bal, ami INTERVAL_H óránként :00-kor fut, és 8 osztható a
COLLECTOR_INTERVAL_HOURS alapértékével (2); a :05 offset bármilyen INTERVAL_H mellett elkerüli
a collector/filter/breaking :00/:30/:45 mintáját). Kritériumok:
  • feed_items az utolsó 7 napból, status='filtered', score >= 7
  • voice_fit['adam'] == true (Python-oldali szűrés, lásd feed_items.get_video_idea_candidates)
  • még nincs hozzá video_idea (dedup: max 1 video_idea / feed_item)
  • a Claude-generálás maga dönti el, hogy a hír "reakció-videó alkalmas"-e ({"skip": true} ha
    nem) -- nincs külön előszűrő Claude-hívás, a suitability-check a generálás RÉSZE (1 hívás)

Találat esetén: talking-point váz Ádám hangján -> Telegram (POSTS_CHAT_ID, "🎥 Heti videó
ötlet" előtaggal, vizuálisan megkülönböztetve egy rendes poszttól) -> ✅ Approve | ✏️ Edit |
🔄 Regenerate | ❌ Skip.

Önálló futtatás (dry-run, nem küld/ír):
    python -m src.core.workers.video_idea_worker
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dotenv import load_dotenv

from src.ai.generators.video_idea_generator import generate_video_idea
from src.core.storage import feed_items as feed_store
from src.core.storage import video_ideas as video_store
from src.core.storage.db import get_client, has_service_key
from src.core.strategy import cadence
from src.integrations.bots import video_idea_bot as vb
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

VIDEO_IDEA_VOICE = "adam"  # egyelőre az egyetlen támogatott hang (Phase 22)
VIDEO_IDEA_MIN_SCORE = 7
VIDEO_IDEA_WINDOW_DAYS = 7
VIDEO_IDEA_CANDIDATE_LIMIT = 30


def _adam_fit(row: dict[str, Any]) -> bool:
    voice_fit = row.get("voice_fit") or {}
    return bool(voice_fit.get(VIDEO_IDEA_VOICE))


async def run_video_idea_check(
    dry_run: bool = False, send: bool = True, item: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Egy heti videó-ötlet-keresési ciklus.

    dry_run=True: generál (valódi Claude-hívás), de NEM ír DB-t, NEM küld Telegramra.
    item: explicit feed_items sor (teszthez); ha None, a DB-ből keresi a jelölteket.
    """
    # Fázis 23: a videó-ötlet a digest-hangoknál (alapból: adam) NEM önálló heti job többé —
    # a jelöltjei az adam_digest_worker poolját erősítik. A /create_video kézi parancs
    # ettől függetlenül működik (az a video_idea_bot._pick_and_generate-en megy, nem ezen).
    if cadence.is_digest_voice(VIDEO_IDEA_VOICE):
        logger.info(
            "[video-idea] %s digest-hang — a heti videó-ötlet az adam_digest_worker-be futott be.",
            VIDEO_IDEA_VOICE,
        )
        return {
            "candidates": 0,
            "qualified": 0,
            "tried": 0,
            "produced": None,
            "dry_run": dry_run,
            "skipped_reason": "digest_voice",
        }

    client = get_client(use_service_key=has_service_key())

    if item is not None:
        candidates = [item]
    else:
        candidates = feed_store.get_video_idea_candidates(
            min_score=VIDEO_IDEA_MIN_SCORE,
            window_days=VIDEO_IDEA_WINDOW_DAYS,
            limit=VIDEO_IDEA_CANDIDATE_LIMIT,
            client=client,
        )

    qualified = [r for r in candidates if _adam_fit(r)]
    logger.info("[video-idea] %d jelölt | %d adam voice_fit", len(candidates), len(qualified))

    produced: dict[str, Any] | None = None
    tried = 0
    for row in qualified:
        if not dry_run and video_store.has_video_idea_for_feed_item(row["id"], client=client):
            continue
        tried += 1
        idea = await generate_video_idea(row, voice=VIDEO_IDEA_VOICE)
        if not idea or idea.get("skip"):
            logger.info(
                "[video-idea] kihagyva (%s): %s",
                (row.get("title") or "")[:60],
                (idea or {}).get("reason", "nincs indoklás"),
            )
            continue

        entry = {
            "feed_item_id": row["id"],
            "feed_item_title": row.get("title"),
            "voice": VIDEO_IDEA_VOICE,
            "hook": idea.get("hook"),
            "fabrication_risk": idea.get("fabrication_risk"),
        }

        if dry_run:
            produced = entry
            break

        idea["feed_item_id"] = row["id"]
        idea["voice"] = VIDEO_IDEA_VOICE
        try:
            idea_id = video_store.insert_video_idea(idea, client=client)
        except video_store.DuplicateVideoIdeaError:
            # Ritka verseny-helyzet: időközben (a Claude-hívás alatt) már létrejött egy
            # video_idea ugyanehhez a feed_item_id-hoz (pl. egy egyidejű /create_video).
            # Nem hiba -- a dedup a DB szinten is helyesen érvényesült, csak nincs mit tenni.
            logger.info(
                "[video-idea] már létezett video_idea ehhez a hírhez (verseny-helyzet), kihagyva: %s",
                row.get("title") or row["id"],
            )
            continue
        entry["video_id"] = idea_id
        if send:
            await vb.send_video_idea_for_approval(idea_id, idea, row)
        produced = entry
        break

    summary = {
        "candidates": len(candidates),
        "qualified": len(qualified),
        "tried": tried,
        "produced": produced,
        "dry_run": dry_run,
    }
    logger.info(
        "[video-idea]%s %s",
        " [DRY]" if dry_run else "",
        "talalt jelolt" if produced else "nincs alkalmas jelolt",
    )
    return summary


def main() -> int:
    setup_logging()
    asyncio.run(run_video_idea_check(dry_run=True, send=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
