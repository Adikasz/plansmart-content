"""Phase 21 Part 1 — /metrics (/overview) + /metrics_today Telegram parancsok.

Tiszta megjelenítési réteg a src/storage/metrics.py adat-aggregációja fölött. Külön Router —
a src/workers/main.py fűzi be a többi mellé (nincs catch-all handler, tetszőleges sorrendben
mehet a többi router mellett).
"""
from __future__ import annotations

import html
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.storage import metrics as metrics_store
from src.storage import posts as posts_store

logger = logging.getLogger(__name__)

router = Router()

VOICE_ORDER = ("david", "adam", "plansmart")


def _fmt_usd(v: float) -> str:
    return f"${v:.2f}"


def _format_section(title: str, data: dict) -> str:
    c, o, costs = data["content"], data["outreach"], data["costs"]
    by_voice = ", ".join(f"{v}: {c['by_voice'].get(v, 0)}" for v in VOICE_ORDER)

    if o["replied"] is None:
        replied_line = "  Válaszolt: — (futtasd a scripts/migration_21_prospect_tracking.sql-t)"
    else:
        replied_line = f"  Válaszolt: <b>{o['replied']}</b>"

    lines = [
        f"<b>{title}</b>",
        "",
        "<b>TARTALOM</b>",
        f"  Generált poszt: <b>{c['generated_total']}</b> ({by_voice})",
        f"  Jóváhagyva: <b>{c['approved']}</b> | Elutasítva: <b>{c['rejected']}</b> "
        f"| Szerkesztve: <b>{c['edited']}</b>",
        f"  Kiposztolva (manuálisan jelölve): <b>{c['published']}</b>",
        f"  Breaking news: <b>{c['breaking']}</b>",
        "",
        "<b>KAPCSOLATÉPÍTÉS</b>",
        f"  Új kutatott jelölt: <b>{o['researched']}</b>",
        f"  Jóváhagyva küldésre: <b>{o['approved_to_send']}</b>",
        f"  Ténylegesen elküldve (/mark_sent): <b>{o['sent']}</b>",
        replied_line,
        "",
        "<b>KÖLTSÉG</b>",
        f"  Claude API (Sonnet + Haiku): <b>{_fmt_usd(costs.get('claude_api', 0.0))}</b>",
        f"  Muapi (vizuál): <b>{_fmt_usd(costs.get('muapi_image', 0.0))}</b>",
        f"  Összesen: <b>{_fmt_usd(costs.get('total', 0.0))}</b>",
    ]
    return "\n".join(lines)


def _format_daily_costs(since_iso: str) -> str:
    rows = posts_store.daily_cost_breakdown(since_iso)
    if not rows:
        return ""
    lines = ["", "<b>Napi bontás (költség)</b>"]
    for r in rows:
        claude = r.get("claude_api", 0.0)
        muapi = r.get("muapi_image", 0.0)
        lines.append(f"  {r['date']}: {_fmt_usd(r.get('total', 0.0))} "
                      f"(Claude {_fmt_usd(claude)}, Muapi {_fmt_usd(muapi)})")
    return "\n".join(lines)


@router.message(Command("metrics"))
@router.message(Command("overview"))
async def metrics_cmd(message: Message) -> None:
    try:
        today = metrics_store.collect_today()
        week = metrics_store.collect_week()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Metrics lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return

    parts = [
        "📊 <b>PlanSmart Content — heti áttekintő</b>",
        "",
        _format_section("📅 MA", today),
        "",
        _format_section("📈 EZ A HÉT (hétfőtől)", week),
    ]
    daily = _format_daily_costs(week["since"])
    if daily:
        parts.append(daily)
    await message.answer("\n".join(parts))


@router.message(Command("metrics_today"))
async def metrics_today_cmd(message: Message) -> None:
    try:
        today = metrics_store.collect_today()
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Metrics lekérdezés hiba: {html.escape(str(exc)[:150])}")
        return
    text = "📊 <b>PlanSmart Content — mai gyorsellenőrzés</b>\n\n" + _format_section("📅 MA", today)
    await message.answer(text)
