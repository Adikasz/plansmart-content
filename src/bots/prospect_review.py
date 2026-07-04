"""Phase 18 / Part 4-5 — prospects Telegram review (GATED, ember-jóváhagyás).

Parancsok:
  /prospects          — a note_drafted jelölteket EGYESÉVEL átnézed:
                        ✅ Approve | ✏️ Edit | ⏭️ Skip
  /prospects_pending  — hány JÓVÁHAGYOTT-de-még-nem-küldött (approved_to_send) van
  /mark_sent <id>     — a human MANUÁLISAN elküldte a kapcsolatkérést → status='sent'
  /pnote <id> <szöveg> — a jegyzet kézi átírása (Edit fallback; DM catch-all nélkül)

Biztonság (kritikus): NINCS auto-küldés, NINCS LinkedIn API, NINCS scraping.
Az ✅ Approve CSAK státuszt állít (approved_to_send). A tényleges kapcsolatkérést
Dávid/Ádám küldi manuálisan, saját LinkedIn sessionben; utána /mark_sent-tel jelöli.

Külön Router — a src/workers/main.py a posts router ELÉ fűzi be (lásd ott a kommentet),
hogy a privát-chat parancsokat ne nyelje el a posts bot DM-catch-all handlere. Ezért
NINCS itt catch-all üzenet-handler (a szerkesztés a /pnote paranccsal megy).
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.storage import prospects as store

logger = logging.getLogger(__name__)

router = Router()

VOICE_DISPLAY = {"david": ("🔨", "david"), "adam": ("📊", "adam")}
CATEGORY_LABEL = {
    "hu_sme_owner": "HU KKV-tulaj",
    "intl_sme_owner": "Nemzetközi KKV-tulaj",
    "ai_specialist": "AI szakértő",
    "industry_peer": "Iparági partner",
}
CHAR_LIMIT = 300
_TABLE_MISSING = ("⚠️ A prospects tábla még nincs a Supabase-ben — futtasd a "
                  "<code>scripts/migration_18_prospects.sql</code>-t.")


class ProspectCB(CallbackData, prefix="prospect"):
    action: str
    pid: str


# ── segédek ────────────────────────────────────────────────────────────
def _table_state() -> tuple[bool, str]:
    """(kész?, hibaüzenet) — a tábla-lét egyszeri, hibatűrő ellenőrzése."""
    try:
        return store.table_ready(), ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:120]


async def _guard(message: Message) -> bool:
    ok, err = _table_state()
    if ok:
        return True
    await message.answer(_TABLE_MISSING if not err else f"⚠️ prospects tábla hiba: {html.escape(err)}")
    return False


def _short(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _kb(pid: str) -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=ProspectCB(action=action, pid=pid).pack())

    return InlineKeyboardMarkup(inline_keyboard=[[
        btn("✅ Approve", "approve"), btn("✏️ Edit", "edit"), btn("⏭️ Skip", "skip"),
    ]])


def _format_card(p: dict, remaining: int = 0) -> str:
    emoji, vname = VOICE_DISPLAY.get(p.get("voice") or "", ("👤", p.get("voice") or "?"))
    cat = CATEGORY_LABEL.get(p.get("category") or "", p.get("category") or "?")
    loc = ", ".join(x for x in (p.get("city"), p.get("country")) if x) or "—"
    note = p.get("connection_note_draft") or "(nincs jegyzet)"
    over = "  ⚠️ 300+ kar" if len(note) > CHAR_LIMIT else ""

    head = f"👤 <b>{html.escape(p.get('name') or '?')}</b>"
    if p.get("title"):
        head += f" — {html.escape(p['title'])}"
    parts = [
        head,
        f"🏢 {html.escape(p.get('company') or '—')} · {html.escape(loc)}",
        f"🏷 {html.escape(cat)} · ✍️ {emoji} {html.escape(vname)}/{html.escape(p.get('note_language') or '?')}",
    ]
    if p.get("company_size_estimate"):
        parts.append(f"👥 {html.escape(p['company_size_estimate'])}")
    if p.get("relevance_notes"):
        parts.append(f"📌 {html.escape(_short(p['relevance_notes'], 240))}")
    if p.get("source"):
        parts.append(f"🔎 {html.escape(_short(p['source'], 160))}")
    parts += [
        "🔗 <i>A LinkedIn profilt bejelentkezve keresd meg (URL-t nem tárolunk).</i>",
        "",
        f"✉️ <b>Jegyzet</b> ({len(note)} kar){over}:",
        f"<i>{html.escape(note)}</i>",
    ]
    if remaining:
        parts += ["", f"<i>({remaining} jelölt vár review-ra)</i>"]
    return "\n".join(parts)


async def _safe_edit(message: Message, text: str) -> None:
    try:
        await message.edit_text(text, reply_markup=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[prospects] üzenet-szerkesztés sikertelen: %s", str(exc)[:120])


async def _send_one(chat_id: int, p: dict | None, bot: Bot, remaining: int = 0) -> None:
    if not p:
        return
    await bot.send_message(chat_id, _format_card(p, remaining), reply_markup=_kb(p["id"]))


async def _send_next(chat_id: int, bot: Bot) -> None:
    """A következő note_drafted jelöltet küldi (a legrégebbit); ha nincs több, lezárja."""
    try:
        drafted = store.by_status("note_drafted")
    except Exception as exc:  # noqa: BLE001
        await bot.send_message(chat_id, f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:120])}")
        return
    if not drafted:
        await bot.send_message(chat_id, "✅ Nincs több review-ra váró jelölt. Kész!")
        return
    await _send_one(chat_id, drafted[0], bot, remaining=len(drafted))


# ── parancsok ──────────────────────────────────────────────────────────
@router.message(Command("prospects"))
async def prospects_cmd(message: Message, bot: Bot) -> None:
    if not await _guard(message):
        return
    try:
        drafted = store.by_status("note_drafted")
        researched = store.count_by_status("researched")
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not drafted:
        hint = (f"\n\nℹ️ {researched} kikutatott jelölt vár jegyzet-generálásra "
                "(note_generator).") if researched else ""
        await message.answer("Nincs review-ra váró jelölt (note_drafted = 0)." + hint)
        return
    await message.answer(f"📋 <b>{len(drafted)} jelölt vár jóváhagyásra.</b> Nézd át egyesével:")
    await _send_one(message.chat.id, drafted[0], bot, remaining=len(drafted))


@router.callback_query(ProspectCB.filter(F.action == "approve"))
async def approve_cb(query: CallbackQuery, callback_data: ProspectCB, bot: Bot) -> None:
    pid = callback_data.pid
    try:
        p = store.get(pid) or {}
        store.set_status(pid, "approved_to_send")
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    await _safe_edit(query.message, f"✅ <b>Jóváhagyva küldésre</b> — {html.escape(p.get('name') or pid)}\n"
                                    f"<i>id: {pid} · manuális küldés után: /mark_sent {pid}</i>")
    await query.answer("Jóváhagyva ✅")
    await _send_next(query.message.chat.id, bot)


@router.callback_query(ProspectCB.filter(F.action == "skip"))
async def skip_cb(query: CallbackQuery, callback_data: ProspectCB, bot: Bot) -> None:
    pid = callback_data.pid
    try:
        p = store.get(pid) or {}
        store.set_status(pid, "skipped")
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    await _safe_edit(query.message, f"⏭️ <b>Kihagyva</b> — {html.escape(p.get('name') or pid)}")
    await query.answer("Kihagyva")
    await _send_next(query.message.chat.id, bot)


@router.callback_query(ProspectCB.filter(F.action == "edit"))
async def edit_cb(query: CallbackQuery, callback_data: ProspectCB) -> None:
    pid = callback_data.pid
    try:
        p = store.get(pid) or {}
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    cur = p.get("connection_note_draft") or ""
    await query.message.answer(
        "✏️ Írd át a jegyzetet — másold ki, javítsd a szöveget, küldd vissza:\n"
        f"<code>/pnote {pid} {html.escape(cur)}</code>"
    )
    await query.answer("Szerkesztés: lásd a /pnote sablont ⬆️")


@router.message(Command("pnote"))
async def pnote_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await _guard(message):
        return
    pid, _, new = (command.args or "").strip().partition(" ")
    new = new.strip()
    if not pid or not new:
        await message.answer("Használat: <code>/pnote &lt;id&gt; &lt;új jegyzet&gt;</code>")
        return
    try:
        p = store.get(pid)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not p:
        await message.answer(f"Nincs ilyen jelölt: <code>{html.escape(pid)}</code>")
        return
    voice = p.get("voice") or "adam"
    lang = p.get("note_language") or "hu"
    store.save_note(pid, new, voice, lang)  # status → note_drafted
    warn = "  ⚠️ 300 kar felett!" if len(new) > CHAR_LIMIT else ""
    await message.answer(f"✏️ Jegyzet frissítve ({len(new)} kar{warn}). Újraküldöm review-ra:")
    await _send_one(message.chat.id, store.get(pid), bot)


@router.message(Command("prospects_pending"))
async def pending_cmd(message: Message) -> None:
    if not await _guard(message):
        return
    try:
        rows = store.by_status("approved_to_send")
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not rows:
        await message.answer("📭 Nincs jóváhagyott-de-még-nem-küldött jelölt (approved_to_send = 0).")
        return
    lines = [f"📨 <b>{len(rows)} jóváhagyott jelölt vár manuális küldésre:</b>", ""]
    for p in rows[:30]:
        company = f" · {html.escape(p['company'])}" if p.get("company") else ""
        lines.append(f"• <code>{p['id']}</code> — {html.escape(p.get('name') or '?')}{company}")
    if len(rows) > 30:
        lines.append(f"… +{len(rows) - 30} további")
    lines += ["", "Küldés után jelöld: <code>/mark_sent &lt;id&gt;</code>"]
    await message.answer("\n".join(lines))


@router.message(Command("mark_sent"))
async def mark_sent_cmd(message: Message, command: CommandObject) -> None:
    if not await _guard(message):
        return
    pid = ((command.args or "").strip().split() or [""])[0]
    if not pid:
        await message.answer("Használat: <code>/mark_sent &lt;id&gt;</code>")
        return
    try:
        p = store.get(pid)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not p:
        await message.answer(f"Nincs ilyen jelölt: <code>{html.escape(pid)}</code>")
        return
    prev = p.get("status")
    store.mark_sent(pid)
    await message.answer(f"✅ Elküldöttként jelölve: <b>{html.escape(p.get('name') or pid)}</b> "
                         f"(korábbi státusz: {html.escape(str(prev))} → sent).")
