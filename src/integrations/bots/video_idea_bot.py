"""Phase 22 — /create_video, /mark_filmed, /edit_video + a heti videó-ötlet Telegram
jóváhagyási flow-ja.

Külön Router + külön CallbackData (VideoIdeaCB, prefix="video") -- a videó-ötlet jóváhagyása
NEM poszt-jóváhagyás (nincs LinkedIn auto-posztolás, nincs post_to_linkedin hívás), ezért nem
a telegram_bot.py ApprovalCB/approve_cb-jét hasznosítja újra, hanem saját, egyszerűbb
státusz-flip logikát -- ugyanaz a minta, mint a prospect_review.py két külön CallbackData
osztálya (ProspectCB vs. InteractionCB) vagy a reactions_bot.py egyszerű approve_cb-je.

Az ✏️ Edit gomb a prospect_review.py /pnote mintáját követi (NEM a telegram_bot.py DM-catch-
all mintáját): a gomb egy kész /edit_video sablon-parancsot küld vissza, amit a felhasználó
kimásol és kitölt -- nincs stateful DM-flow, nincs privát-chat catch-all handler (ezért NEM
kell a routert a tb.router elé fűzni emiatt -- de A PARANCSOK miatt IGEN, lásd main.py
kommentje: telegram_bot.py catch-all-ja privát chatben mindent elnyelne, ha ez a router
utána jönne).

Külön Router — a src/core/workers/main.py a posts router ELÉ fűzi be.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING, Any, cast

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from src.ai.generators.video_idea_generator import generate_video_idea
from src.core.config.settings import get_settings
from src.core.storage import feed_items as feed_store
from src.core.storage import video_ideas as video_store
from src.core.storage.db import get_client, has_service_key
from src.integrations.bots.telegram_bot import get_bot

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

router = Router()

POSTS_CHAT_ID = get_settings().telegram_posts_chat_id
SUPPORTED_VOICES = {"adam"}
VIDEO_IDEA_MIN_SCORE = 7
VIDEO_IDEA_WINDOW_DAYS = 7
VIDEO_IDEA_CANDIDATE_LIMIT = 30
UNSUPPORTED_VOICE_MSG = "Egyelőre csak Ádám hangján érhető el, hamarosan bővül."
_TABLE_MISSING = (
    "⚠️ A video_ideas tábla még nincs a Supabase-ben — futtasd a "
    "<code>scripts/migration_23_video_ideas.sql</code>-t."
)


async def _guard(message: Message) -> bool:
    try:
        ok = video_store.table_ready()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ video_ideas tábla hiba: {html.escape(str(exc)[:120])}")
        return False
    if not ok:
        await message.answer(_TABLE_MISSING)
        return False
    return True


class VideoIdeaCB(CallbackData, prefix="video"):
    action: str
    video_id: str


def _kb(video_id: str) -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=text, callback_data=VideoIdeaCB(action=action, video_id=video_id).pack()
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                btn("✅ Approve", "approve"),
                btn("✏️ Edit", "edit"),
                btn("🔄 Regenerate", "regenerate"),
                btn("❌ Skip", "skip"),
            ]
        ]
    )


def format_video_idea_message(idea: dict[str, Any], feed_title: str) -> str:
    points = "\n".join(f"- {html.escape(p)}" for p in (idea.get("talking_points") or []))
    duration = idea.get("estimated_duration_seconds")
    duration_txt = f"~{duration}s" if duration else "?"
    fab_line = ""
    if idea.get("fabrication_risk"):
        fab_line = (
            f"\n\n⚠️ <b>Fabrikáció-kockázat:</b> {html.escape(idea.get('fabrication_reason') or '')}"
        )
    return (
        f"🎥 <b>Heti videó ötlet — Ádám</b>\n"
        f"📰 Reagálás: {html.escape(feed_title or '?')}\n\n"
        f"<b>HOOK:</b>\n{html.escape(idea.get('hook') or '')}\n\n"
        f"<b>TÉMÁK:</b>\n{points}\n\n"
        f"<b>ZÁRÁS:</b>\n{html.escape(idea.get('closing_thought') or '')}\n\n"
        f"📝 <b>Javasolt LinkedIn caption</b> (a videóhoz):\n{html.escape(idea.get('suggested_caption') or '')}\n\n"
        f"⏱️ <b>Becsült hossz:</b> {duration_txt}"
        f"{fab_line}"
    )


async def send_video_idea_for_approval(
    video_id: str, idea: dict[str, Any], feed_row: dict[str, Any], bot: Bot | None = None
) -> Message:
    bot = bot or get_bot()
    text = format_video_idea_message(idea, feed_row.get("title", ""))
    return await bot.send_message(POSTS_CHAT_ID, text, reply_markup=_kb(video_id))


async def _safe_edit(
    message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[video-idea] üzenet-szerkesztés sikertelen: %s", str(exc)[:120])


def _msg_base_text(message: Message) -> str:
    try:
        t = message.html_text
        if t:
            return t
    except Exception:  # noqa: BLE001
        pass
    return message.text or ""


# ── /create_video, /mark_filmed, /edit_video ─────────────────────────────
async def _pick_and_generate(
    voice: str, client: Client, forced_feed_id: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Visszaad (idea, feed_row, error_message). Egyik oldal None a másik javára."""
    if forced_feed_id:
        row = feed_store.get_by_id(forced_feed_id, client=client)
        if not row:
            return None, None, f"Nincs ilyen feed_item: {forced_feed_id}"
        candidates = [row]
    else:
        candidates = feed_store.get_video_idea_candidates(
            min_score=VIDEO_IDEA_MIN_SCORE,
            window_days=VIDEO_IDEA_WINDOW_DAYS,
            limit=VIDEO_IDEA_CANDIDATE_LIMIT,
            client=client,
        )
        candidates = [r for r in candidates if (r.get("voice_fit") or {}).get(voice)]

    for row in candidates:
        if not forced_feed_id and video_store.has_video_idea_for_feed_item(
            row["id"], client=client
        ):
            continue
        idea = await generate_video_idea(row, voice=voice)
        if not idea or idea.get("skip"):
            if forced_feed_id:
                reason = (idea or {}).get(
                    "reason", "nincs indoklás (a modell nem adott értelmezhető választ)"
                )
                return None, None, f"A modell kihagyást javasolt erre a hírre: {reason}"
            continue
        return idea, row, None

    return (
        None,
        None,
        "Nincs megfelelő friss hír videó-ötlethez (score>=7, adam voice_fit, még fel nem használva).",
    )


