"""Phase 18 / Part 4-5 — prospects Telegram review (GATED, ember-jóváhagyás).
Phase 21: kapcsolatépítés élet-ciklus követés (post_send interactions).

Parancsok:
  /prospects          — a note_drafted jelölteket EGYESÉVEL átnézed:
                        ✅ Approve | ✏️ Edit | ⏭️ Skip
  /prospects_pending  — hány JÓVÁHAGYOTT-de-még-nem-küldött (approved_to_send) van
  /mark_sent <id>     — a human MANUÁLISAN elküldte a kapcsolatkérést → status='sent'
                        (Phase 21 óta: 'connection_sent' interaction-t is logol)
  /pnote <id> <szöveg> — a jegyzet kézi átírása (Edit fallback; DM catch-all nélkül)
  /update_prospect <id> — élet-ciklus frissítés gombokkal (Phase 21)
  /add_note <id> <szöveg> — szabad szöveges jegyzet az interakció-history-hoz (Phase 21)
  /prospects_status   — összesítő tábla a küldött jelöltek jelenlegi stage-e szerint (Phase 21)

Biztonság (kritikus): NINCS auto-küldés, NINCS LinkedIn API, NINCS scraping.
Az ✅ Approve CSAK státuszt állít (approved_to_send). A tényleges kapcsolatkérést
Dávid/Ádám küldi manuálisan, saját LinkedIn sessionben; utána /mark_sent-tel jelöli.

Külön Router — a src/core/workers/main.py a posts router ELÉ fűzi be (lásd ott a kommentet),
hogy a privát-chat parancsokat ne nyelje el a posts bot DM-catch-all handlere. Ezért
NINCS itt catch-all üzenet-handler (a szerkesztés a /pnote / /add_note paranccsal megy).
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.storage import prospect_interactions as interactions_store
from src.core.storage import prospects as store

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
_INTERACTIONS_TABLE_MISSING = (
    "⚠️ A prospect_interactions tábla még nincs a Supabase-ben — futtasd a "
    "<code>scripts/migration_21_prospect_tracking.sql</code>-t."
)

# /update_prospect gombok: (label, interaction_type | None a jegyzet-gombhoz)
_STAGE_BUTTONS = [
    ("✅ Elfogadta a kapcsolatot", "connection_accepted"),
    ("💬 Válaszolt", "replied"),
    ("📅 Meeting/hívás", "meeting_booked"),
    ("❄️ Nem reagált", "went_cold"),
    ("❌ Nem érdekli", "not_interested"),
]
_INTERACTION_LABEL = {
    "connection_sent": "📤 Kapcsolatkérés elküldve",
    "connection_accepted": "✅ Elfogadta a kapcsolatot",
    "replied": "💬 Válaszolt",
    "meeting_booked": "📅 Meeting/hívás foglalva",
    "went_cold": "❄️ Nem reagált",
    "not_interested": "❌ Nem érdekli",
    "note": "✏️ Jegyzet",
}


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
    note_hint = ""
    try:
        if interactions_store.table_ready():
            interactions_store.log_interaction(pid, "connection_sent")
        else:
            note_hint = f"\n\n{_INTERACTIONS_TABLE_MISSING}"
    except Exception as exc:  # noqa: BLE001 — a mark_sent már megtörtént, ez csak extra tracking
        logger.warning("[prospects] connection_sent interaction logolás sikertelen: %s", str(exc)[:120])
    await message.answer(
        f"✅ Elküldöttként jelölve: <b>{html.escape(p.get('name') or pid)}</b> "
        f"(korábbi státusz: {html.escape(str(prev))} → sent).\n\n"
        f"Pár nap múlva, ha van fejlemény: <code>/update_prospect {pid}</code>{note_hint}"
    )


# ── Phase 21: élet-ciklus követés (post-send interactions) ──────────────
class InteractionCB(CallbackData, prefix="interact"):
    action: str
    pid: str


def _interaction_kb(pid: str) -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=InteractionCB(action=action, pid=pid).pack())

    rows = [[btn(label, action)] for label, action in _STAGE_BUTTONS]
    rows.append([btn("✏️ Jegyzet hozzáadása", "note")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_history(rows: list[dict]) -> str:
    if not rows:
        return "<i>(még nincs rögzített interakció)</i>"
    lines = []
    for r in rows[:15]:
        label = _INTERACTION_LABEL.get(r.get("interaction_type"), r.get("interaction_type") or "?")
        date = (r.get("interaction_date") or "")[:10]
        line = f"  {date} — {label}"
        if r.get("notes"):
            line += f": {html.escape(_short(r['notes'], 100))}"
        lines.append(line)
    if len(rows) > 15:
        lines.append(f"  … +{len(rows) - 15} korábbi")
    return "\n".join(lines)


async def _guard_interactions(message: Message) -> bool:
    try:
        ok = interactions_store.table_ready()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ prospect_interactions tábla hiba: {html.escape(str(exc)[:120])}")
        return False
    if not ok:
        await message.answer(_INTERACTIONS_TABLE_MISSING)
        return False
    return True


async def _send_update_card(chat_id: int, pid: str, bot: Bot) -> None:
    p = store.get(pid)
    if not p:
        await bot.send_message(chat_id, f"Nincs ilyen jelölt: <code>{html.escape(pid)}</code>")
        return
    if p.get("status") not in ("sent", "connected", "declined"):
        await bot.send_message(
            chat_id,
            f"⚠️ <b>{html.escape(p.get('name') or pid)}</b> még nincs elküldve "
            f"(jelenlegi státusz: {html.escape(str(p.get('status')))}). "
            f"Küldés után: <code>/mark_sent {pid}</code>",
        )
        return
    rows = interactions_store.history_for(pid)
    stage = interactions_store.current_stage(pid, rows)
    stage_label = interactions_store.bucket_label(stage)
    company = f" · {html.escape(p['company'])}" if p.get("company") else ""
    text = (
        f"👤 <b>{html.escape(p.get('name') or pid)}</b>{company}\n"
        f"📍 Jelenlegi állapot: <b>{html.escape(stage_label)}</b>\n\n"
        f"<b>Előzmények:</b>\n{_format_history(rows)}\n\n"
        f"Frissítsd az állapotot:"
    )
    await bot.send_message(chat_id, text, reply_markup=_interaction_kb(pid))


@router.message(Command("update_prospect"))
async def update_prospect_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await _guard(message) or not await _guard_interactions(message):
        return
    pid = ((command.args or "").strip().split() or [""])[0]
    if not pid:
        await message.answer("Használat: <code>/update_prospect &lt;id&gt;</code>")
        return
    await _send_update_card(message.chat.id, pid, bot)


@router.callback_query(InteractionCB.filter(F.action != "note"))
async def interaction_stage_cb(query: CallbackQuery, callback_data: InteractionCB, bot: Bot) -> None:
    pid, action = callback_data.pid, callback_data.action
    try:
        interactions_store.log_interaction(pid, action)
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    label = _INTERACTION_LABEL.get(action, action)
    await query.answer(f"Rögzítve: {label}")
    try:
        await query.message.delete()
    except Exception:  # noqa: BLE001 — nem kritikus, ha nem törölhető
        pass
    await _send_update_card(query.message.chat.id, pid, bot)


@router.callback_query(InteractionCB.filter(F.action == "note"))
async def interaction_note_cb(query: CallbackQuery, callback_data: InteractionCB) -> None:
    pid = callback_data.pid
    await query.message.answer(
        "✏️ Írd be a jegyzetet (másold ki, egészítsd ki, küldd vissza):\n"
        f"<code>/add_note {pid} &lt;szöveg&gt;</code>"
    )
    await query.answer("Jegyzet: lásd az /add_note sablont ⬆️")


@router.message(Command("add_note"))
async def add_note_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await _guard_interactions(message):
        return
    pid, _, note = (command.args or "").strip().partition(" ")
    note = note.strip()
    if not pid or not note:
        await message.answer("Használat: <code>/add_note &lt;id&gt; &lt;szöveg&gt;</code>")
        return
    try:
        interactions_store.log_interaction(pid, "note", notes=note)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Jegyzet mentés hiba: {html.escape(str(exc)[:150])}")
        return
    await message.answer(f"✏️ Jegyzet mentve ({len(note)} kar).")
    await _send_update_card(message.chat.id, pid, bot)


@router.message(Command("prospects_status"))
async def prospects_status_cmd(message: Message) -> None:
    if not await _guard(message) or not await _guard_interactions(message):
        return
    try:
        counts = interactions_store.status_summary()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    total = sum(counts.values())
    if not total:
        await message.answer("📭 Nincs még elküldött jelölt (státusz='sent') a nyomon követéshez.")
        return
    lines = ["📈 <b>Kapcsolatépítés — jelenlegi állapotok</b>", ""]
    for label in interactions_store.BUCKET_ORDER:
        lines.append(f"  {label}: <b>{counts.get(label, 0)}</b>")
    lines.append(f"\n<i>összesen: {total}</i>")
    await message.answer("\n".join(lines))
