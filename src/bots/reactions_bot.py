"""Phase 19 / B1 — manuális-módú reakció-asszisztens (Telegram, aiogram 3.x).

Amíg a LinkedIn Community Management API nincs jóváhagyva, Dávid/Ádám KÉZZEL kapja
a kommenteket/DM-eket. Ide beillesztik, a bot javasol egy választ a megfelelő hangon,
ők szerkesztik és MANUÁLISAN küldik el. Semmi automata LinkedIn akció.

Parancsok:
  /reply_comment <voice>  — komment-válasz drafting flow
  /reply_dm <voice>       — DM-válasz drafting flow  (voice: david | adam | plansmart)

Flow: a bot elkéri a kontextust (a posztot / korábbi DM-et), majd magát a bejövő
kommentet/DM-et, osztályozza (Haiku), és — ha érdemes — javasol egy választ (Sonnet)
gombokkal: ✅ Approve | ✏️ Edit | 🔄 Regenerate | ❌ Cancel. Approve → tiszta,
másolható szöveg. A döntések a reactions Supabase táblába kerülnek (best-effort).

Külön Router — a src/workers/main.py a prospect + posts routerek ELÉ fűzi be. A
free-text handler CSAK akkor kap el üzenetet, ha az adott (chat, user)-nek van AKTÍV
flow-ja, így a nem-flow DM-ek rendben átesnek a többi routerhez.
"""
from __future__ import annotations

import html
import logging
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.outreach.reaction_generator import build_reaction, generate_reply
from src.storage import reactions as store

logger = logging.getLogger(__name__)

router = Router()

VOICES = {"david", "adam", "plansmart"}
_NO_CONTEXT = {"-", "—", "–", ".", "nincs", "none", "skip"}

CLASSIFICATION_LABEL = {
    "question_or_engagement": "❓ Kérdés/beszélgetés",
    "lead_signal": "🎯 Lead jelzés",
    "appreciative_only": "🙏 Elismerés",
    "spam_or_troll": "🚫 Spam/troll",
    "competitor_pitch": "🤝 Konkurens",
}

REPLY_USAGE = (
    "Használat:\n"
    "<code>/reply_comment &lt;voice&gt;</code>  vagy  <code>/reply_dm &lt;voice&gt;</code>\n"
    "voice: <b>david</b> | <b>adam</b> | <b>plansmart</b>\n\n"
    "A bot végigvezet: beilleszted a kontextust, majd a bejövő kommentet/DM-et, "
    "és kapsz egy szerkeszthető válasz-javaslatot."
)

# (chat_id, user_id) -> aktív flow állapota (in-memory MVP, mint a posts bot _pending_edits-je).
#   {kind, voice, step, context, incoming, classification, language, is_first_dm, rid, reply}
_flows: dict[tuple[int, int], dict] = {}


class ReactionCB(CallbackData, prefix="react"):
    action: str
    rid: str


# ── segédek ────────────────────────────────────────────────────────────
def _parse_voice(args: str | None) -> str | None:
    tok = ((args or "").strip().split() or [""])[0].lower()
    return tok if tok in VOICES else None


def _kb(rid: str) -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=ReactionCB(action=action, rid=rid).pack())

    return InlineKeyboardMarkup(inline_keyboard=[[
        btn("✅ Approve", "approve"), btn("✏️ Edit", "edit"),
        btn("🔄 Regenerate", "regenerate"), btn("❌ Cancel", "cancel"),
    ]])


def format_suggestion(classification: str, language: str, reply: str) -> str:
    label = CLASSIFICATION_LABEL.get(classification, classification)
    return (
        f"💬 <b>Javasolt válasz</b> — {label} · {html.escape(language)}\n\n"
        f"{html.escape(reply)}\n\n"
        "<i>Szerkeszd/hagyd jóvá a gombokkal.</i>"
    )


def format_final(reply: str, header: str) -> str:
    """Jóváhagyott/szerkesztett — <code>-ban a tap-to-copy másoláshoz."""
    return f"{header}\n\n<code>{html.escape(reply)}</code>"


def _has_active_flow(message: Message) -> bool:
    u = message.from_user
    return bool(u) and (message.chat.id, u.id) in _flows


