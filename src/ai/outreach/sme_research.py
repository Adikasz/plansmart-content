"""Phase 19 — SME-tulajdonos kutató (publikus, NEM-LinkedIn diszkréciós szögek).

Mit csinál:
  • Egy régió + iparág párra VALÓS, publikusan dokumentált SME tulajdonosokat / alapítókat /
    ügyvezetőket (kb. 10-200 fő) azonosít a Claude web_search server-tool-jával — DE nem a
    LinkedInről, hanem "diszkréciós szögekből": regionális üzleti sajtó "small business
    spotlight", kamarai tag-portrék, regionális vállalkozói díjak, üzleti/vállalkozói
    podcast-vendégek, SME/entrepreneurship konferencia-előadók, szakmai szövetségi
    testületi tagok, franchise-tulajdonos sajtóportrék.

Mit NEM csinál (ToS-biztonság — kritikus, ugyanaz mint a prospect_research):
  • NEM scrape-eli/nyitja meg a LinkedIn oldalakat.
  • NEM talál ki neveket/cégeket/URL-eket; NEM ad vissza generikus placeholdert ("Founders").
  • NEM tölti ki a linkedin_url-t.

A nyers httpx REST + pause_turn loopot és a konstansokat a prospect_research-ből használjuk
újra (a pinnelt anthropic==0.28.0 nem tudja a szerver-oldali web_search-t) — ugyanaz a minta,
mint a prospect_verifier-nél.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.ai.generators.base_generator import _repair_and_parse
from src.ai.outreach.prospect_research import (
    ANTHROPIC_VERSION,
    API_URL,
    MAX_ROUNDS,
    MODEL,
    REQUEST_TIMEOUT_S,
    WEB_SEARCH_TOOL,
    _count_searches,
    _final_text,
    _norm_name,
)

logger = logging.getLogger(__name__)

MAX_TOKENS = 4000
DEFAULT_MAX_SEARCHES = 6

# Nyilvánvaló placeholder-nevek kiszűrése (a verify amúgy is elkapná, de ne pazaroljunk rá keresést).
_PLACEHOLDER_TOKENS = (
    "founder",
    "founders",
    "team",
    "owner",
    "owners",
    "management",
    "group",
    "staff",
    "leadership",
    "co-founders",
)

SYSTEM_PROMPT = (
    "You are a B2B outreach RESEARCH assistant for PlanSmart, a Hungarian AI-automation agency. "
    "Your ONLY job is to identify REAL, publicly-documented SME OWNERS / FOUNDERS / CO-FOUNDERS / "
    "MANAGING DIRECTORS / CEOs of small-and-medium businesses (roughly 10-200 employees) who would "
    "be good LinkedIn connections, using the web_search tool. These are potential FUTURE CLIENTS — "
    "owner-operators whose business has repetitive manual work that AI automation could remove.\n\n"
    "HARD RULES (a violation makes the output useless):\n"
    "1. Propose ONLY real, NAMED individuals you actually found via web_search. NEVER invent a name, "
    "title, company, or fact. If you cannot find enough real people, return FEWER. NEVER return a "
    "generic placeholder (e.g. 'Founders', 'The team', 'Management') — a real FULL personal name is "
    "mandatory.\n"
    "2. Find them ONLY through PUBLIC, NON-LinkedIn sources: regional/local business-press features "
    "and 'small business spotlight' articles, chamber-of-commerce member spotlights, regional "
    "entrepreneur / business award lists, business & entrepreneurship PODCAST guest lists, regional "
    "SME / entrepreneurship CONFERENCE speaker lists, industry-association board members or "
    "spokespeople, and franchise-owner features in local press. NEVER access, open, or scrape "
    "LinkedIn profile pages.\n"
    "3. NEVER fabricate a LinkedIn URL. Leave linkedin_url as an empty string — a human finds the "
    "real profile in a logged-in session.\n"
    "4. For each person, 'source' MUST name the concrete public source (publication / award / event / "
    "podcast / chamber) and include a URL when available. relevance_notes: 1-2 concrete sentences "
    "grounded in what you actually found (NOT generic flattery).\n"
    "5. At most ONE person per company; prefer people from DIFFERENT companies.\n"
    "6. They must run an SME (owner/founder/co-founder/MD/CEO), NOT a large corporation and NOT a "
    "mere employee. Prefer businesses that plausibly have manual, automatable operations.\n\n"
    "Output ONLY a single JSON object, no prose:\n"
    '{"candidates":[{"name":"","title":"","company":"","company_size_estimate":"","country":"",'
    '"city":"","industry":"","relevance_notes":"","source":"","linkedin_url":""}]}\n'
    "linkedin_url MUST be an empty string for every candidate."
)

ANGLES = (
    "regional/local business-press 'small business spotlight' features",
    "chamber-of-commerce member spotlights",
    "regional entrepreneur / business award lists (winners & finalists)",
    "business & entrepreneurship podcast guest appearances",
    "regional SME / entrepreneurship conference speaker lists",
    "industry-association board members / spokespeople",
    "franchise-owner features in local press",
)


def _user_prompt(
    region_label: str, industry: str, count: int, exclude_terms: list[str] | None = None
) -> str:
    angles = "\n".join(f"- {a}" for a in ANGLES)
    parts = [
        f"TARGET REGION: {region_label}",
        f"INDUSTRY FOCUS: {industry}",
        "DISCOVERY ANGLES — search THESE public sources (NOT LinkedIn); a candidate is only valid "
        f"if you found them through one of these:\n{angles}",
    ]
    if exclude_terms:
        listed = "; ".join(t for t in exclude_terms if t)[:1600]
        parts.append(
            "ALREADY COVERED — do NOT return any of these people or companies again; find DIFFERENT "
            f"ones from other firms:\n{listed}"
        )
    parts.append(
        f"Find up to {count} DIFFERENT real, NAMED SME owners/founders/MDs in this region+industry, "
        "each from a different company, each traceable to one of the public discovery angles above. "
        "Use web_search first, then return the JSON object. Return FEWER rather than inventing anyone."
    )
    return "\n\n".join(parts)


def _tool_def(max_searches: int) -> dict[str, Any]:
    return {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max(1, max_searches)}


def _looks_placeholder(name: str) -> bool:
    low = name.strip().lower()
    if " " not in low:  # csak vezetéknév / egy szó → gyanús
        return True
    return any(tok == w for w in low.split() for tok in _PLACEHOLDER_TOKENS)


def _clean(candidates: list[dict[str, Any]], region_label: str) -> list[dict[str, Any]]:
    out = []
    for c in candidates:
        name = str(c.get("name") or "").strip()
        if not name or _looks_placeholder(name):
            continue
        out.append(
            {
                "name": name,
                "title": str(c.get("title") or "").strip() or None,
                "company": str(c.get("company") or "").strip() or None,
                "company_size_estimate": str(c.get("company_size_estimate") or "").strip() or None,
                "country": str(c.get("country") or "").strip() or None,
                "city": str(c.get("city") or "").strip() or None,
                "industry": str(c.get("industry") or "").strip() or None,
                "linkedin_url": None,
                "relevance_notes": str(c.get("relevance_notes") or "").strip() or None,
                "source": str(c.get("source") or "").strip() or None,
                "region_label": region_label,
            }
        )
    return out


async def research_sme(
    region_label: str,
    industry: str,
    count: int = 12,
    exclude_terms: list[str] | None = None,
    max_searches: int = DEFAULT_MAX_SEARCHES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """SME-tulajdonos jelöltek kutatása egy régió+iparág párra (NEM ment — a hívó dolga).

    Visszaad: (jelöltek, meta). meta: {searches, in_tokens, out_tokens, rounds, parsed_ok,
    raw_count, dropped_placeholder}.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Hiányzó ANTHROPIC_API_KEY (a web_search kutatás kell).")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _user_prompt(region_label, industry, count, exclude_terms)}
    ]
    body_base = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "tools": [_tool_def(max_searches)],
    }
    searches = in_tok = out_tok = 0
    last_content: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        for rnd in range(1, MAX_ROUNDS + 1):  # noqa: B007 — rnd a loop után a meta-ban kell
            resp = await client.post(
                API_URL, headers=headers, json={**body_base, "messages": messages}
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic API {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            content = data.get("content", []) or []
            last_content = content
            searches += _count_searches(content)
            usage = data.get("usage", {}) or {}
            in_tok += int(usage.get("input_tokens", 0) or 0)
            out_tok += int(usage.get("output_tokens", 0) or 0)
            if data.get("stop_reason") == "pause_turn":
                messages.append({"role": "assistant", "content": content})
                continue
            break

    parsed = _repair_and_parse(_final_text(last_content)) or {}
    raw = parsed.get("candidates", []) if isinstance(parsed, dict) else []
    cleaned = _clean(raw, region_label)
    # cégen belüli de-dup a jelölt-listán belül (nevek szerint) — a globális de-dup a hívónál
    seen_local, uniq = set(), []
    for c in cleaned:
        nk = _norm_name(c["name"])
        if nk and nk not in seen_local:
            seen_local.add(nk)
            uniq.append(c)
    meta = {
        "searches": searches,
        "in_tokens": in_tok,
        "out_tokens": out_tok,
        "rounds": rnd,
        "parsed_ok": bool(uniq),
        "raw_count": len(raw),
        "dropped_placeholder": len(raw) - len(cleaned),
    }
    return uniq, meta
