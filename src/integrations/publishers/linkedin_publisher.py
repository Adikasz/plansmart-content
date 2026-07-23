"""LinkedIn publisher — szöveges poszt a UGC Posts API-n keresztül.

POST https://api.linkedin.com/v2/ugcPosts  (text-only, kép nélkül).
Sikeres posztolásnál a post URN a `published` táblába kerül.

FIGYELEM: éles API hívás csak valós access tokennel. Addig dry_run=True (mock).
Megjegyzés: a LinkedInnek van újabb /rest/posts API-ja is; itt a kérésnek
megfelelően a /v2/ugcPosts végpontot használjuk.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from src.core.storage.db import get_client, has_service_key

logger = logging.getLogger(__name__)
load_dotenv(override=False)

UGC_URL = "https://api.linkedin.com/v2/ugcPosts"
BASE_HEADERS = {
    "X-Restli-Protocol-Version": "2.0.0",
    "Content-Type": "application/json",
}
REQUEST_TIMEOUT = 20.0


def _mock_enabled(explicit: bool | None) -> bool:
    """Mock mód: explicit kapcsoló, vagy LINKEDIN_MOCK=true a .env-ben."""
    if explicit is not None:
        return explicit
    return os.environ.get("LINKEDIN_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def _fake_share_urn(author_urn: str, text: str) -> str:
    """Determinisztikus, valósághű mock URN — 'urn:li:share:<19 jegyű szám>'."""
    digest = hashlib.sha256(f"{author_urn}|{text}".encode("utf-8")).hexdigest()
    number = int(digest, 16) % (10**19)
    return f"urn:li:share:{number}"


def compose_text(content: str, hashtags: list[str] | None = None) -> str:
    """A poszt szövege + hashtagek (a UGC API a hashtageket a szövegben várja)."""
    tags = " ".join(hashtags or [])
    body = (content or "").strip()
    return f"{body}\n\n{tags}".strip() if tags else body


def build_ugc_payload(author_urn: str, content: str, hashtags: list[str] | None = None) -> dict:
    """A /v2/ugcPosts request body — text-only share, PUBLIC láthatóság."""
    return {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": compose_text(content, hashtags)},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }


def _post_url(post_urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{post_urn}"


def _log_published(post_id: str | None, post_urn: str, platform: str, client=None) -> None:
    client = client or get_client(use_service_key=has_service_key())
    client.table("published").insert(
        {
            "post_id": post_id,
            "platform": platform,
            "platform_post_id": post_urn,
            "platform_post_url": _post_url(post_urn),
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()


async def post_to_linkedin(
    access_token: str,
    author_urn: str,
    content: str,
    hashtags: list[str] | None = None,
    image_url: str | None = None,
    *,
    platform: str = "linkedin",
    post_id: str | None = None,
    mock: bool | None = None,
) -> str:
    """Szöveges LinkedIn poszt (UGC). Visszaadja a post URN-t ('urn:li:share:...'); hibára dob.

    author_urn: 'urn:li:person:<id>' (Dávid/Ádám) vagy 'urn:li:organization:<id>' (PlanSmart).
    image_url: egyelőre NINCS bekötve — a poszt text-only (shareMediaCategory=NONE). A paramétert
        elfogadjuk, hogy a hívók már most átadhassák; a kép-támogatás külön lépés lesz.

    MOCK MÓD (mock=True VAGY LINKEDIN_MOCK=true a .env-ben): NINCS API hívás és NINCS DB írás —
        kiírja a pontos payloadot és egy valósághű fake URN-t ad vissza.
    """
    payload = build_ugc_payload(author_urn, content, hashtags)
    if image_url:
        logger.info("image_url megadva, de a kép-posztolás még nincs bekötve — text-only megy: %s", image_url)

    if _mock_enabled(mock):
        fake_urn = _fake_share_urn(author_urn, payload["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"])
        logger.info(
            "[LINKEDIN_MOCK] POST %s\n%s\n-> %s (mock, nincs éles hívás)",
            UGC_URL, json.dumps(payload, ensure_ascii=False, indent=2), fake_urn,
        )
        return fake_urn

    headers = {**BASE_HEADERS, "Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(UGC_URL, json=payload, headers=headers)
        resp.raise_for_status()

    # A létrejött poszt URN-je az X-RestLi-Id header-ben (vagy a body id mezőjében) jön.
    post_urn = resp.headers.get("x-restli-id") or (resp.json() or {}).get("id")
    if not post_urn:
        raise RuntimeError("A LinkedIn válaszban nincs post URN (x-restli-id / id).")

    _log_published(post_id, post_urn, platform)
    logger.info("LinkedIn poszt kész: %s (post_id=%s)", post_urn, post_id)
    return post_urn
