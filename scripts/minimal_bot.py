"""Minimális izolációs teszt — csak polling + 1 message handler.

Cél: eldönteni, hogy a polling egyáltalán kézbesít-e üzenetet
(független az src/integrations/bots/telegram_bot.py kódjától).

Futtatás (egyszerre CSAK EGY poller fusson erre a tokenre!):
    python scripts/minimal_bot.py
"""
import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher()


@dp.message()
async def any_message(message: Message):
    print(f"GOT MESSAGE: chat={message.chat.type} text={message.text!r}", flush=True)
    await message.reply("ok")


async def main():
    print("Starting polling...", flush=True)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


asyncio.run(main())
