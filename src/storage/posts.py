"""posts + approvals tárolási réteg — a Telegram approval flow-hoz.

(PROJECT_PLAN.md / CLAUDE.md: src/storage/posts.py a posts táblához.)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.db import get_client, has_service_key

logger = logging.getLogger(__name__)


def _c(client=None):
    return client or get_client(use_service_key=has_service_key())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_post(post: dict[str, Any], client=None) -> str:
    """Beszúr (upsert) egy posts sort; visszaadja az id-t. Hiányzó id-t generál."""
    post_id = post.get("id") or uuid.uuid4().hex[:12]
    row = {
        "id": post_id,
        "feed_item_id": post.get("feed_item_id"),
        "voice": post["voice"],
        "platform": post["platform"],
        "content": post["content"],
        "content_type": post.get("content_type", "post"),
        "hashtags": post.get("hashtags") or [],
        "status": post.get("status", "pending"),
        "model_used": post.get("model_used"),
    }
    if post.get("visual_url") is not None:
        row["visual_url"] = post["visual_url"]
    if post.get("base_image_url") is not None:  # Phase 12.5: nyers Muapi alapkép (overlay előtt)
        row["base_image_url"] = post["base_image_url"]
    if post.get("workshop_mentioned"):  # csak ha igaz — különben a DEFAULT FALSE marad
        row["workshop_mentioned"] = True
    # LinkedIn optimalizálás (Phase 7.7): final_content + diagnosztika oszlopok.
    if post.get("final_content") is not None:
        row["final_content"] = post["final_content"]
    if post.get("hook_type"):
        row["hook_type"] = post["hook_type"]
    if post.get("hook_score") is not None:
        row["hook_score"] = post["hook_score"]
    if post.get("estimated_engagement_tier"):
        row["estimated_engagement_tier"] = post["estimated_engagement_tier"]
    # Phase 10: breaking news + Telegram kiküldés időbélyeg.
    if post.get("is_breaking"):
        row["is_breaking"] = True
    if post.get("sent_at"):
        row["sent_at"] = post["sent_at"]
    # Stratégia-metaadat (content_strategy): a strategy content_type + a seed kulcsa.
    metadata = dict(post.get("metadata") or {})
    if post.get("strategy_type"):
        metadata["strategy_type"] = post["strategy_type"]
    if post.get("seed_key"):
        metadata["seed_key"] = post["seed_key"]
    # Optimalizálási extra-metaadat (oszlop nélkül): nyers poszt + bővebb diagnosztika.
    if post.get("raw_content"):
        metadata["raw_content"] = post["raw_content"]
    if post.get("structure_score") is not None:
        metadata["structure_score"] = post["structure_score"]
    if post.get("optimizer_warnings"):
        metadata["optimizer_warnings"] = post["optimizer_warnings"]
    if post.get("hook_variants"):
        metadata["hook_variants"] = post["hook_variants"]
    if metadata:
        row["metadata"] = metadata
    _c(client).table("posts").upsert(row).execute()
    return post_id


def weekly_content_type_counts(voice: str, since_iso: str, client=None) -> dict[str, int]:
    """Az adott voice posztjainak strategy content_type szerinti darabszáma a hét óta.

    A 'skipped' posztokat nem számoljuk (nem mentek ki). A content_type a
    metadata.strategy_type-ból jön (lásd insert_post).
    """
    resp = (
        _c(client).table("posts").select("metadata, status")
        .eq("voice", voice).gte("generated_at", since_iso).execute()
    )
    counts: dict[str, int] = {}
    for row in resp.data or []:
        if (row.get("status") or "") == "skipped":
            continue
        ct = (row.get("metadata") or {}).get("strategy_type")
        if ct:
            counts[ct] = counts.get(ct, 0) + 1
    return counts


def used_seed_keys(
    voice: str, content_type: str, since_iso: str | None = None, client=None
) -> set[str]:
    """A már felhasznált seed-kulcsok (educational topic / case_study client / workshop topic).

    since_iso=None → minden idő (pl. case_study: egyszer használjuk). Megadott since →
    csak az adott időszak (pl. educational: 30 nap).
    """
    query = _c(client).table("posts").select("metadata").eq("voice", voice)
    if since_iso:
        query = query.gte("generated_at", since_iso)
    resp = query.execute()
    keys: set[str] = set()
    for row in resp.data or []:
        md = row.get("metadata") or {}
        if md.get("strategy_type") == content_type and md.get("seed_key"):
            keys.add(md["seed_key"])
    return keys


def save_visual(
    post_id: str,
    visual_url: str,
    base_image_url: str | None = None,
    visual_template: str | None = None,
    portrait_used: bool | None = None,
    client=None,
) -> None:
    """A végleges (komponált) vizuál URL-jét menti; opcionálisan a nyers Muapi alapképet is.

    visual_template/portrait_used (Phase 21b -- az engagement_report BY VISUAL bontásához):
    nincs rá dedikált oszlop, a meglévő metadata JSONB-be kerül (ugyanaz a minta mint a
    strategy_type/seed_key insert_post-ban) -- read-modify-write, hogy az insert_post által
    már beírt egyéb metadata kulcsok (pl. strategy_type) ne vesszenek el.
    """
    patch: dict[str, Any] = {"visual_url": visual_url}
    if base_image_url:
        patch["base_image_url"] = base_image_url
    if visual_template is None and portrait_used is None:
        _c(client).table("posts").update(patch).eq("id", post_id).execute()
        return
    c = _c(client)
    current = get_post(post_id, client=c) or {}
    metadata = dict(current.get("metadata") or {})
    if visual_template is not None:
        metadata["visual_template"] = visual_template
    if portrait_used is not None:
        metadata["portrait_used"] = portrait_used
    patch["metadata"] = metadata
    c.table("posts").update(patch).eq("id", post_id).execute()


def record_cost(
    post_id: str | None,
    model: str,
    cost_usd: float | None,
    request_id: str | None = None,
    kind: str = "muapi_image",
    client=None,
) -> None:
    """Egy külső API hívás (pl. Muapi képgenerálás) költségének logolása a costs táblába.

    A havi költés monitorozásához. Hiba esetén feljebb propagál — a hívó best-effort kezeli.
    """
    _c(client).table("costs").insert(
        {
            "post_id": post_id,
            "kind": kind,
            "model": model,
            "cost_usd": cost_usd,
            "request_id": request_id,
            "generated_at": _now(),
        }
    ).execute()


def get_post(post_id: str, client=None) -> dict[str, Any] | None:
    resp = _c(client).table("posts").select("*").eq("id", post_id).limit(1).execute()
    return (resp.data or [None])[0]


def get_posts_by_ids(post_ids: list[str], client=None) -> dict[str, dict[str, Any]]:
    """Több poszt lekérése id szerint egy dict-be ({id: row}) -- csak a report-hoz kellő
    mezőkkel (könnyebb payload, mint select("*")). 100-as csomagokban (in_ limit), mint a
    feed_items.dedupe_and_save mintája."""
    c = _c(client)
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(post_ids), 100):
        chunk = post_ids[i : i + 100]
        resp = (
            c.table("posts").select("id,voice,hook_type,is_breaking,metadata")
            .in_("id", chunk).execute()
        )
        for row in resp.data or []:
            out[row["id"]] = row
    return out


def update_post_status(post_id: str, status: str, client=None, **fields: Any) -> None:
    """Frissíti a posts.status-t (és opcionális mezőket: approved_at, approved_by, ...)."""
    patch: dict[str, Any] = {"status": status}
    patch.update({k: v for k, v in fields.items() if v is not None})
    _c(client).table("posts").update(patch).eq("id", post_id).execute()


def record_approval(
    post_id: str,
    telegram_chat_id: int,
    telegram_message_id: int,
    action: str,
    telegram_user_id: int | None = None,
    action_data: dict[str, Any] | None = None,
    client=None,
) -> None:
    """Egy approval action rögzítése az approvals táblába."""
    _c(client).table("approvals").insert(
        {
            "post_id": post_id,
            "telegram_chat_id": telegram_chat_id,
            "telegram_message_id": telegram_message_id,
            "telegram_user_id": telegram_user_id,
            "action": action,
            "action_data": action_data,
        }
    ).execute()


def mark_approved(post_id: str, by: str, client=None) -> None:
    update_post_status(post_id, "approved", client=client, approved_at=_now(), approved_by=by)


def has_recent_post(feed_item_id: str, voice: str, since_iso: str, client=None) -> bool:
    """Van-e már ehhez a hírhez ehhez a hanghoz generált poszt az adott időpont óta?"""
    if not feed_item_id:
        return False
    resp = (
        _c(client).table("posts").select("id")
        .eq("feed_item_id", feed_item_id).eq("voice", voice)
        .gte("generated_at", since_iso).limit(1).execute()
    )
    return bool(resp.data)


def has_post_for_feed_item(feed_item_id: str, client=None) -> bool:
    """Van-e már BÁRMILYEN poszt ehhez a hírhez? (breaking dedup: max 1 / feed_item)."""
    if not feed_item_id:
        return False
    resp = (
        _c(client).table("posts").select("id").eq("feed_item_id", feed_item_id).limit(1).execute()
    )
    return bool(resp.data)


def breaking_count_since(since_iso: str, client=None) -> int:
    """Kiküldött breaking posztok száma egy időpont óta (napi limit ellenőrzéshez)."""
    resp = (
        _c(client).table("posts").select("id")
        .eq("is_breaking", True).gte("sent_at", since_iso).execute()
    )
    return len(resp.data or [])


def mark_sent(post_id: str, sent_at: str | None = None, client=None) -> None:
    """A poszt sent_at mezőjének beállítása (Telegram kiküldés után)."""
    _c(client).table("posts").update({"sent_at": sent_at or _now()}).eq("id", post_id).execute()


def mark_posted(post_id: str, by: str, client=None) -> None:
    """Phase 21b: a human ténylegesen posztolta LinkedInre KÉZZEL (a LinkedIn API még nem
    éles -- lásd approve_cb mock ága, ami approved-nál megáll).

    status='posted' -- a schema.sql posts_status_check ezt már régóta engedi, de eddig
    semmi nem állította be (csak 'approved'/'published' volt élesben használva) -- ez a
    hiányzó, EMBER-vezérelt "valóban kiment LinkedInre" esemény, külön a 'published'-től
    (ami a jövőbeli automata LinkedIn API-hívást jelentené). sent_at innentől az
    engagement_metrics.hours_since_post nulla-órája (lásd src/storage/engagement.py).
    """
    now = _now()
    update_post_status(post_id, "posted", client=client, approved_at=now, approved_by=by)
    mark_sent(post_id, now, client=client)


def status_counts_since(since_iso: str, client=None) -> dict[str, int]:
    """Posztok státusz-szerinti darabszáma egy időpont óta (pl. ma 00:00 UTC)."""
    resp = (
        _c(client).table("posts").select("status").gte("generated_at", since_iso).execute()
    )
    counts: dict[str, int] = {}
    for row in resp.data or []:
        status = row.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def mark_published(post_id: str, by: str, client=None) -> None:
    """A posztot 'published' státuszra állítja (sikeres LinkedIn posztolás után)."""
    update_post_status(post_id, "published", client=client, approved_at=_now(), approved_by=by)


def generated_counts_by_voice_since(since_iso: str, client=None) -> dict[str, int]:
    """Generált posztok száma hangonként egy időpont óta (/metrics TARTALOM szekció)."""
    resp = _c(client).table("posts").select("voice").gte("generated_at", since_iso).execute()
    counts: dict[str, int] = {}
    for row in resp.data or []:
        voice = row.get("voice") or "?"
        counts[voice] = counts.get(voice, 0) + 1
    return counts


def approval_action_counts_since(since_iso: str, client=None) -> dict[str, int]:
    """Az approvals tábla action-jeinek darabszáma egy időpont óta (actioned_at alapján).

    Az approvals minden emberi döntést rögzít időbélyeggel (approve/skip/edited/regenerate) —
    ez pontosabb "Ma/Ez a hét jóváhagyva" méréshez, mint a posts.status pillanatfelvétele
    (ami felülíródik, ha egy poszt később tovább lép státuszban).
    """
    resp = (
        _c(client).table("approvals").select("action").gte("actioned_at", since_iso).execute()
    )
    counts: dict[str, int] = {}
    for row in resp.data or []:
        action = row.get("action") or "?"
        counts[action] = counts.get(action, 0) + 1
    return counts


def published_count_since(since_iso: str, client=None) -> int:
    """Manuálisan kiposztoltként jelölt posztok száma egy időpont óta (approved_at = a
    mark_published hívás időpontja, ÚJRA beírva minden publish-nál -- lásd mark_published)."""
    resp = (
        _c(client).table("posts").select("id")
        .eq("status", "published").gte("approved_at", since_iso).execute()
    )
    return len(resp.data or [])


def cost_summary_since(since_iso: str, client=None) -> dict[str, float]:
    """Költség-összesítő kind szerint (claude_api / muapi_image / összesen) egy időpont óta."""
    resp = (
        _c(client).table("costs").select("kind,cost_usd").gte("generated_at", since_iso).execute()
    )
    summary: dict[str, float] = {}
    for row in resp.data or []:
        kind = row.get("kind") or "unknown"
        cost = row.get("cost_usd") or 0
        summary[kind] = summary.get(kind, 0.0) + float(cost)
    summary["total"] = sum(summary.values())
    return summary


def daily_cost_breakdown(since_iso: str, client=None) -> list[dict[str, Any]]:
    """Napi bontású költség (generated_at napja szerint csoportosítva) egy időpont óta.

    Nincs külön "nap" oszlop a costs táblán -- a generated_at timestampből számoljuk, így
    nem kell séma-módosítás a napi bontáshoz.
    """
    resp = (
        _c(client).table("costs").select("kind,cost_usd,generated_at")
        .gte("generated_at", since_iso).order("generated_at").execute()
    )
    by_day: dict[str, dict[str, float]] = {}
    for row in resp.data or []:
        day = (row.get("generated_at") or "")[:10]
        if not day:
            continue
        bucket = by_day.setdefault(day, {})
        kind = row.get("kind") or "unknown"
        cost = float(row.get("cost_usd") or 0)
        bucket[kind] = bucket.get(kind, 0.0) + cost
        bucket["total"] = bucket.get("total", 0.0) + cost
    return [{"date": day, **vals} for day, vals in sorted(by_day.items())]


def mark_edited(post_id: str, new_content: str, client=None) -> None:
    current = get_post(post_id, client=client) or {}
    update_post_status(
        post_id, "edited", client=client,
        edited_content=new_content, edit_count=(current.get("edit_count") or 0) + 1,
    )