@router.message(Command("create_video"))
async def create_video_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await _guard(message):
        return
    args = (command.args or "").strip().split()
    if not args:
        await message.answer("Használat: <code>/create_video &lt;voice&gt; [feed_item_id]</code>")
        return
    voice = args[0].lower()
    if voice not in SUPPORTED_VOICES:
        await message.answer(UNSUPPORTED_VOICE_MSG)
        return
    forced_feed_id = args[1] if len(args) > 1 else None

    await message.answer("⏳ Videó-ötlet keresése/generálása…")
    client = get_client(use_service_key=has_service_key())
    try:
        idea, feed_row, error = await _pick_and_generate(voice, client, forced_feed_id)
        if error:
            await message.answer(f"⚠️ {html.escape(error)}")
            return
        # error is None → a _pick_and_generate szerződése szerint idea és feed_row nem None.
        idea = cast(dict[str, Any], idea)
        feed_row = cast(dict[str, Any], feed_row)

        idea["feed_item_id"] = feed_row["id"]
        idea["voice"] = voice
        video_id = video_store.insert_video_idea(idea, client=client)
        await send_video_idea_for_approval(video_id, idea, feed_row, bot=bot)
        await message.answer(f"✅ Videó-ötlet elküldve jóváhagyásra (video_id={video_id}).")
    except video_store.DuplicateVideoIdeaError:
        await message.answer(
            "⚠️ Időközben már készült video-ötlet ehhez a hírhez (pl. a heti "
            "automata futás közben) — próbáld máskor, vagy adj meg másik hírt."
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[video-idea] /create_video hiba")
        await message.answer(f"❌ Hiba történt: {html.escape(str(exc)[:150])}")


@router.message(Command("mark_filmed"))
async def mark_filmed_cmd(message: Message, command: CommandObject) -> None:
    if not await _guard(message):
        return
    video_id = ((command.args or "").strip().split() or [""])[0]
    if not video_id:
        await message.answer("Használat: <code>/mark_filmed &lt;video_id&gt;</code>")
        return
    try:
        idea = video_store.get_video_idea(video_id)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not idea:
        await message.answer(f"Nincs ilyen videó-ötlet: <code>{html.escape(video_id)}</code>")
        return
    user = cast(User, message.from_user)
    by = user.username or user.full_name
    video_store.mark_filmed(video_id, by)
    await message.answer(
        f"🎬 Leforgatottként jelölve: <b>{html.escape(idea.get('title') or video_id)}</b>."
    )


@router.message(Command("edit_video"))
async def edit_video_cmd(message: Message, command: CommandObject) -> None:
    if not await _guard(message):
        return
    video_id, _, new_text = (command.args or "").strip().partition(" ")
    new_text = new_text.strip()
    if not video_id or not new_text:
        await message.answer("Használat: <code>/edit_video &lt;video_id&gt; &lt;jegyzet&gt;</code>")
        return
    try:
        idea = video_store.get_video_idea(video_id)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not idea:
        await message.answer(f"Nincs ilyen videó-ötlet: <code>{html.escape(video_id)}</code>")
        return
    video_store.mark_edited(video_id, new_text)
    await message.answer(f"✏️ Jegyzet frissítve ({len(new_text)} kar).")


# ── Callback handlerek ─────────────────────────────────────────────────
@router.callback_query(VideoIdeaCB.filter(F.action == "approve"))
async def approve_cb(query: CallbackQuery, callback_data: VideoIdeaCB) -> None:
    video_id = callback_data.video_id
    by = query.from_user.username or query.from_user.full_name
    try:
        video_store.mark_approved(video_id, by)
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    msg = cast(Message, query.message)
    await _safe_edit(msg, f"{_msg_base_text(msg)}\n\n✅ Approved — @{by}", reply_markup=None)
    await query.answer("Approved ✅")


@router.callback_query(VideoIdeaCB.filter(F.action == "skip"))
async def skip_cb(query: CallbackQuery, callback_data: VideoIdeaCB) -> None:
    video_id = callback_data.video_id
    by = query.from_user.username or query.from_user.full_name
    try:
        video_store.update_status(video_id, "skipped")
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    msg = cast(Message, query.message)
    await _safe_edit(msg, f"{_msg_base_text(msg)}\n\n❌ Skipped — @{by}", reply_markup=None)
    await query.answer("Skipped")


@router.callback_query(VideoIdeaCB.filter(F.action == "edit"))
async def edit_cb(query: CallbackQuery, callback_data: VideoIdeaCB) -> None:
    video_id = callback_data.video_id
    try:
        idea = video_store.get_video_idea(video_id) or {}
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    cur = idea.get("edited_notes") or idea.get("hook") or ""
    await cast(Message, query.message).answer(
        "✏️ Írd át a jegyzeteket — másold ki, javítsd, küldd vissza:\n"
        f"<code>/edit_video {video_id} {html.escape(cur)}</code>"
    )
    await query.answer("Szerkesztés: lásd az /edit_video sablont ⬆️")


@router.callback_query(VideoIdeaCB.filter(F.action == "regenerate"))
async def regenerate_cb(query: CallbackQuery, callback_data: VideoIdeaCB) -> None:
    video_id = callback_data.video_id
    msg = cast(Message, query.message)
    await query.answer("🔄 Újragenerálás folyamatban…")
    client = get_client(use_service_key=has_service_key())
    try:
        idea = video_store.get_video_idea(video_id, client=client)
    except Exception as exc:  # noqa: BLE001
        await msg.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not idea or not idea.get("feed_item_id"):
        await msg.answer("⚠️ Nem található a videó-ötlet vagy a hozzá tartozó hír.")
        return
    feed_row = feed_store.get_by_id(idea["feed_item_id"], client=client)
    if not feed_row:
        await msg.answer("⚠️ A hozzá tartozó hír már nem található.")
        return
    try:
        new_idea = await generate_video_idea(feed_row, voice=idea["voice"])
        if not new_idea or new_idea.get("skip"):
            reason = (new_idea or {}).get("reason", "nincs indoklás")
            await msg.answer(
                f"⚠️ Az újragenerált verzió is kihagyást javasolt: {html.escape(reason)}"
            )
            return
        video_store.update_content(video_id, new_idea, client=client)
        text = format_video_idea_message(new_idea, feed_row.get("title", ""))
        await _safe_edit(msg, text, reply_markup=_kb(video_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[video-idea] regenerálási hiba")
        await msg.answer(f"❌ Újragenerálási hiba: {html.escape(str(exc)[:150])}")
