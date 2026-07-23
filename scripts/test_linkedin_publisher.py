"""Phase 8 MOCK teszt: LinkedIn UGC payload formátum — NINCS éles API hívás.

LINKEDIN_MOCK=true mellett mindhárom fiókra (Dávid, Ádám = person; PlanSmart =
organization) felépíti és kiírja a pontos JSON-t, amit a /v2/ugcPosts-ra küldenénk,
meghívja a post_to_linkedin()-t mock módban, és ellenőrzi a UGC Posts spec formátumot.

Az author URN-eket a config/accounts.yml `linkedin_urn` mezőjéből olvassa; ha üres
(még nincs OAuth), placeholder URN-t használ.

Futtatás:
    python scripts/test_linkedin_publisher.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A mock módot a publisher importja ELŐTT állítjuk be — nincs éles hálózati hívás.
os.environ["LINKEDIN_MOCK"] = "true"

import yaml  # noqa: E402

from src.integrations.publishers.linkedin_publisher import (  # noqa: E402
    BASE_HEADERS, UGC_URL, build_ugc_payload, post_to_linkedin,
)

logger = logging.getLogger("test_linkedin")

PLACEHOLDER_URN = {
    "person": "urn:li:person:{}_PLACEHOLDER",
    "organization": "urn:li:organization:{}_PLACEHOLDER",
}

# Voice-specifikus minta tartalom (a valós generátor kimenetét utánozza).
SAMPLES = {
    "david": {
        "content": (
            "Tegnap este átírtam a queue logikát. A poll loop 16 percről 3,5 másodpercre esett, "
            "0 új alkalmazottal.\n\nA lényeg: nem skáláztam a csapatot, a rendszert skáláztam."
        ),
        "hashtags": ["#BuildInPublic", "#automation"],
    },
    "adam": {
        "content": (
            "Beszéltem egy 40 fős cég vezetőjével múlt héten. Egy mondat maradt meg:\n\n"
            "„Nem több embert akarok, hanem kevesebb kézi munkát.” Pontosan erről szól az operációs rendszer."
        ),
        "hashtags": ["#KKV", "#hatekonysag"],
    },
    "plansmart": {
        "content": (
            "A múlt hónapban egy ügyfelünknél 16 perces manuális folyamatot 3,5 másodpercre vittünk le.\n\n"
            "0 új alkalmazott. Csak okosabb operáció."
        ),
        "hashtags": ["#PlanSmart", "#caseStudy"],
    },
}


def _load_accounts() -> dict:
    with open(ROOT / "config" / "accounts.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh).get("accounts", {})


def _author_urn(account_id: str, cfg: dict) -> tuple[str, str]:
    """(author_urn, author_type) az accounts.yml-ből; placeholder, ha linkedin_urn üres."""
    li = (cfg or {}).get("linkedin", {})
    author_type = li.get("author_type", "person")
    urn = (li.get("linkedin_urn") or "").strip()
    if not urn:
        urn = PLACEHOLDER_URN[author_type].format(account_id.upper())
    return urn, author_type


def _verify(payload: dict, author_urn: str, hashtags: list[str]) -> None:
    sc = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert payload["author"] == author_urn, "author URN eltér"
    assert payload["lifecycleState"] == "PUBLISHED", "lifecycleState != PUBLISHED"
    assert sc["shareMediaCategory"] == "NONE", "shareMediaCategory != NONE (text-only)"
    assert payload["visibility"]["com.linkedin.ugc.MemberNetworkVisibility"] == "PUBLIC", "visibility != PUBLIC"
    assert all(h in sc["shareCommentary"]["text"] for h in hashtags), "hiányzó hashtag a szövegben"


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")

    accounts = _load_accounts()
    logger.info("LINKEDIN_MOCK=%s — NINCS éles API hívás.", os.environ["LINKEDIN_MOCK"])
    logger.info("POST %s", UGC_URL)
    logger.info("Headers: %s + Authorization: Bearer <access_token>\n", BASE_HEADERS)

    results = []
    for account_id in ("david", "adam", "plansmart"):
        cfg = accounts.get(account_id, {})
        author_urn, kind = _author_urn(account_id, cfg)
        sample = SAMPLES[account_id]
        payload = build_ugc_payload(author_urn, sample["content"], sample["hashtags"])

        logger.info("=" * 72)
        logger.info("ACCOUNT: %-9s (%s)  author=%s", account_id, kind, author_urn)
        logger.info("=" * 72)
        logger.info(json.dumps(payload, ensure_ascii=False, indent=2))

        _verify(payload, author_urn, sample["hashtags"])
        logger.info("  [OK] payload struktúra helyes (UGC Posts spec)")

        # Mock hívás: kiírja a payloadot (a publisheren belül) és fake URN-t ad vissza.
        urn = await post_to_linkedin(
            "FAKE_TOKEN", author_urn, sample["content"], sample["hashtags"],
            post_id=f"test_{account_id}",
        )
        logger.info("  -> mock URN: %s\n", urn)
        results.append((account_id, urn))

    logger.info("=" * 72)
    logger.info("ÖSSZEGZÉS — 3 mock poszt URN:")
    for account_id, urn in results:
        logger.info("  %-9s %s", account_id, urn)
    logger.info("\nMOCK MODE — valós LinkedIn API hívás NEM történt. ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
