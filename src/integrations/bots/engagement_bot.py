"""Phase 21b — /log_stats + /engagement_report Telegram parancsok.

A LinkedIn API még nem éles: Dávid/Ádám kézzel nézi meg a számokat LinkedInen, és jelenti be
a /log_stats paranccsal. Ez a fájl a megjelenítési réteg a src/core/storage/engagement.py (mentés)
+ src/core/storage/engagement_report.py (tiszta aggregáció) fölött. Külön Router — a
src/core/workers/main.py fűzi be a többi mellé (nincs catch-all handler).
"""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.core.storage import engagement as engagement_store
from src.core.storage import engagement_report as report
from src.core.storage import posts as posts_store

logger = logging.getLogger(__name__)

router = Router()

_TABLE_MISSING = (
    "⚠️ Az engagement_metrics tábla még nincs a Supabase-ben — futtasd a "
    "<code>scripts/migration_22_engagement.sql</code>-t."
)

LOG_STATS_USAGE = (
    "Használat:\n"
    "<code>/log_stats &lt;post_id&gt; views=450 likes=12 comments=3 shares=1</code>\n\n"
    "Legalább egy metrikát adj meg. Opcionális: <code>final=true</code> (kézi felülírás — "
    "alapból automatikusan végleges lesz, ha 48+ óra telt el a posztolás óta)."
)

VOICE_ORDER = ("david", "adam", "plansmart")
HOOK_ORDER = ("A", "B", "C", "D", "E")
CONTENT_TYPE_ORDER = (
    "educational",
    "workshop_promo",
    "case_study",
    "ai_news",
    "ai_news_breaking",
    "egyeb",
)
CONTENT_TYPE_LABEL = {
    "educational": "oktató",
    "workshop_promo": "workshop",
    "case_study": "case study",
    "ai_news": "news",
    "ai_news_breaking": "breaking news",
    "egyeb": "egyéb",
}
TEMPLATE_ORDER = ("STAT_CARD", "QUOTE_STYLE", "SPLIT_COMPARISON", "MINIMAL_TYPOGRAPHIC")


async def _guard(message: Message) -> bool:
    try:
        ok = engagement_store.table_ready()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ engagement_metrics tábla hiba: {html.escape(str(exc)[:120])}")
        return False
    if not ok:
        await message.answer(_TABLE_MISSING)
        return False
    return True


def _parse_kv_args(args: str) -> tuple[str, dict[str, str]]:
    tokens = (args or "").split()
    if not tokens:
        return "", {}
    kv: dict[str, str] = {}
    for t in tokens[1:]:
        if "=" in t:
            k, _, v = t.partition("=")
            kv[k.strip().lower()] = v.strip()
    return tokens[0], kv


def _parse_int(s: str | None) -> int | None:
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        raise ValueError(f"'{s}' nem egész szám") from None


@router.message(Command("log_stats"))
async def log_stats_cmd(message: Message, command: CommandObject) -> None:
    if not await _guard(message):
        return
    post_id, kv = _parse_kv_args(command.args or "")
    if not post_id:
        await message.answer(LOG_STATS_USAGE)
        return

    try:
        post = posts_store.get_post(post_id)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    if not post:
        await message.answer(f"Nincs ilyen poszt: <code>{html.escape(post_id)}</code>")
        return

    if not kv:
        # "Bot kérdez" mód -- szándékosan NEM egy stateful multi-turn kérdező flow (lásd Phase
        # 21b spec: "keep this simple, don't over-engineer a reminder scheduler yet"), hanem a
        # már bevett /pnote-/add_note-stílusú "itt a pontos parancs, töltsd ki" minta.
        preview = (post.get("content") or "")[:80].replace("\n", " ")
        await message.answer(
            f"📊 <b>{html.escape(post.get('voice') or '?')}</b> poszt — {html.escape(preview)}…\n\n"
            f"Add meg a LinkedInen látott számokat:\n"
            f"<code>/log_stats {post_id} views=... likes=... comments=... shares=...</code>"
        )
        return

    try:
        views = _parse_int(kv.get("views"))
        likes = _parse_int(kv.get("likes"))
        comments = _parse_int(kv.get("comments"))
        shares = _parse_int(kv.get("shares"))
    except ValueError as exc:
        await message.answer(f"⚠️ Érvénytelen szám: {html.escape(str(exc))}\n\n{LOG_STATS_USAGE}")
        return

    final_raw = kv.get("final")
    is_final = final_raw.lower() in ("1", "true", "igen", "yes") if final_raw is not None else None

    try:
        result = engagement_store.log(
            post_id,
            views=views,
            likes=likes,
            comments=comments,
            shares=shares,
            is_final_snapshot=is_final,
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}\n\n{LOG_STATS_USAGE}")
        return
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Mentés hiba: {html.escape(str(exc)[:150])}")
        return

    given = [
        f"{label}: {v}"
        for label, v in (
            ("views", views),
            ("likes", likes),
            ("comments", comments),
            ("shares", shares),
        )
        if v is not None
    ]
    hours = result["hours_since_post"]
    hours_txt = f"{hours}h" if hours is not None else "ismeretlen (nincs sent_at)"
    final_txt = " · <b>VÉGLEGES</b>" if result["is_final_snapshot"] else ""
    lines = [f"✅ Mentve ({', '.join(given)}) — {hours_txt} a posztolás után{final_txt}."]
    if result["warning"]:
        lines.append(f"\n⚠️ {html.escape(result['warning'])}")
    await message.answer("\n".join(lines))


