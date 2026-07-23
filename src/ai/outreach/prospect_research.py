"""Phase 18 / Part 2 — prospect-kutató (publikus web-keresés a Claude web_search tool-lal).

Mit csinál:
  • Egy target kategória (+ opcionális kulcsszó) alapján a Claude web_search server-tool-jával
    VALÓS, publikusan dokumentált jelölteket azonosít (cégoldal, sajtó, konferencia-előadói
    listák, podcast-vendégek, cikkek).
  • A jelölteket a prospects táblába menti status='researched'.

Mit NEM csinál (ToS-biztonság — kritikus):
  • NEM scrape-eli a LinkedIn profiloldalakat.
  • NEM talál ki neveket/cégeket/URL-eket (csak amit a keresés valóban visszaad).
  • NEM tölti ki a linkedin_url-t — azt a human (Dávid/Ádám) keresi meg maga, bejelentkezve.

Miért httpx és nem az anthropic SDK:
  A projekt anthropic==0.28.0-ra van pinnelve (requirements.txt), ami RÉGEBBI mint a
  szerver-oldali web_search tool — a régi SDK nem tudja sem elküldeni a tool-t, sem
  parse-olni a `web_search_tool_result` blokkokat. Az SDK bump az összes meglévő AI-utat
  (evaluator/generator/visual_eval) érintené → kockázatos. Ezért CSAK ehhez a hív
  hoz nyers httpx REST-et használunk; a note_generator marad a projekt SDK-mintáján.

Önálló futtatás:
    python -m src.ai.outreach.prospect_research --category hu_sme_owner --keywords logisztika --count 5
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

from src.ai.generators.base_generator import _repair_and_parse
from src.core.storage import prospects as store
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASE_STUDIES = PROJECT_ROOT / "prompts" / "case_studies.yml"

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-sonnet-4-6"          # a projekt standard modellje (web_search-képes)
WEB_SEARCH_TOOL = "web_search_20250305"  # az alap variáns (széles kompatibilitás)
MAX_TOKENS = 4000
MAX_SEARCH_USES = 6
MAX_ROUNDS = 5                       # pause_turn (server-tool iterációs limit) kezelés
REQUEST_TIMEOUT_S = 180.0

CATEGORIES = ("hu_sme_owner", "intl_sme_owner", "ai_specialist", "industry_peer")

# Kategória-leírások a kutató promptnak (mit keressen, mit NE).
CATEGORY_BRIEF = {
    "hu_sme_owner": (
        "Hungarian SME owners / managing directors / founders (roughly 10-100 employees) in "
        "industries PlanSmart already serves. These are potential future clients — people whose "
        "business has repetitive manual work AI automation could remove."
    ),
    "intl_sme_owner": (
        "International SME owners or operators who PUBLICLY discuss AI adoption, automation, or "
        "agentic workflows for their own business (thought-leader adjacent). NOT competitors — "
        "practitioners and business owners, not agencies selling the same service."
    ),
    "ai_specialist": (
        "AI / automation specialists and practitioners worth knowing for network-building — "
        "COMPLEMENTARY players (e.g. tooling, data, integration niches), explicitly NOT direct "
        "competitors of a Hungarian AI-automation agency."
    ),
    "industry_peer": (
        "Peer agencies or consultants in ADJACENT spaces (design, growth, ops, RPA, data) who are "
        "complementary collaborators, NOT direct competitors."
    ),
}


def _served_industries() -> str:
    """A case_studies.yml-ből kiolvassa, milyen iparágakat/problémákat szolgálunk már ki —
    ez alapozza meg a hu_sme_owner kutatást (ne a levegőbe keressünk)."""
    try:
        data = yaml.safe_load(CASE_STUDIES.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError) as exc:
        logger.warning("[research] case_studies.yml nem olvasható (%s)", exc)
        return ""
    lines = []
    for cs in data.get("case_studies", []):
        client = cs.get("client", "")
        problem = cs.get("problem", "")
        if client or problem:
            lines.append(f"- {client}: {problem}")
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You are a B2B outreach RESEARCH assistant for PlanSmart, a Hungarian AI-automation agency. "
    "Your ONLY job is to identify REAL, publicly-documented people who would be good LinkedIn "
    "connections in a given category, using the web_search tool.\n\n"
    "HARD RULES (a violation makes the output useless):\n"
    "1. Propose ONLY real people/companies you actually found via web_search. NEVER invent a name, "
    "title, company, or statistic. If you cannot find enough real candidates, return fewer.\n"
    "2. NEVER access, open, or scrape LinkedIn profile pages. Use other public sources: company "
    "websites, press/news, conference & meetup speaker lists, podcast guest lists, interviews, "
    "award lists, association member pages.\n"
    "3. NEVER fabricate a LinkedIn URL. Leave linkedin_url as an empty string — a human will find "
    "the real profile themselves in a logged-in session.\n"
    "4. Prefer NAMED individuals (owner / founder / managing director / relevant lead) over generic "
    "company entries.\n"
    "5. For each candidate, 'source' must describe where you found them (publication / event / "
    "company site) and include a URL when available. relevance_notes: 1-2 concrete sentences on "
    "why they fit — grounded in what you found, not generic flattery.\n"
    "6. At most ONE person per company — prefer candidates from DIFFERENT companies for a diverse "
    "batch. Do not return two people from the same firm.\n\n"
    "Output ONLY a single JSON object, no prose around it:\n"
    '{"candidates":[{"name":"","title":"","company":"","company_size_estimate":"","country":"",'
    '"city":"","category":"","relevance_notes":"","source":"","linkedin_url":""}]}\n'
    "linkedin_url MUST be an empty string for every candidate."
)


def _user_prompt(
    category: str, keywords: str | None, count: int, exclude_terms: list[str] | None = None
) -> str:
    brief = CATEGORY_BRIEF.get(category, "")
    parts = [f"TARGET CATEGORY: {category}\n{brief}"]
    if category == "hu_sme_owner":
        served = _served_industries()
        if served:
            parts.append(
                "Industries PlanSmart already has case studies for (target similar SME owners):\n"
                + served
            )
    if keywords:
        parts.append(f"Focus industry / keyword: {keywords}")
    if exclude_terms:
        # Per-full-run de-dup segéd: a modell NE hozza vissza a már lefedett embereket/cégeket.
        # (A kemény szűrés a hívónál/`_clean`-ben van; ez csak csökkenti a pazarlást.)
        listed = "; ".join(t for t in exclude_terms if t)[:1600]
        parts.append(
            "ALREADY COVERED — do NOT return any of these people or companies again; find DIFFERENT "
            f"ones from other firms:\n{listed}"
        )
    parts.append(
        f"Find exactly {count} candidates, EACH FROM A DIFFERENT COMPANY (at most one person per "
        "firm). Use web_search first, then return the JSON object. Every candidate must be a real "
        "person you found in the search results."
    )
    return "\n\n".join(parts)


def _tool_def(category: str, max_searches: int = MAX_SEARCH_USES) -> dict[str, Any]:
    # NB: a web_search user_location csak bizonyos országokat enged (HU nem támogatott) —
    # a magyar fókuszt a prompt + a magyar kulcsszavak adják, nem a user_location.
    # max_searches: a keresések száma a KÖLTSÉG fő hajtóereje (a találati oldalak input-tokenjei) —
    # kevesebb keresés = olcsóbb kör, kevesebb jelölt.
    return {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max(1, max_searches)}


def _final_text(content: list[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


def _count_searches(content: list[dict[str, Any]]) -> int:
    return sum(1 for b in content if b.get("type") == "server_tool_use"
              and b.get("name") == "web_search")


async def _call_with_search(
    category: str, keywords: str | None, count: int,
    exclude_terms: list[str] | None = None, max_searches: int = MAX_SEARCH_USES,
) -> tuple[str, dict]:
    """A web_search-ös Messages hívás, pause_turn (server-tool loop) kezeléssel.
    Visszaad: (végső szöveg, meta) — meta: {searches, in_tokens, out_tokens, rounds}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Hiányzó ANTHROPIC_API_KEY (a web_search hívás kell).")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _user_prompt(category, keywords, count, exclude_terms)}
    ]
    body_base = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "tools": [_tool_def(category, max_searches)],
    }
    searches = in_tok = out_tok = 0
    last_content: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        for rnd in range(1, MAX_ROUNDS + 1):
            resp = await client.post(API_URL, headers=headers, json={**body_base, "messages": messages})
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
            return _final_text(content), {"searches": searches, "in_tokens": in_tok,
                                          "out_tokens": out_tok, "rounds": rnd}
    return _final_text(last_content), {"searches": searches, "in_tokens": in_tok,
                                       "out_tokens": out_tok, "rounds": MAX_ROUNDS}


