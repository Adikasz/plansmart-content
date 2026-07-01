"""Telegram approval bot (aiogram 3.x) — generált posztok jóváhagyása.

4 inline gomb: ✅ Approve | ✏️ Edit | 🔄 Regenerate | ❌ Skip.
A döntések a Supabase posts + approvals tábláiba kerülnek.

Futtatás:
    python -m src.bots.telegram_bot
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from src.generators.base_generator import generate as generate_post
from src.publishers import token_store
from src.publishers.linkedin_publisher import post_to_linkedin
from src.storage import posts as posts_store
from src.strategy import content_strategy
from src.visuals import visual_generator

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSTS_CHAT_ID = int(os.environ.get("TELEGRAM_POSTS_CHAT_ID", "0"))
REACTIONS_CHAT_ID = int(os.environ.get("TELEGRAM_REACTIONS_CHAT_ID", "0"))
TELEGRAM_CAPTION_LIMIT = 1024  # Telegram photo caption max hossza


def _linkedin_mock() -> bool:
    """LINKEDIN_MOCK=true a .env-ben → nincs éles LinkedIn API hívás."""
    return os.environ.get("LINKEDIN_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def _accounts_cfg() -> dict:
    with open(PROJECT_ROOT / "config" / "accounts.yml", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("accounts", {})


def _author_urn(voice: str) -> str | None:
    """A voice LinkedIn author URN-je az accounts.yml-ből (üres, ha még nincs OAuth)."""
    li = (_accounts_cfg().get(voice) or {}).get("linkedin", {})
    return (li.get("linkedin_urn") or "").strip() or None

VOICE_DISPLAY = {"david": ("DÁVID", "🔨"), "adam": ("ÁDÁM", "📊"), "plansmart": ("PLANSMART", "🏢")}
PLATFORM_DISPLAY = {"linkedin": "LinkedIn", "twitter": "X"}

# /create parancs — kézi poszt generálás
VOICE_PROMPTS = {
    "david": "prompts/voice_david.md",
    "adam": "prompts/voice_adam.md",
    "plansmart": "prompts/voice_plansmart.md",
}
PLATFORMS = {"linkedin", "twitter"}
CREATE_USAGE = (
    "Használat:\n"
    "<code>/create &lt;voice&gt; &lt;platform&gt;</code>\n"
    "&lt;többsoros instrukció&gt;\n\n"
    "voice: david | adam | plansmart  •  platform: linkedin | twitter (plansmart: csak linkedin)\n\n"
    "Példa:\n"
    "<code>/create david linkedin\n"
    "Téma: Múlt héten egy ügyfélnél bevezettünk egy n8n workflow-t.\n"
    "Mit szeretnék elmondani: A KKV-knak megéri kis lépésekben kezdeni.</code>"
)

router = Router()
_bot: Bot | None = None
# user_id -> aktív szerkesztés kontextusa {post_id, chat_id, message_id} (DM flow, in-memory MVP).
_pending_edits: dict[int, dict] = {}
# post_id -> (chat_id, message_id): hol van a csoportposzt, hogy "✏️ Edited"-re frissíthessük.
_post_messages: dict[str, tuple[int, int]] = {}


class ApprovalCB(CallbackData, prefix="appr"):
    action: str
    post_id: str


def get_bot() -> Bot:
    """Cache-elt Bot példány (HTML parse mode)."""
    global _bot
    if _bot is None:
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        _bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return _bot


def _source_domain(url: str) -> str:
    netloc = urlparse(url or "").netloc
    return netloc[4:] if netloc.startswith("www.") else (netloc or "—")


def format_approval_message(post: dict) -> str:
    name, emoji = VOICE_DISPLAY.get(post.get("voice", ""), (str(post.get("voice", "?")).upper(), "📝"))
    platform = PLATFORM_DISPLAY.get(post.get("platform", ""), post.get("platform", ""))
    score = post.get("score")
    if score is None:
        meta = "Kézi poszt (/create)"
    else:
        meta = f"Score: {score}/10 | Forrás: {_source_domain(post.get('feed_item_url', ''))}"
    parts = [
        f"<b>[{name} — {platform}]</b> {emoji}",
        meta,
        "",
        html.escape(post.get("content", "")),
    ]
    tags = " ".join(post.get("hashtags") or [])
    if tags:
        parts += ["", tags]
    return "\n".join(parts)


def build_keyboard(post_id: str) -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=ApprovalCB(action=action, post_id=post_id).pack())

    return InlineKeyboardMarkup(inline_keyboard=[[
        btn("✅ Approve", "approve"),
        btn("✏️ Edit", "edit"),
        btn("🔄 Regenerate", "regenerate"),
        btn("❌ Skip", "skip"),
    ]])


async def _attach_visual(post: dict) -> dict:
    """Best-effort: vizuált generál a poszthoz (Muapi) és beállítja a visual_url-t.

    Hiba (hiányzó MUAPI_API_KEY, Muapi hiba) esetén csak warn — a poszt jóváhagyása
    így is megy, csak kép nélkül.
    """
    try:
        # Phase 12.5: szöveg-mentes alapkép (Muapi) → magyar PIL overlay → feltöltés.
        vis = await visual_generator.compose_visual(post)
        post["visual_url"] = vis["image_url"]
        post["base_image_url"] = vis.get("base_image_url")
        if post.get("id"):
            posts_store.save_visual(post["id"], vis["image_url"], base_image_url=vis.get("base_image_url"))
        logger.info("Vizual kesz (%s): %s", post.get("voice"), vis["image_url"])
    except Exception as exc:
        logger.warning("Vizual generalas kihagyva: %s", str(exc)[:160])
    return post


async def send_for_approval(
    post: dict, chat_id: int, bot: Bot | None = None, header: str = ""
) -> Message:
    """Egy generált posztot elküld jóváhagyásra — képpel (ha van visual_url) + caption.

    header: opcionális előtag a caption elé (pl. "🚨 BREAKING — Azonnali hír\\n\\n" vagy
    "☀️ Reggeli poszt — 2026-06-24\\n\\n"). Phase 10: breaking / reggeli poszt jelölés.
    """
    bot = bot or get_bot()
    post_id = post.get("id") or uuid.uuid4().hex[:12]
    caption = header + format_approval_message({**post, "id": post_id})
    keyboard = build_keyboard(post_id)
    visual_url = post.get("visual_url")

    if visual_url and len(caption) <= TELEGRAM_CAPTION_LIMIT:
        sent = await bot.send_photo(chat_id, photo=visual_url, caption=caption, reply_markup=keyboard)
    elif visual_url:
        # A poszt hosszabb a caption-limitnél: kép külön, a gombos szöveg külön üzenetben.
        try:
            await bot.send_photo(chat_id, photo=visual_url)
        except Exception as exc:
            logger.warning("Kep kuldes sikertelen (%s) — csak szoveg megy.", str(exc)[:100])
        sent = await bot.send_message(chat_id, caption, reply_markup=keyboard)
    else:
        sent = await bot.send_message(chat_id, caption, reply_markup=keyboard)

    _post_messages[post_id] = (chat_id, sent.message_id)  # később az "✏️ Edited" frissítéshez
    return sent


# ── Parancsok ──────────────────────────────────────────────────────────
@router.message(CommandStart())
async def start_cmd(message: Message) -> None:
    if message.chat.type == "private":
        await message.answer(
            "PlanSmart Content Bot aktív ✅\n\n"
            "Szerkesztés: a csoportban kattints egy poszt ✏️ Edit gombjára, "
            "majd az új szöveget ide (DM-be) küldd."
        )
    else:
        await message.answer("PlanSmart Content Bot aktív ✅")


@router.message(Command("test"))
async def test_cmd(message: Message, bot: Bot) -> None:
    mock = {
        "voice": "david", "platform": "linkedin", "score": 9,
        "feed_item_url": "https://www.anthropic.com/news/claude-opus-4-8",
        "content": "Teszt poszt. Az Anthropic kihozta a Claude Opus 4.8-at — "
                   "ma este megnézem élesben, és ha tartja amit ígér, átmigrálom a pipeline-t.",
        "hashtags": ["#ClaudeAPI", "#BuildInPublic"],
    }
    mock["id"] = posts_store.insert_post(mock)
    await _attach_visual(mock)
    sent = await send_for_approval(mock, POSTS_CHAT_ID, bot)
    await message.answer(f"Teszt poszt elküldve (post_id={mock['id']}, msg={sent.message_id}).")


def _result_to_post(result: dict, voice: str, platform: str) -> dict:
    """A generátor JSON-jából a kiválasztott platform tartalmát posts-sorrá alakítja."""
    if platform == "twitter":
        tw = result.get("twitter") or {}
        tweets = [t for t in (tw.get("tweets") or []) if t]
        content, content_type, hashtags = "\n\n".join(tweets), tw.get("type", "tweet"), []
    else:
        li = result.get("linkedin") or {}
        content, content_type, hashtags = li.get("content", ""), "post", (li.get("hashtags") or [])
    return {
        "voice": voice, "platform": platform, "content": content,
        "content_type": content_type, "hashtags": hashtags,
        "feed_item_url": "", "score": None,  # kézi poszt: nincs forrás/score
    }


@router.message(Command("create"))
async def create_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    first_line, _, instruction = (command.args or "").partition("\n")
    tokens = first_line.split()
    instruction = instruction.strip()
    if len(tokens) < 2 or not instruction:
        await message.answer(CREATE_USAGE)
        return
    voice, platform = tokens[0].lower(), tokens[1].lower()
    if voice not in VOICE_PROMPTS:
        await message.answer(f"Ismeretlen voice: <b>{voice}</b>.\n\n{CREATE_USAGE}")
        return
    if platform not in PLATFORMS:
        await message.answer(f"Ismeretlen platform: <b>{platform}</b>.\n\n{CREATE_USAGE}")
        return
    if voice == "plansmart" and platform != "linkedin":
        await message.answer("A PlanSmart hang csak <b>linkedin</b>-t támogat.")
        return

    await message.answer(f"⏳ Generálás: {voice} / {platform} …")
    manual = {"type": "manual_instruction", "instruction": instruction, "voice": voice, "platform": platform}
    try:
        result = await generate_post(manual, VOICE_PROMPTS[voice])
    except Exception as exc:
        logger.warning("Manual generalas hiba: %s", str(exc)[:150])
        await message.answer(f"❌ Generálási hiba: {str(exc)[:150]}")
        return
    if result is None:
        await message.answer("A modell nem generált posztot (skip).")
        return

    post = _result_to_post(result, voice, platform)
    if not post["content"].strip():
        await message.answer(f"❌ A modell nem adott vissza tartalmat a(z) {platform} platformra.")
        return
    post["id"] = posts_store.insert_post(post)
    await message.answer("🎨 Vizuál generálása …")
    await _attach_visual(post)
    await send_for_approval(post, POSTS_CHAT_ID, bot)
    await message.answer(f"✅ Poszt jóváhagyásra küldve (post_id={post['id']}).")


# ── Callback handlerek ─────────────────────────────────────────────────
def _msg_base_text(message: Message) -> str:
    """A meglévő üzenet HTML szövege — szöveges ÉS kép-caption üzenetnél is működik."""
    try:
        t = message.html_text
        if t:
            return t
    except Exception:  # pl. nincs text/caption entity
        pass
    return message.caption or message.text or ""


async def _safe_edit_message(message: Message, text: str, reply_markup=None) -> None:
    """Szerkeszti az üzenetet — szövegként, vagy ha kép, caption-ként (limitre vágva)."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await message.edit_caption(caption=text[:TELEGRAM_CAPTION_LIMIT], reply_markup=reply_markup)
        except Exception as exc:
            logger.warning("Nem sikerult szerkeszteni az uzenetet: %s", str(exc)[:120])


