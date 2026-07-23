"""Teszt-posztok takarítása a Supabase posts + approvals tábláiból.

Törlési feltétel (posts):  generated_at < ma (UTC)  VAGY  (status='pending' ÉS voice='test')
Az approvals soroknál a 'created_at' megfelelője az `actioned_at` (a posts-é a `generated_at`).
Az adott posztokhoz tartozó approvals sorok FK ON DELETE CASCADE miatt a poszt törlésekor
automatikusan eltűnnek; emellett a régi (actioned_at < ma) approvals sorokat is takarítjuk.

Biztonság: FUTTASD ELŐSZÖR --dry-run-nal — az csak kiírja, mit törölne.

Használat:
    python scripts/cleanup_test_posts.py --dry-run     # csak előnézet, NEM töröl
    python scripts/cleanup_test_posts.py               # ténylegesen töröl
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from src.core.storage.db import get_client, has_service_key  # noqa: E402

logger = logging.getLogger("cleanup_test_posts")

# A "created_at" logikai mező tényleges oszlopnevei táblánként.
POSTS_TIME_COL = "generated_at"
APPROVALS_TIME_COL = "actioned_at"


def _today_start_iso() -> str:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _posts_or_filter(today_iso: str) -> str:
    """PostgREST or_() kifejezés: generated_at < ma  VAGY  (status=pending ÉS voice=test)."""
    return f"{POSTS_TIME_COL}.lt.{today_iso},and(status.eq.pending,voice.eq.test)"


def _select_target_posts(client, today_iso: str) -> list[dict]:
    resp = (
        client.table("posts")
        .select("id, voice, platform, status, " + POSTS_TIME_COL)
        .or_(_posts_or_filter(today_iso))
        .execute()
    )
    return resp.data or []


def _select_target_approvals(client, today_iso: str, post_ids: list[str]) -> list[dict]:
    """Régi approvals (actioned_at < ma) + a törlendő posztokhoz tartozó approvals (cascade)."""
    by_time = (
        client.table("approvals").select("id, post_id, action, " + APPROVALS_TIME_COL)
        .lt(APPROVALS_TIME_COL, today_iso).execute().data or []
    )
    seen = {r["id"] for r in by_time}
    rows = list(by_time)
    if post_ids:
        by_post = (
            client.table("approvals").select("id, post_id, action, " + APPROVALS_TIME_COL)
            .in_("post_id", post_ids).execute().data or []
        )
        for r in by_post:
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
    return rows


def _print_rows(title: str, rows: list[dict], limit: int = 20) -> None:
    logger.info("%s: %d sor", title, len(rows))
    for r in rows[:limit]:
        logger.info("   %s", {k: v for k, v in r.items()})
    if len(rows) > limit:
        logger.info("   … és még %d sor", len(rows) - limit)


def run(dry_run: bool) -> int:
    if not has_service_key():
        logger.warning("Nincs SUPABASE_SERVICE_KEY — RLS miatt a törlés sikertelen lehet.")
    client = get_client(use_service_key=has_service_key())
    today_iso = _today_start_iso()
    logger.info("Ma (UTC) kezdete: %s", today_iso)
    logger.info("Feltétel (posts): %s < ma  VAGY  (status=pending ÉS voice=test)\n", POSTS_TIME_COL)

    posts = _select_target_posts(client, today_iso)
    post_ids = [r["id"] for r in posts]
    approvals = _select_target_approvals(client, today_iso, post_ids)

    _print_rows("Törlendő POSTS", posts)
    _print_rows("Törlendő APPROVALS (idő + cascade)", approvals)

    if not posts and not approvals:
        logger.info("\nNincs törölni való. ✅")
        return 0

    if dry_run:
        logger.info("\n[DRY RUN] Semmit nem töröltem. Futtasd --dry-run nélkül a tényleges törléshez.")
        return 0

    # 1) Régi approvals (actioned_at < ma) — explicit törlés.
    client.table("approvals").delete().lt(APPROVALS_TIME_COL, today_iso).execute()
    # 2) Posts törlése a feltétellel — a hozzájuk tartozó approvals FK CASCADE-del eltűnik.
    try:
        client.table("posts").delete().or_(_posts_or_filter(today_iso)).execute()
    except Exception as exc:
        logger.error(
            "Posts törlés hiba (talán a `published` tábla FK-zik egy posztra): %s", str(exc)[:200]
        )
        logger.error("Tipp: előbb töröld a kapcsolódó published sorokat, vagy zárd ki azokat a posztokat.")
        return 1

    # Ellenőrzés
    left_posts = _select_target_posts(client, today_iso)
    left_appr = _select_target_approvals(client, today_iso, [r["id"] for r in left_posts])
    logger.info(
        "\nTörölve. Maradék (a feltételre): posts=%d, approvals=%d", len(left_posts), len(left_appr)
    )
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")

    ap = argparse.ArgumentParser(description="Teszt-posztok takarítása (posts + approvals).")
    ap.add_argument("--dry-run", action="store_true", help="csak kiírja, mit törölne — NEM töröl")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