def _persist_new(flow: dict, draft: str | None, status: str) -> str:
    """Best-effort insert a reactions táblába; visszaadja az id-t (mentés nélkül is)."""
    rid = uuid.uuid4().hex[:12]
    try:
        store.insert_reaction({
            "id": rid, "voice": flow["voice"], "type": flow["kind"],
            "incoming_text": flow["incoming"], "context_text": flow.get("context") or None,
            "our_reply_draft": draft, "classification": flow.get("classification"),
            "language": flow.get("language"), "status": status,
        })
    except Exception as exc:  # noqa: BLE001 — a UI a DB nélkül is működik (tábla hiányozhat)
        logger.warning("[reactions] mentés kihagyva (%s) — futott a migration_19?", str(exc)[:120])
    return rid


def _persist_update(rid: str, draft: str, status: str) -> None:
    try:
        store.update_draft(rid, draft, status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reactions] draft-frissítés kihagyva (%s)", str(exc)[:120])


def _persist_status(rid: str, status: str) -> None:
    try:
        store.set_status(rid, status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reactions] státusz-frissítés kihagyva (%s)", str(exc)[:120])


async def _safe_edit(message: Message | None, text: str, reply_markup=None) -> None:
    if not message:
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reactions] üzenet-szerkesztés sikertelen: %s", str(exc)[:120])


# ── parancsok ──────────────────────────────────────────────────────────
async def _start_flow(message: Message, command: CommandObject, kind: str) -> None:
    voice = _parse_voice(command.args)
    if not voice:
        await message.answer(f"Ismeretlen vagy hiányzó voice.\n\n{REPLY_USAGE}")
        return
    _flows[(message.chat.id, message.from_user.id)] = {
        "kind": kind, "voice": voice, "step": "await_context", "context": "", "incoming": "",
    }
    if kind == "comment":
        ask = ("✍️ <b>Komment-válasz</b> ({v}).\n\nIlleszd be a LinkedIn posztot, amire a kommentet "
               "kaptad. Ha nem lényeges, küldj egy kötőjelet: <code>-</code>").format(v=voice)
    else:
        ask = ("✍️ <b>DM-válasz</b> ({v}).\n\nIlleszd be a korábbi DM kontextust, ha van. Ha ez az "
               "ELSŐ üzenet tőle, küldj egy kötőjelet: <code>-</code>").format(v=voice)
    await message.answer(ask)


@router.message(Command("reply_comment"))
async def reply_comment_cmd(message: Message, command: CommandObject) -> None:
    await _start_flow(message, command, "comment")


@router.message(Command("reply_dm"))
async def reply_dm_cmd(message: Message, command: CommandObject) -> None:
    await _start_flow(message, command, "dm")


# ── flow free-text (CSAK aktív flow-nál kap el) ────────────────────────
@router.message(_has_active_flow)
async def flow_text(message: Message) -> None:
    key = (message.chat.id, message.from_user.id)
    flow = _flows.get(key)
    if not flow:
        return
    text = (message.text or "").strip()
    step = flow["step"]

    if step == "await_context":
        flow["context"] = "" if text.lower() in _NO_CONTEXT else text
        flow["step"] = "await_incoming"
        prompt = ("Most illeszd be magát a kommentet:" if flow["kind"] == "comment"
                  else "Most illeszd be a bejövő DM üzenetet:")
        await message.answer(prompt)
        return

    if step == "await_incoming":
        if not text:
            await message.answer("Üres — küldj szöveget.")
            return
        flow["incoming"] = text
        await _run_and_show(message, flow, key)
        return

    if step == "await_edit":
        if not text:
            await message.answer("Üres — küldj szöveget.")
            return
        rid = flow.get("rid", "")
        _persist_update(rid, text, "edited")
        await message.answer(format_final(text, "✏️ <b>Szerkesztve</b> — másold ki:"))
        _flows.pop(key, None)
        return

    await message.answer("Használd a javaslat alatti gombokat: ✅ / ✏️ / 🔄 / ❌")