async def _safe_edit_by_id(bot: Bot, chat_id: int, message_id: int, text: str, reply_markup=None) -> None:
    """Mint _safe_edit_message, de chat_id+message_id alapján (a csoportposzt frissítéséhez)."""
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
    except Exception:
        try:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id,
                caption=text[:TELEGRAM_CAPTION_LIMIT], reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.warning("Nem sikerult frissiteni a csoportposztot: %s", str(exc)[:120])


async def _finalize(query: CallbackQuery, cb: ApprovalCB, status: str, footer: str) -> None:
    by = query.from_user.username or query.from_user.full_name
    if status == "approved":
        posts_store.mark_approved(cb.post_id, by)
    else:
        posts_store.update_post_status(cb.post_id, status)
    posts_store.record_approval(
        cb.post_id, query.message.chat.id, query.message.message_id,
        action=cb.action, telegram_user_id=query.from_user.id,
    )
    await _safe_edit_message(query.message, f"{_msg_base_text(query.message)}\n\n{footer} — @{by}", reply_markup=None)
    await query.answer(footer)


async def _publish_failed(query: CallbackQuery, base: str, by: str, post_id: str, error: str) -> None:
    """Sikertelen posztolás: marad 'approved' (újrapróbálható), a gombok megmaradnak."""
    posts_store.mark_approved(post_id, by)
    await _safe_edit_message(
        query.message,
        f"{base}\n\n❌ Publish failed: {html.escape(error[:150])} — @{by}",
        reply_markup=build_keyboard(post_id),  # gombok maradnak → újra Approve-olható
    )
    await query.answer("Publish failed", show_alert=True)


