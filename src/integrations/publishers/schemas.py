"""A LinkedIn UGC Posts API válaszának tipizált sémája (külső API határ).

A `post_to_linkedin` a post URN-t az `x-restli-id` headerből VAGY a válasz-body `id`
mezőjéből olvassa. Ez a modell a body-t validálja; a `post_urn()` egységes hozzáférést
ad a headerhez és a body-hoz, egyetlen forrásból származó None-fallbackkel.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class LinkedInUGCResponse(BaseModel):
    """A /v2/ugcPosts válasz-body (a lényeg: a létrejött poszt `id`/URN-je)."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None

    @classmethod
    def post_urn(cls, body: dict[str, Any] | None, header_id: str | None = None) -> str | None:
        """A poszt URN: elsőként az x-restli-id header, különben a body `id` mezője."""
        if header_id:
            return header_id
        try:
            return cls.model_validate(body or {}).id
        except Exception:
            return None
