"""Phase 7 teszt: Telegram approval bot — connectivity + formázás + DB-logika + élő poszt.

A 4 gomb DB-hatását szimuláljuk (a handlerek ugyanezeket a posts_store függvényeket hívják),
majd küldünk egy élő, kattintható posztot a posts csatornára.

Futtatás:
    python scripts/test_telegram.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from src.integrations.bots.telegram_bot import (  # noqa: E402
    ApprovalCB, POSTS_CHAT_ID, build_keyboard, format_approval_message, get_bot, send_for_approval,
)
from src.core.storage import posts as ps  # noqa: E402
from src.core.storage.db import get_client, has_service_key  # noqa: E402

logger = logging.getLogger("test_telegram")

MOCK = {
    "voice": "david", "platform": "linkedin", "score": 9,
    "feed_item_url": "https://www.anthropic.com/news/claude-opus-4-8",
    "content": "Az Anthropic kihozta a Claude Opus 4.8-at. Ma este megnézem élesben, "
               "és ha tartja amit ígér a long-running consistency-ből, átmigrálom a pipeline-t.",
    "hashtags": ["#ClaudeAPI", "#BuildInPublic"],
}


def test_format() -> None:
    logger.info("=== 1. FORMAZAS + GOMBOK ===")
    logger.info("%s\n", format_approval_message(MOCK))
    row = build_keyboard("abc123def456").inline_keyboard[0]
    actions = [ApprovalCB.unpack(b.callback_data).action for b in row]
    labels = [b.text for b in row]
    logger.info("gombok: %s", labels)
    logger.info("callback action-ok: %s", actions)
    assert len(row) == 4, "4 gomb kell"
    assert actions == ["approve", "edit", "regenerate", "skip"], actions
    logger.info("  [OK] 4 gomb, helyes callback_data\n")


def test_db_logic(client) -> None:
    logger.info("=== 2. DB-LOGIKA (a 4 gomb hatasa a posts + approvals tablakra) ===")
    cases = [("approve", "approved"), ("skip", "skipped"),
             ("regenerate", "regenerated"), ("edit", "edited")]
    ids = []
    for action, status in cases:
        pid = ps.insert_post({**MOCK})
        ids.append(pid)
        if action == "approve":
            ps.mark_approved(pid, "tester")
        elif action == "edit":
            ps.mark_edited(pid, "Szerkesztett verzió szövege.")
        else:
            ps.update_post_status(pid, status)
        ps.record_approval(
            pid, POSTS_CHAT_ID, 111111, action=action, telegram_user_id=42,
            action_data={"edited_content": "..."} if action == "edit" else None,
        )
        final = ps.get_post(pid) or {}
        appr = client.table("approvals").select("action").eq("post_id", pid).execute().data or []
        logger.info("  %-11s -> posts.status=%-12s approvals=%s", action, final.get("status"), [a["action"] for a in appr])
        assert final.get("status") == status, f"{action}: {final.get('status')} != {status}"
        assert len(appr) == 1, f"{action}: approvals={len(appr)}"
    # takaritas (approvals CASCADE törlődik a posts törlésekor)
    for pid in ids:
        client.table("posts").delete().eq("id", pid).execute()
    logger.info("  [OK] mind a 4 action helyesen ir; teszt sorok torolve\n")


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    client = get_client(use_service_key=has_service_key())
    test_format()
    test_db_logic(client)

    logger.info("=== 3. TELEGRAM CONNECTIVITY + ELO POSZT ===")
    bot = get_bot()
    me = await bot.get_me()
    logger.info("  getMe OK: @%s (id=%s)", me.username, me.id)

    live = {**MOCK, "id": ps.insert_post({**MOCK})}
    sent = await send_for_approval(live, POSTS_CHAT_ID, bot)
    logger.info("  ELO poszt elkuldve a posts csatornara: post_id=%s, message_id=%s", live["id"], sent.message_id)
    await bot.session.close()

    logger.info("\nMost inditsd a botot (python -m src.integrations.bots.telegram_bot) es kattints a gombokra.")
    logger.info("A kattintasok az approvals tablaba kerulnek (post_id=%s).", live["id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