@router.callback_query(ApprovalCB.filter(F.action == "approve"))
async def approve_cb(query: CallbackQuery, callback_data: ApprovalCB) -> None:
    """✅ Approve → LinkedIn posztolás (mock vagy éles), majd a Telegram üzenet frissítése."""
    post_id = callback_data.post_id
    by = query.from_user.username or query.from_user.full_name
    post = posts_store.get_post(post_id) or {}
    posts_store.record_approval(
        post_id, query.message.chat.id, query.message.message_id,
        action="approve", telegram_user_id=query.from_user.id,
    )

    base = _msg_base_text(query.message)
    platform = post.get("platform", "linkedin")
    voice = post.get("voice", "")
    content = post.get("edited_content") or post.get("content") or ""
    hashtags = post.get("hashtags") or []

    # Automatikus posztolás csak LinkedInre; minden más egyelőre csak approve.
    if platform != "linkedin":
        posts_store.mark_approved(post_id, by)
        await _safe_edit_message(query.message, f"{base}\n\n✅ Approved ({platform}) — @{by}", reply_markup=None)
        await query.answer("Approved")
        return

    mock = _linkedin_mock()
    author_urn = _author_urn(voice)
    access_token = ""

    if mock:
        # Mock módban nem kell valós token; placeholder URN, ha még nincs OAuth.
        author_urn = author_urn or f"urn:li:person:{voice.upper()}_PLACEHOLDER"
    else:
        if not author_urn:
            await _publish_failed(query, base, by, post_id, f"nincs linkedin_urn az accounts.yml-ben ({voice})")
            return
        try:
            token = token_store.get_token(voice)
        except Exception as exc:
            await _publish_failed(query, base, by, post_id, f"token lekérés hiba: {exc}")
            return
        if not token or not token.get("access_token"):
            await _publish_failed(query, base, by, post_id, f"nincs érvényes token ({voice})")
            return
        access_token = token["access_token"]

    try:
        urn = await post_to_linkedin(access_token, author_urn, content, hashtags, post_id=post_id, mock=mock)
    except Exception as exc:
        await _publish_failed(query, base, by, post_id, str(exc))
        return

    if mock:
        posts_store.mark_approved(post_id, by)
        footer = "✅ Approved (mock) — LinkedIn API pending"
    else:
        # post_to_linkedin éles ágon már beírta a published táblába; itt csak a státusz.
        posts_store.mark_published(post_id, by)
        footer = f"✅ Published — {urn}"
    await _safe_edit_message(query.message, f"{base}\n\n{footer} — @{by}", reply_markup=None)
    await query.answer(footer[:200])