def _clean(candidates: list[dict], category: str) -> list[dict]:
    """Biztonsági normalizálás: kényszerített üres linkedin_url, kategória, kötelező név."""
    out = []
    for c in candidates:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "title": str(c.get("title") or "").strip() or None,
            "company": str(c.get("company") or "").strip() or None,
            "company_size_estimate": str(c.get("company_size_estimate") or "").strip() or None,
            "country": str(c.get("country") or "").strip() or None,
            "city": str(c.get("city") or "").strip() or None,
            "category": category,               # a kért kategóriát kényszerítjük
            "linkedin_url": None,               # SOSEM a modell URL-je (a human tölti)
            "relevance_notes": str(c.get("relevance_notes") or "").strip() or None,
            "source": str(c.get("source") or "").strip() or None,
            "status": "researched",
        })
    return out


# Cégnév-normalizálás a de-duphoz: levágja a jogi formát + írásjeleket, kisbetűsít.
_LEGAL_SUFFIXES = (
    "kft", "zrt", "nyrt", "bt", "kkt", "ev", "gmbh", "ltd", "llc", "inc", "co", "corp",
    "srl", "sarl", "sa", "ag", "bv", "oy", "ab", "as", "spa", "plc", "group", "csoport",
)


def _norm_company(name: str | None) -> str:
    """Cégnév kulcs a de-duphoz (üres string, ha nincs cég → sosem csoportosítjuk össze)."""
    base = (name or "").strip().lower()
    if not base:
        return ""
    for ch in ".,-–—&/()":
        base = base.replace(ch, " ")
    tokens = [t for t in base.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def _norm_name(name: str | None) -> str:
    """Személynév kulcs a de-duphoz (kisbetűs, összenyomott whitespace)."""
    return " ".join((name or "").strip().lower().split())


def _exclude_keys(exclude_terms: list[str] | None) -> set[str]:
    """A kizárt nevek/cégek normalizált kulcsai (név- és cég-alakban is), a post-filterhez."""
    keys: set[str] = set()
    for term in exclude_terms or []:
        nk, ck = _norm_name(term), _norm_company(term)
        if nk:
            keys.add(nk)
        if ck:
            keys.add(ck)
    return keys


def _has_quote(text: str | None) -> bool:
    return any(q in (text or "") for q in ('"', "“", "”", "„", "»", "«"))


def _strength(c: dict) -> int:
    """A jelölt 'tisztaság/erősség' pontszáma — a de-dup ez alapján dönt (magasabb = jobb)."""
    score = 0
    if "http" in (c.get("source") or "").lower():
        score += 2                                  # ellenőrizhető forrás-URL
    notes = c.get("relevance_notes") or ""
    if _has_quote(notes):
        score += 2                                  # konkrét idézet = erős, forrásolt horog
    for field in ("title", "city", "company_size_estimate", "country"):
        if c.get(field):
            score += 1                              # teljesebb rekord
    score += min(len(notes) // 100, 2)              # specifikusabb indoklás (max +2)
    return score


def _dedupe_by_company(candidates: list[dict]) -> tuple[list[dict], dict]:
    """Cégenként max 1 jelölt. Egyértelmű győztes → a többit eldobjuk; döntetlen → mindet
    megtartjuk, `_dedup_tie=True` jelöléssel (a human dönt a review-ban).

    Visszaad: (megtartott jelöltek eredeti sorrendben, report{dropped, ties, groups})."""
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(candidates):
        key = _norm_company(c.get("company"))
        gkey = key if key else f"__nocompany_{i}"    # cég nélkülieket sosem vonjuk össze
        groups.setdefault(gkey, []).append(i)

    keep_idx: set[int] = set()
    dropped: list[dict] = []
    ties: list[str] = []
    for gkey, idxs in groups.items():
        if len(idxs) == 1:
            keep_idx.add(idxs[0])
            continue
        scored = sorted(idxs, key=lambda i: _strength(candidates[i]), reverse=True)
        top = _strength(candidates[scored[0]])
        winners = [i for i in scored if _strength(candidates[i]) == top]
        if len(winners) == 1:                        # egyértelmű győztes
            keep_idx.add(winners[0])
            dropped += [candidates[i] for i in scored if i not in winners]
        else:                                        # döntetlen → mindet megtartjuk, jelölve
            for i in winners:
                candidates[i]["_dedup_tie"] = True
                keep_idx.add(i)
            dropped += [candidates[i] for i in scored if i not in winners]
            ties.append(candidates[winners[0]].get("company") or gkey)

    kept = [c for i, c in enumerate(candidates) if i in keep_idx]  # eredeti sorrend megtartva
    return kept, {"dropped": dropped, "ties": ties}


async def research_prospects(
    category: str,
    keywords: str | None = None,
    count: int = 5,
    persist: bool = True,
    exclude_terms: list[str] | None = None,
    max_searches: int = MAX_SEARCH_USES,
) -> tuple[list[dict], dict]:
    """A kategória jelöltjeinek kutatása. Visszaad: (jelöltek, meta).

    persist=True: best-effort mentés a prospects táblába (ha a migráció már lefutott).
    A jelöltek listája akkor is visszajön, ha a tábla még nem létezik (a Part 6 teszt így
    a migráció ELŐTT is meg tudja mutatni az eredményt).

    exclude_terms: már lefedett nevek/cégek — a PROMPT-ba kerülnek (a modell kerülje őket),
    ÉS a parse után kemény szűrjük is (per-full-run de-dup segéd; a végső de-dup a hívónál).
    """
    if category not in CATEGORIES:
        raise ValueError(f"Ismeretlen kategória: {category} (választható: {CATEGORIES})")
    text, meta = await _call_with_search(category, keywords, count, exclude_terms, max_searches)
    parsed = _repair_and_parse(text) or {}
    raw = _clean(parsed.get("candidates", []) if isinstance(parsed, dict) else [], category)

    ex_keys = _exclude_keys(exclude_terms)
    if ex_keys:
        before = len(raw)
        raw = [c for c in raw
               if _norm_name(c.get("name")) not in ex_keys
               and (not _norm_company(c.get("company")) or _norm_company(c.get("company")) not in ex_keys)]
        meta["excluded_prefilter"] = before - len(raw)
    meta["parsed_ok"] = bool(raw)

    # De-dup: cégenként max 1 (egyértelmű győztes marad; döntetlen → mindet jelöljük).
    candidates, dedup = _dedupe_by_company(raw)
    meta["raw_count"] = len(raw)
    meta["deduped_count"] = len(candidates)
    meta["dedup_dropped"] = len(dedup["dropped"])
    meta["dedup_ties"] = dedup["ties"]
    if dedup["dropped"]:
        dropped_names = ", ".join(f"{d.get('name')} ({d.get('company')})" for d in dedup["dropped"])
        logger.info("[research] de-dup: %d azonos-cég jelölt eldobva → %s", len(dedup["dropped"]), dropped_names)
    if dedup["ties"]:
        logger.warning("[research] de-dup DÖNTETLEN (kézi választás kell): %s", ", ".join(dedup["ties"]))

    saved = 0
    if persist and candidates:
        try:
            if store.table_ready():
                for c in candidates:
                    c["id"] = store.insert_prospect(c)
                saved = len(candidates)
            else:
                logger.warning("[research] a prospects tábla még nincs — mentés kihagyva "
                                "(futtasd a migration_18_prospects.sql-t). A jelöltek visszajönnek.")
        except Exception as exc:  # noqa: BLE001 — a mentés sosem buktathatja meg a kutatást
            logger.warning("[research] Supabase mentés hiba (%s) — jelöltek memóriában maradnak", exc)
    meta["saved"] = saved
    return candidates, meta


async def _demo(category: str, keywords: str | None, count: int) -> int:
    import json

    candidates, meta = await research_prospects(category, keywords, count, persist=True)
    print(json.dumps({"meta": meta, "candidates": candidates}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="hu_sme_owner", choices=list(CATEGORIES))
    ap.add_argument("--keywords", default=None)
    ap.add_argument("--count", type=int, default=5)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_demo(args.category, args.keywords, args.count)))
