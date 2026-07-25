"""Phase 19 / B4 — ÉLES smoke teszt a reakció-asszisztensre (valós Haiku + Sonnet + Supabase).

⚠️ Ez NEM CI-teszt: valódi Anthropic API-t hív (pénzbe kerül) és a Supabase reactions
táblába ír. A 3 scenariót futtatja végig, ahogy a Telegram bot tenné:
  1. /reply_comment david  — technikai kérdés komment ("How do you handle rate limits in n8n?")
  2. /reply_dm adam         — lead-signal DM (30 fős logisztikai cég, méret-kérdés)
  3. /reply_comment plansmart — spam-szerű komment ("Check out my services!")

Kiírja: osztályozás, generált válasz, és visszaolvasva a reactions tábla sorát.

Futtatás:
    .venv/Scripts/python -m scripts.test_reactions
"""
from __future__ import annotations

import asyncio
import uuid

from dotenv import load_dotenv

from src.ai.outreach.reaction_generator import build_reaction
from src.core.storage import reactions as store
from src.utils.logging import setup_logging

load_dotenv(override=False)

SCENARIOS = [
    {
        "title": "1) /reply_comment david — technikai kérdés",
        "voice": "david", "type": "comment", "is_first_dm": False,
        "context": "Poszt: hogyan skálázunk n8n workflow-kat kis csapatban.",
        "incoming": "How do you handle rate limits in n8n?",
    },
    {
        "title": "2) /reply_dm adam — lead-signal (első üzenet)",
        "voice": "adam", "type": "dm", "is_first_dm": True,
        "context": "",
        "incoming": "Hi, we're a 30-person logistics firm, do you work with our size?",
    },
    {
        "title": "3) /reply_comment plansmart — spam-szerű",
        "voice": "plansmart", "type": "comment", "is_first_dm": False,
        "context": "Poszt: ügyfél-esettanulmány automatizációról.",
        "incoming": "Check out my services! Best AI agency, DM me for a deal 🔥",
    },
]

LINE = "─" * 72


def _persist(sc: dict, result: dict) -> str | None:
    """A bot _persist_new-jével egyenértékű: best-effort insert, majd readback."""
    if not store.table_ready():
        return None
    rid = uuid.uuid4().hex[:12]
    status = "skipped" if result["skip"] else "drafted"
    store.insert_reaction({
        "id": rid, "voice": sc["voice"], "type": sc["type"],
        "incoming_text": sc["incoming"], "context_text": sc["context"] or None,
        "our_reply_draft": result.get("reply"), "classification": result["classification"],
        "language": result["language"], "status": status,
    })
    return rid


async def _run() -> int:
    table_ok = store.table_ready()
    print(f"\nreactions tábla elérhető: {'IGEN' if table_ok else 'NEM (futtasd: scripts/migration_19_reactions.sql)'}\n")

    for sc in SCENARIOS:
        print(LINE)
        print(sc["title"])
        print(f"  bejövő: {sc['incoming']!r}")
        result = await build_reaction(
            sc["voice"], sc["type"], sc["incoming"],
            context_text=sc["context"], is_first_dm=sc["is_first_dm"],
        )
        print(f"  → osztályozás: {result['classification']}  (nyelv: {result['language']}; ok: {result['reason']})")
        if result["skip"]:
            print(f"  → SKIP: {result['skip_message']}")
        else:
            print(f"  → javasolt válasz:\n      {result['reply']}")

        rid = _persist(sc, result)
        if rid:
            row = store.get(rid) or {}
            print(f"  → mentve a reactions táblába: id={rid}  status={row.get('status')}  "
                  f"classification={row.get('classification')}")
        elif table_ok:
            print("  → mentés kihagyva (nem várt hiba)")
        else:
            print("  → mentés kihagyva (tábla még nincs — a migráció után újrafuttatva perzisztál)")
        print()

    return 0


def main() -> int:
    setup_logging()
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