@router.callback_query(ApprovalCB.filter(F.action == "skip"))
async def skip_cb(query: CallbackQuery, callback_data: ApprovalCB) -> None:
    await _finalize(query, callback_data, "skipped", "❌ Skipped")


@router.callback_query(ApprovalCB.filter(F.action == "regenerate"))
async def regenerate_cb(query: CallbackQuery, callback_data: ApprovalCB) -> None:
    await _finalize(query, callback_data, "regenerated", "🔄 Regenerating...")


@router.callback_query(ApprovalCB.filter(F.action == "edit"))
async def edit_cb(query: CallbackQuery, callback_data: ApprovalCB, bot: Bot) -> None:
    post_id = callback_data.post_id
    user_id = query.from_user.id
    post = posts_store.get_post(post_id) or {}
    original = post.get("content") or "(nincs tartalom)"
    # Jegyezzük meg a csoportposzt helyét, hogy később "✏️ Edited"-re frissíthessük.
    _post_messages[post_id] = (query.message.chat.id, query.message.message_id)

    dm_text = (
        "✏️ Küldj új szöveget ide (privát üzenet):\n\n"
        f"<b>Eredeti ({post.get('voice', '?')} — {post.get('platform', '?')}):</b>\n"
        f"{html.escape(original)}"
    )
    try:
        await bot.send_message(user_id, dm_text)
        _pending_edits[user_id] = {
            "post_id": post_id,
            "chat_id": query.message.chat.id,
            "message_id": query.message.message_id,
        }
        logger.info("Edit DM elkuldve: user=%s post=%s", user_id, post_id)
        await query.answer("Nézd meg a privát üzeneteidet (DM) ✏️")
    except Exception as exc:  # a user még nem indította el a botot DM-ben
        logger.warning("DM sikertelen (user=%s): %s", user_id, str(exc)[:120])
        await query.answer("Nem tudok privát üzenetet küldeni — lásd a csoportüzenetet.", show_alert=True)
        await bot.send_message(
            query.message.chat.id,
            f"@{query.from_user.username or query.from_user.id} — előbb indítsd el a botot DM-ben "
            f"(/start @{(await bot.me()).username}), vagy írd be ide:\n"
            f"<code>/edit {post_id} &lt;új szöveg&gt;</code>",
        )


