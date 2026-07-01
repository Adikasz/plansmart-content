"""Phase 7 edit-flow regressziós teszt — szintetikus reply Update a dispatcheren át.

Valódi Telegram + Supabase, user kattintás nélkül:
  1. küld egy posztot (+ DB sor) és egy edit-promptot,
  2. feltölti a _pending_edits-et (mint az edit_cb),
  3. küld egy VALÓDI reply üzenetet a promptra,
  4. ezt Update-ként a dispatcherbe táplálja -> edit_reply lefut,
  5. ellenőrzi: posts.status="edited" és approvals action="edited".

Futtatás (a futó bot legyen leállítva!):
    python scripts/test_edit_flow.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from aiogram import Dispatcher  # noqa: E402
from aiogram.types import Update  # noqa: E402

from src.bots import telegram_bot as tb  # noqa: E402
from src.storage import posts as ps  # noqa: E402
from src.storage.db import get_client, has_service_key  # noqa: E402

logger = logging.getLogger("test_edit_flow")


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)

    client = get_client(use_service_key=has_service_key())
    bot = tb.get_bot()
    chat = tb.POSTS_CHAT_ID

    # 1. orig poszt + DB sor
    post = {
        "voice": "david", "platform": "linkedin", "score": 9,
        "feed_item_url": "https://www.anthropic.com/news/test",
        "content": "Eredeti generált szöveg a teszthez.", "hashtags": ["#test"],
    }
    post["id"] = ps.insert_post(post)
    orig = await tb.send_for_approval(post, chat, bot)
    logger.info("orig poszt: msg=%s post_id=%s", orig.message_id, post["id"])

    # 2. edit-prompt + _pending_edits (mint az edit_cb)
    prompt = await orig.reply("✏️ Küldj egy új szöveget válaszként erre az üzenetre")
    ctx = {
        "post_id": post["id"], "orig_message_id": orig.message_id,
        "prompt_message_id": prompt.message_id, "chat_id": chat,
    }
    tb._pending_edits[prompt.message_id] = ctx
    tb._pending_edits[orig.message_id] = ctx

    # 3. valódi reply üzenet a promptra
    reply_msg = await bot.send_message(chat, "Ez az új szöveg", reply_to_message_id=prompt.message_id)
    logger.info(
        "reply uzenet: msg=%s reply_to=%s reply_to.is_bot=%s",
        reply_msg.message_id, reply_msg.reply_to_message.message_id,
        reply_msg.reply_to_message.from_user.is_bot,
    )

    # 4. dispatcherbe táplálás (mintha Telegram kézbesítené)
    dp = Dispatcher()
    dp.include_router(tb.router)
    await dp.feed_update(bot, Update(update_id=999001, message=reply_msg))

    # 5. ellenőrzés
    final = ps.get_post(post["id"]) or {}
    appr = client.table("approvals").select("action").eq("post_id", post["id"]).execute().data or []
    logger.info("\n=== EREDMENY ===")
    logger.info("  posts.status        = %s  (vart: edited)", final.get("status"))
    logger.info("  posts.edited_content= %r", final.get("edited_content"))
    logger.info("  posts.edit_count    = %s", final.get("edit_count"))
    logger.info("  approvals actions   = %s  (vart: tartalmaz 'edited')", [a["action"] for a in appr])
    ok = final.get("status") == "edited" and any(a["action"] == "edited" for a in appr)
    logger.info("\n  %s", "PASS — az edit flow mukodik ✅" if ok else "FAIL ❌")

    # takarítás: DB teszt sor (approvals CASCADE) + Telegram teszt üzenetek
    client.table("posts").delete().eq("id", post["id"]).execute()
    for mid in (orig.message_id, prompt.message_id, reply_msg.message_id):
        try:
            await bot.delete_message(chat, mid)
        except Exception:
            pass
    await bot.session.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
