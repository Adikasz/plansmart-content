"""RSS collector — a config/sources.yml `rss_sources` listáját figyeli.

Aszinkron végigjárja az RSS forrásokat (httpx böngésző-szerű UA-val), parse-olja
a feedet (feedparser), retry-zik átmeneti hibákra (429 + hálózati), forrásonként
maximálja az elemszámot. A dedup + mentés a src.storage.feed_items-ben, a /news
végű (RSS nélküli) oldalak scrapelése a src.collectors.nextjs_scraper-ben.

Forrásonkénti státusz: OK | FIXED | STILL_FAILING | DISABLED.

Önállóan futtatva forrásonkénti összesítést ír ki:
    python -m src.collectors.rss_collector
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml
from dotenv import load_dotenv

from src.collectors.nextjs_scraper import fetch_nextjs_json
from src.config.settings import get_settings
from src.storage.feed_items import dedupe_and_save
from src.storage.models import (
    DISABLED,
    FIXED,
    OK,
    STILL_FAILING,
    FeedItem,
    SourceResult,
    make_id,
)
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)

load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCES_FILE = PROJECT_ROOT / "config" / "sources.yml"

# Operacios konstansok (env-bol felulirhatok, nem hardcode-olunk fixen).
REQUEST_TIMEOUT = get_settings().rss_timeout_sec
MAX_CONCURRENCY = get_settings().rss_max_concurrency
DEFAULT_MAX_ITEMS = get_settings().rss_max_items
MAX_RETRIES = get_settings().rss_max_retries
RETRY_BACKOFF = [1.0, 2.0, 4.0]  # masodperc — exponencialis (1s, 2s, 4s)

# Bongeszo-szeru User-Agent, hogy a bot-blokkolt feedek (pl. The Verge) atengedjenek.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
USER_AGENT = get_settings().rss_user_agent or BROWSER_UA


def load_rss_sources(path: Path = SOURCES_FILE) -> list[dict[str, Any]]:
    """rss_sources lista betoltese a sources.yml-bol."""
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("rss_sources", []) or []


def _exc_brief(exc: Exception) -> str:
    """Rovid, olvashato hibauzenet."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {exc}"[:80]


def _is_retryable(exc: Exception) -> bool:
    """Csak 429-re es atmeneti hibakra (5xx, halozati) retry-zunk — 403/404-re NEM."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return isinstance(exc, httpx.TransportError)  # timeout, connect, read error


async def _get_with_retry(client: httpx.AsyncClient, name: str, url: str) -> httpx.Response:
    """GET retry-val: 429 + atmeneti hibakra ujraprobal (1s/2s/4s). 403/404-re azonnal dob.

    Megosztott util — az rss_collector es a nextjs_scraper is ezt hasznalja.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if _is_retryable(exc) and attempt < MAX_RETRIES:
                delay = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning(
                    "Retry [%s] %d/%d — %.0f mp mulva (%s)",
                    name, attempt + 1, MAX_RETRIES, delay, _exc_brief(exc),
                )
                await asyncio.sleep(delay)
                continue
            raise


def _parse_entry(entry: Any, source_name: str, priority: int, tags: list[str],
                 feed_title: str | None) -> FeedItem | None:
    link = entry.get("link")
    if not link:
        return None

    published_at = None
    for key in ("published_parsed", "updated_parsed"):
        ts = entry.get(key)
        if ts:
            published_at = datetime(*ts[:6], tzinfo=timezone.utc).isoformat()
            break

    content = entry.get("summary")
    if not content and entry.get("content"):
        content = entry["content"][0].get("value")

    return FeedItem(
        id=make_id(link),
        source_name=source_name,
        source_priority=priority,
        url=link,
        title=entry.get("title"),
        content=content,
        author=entry.get("author"),
        published_at=published_at,
        tags=tags,
        raw_data={"feed_title": feed_title, "entry_id": entry.get("id")},
    )