async def _apply_edit(bot: Bot, post_id: str, new_text: str, user, chat_id, message_id) -> None:
    """Elmenti a szerkesztett szöveget, és (ha ismert) a csoportposztot "✏️ Edited"-re frissíti."""
    posts_store.mark_edited(post_id, new_text)
    posts_store.record_approval(
        post_id, chat_id, message_id, action="edited",
        telegram_user_id=user.id, action_data={"edited_content": new_text},
    )
    if chat_id and message_id:
        preview = new_text[:50] + ("…" if len(new_text) > 50 else "")
        await _safe_edit_by_id(
            bot, chat_id, message_id, f"✏️ Edited: {html.escape(preview)}", reply_markup=None,
        )
    logger.info("Edit mentve: post_id=%s (%d karakter)", post_id, len(new_text))


# Fallback DM nélkül: /edit <post_id> <új szöveg> — parancs, így a csoportban is megérkezik.
@router.message(Command("edit"))
async def edit_command(message: Message, command: CommandObject, bot: Bot) -> None:
    post_id, _, new_text = (command.args or "").strip().partition(" ")
    if not post_id or not new_text.strip():
        await message.answer("Használat: /edit <post_id> <új szöveg>")
        return
    chat_id, message_id = _post_messages.get(post_id, (None, None))
    await _apply_edit(bot, post_id, new_text.strip(), message.from_user, chat_id, message_id)
    await message.answer(f"✏️ Szerkesztés mentve (post_id={post_id}).")


# DM-ben érkező új szöveg (privacy mode-tól függetlenül kézbesül).
@router.message(F.chat.type == "private")
async def dm_edit_reply(message: Message, bot: Bot) -> None:
    ctx = _pending_edits.pop(message.from_user.id, None)
    if not ctx:
        await message.answer("Nincs aktív szerkesztés. A csoportban kattints egy poszt ✏️ Edit gombjára.")
        return
    new_text = (message.text or "").strip()
    if not new_text:
        _pending_edits[message.from_user.id] = ctx  # visszatesszük, várunk szövegre
        await message.answer("Üres üzenet — küldj szöveget.")
        return
    await _apply_edit(bot, ctx["post_id"], new_text, message.from_user, ctx.get("chat_id"), ctx.get("message_id"))
    await message.answer("✏️ Szerkesztés mentve, a csoportposzt frissítve.")


def _format_token(info: dict) -> str:
    """Token állapot egy soros, emberi formában a /status-hoz."""
    if info.get("error"):
        return f"⚠️ tábla/hiba ({info['error']})"
    if not info.get("exists"):
        return "⚠️ nincs token (OAuth szükséges)"
    days = info.get("days_left")
    if days is None:
        return "✅ token (nincs lejárati idő)"
    if days <= 0:
        return f"❌ LEJÁRT ({days:.0f} nap) — újra-auth kell"
    if days < token_store.WARN_THRESHOLD_DAYS:
        return f"⚠️ {days:.0f} nap — hamarosan lejár, refresh kell"
    return f"✅ {days:.0f} nap"