async def _run_and_show(message: Message, flow: dict, key: tuple[int, int]) -> None:
    is_first_dm = flow["kind"] == "dm" and not flow["context"].strip()
    flow["is_first_dm"] = is_first_dm
    await message.answer("⏳ Elemzés + válasz-javaslat…")
    try:
        result = await build_reaction(
            flow["voice"], flow["kind"], flow["incoming"],
            context_text=flow["context"], is_first_dm=is_first_dm,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reactions] build_reaction hiba: %s", str(exc)[:150])
        await message.answer(f"❌ Hiba a generálásnál: {html.escape(str(exc)[:150])}")
        _flows.pop(key, None)
        return

    flow["classification"] = result["classification"]
    flow["language"] = result["language"]

    if result["skip"]:
        _persist_new(flow, None, "skipped")
        await message.answer(f"{result['skip_message']}\n\n<i>Rögzítve (skipped).</i>")
        _flows.pop(key, None)
        return

    reply = result["reply"] or ""
    rid = _persist_new(flow, reply, "drafted")
    flow["rid"] = rid
    flow["reply"] = reply
    flow["step"] = "await_action"
    await message.answer(
        format_suggestion(result["classification"], result["language"], reply),
        reply_markup=_kb(rid),
    )


# ── callback gombok ────────────────────────────────────────────────────
def _flow_for(query: CallbackQuery) -> tuple[tuple[int, int], dict | None]:
    key = (query.message.chat.id, query.from_user.id) if query.message else (0, query.from_user.id)
    return key, _flows.get(key)


@router.callback_query(ReactionCB.filter(F.action == "approve"))
async def approve_cb(query: CallbackQuery, callback_data: ReactionCB) -> None:
    key, flow = _flow_for(query)
    _persist_status(callback_data.rid, "approved")
    reply = (flow or {}).get("reply")
    if not reply:  # bot újraindult közben — a DB-ből próbáljuk
        try:
            reply = (store.get(callback_data.rid) or {}).get("our_reply_draft")
        except Exception:  # noqa: BLE001
            reply = None
    _flows.pop(key, None)
    await _safe_edit(query.message, format_final(reply or "(nincs szöveg)", "✅ <b>Jóváhagyva</b> — másold ki:"))
    await query.answer("Jóváhagyva ✅ — másold ki a szöveget.")


@router.callback_query(ReactionCB.filter(F.action == "edit"))
async def edit_cb(query: CallbackQuery, callback_data: ReactionCB) -> None:
    key, flow = _flow_for(query)
    if not flow:
        await query.answer("A folyamat lejárt — indítsd újra a /reply_comment vagy /reply_dm paranccsal.",
                           show_alert=True)
        return
    flow["step"] = "await_edit"
    await query.message.answer("✏️ Illeszd be a javított szöveget (ide, üzenetként):")
    await query.answer("Küldd be a javított szöveget ✏️")


@router.callback_query(ReactionCB.filter(F.action == "regenerate"))
async def regenerate_cb(query: CallbackQuery, callback_data: ReactionCB) -> None:
    key, flow = _flow_for(query)
    if not flow:
        await query.answer("A folyamat lejárt — indítsd újra.", show_alert=True)
        return
    await query.answer("Új javaslat generálása…")
    try:
        reply = await generate_reply(
            flow["voice"], flow["kind"], flow["incoming"], flow["classification"],
            context_text=flow["context"], language=flow["language"],
            is_first_dm=flow.get("is_first_dm", False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reactions] regenerate hiba: %s", str(exc)[:150])
        await query.answer(f"Hiba: {str(exc)[:150]}", show_alert=True)
        return
    flow["reply"] = reply
    _persist_update(callback_data.rid, reply, "drafted")
    await _safe_edit(
        query.message,
        format_suggestion(flow["classification"], flow["language"], reply),
        reply_markup=_kb(callback_data.rid),
    )


@router.callback_query(ReactionCB.filter(F.action == "cancel"))
async def cancel_cb(query: CallbackQuery, callback_data: ReactionCB) -> None:
    key, _flow = _flow_for(query)
    _persist_status(callback_data.rid, "skipped")
    _flows.pop(key, None)
    await _safe_edit(query.message, "❌ <b>Elvetve.</b>")
    await query.answer("Elvetve")