async def fetch_source(client: httpx.AsyncClient, source: dict[str, Any]) -> SourceResult:
    """Egy forras lekerese. RSS-t feedparser-rel; a /news vegu oldalakat Next.js scrapinggel."""
    name = source.get("name") or source.get("url", "unknown")
    ok_label = FIXED if source.get("previously_broken") else OK

    if source.get("enabled", True) is False:
        logger.info("Kihagyva (disabled): %s", name)
        return SourceResult(name=name, label=DISABLED)

    url = source.get("url")
    if not url:
        return SourceResult(name=name, label=STILL_FAILING, error="hianyzo url")

    # Routing: az RSS nelkuli Next.js /news oldalak scraping-gel (nem feedparser).
    if url.rstrip("/").endswith("/news"):
        return await fetch_nextjs_json(client, source)

    priority = int(source.get("priority", 3))
    tags = source.get("tags") or []
    max_items = int(source.get("max_items", DEFAULT_MAX_ITEMS))

    try:
        resp = await _get_with_retry(client, name, url)
    except Exception as exc:
        logger.warning("Forras hiba [%s]: %s", name, _exc_brief(exc))
        return SourceResult(name=name, label=STILL_FAILING, error=_exc_brief(exc))

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        err = str(parsed.get("bozo_exception", "parse error"))[:60]
        logger.warning("Parse hiba [%s]: %s", name, err)
        return SourceResult(name=name, label=STILL_FAILING, error=err)

    feed_title = parsed.feed.get("title")
    items: list[FeedItem] = []
    for entry in parsed.entries[:max_items]:  # cap: csak a legujabb max_items elem
        item = _parse_entry(entry, name, priority, tags, feed_title)
        if item:
            items.append(item)

    if not items:
        logger.info("Ures feed [%s]: 0 elem", name)
        return SourceResult(
            name=name, label=STILL_FAILING, raw_count=len(parsed.entries),
            error="ures feed (0 elem)",
        )

    return SourceResult(
        name=name, label=ok_label, count=len(items),
        raw_count=len(parsed.entries), items=items,
    )


async def collect(sources: list[dict[str, Any]] | None = None) -> list[SourceResult]:
    """Az osszes RSS forras parhuzamos lekerese (concurrency-limittel)."""
    sources = sources if sources is not None else load_rss_sources()
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, follow_redirects=True, headers=headers
    ) as client:

        async def _guarded(src: dict[str, Any]) -> SourceResult:
            async with sem:
                return await fetch_source(client, src)

        return await asyncio.gather(*[_guarded(s) for s in sources])


def _print_summary(results: list[SourceResult], dedup: dict[str, Any]) -> None:
    width = max((len(r.name) for r in results), default=12)
    order = {FIXED: 0, OK: 1, STILL_FAILING: 2, DISABLED: 3}

    logger.info("%-*s  %6s  %s", width, "FORRAS", "ITEMS", "STATUS")
    logger.info("%s", "-" * (width + 26))
    for r in sorted(results, key=lambda x: (order.get(x.label, 9), -x.count, x.name)):
        detail = f"  ({r.error})" if r.error else ""
        logger.info("%-*s  %6d  %-13s%s", width, r.name, r.count, r.label, detail)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.label] = counts.get(r.label, 0) + 1
    succeed = counts.get(OK, 0) + counts.get(FIXED, 0)
    total_items = sum(r.count for r in results)

    logger.info("%s", "-" * (width + 26))
    logger.info(
        "Sikeres (OK+FIXED): %d/%d  |  FIXED %d  |  STILL_FAILING %d  |  DISABLED %d",
        succeed, len(results), counts.get(FIXED, 0),
        counts.get(STILL_FAILING, 0), counts.get(DISABLED, 0),
    )
    logger.info("Osszes elem (cap utan): %d", total_items)
    if dedup["connected"]:
        logger.info(
            "Supabase: %d uj mentve, %d duplikatum kihagyva", dedup["saved"], dedup["duplicates"]
        )
    else:
        logger.info(
            "Supabase: nincs kapcsolat — %d egyedi elem a futasban (%d in-run duplikatum)",
            dedup["new"], dedup["duplicates"],
        )


async def _amain() -> int:
    t0 = time.monotonic()
    results = await collect()
    elapsed = time.monotonic() - t0

    all_items = [item for r in results for item in r.items]
    dedup = dedupe_and_save(all_items)

    _print_summary(results, dedup)
    logger.info("Lekeres ideje: %.1f mp", elapsed)

    succeed = sum(1 for r in results if r.label in (OK, FIXED))
    return 0 if succeed >= 1 else 1


def main() -> int:
    setup_logging()
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())