def _week_start_iso() -> str:
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _format_strategy_account(rep: dict) -> str:
    name, _emoji = VOICE_DISPLAY.get(rep["account"], (rep["account"].upper(), ""))
    order = content_strategy.CONTENT_TYPES
    label = content_strategy.TYPE_LABEL

    def pct_line(dist: dict) -> str:
        return ", ".join(f"{round(dist.get(c, 0) * 100)}% {label[c]}" for c in order if c in rep["target"])

    return "\n".join([
        f"<b>{name}</b> (LinkedIn)",
        f"  Cél: {pct_line(rep['target'])}",
        f"  Tényleges: {pct_line(rep['actual'])}  ({rep['total_posts']} poszt / cél {rep['weekly_target']})",
        f"  Következő javaslat: <b>{rep['next_type']}</b>",
    ])


@router.message(Command("strategy"))
async def strategy_cmd(message: Message) -> None:
    """Per-account heti tartalom-eloszlás (cél vs tényleges) + következő javaslat."""
    week_start = _week_start_iso()
    try:
        blocks = []
        for account in content_strategy.accounts():
            counts = posts_store.weekly_content_type_counts(account, week_start)
            rep = content_strategy.distribution_report(account, counts)
            blocks.append(_format_strategy_account(rep))
    except Exception as exc:
        await message.answer(f"❌ Stratégia lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    await message.answer("📋 <b>Tartalom stratégia — heti állás (hétfőtől)</b>\n\n" + "\n\n".join(blocks))


@router.message(Command("status"))
async def status_cmd(message: Message) -> None:
    """Napi poszt-statisztika + token lejárati figyelmeztetések fiókonként."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        counts = posts_store.status_counts_since(today_start)
    except Exception as exc:
        await message.answer(f"❌ Státusz lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return

    known = ("pending", "approved", "published", "skipped")
    lines = ["📊 <b>Állapot — ma (UTC)</b>", "", "<b>Posztok:</b>"]
    lines += [f"  {st}: <b>{counts.get(st, 0)}</b>" for st in known]
    for st, n in counts.items():  # egyéb státuszok (edited, regenerated, posted, failed…)
        if st not in known:
            lines.append(f"  {st}: {n}")
    total = sum(counts.values())
    lines.append(f"  <i>összesen: {total}</i>")

    mode = "MOCK" if _linkedin_mock() else "ÉLES"
    lines += ["", f"<b>Tokenek</b> (LinkedIn mód: {mode}):"]
    for acc in ("david", "adam", "plansmart"):
        lines.append(f"  {acc}: {_format_token(token_store.token_status(acc))}")

    await message.answer("\n".join(lines))


@router.message(Command("eval_visuals"))
async def eval_visuals_cmd(message: Message) -> None:
    """Aktuális vizuál-minőség (5 random friss kép pontozva) vs. baseline."""
    await message.answer("🎨 Vizuál minőség mérése (5 random friss kép pontozása)…")
    try:
        from src.optimization.quality_monitor import get_current_quality

        q = await get_current_quality()
    except Exception as exc:
        await message.answer(f"❌ Eval hiba: {html.escape(str(exc)[:150])}")
        return

    cur = q["current"]
    if not cur.get("sample_size"):
        await message.answer("Nincs értékelhető vizuál (nincs friss visual_url-es poszt).")
        return

    lines = ["🎨 <b>Vizuál minőség — most</b>", ""]
    lines.append(f"Össz-átlag: <b>{cur['overall_avg']}</b>/10  (minta: {cur['sample_size']})")
    for v, s in cur["per_voice"].items():
        lines.append(f"  {v}: <b>{s}</b>")
    base = q.get("baseline_winner_avg")
    if base is not None:
        delta = round((cur["overall_avg"] or 0) - base, 2)
        arrow = "🟢" if delta >= 0 else "🔴"
        lines += ["", f"Baseline (győztes <b>{q.get('baseline_winner')}</b>): {base} "
                      f"{arrow} {'+' if delta >= 0 else ''}{delta}"]
    else:
        lines += ["", "<i>Nincs baseline — futtasd: python -m scripts.run_visual_eval</i>"]
    await message.answer("\n".join(lines))


async def _amain() -> None:
    bot = get_bot()
    me = await bot.get_me()
    logger.info("Bot inditva: @%s (id=%s) | posts_chat=%s reactions_chat=%s",
                me.username, me.id, POSTS_CHAT_ID, REACTIONS_CHAT_ID)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
