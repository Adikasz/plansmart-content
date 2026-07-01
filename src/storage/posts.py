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


def save_visual(post_id: str, visual_url: str, base_image_url: str | None = None, client=None) -> None:
    """A végleges (komponált) vizuál URL-jét menti; opcionálisan a nyers Muapi alapképet is."""
    patch: dict[str, Any] = {"visual_url": visual_url}
    if base_image_url:
        patch["base_image_url"] = base_image_url
    _c(client).table("posts").update(patch).eq("id", post_id).execute()


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


def mark_edited(post_id: str, new_content: str, client=None) -> None:
    current = get_post(post_id, client=client) or {}
    update_post_status(
        post_id, "edited", client=client,
        edited_content=new_content, edit_count=(current.get("edit_count") or 0) + 1,
    )