def _fmt_avg(v: float | None) -> str:
    return "—" if v is None else str(v)


def _fmt_group_line(label: str, stats: dict[str, Any] | None) -> str:
    if stats is None or stats["n"] == 0:
        return f"  {label}: nincs adat"
    metrics = " | ".join(f"{m}: {_fmt_avg(stats.get(f'avg_{m}'))}" for m in report.METRICS)
    caveat = "  ⚠️ kevés adat, még nem megbízható trend." if stats["low_confidence"] else ""
    return f"  {label}: {metrics} (n={stats['n']}){caveat}"


def _format_advisory(best_combo: dict[str, Any] | None) -> str:
    if best_combo is None:
        return "💡 Még nincs elég adat a legjobban teljesítő kombináció meghatározásához."
    hook_label = report.HOOK_LABEL.get(best_combo["hook_type"], best_combo["hook_type"])
    ctype_label = CONTENT_TYPE_LABEL.get(best_combo["content_type"], best_combo["content_type"])
    return (
        f"💡 Legjobban teljesítő kombináció eddig: <b>{best_combo['voice']}</b> + "
        f"<b>{best_combo['hook_type']} ({hook_label})</b> + <b>{ctype_label}</b> "
        f"— de n={best_combo['n']} alapján, várj legalább {report.ADVISORY_MIN_N} mintát "
        f"mielőtt ebből következtetést vonsz."
    )


def format_report(data: dict[str, Any]) -> str:
    if data["total_posts_with_engagement"] == 0:
        return (
            "📈 <b>Engagement report</b>\n\n"
            "Még nincs logolt engagement adat egyetlen poszthoz sem.\n"
            "Használd a <code>/log_stats &lt;post_id&gt; views=... likes=... comments=... shares=...</code> "
            "parancsot, miután megnézted a számokat LinkedInen."
        )

    lines = [
        "📈 <b>Engagement report</b>",
        f"<i>({data['total_posts_with_engagement']} poszthoz van logolt adat)</i>",
        "",
        "<b>HANG SZERINT</b>",
    ]
    for voice in VOICE_ORDER:
        lines.append(_fmt_group_line(voice, data["by_voice"].get(voice)))

    lines += ["", "<b>HOOK TÍPUS SZERINT</b>"]
    for h in HOOK_ORDER:
        label = f"{h} ({report.HOOK_LABEL[h]})"
        lines.append(_fmt_group_line(label, data["by_hook_type"].get(h)))
    if data["hook_type_missing_n"]:
        lines.append(f"  <i>({data['hook_type_missing_n']} poszt hook_type nélkül, kihagyva)</i>")

    lines += ["", "<b>TARTALOM TÍPUS SZERINT</b>"]
    for ct in CONTENT_TYPE_ORDER:
        lines.append(_fmt_group_line(CONTENT_TYPE_LABEL[ct], data["by_content_type"].get(ct)))

    lines += ["", "<b>VIZUÁL SZERINT</b>"]
    lines.append(_fmt_group_line("portréval", data["by_visual"]["with_portrait"]))
    lines.append(_fmt_group_line("portré nélkül", data["by_visual"]["without_portrait"]))
    for t in TEMPLATE_ORDER:
        lines.append(_fmt_group_line(f"  · {t}", data["by_visual"]["by_template"].get(t)))
    if data["by_visual"]["missing_n"]:
        lines.append(
            f"  <i>({data['by_visual']['missing_n']} poszt vizuál-metaadat nélkül, kihagyva "
            f"-- csak az ezután generáltaknál elérhető)</i>"
        )

    lines += ["", _format_advisory(data["best_combo"])]
    return "\n".join(lines)


@router.message(Command("engagement_report"))
async def engagement_report_cmd(message: Message) -> None:
    if not await _guard(message):
        return
    try:
        rows = engagement_store.all_rows()
        if not rows:
            await message.answer(format_report(report.build_report({}, [])))
            return
        post_ids = sorted({r["post_id"] for r in rows if r.get("post_id")})
        posts_by_id = posts_store.get_posts_by_ids(post_ids)
        data = report.build_report(posts_by_id, rows)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Report lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    await message.answer(format_report(data))
