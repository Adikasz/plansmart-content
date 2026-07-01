"""Deduplikáció — a feed_items tábla URL-hash (id) alapján.

is_duplicate(url_hash) -> True ha már létezik a feed_items-ben, False ha új.
A FeedItem.id maga az URL-hash (sha256[:16]), így azt adjuk át.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def is_duplicate(url_hash: str, client=None) -> bool:
    """True, ha a megadott id (URL-hash) már szerepel a feed_items táblában."""
    if client is None:
        from src.storage.db import get_client, has_service_key

        client = get_client(use_service_key=has_service_key())
    resp = client.table("feed_items").select("id").eq("id", url_hash).limit(1).execute()
    return bool(resp.data)
