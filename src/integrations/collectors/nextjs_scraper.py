"""Next.js scraping — RSS nélküli oldalakhoz (pl. Anthropic /news).

Támogatja a legacy __NEXT_DATA__-t és az app-router __next_f RSC-payloadot is.
A fő belépő a fetch_nextjs_json(), amit az rss_collector hív a /news végű URL-ekre.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from src.core.storage.models import FIXED, OK, STILL_FAILING, FeedItem, SourceResult, make_id

logger = logging.getLogger(__name__)


def _walk_for_posts(data: Any) -> list[dict[str, Any]]:
    """A legnagyobb olyan listat keresi, ahol az elemek title + slug/publishedOn mezosek."""
    best: list[dict[str, Any]] = []

    def walk(obj: Any) -> None:
        nonlocal best
        if isinstance(obj, list):
            if obj and all(isinstance(x, dict) for x in obj):
                keys: set[str] = set().union(*(x.keys() for x in obj))
                if (
                    "title" in keys
                    and ("slug" in keys or "publishedOn" in keys)
                    and len(obj) > len(best)
                ):
                    best = obj
            for x in obj:
                walk(x)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)

    walk(data)
    return best


def _posts_from_next_data(html: str) -> list[dict[str, Any]]:
    """Legacy pages-router: <script id="__NEXT_DATA__"> JSON (pl. props.pageProps...sections[].posts)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    return _walk_for_posts(data)


def _next_f_blob(html: str) -> str:
    """A self.__next_f.push([n,"..."]) RSC-chunkok stringjeinek osszefuzese (app-router)."""
    marker = "self.__next_f.push("
    out: list[str] = []
    i = 0
    while True:
        j = html.find(marker, i)
        if j == -1:
            break
        k = j + len(marker)
        depth, in_str, esc, m = 1, False, False, k
        while m < len(html):  # balanced scan, string-tudatos
            c = html[m]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    break
            m += 1
        try:
            arr = json.loads(html[k:m])
            if isinstance(arr, list) and len(arr) >= 2 and isinstance(arr[1], str):
                out.append(arr[1])
        except json.JSONDecodeError:
            pass
        i = m + 1
    return "".join(out)


def _posts_from_next_f(html: str) -> list[dict[str, Any]]:
    """App-router: a __next_f blobbol brace-matchinggel kinyeri a poszt-objektumokat."""
    blob = _next_f_blob(html)
    if not blob:
        return []
    dec = json.JSONDecoder()
    posts: list[dict[str, Any]] = []
    seen: set[str] = set()
    pos = 0
    while True:
        anchor = blob.find('"publishedOn"', pos)
        if anchor == -1:
            break
        pos = anchor + 1
        i, tries = blob.rfind("{", 0, anchor), 0
        while i != -1 and tries < 60:  # visszafele keressuk a befoglalo objektum '{'-jet
            tries += 1
            try:
                obj, end = dec.raw_decode(blob, i)
            except json.JSONDecodeError:
                i = blob.rfind("{", 0, i)
                continue
            if isinstance(obj, dict) and "publishedOn" in obj and end > anchor:
                slug = obj.get("slug")
                key = slug.get("current") if isinstance(slug, dict) else slug
                key = key or obj.get("title")
                if key and key not in seen:
                    seen.add(key)
                    posts.append(obj)
                break
            i = blob.rfind("{", 0, i)
    return posts


def _extract_nextjs_posts(html: str) -> list[dict[str, Any]]:
    """Next.js poszt-lista: eloszor __NEXT_DATA__, aztan app-router __next_f."""
    return _posts_from_next_data(html) or _posts_from_next_f(html)


async def fetch_nextjs_json(client: httpx.AsyncClient, source: dict[str, Any]) -> SourceResult:
    """RSS nelkuli Next.js oldal scrapelese (pl. Anthropic /news).

    A poszt-adatokat a __NEXT_DATA__ vagy az app-router __next_f payloadbol nyeri ki,
    es FeedItem-ekke alakitja. url = <oldal-url>/<slug.current>, content = summary,
    published_at = publishedOn. Sosem dob — a hibat statuszban adja vissza.
    """
    # Lazy import a korkoros import elkerulesere: az rss_collector mar importalja ezt a modult.
    from src.integrations.collectors.rss_collector import (
        DEFAULT_MAX_ITEMS,
        _exc_brief,
        _get_with_retry,
    )

    name = source.get("name") or source.get("url", "unknown")
    ok_label = FIXED if source.get("previously_broken") else OK
    url = source["url"]
    priority = int(source.get("priority", 3))
    tags = source.get("tags") or []
    max_items = int(source.get("max_items", DEFAULT_MAX_ITEMS))

    try:
        resp = await _get_with_retry(client, name, url)
    except Exception as exc:
        logger.warning("Forras hiba [%s]: %s", name, _exc_brief(exc))
        return SourceResult(name=name, label=STILL_FAILING, error=_exc_brief(exc))

    posts = _extract_nextjs_posts(resp.text)
    if not posts:
        logger.info("Ures Next.js oldal [%s]: 0 poszt", name)
        return SourceResult(
            name=name, label=STILL_FAILING, error="0 poszt (__NEXT_DATA__/__next_f)"
        )

    # Legujabb elol: publishedOn szerint csokkeno (ISO8601 -> lexikografikus sort helyes).
    posts.sort(key=lambda p: p.get("publishedOn") or "", reverse=True)

    base = url.rstrip("/")
    items: list[FeedItem] = []
    for post in posts[:max_items]:  # cap
        slug = post.get("slug")
        slug = slug.get("current") if isinstance(slug, dict) else slug
        if not slug:
            continue
        link = f"{base}/{slug}"
        items.append(
            FeedItem(
                id=make_id(link),
                source_name=name,
                source_priority=priority,
                url=link,
                title=post.get("title"),
                content=post.get("summary"),
                author=None,
                published_at=post.get("publishedOn"),
                tags=tags,
                raw_data={"slug": slug, "via": "nextjs"},
            )
        )

    if not items:
        return SourceResult(name=name, label=STILL_FAILING, error="0 poszt slug-gal")

    return SourceResult(
        name=name, label=ok_label, count=len(items), raw_count=len(posts), items=items
    )
