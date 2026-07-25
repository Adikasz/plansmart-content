"""Phase 19 — prospect-verifikátor (FÜGGETLEN valóság-ellenőrzés web_search-csel).

Mit csinál:
  • Egy adag már-kutatott jelöltre (név, pozíció, cég, ország) FÜGGETLENÜL rákeres a
    Claude web_search server-tool-jával, és eldönti: a személy VALÓS-e és a pozíció/cég
    legalább hihető/aktuális-e.
  • Verdikt jelöltenként: 'verified' | 'stale' | 'unverifiable' + egy soros indok + forrás-URL.

Mit NEM csinál (kritikus — a hívó erre számít):
  • NEM ír jegyzetet, NEM kutat új kontextust, NEM módosít semmit a jelölten.
    Ez CSAK egy közel-bináris check: valós-e a lead. A mentés/flag a hívó dolga.
  • NEM bízik a bemenetben — minden nevet önállóan keres (nem a kutató-hívás önbizalma).
  • NEM scrape-el LinkedIn profiloldalt (más publikus forrás: cégoldal, sajtó, konferencia).

Ugyanaz a nyers httpx REST + pause_turn loop mint a prospect_research-nél (a pinnelt
anthropic==0.28.0 nem tudja a szerver-oldali web_search-t) — a low-level konstansokat
onnan importáljuk, hogy egy helyen legyenek.
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
)

logger = logging.getLogger(__name__)

VERDICTS = ("verified", "stale", "unverifiable")
MAX_TOKENS = 4000  # elég nagy, hogy egy adag ÖSSZES verdiktje elférjen (ne csonkolódjon)
DEFAULT_MAX_SEARCHES = 10  # adagonként (≈ 1-2 keresés / személy)

SYSTEM_PROMPT = (
    "You are an INDEPENDENT verification analyst doing a skeptical QA pass. You are given a list "
    "of people that ANOTHER system claimed are real B2B prospects. Do NOT trust that claim. Your "
    "job is to independently confirm, using the web_search tool, whether each person REALLY EXISTS "
    "and whether the claimed title/company is at least plausible and reasonably current.\n\n"
    "RULES:\n"
    "1. Search for each person independently (name + company / name + role). Use public sources: "
    "company websites, press/news, conference or podcast speaker/guest lists, interviews, award "
    "lists, association pages. NEVER open or scrape LinkedIn profile pages.\n"
    "2. Assign exactly one verdict per person:\n"
    "   - 'verified': you found independent public evidence the person is real AND the claimed "
    "title/company is plausible/current (an exact title match is NOT required — a clearly related "
    "current role at that company/space counts).\n"
    "   - 'stale': the person appears real, but the claimed title or company looks outdated, "
    "changed, or mismatched vs. what you found (say what changed).\n"
    "   - 'unverifiable': you could NOT find independent public confirmation this specific person "
    "exists in that role/company. Use this when unsure — do not guess 'verified'.\n"
    "3. Be conservative: absence of evidence = 'unverifiable', not 'verified'. Do NOT invent "
    "sources. Only cite a URL you actually saw in search results.\n"
    "4. This is ONLY an existence/plausibility check. Do NOT rewrite notes or add new research.\n\n"
    "Output ONLY a single JSON object, no prose:\n"
    '{"results":[{"idx":0,"name":"","verdict":"verified|stale|unverifiable","reason":"one short '
    'sentence","source":"url or empty"}]}\n'
    "Return one entry per input person, preserving the given idx."
)


def _user_prompt(items: list[dict[str, Any]]) -> str:
    lines = ["Verify each of these people independently. Search first, then return the JSON.\n"]
    for it in items:
        lines.append(
            f"idx {it['idx']}: name={it.get('name') or '?'} | title={it.get('title') or '?'} | "
            f"company={it.get('company') or '?'} | country={it.get('country') or '?'}"
        )
    return "\n".join(lines)


def _tool_def(max_searches: int) -> dict[str, Any]:
    return {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max(1, max_searches)}


async def verify_batch(
    items: list[dict[str, Any]], max_searches: int = DEFAULT_MAX_SEARCHES
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Egy adag jelölt független ellenőrzése. items: [{idx,name,title,company,country}, ...].

    Visszaad: (verdicts_by_idx, meta). verdicts_by_idx[idx] = {verdict, reason, source}.
    meta: {searches, in_tokens, out_tokens, rounds, parsed_ok}.
    A hívóhoz igazodva SOHA nem dob a modell hibáján kívül — a hiányzó idx-eket a hívó
    kezeli (nem-verifikált → nem flag-eljük agresszíven).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Hiányzó ANTHROPIC_API_KEY (a web_search verifikáció kell).")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    messages: list[dict[str, Any]] = [{"role": "user", "content": _user_prompt(items)}]
    body_base = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "tools": [_tool_def(max_searches)],
    }
    searches = in_tok = out_tok = 0
    last_content: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        for rnd in range(1, MAX_ROUNDS + 1):  # noqa: B007  # rnd a cikluson kívül (meta "rounds") kell
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

    text = _final_text(last_content)
    parsed = _repair_and_parse(text) or {}
    rows = parsed.get("results", []) if isinstance(parsed, dict) else []
    by_idx: dict[int, dict[str, Any]] = {}
    for r in rows:
        try:
            idx = int(r.get("idx"))
        except (TypeError, ValueError):
            continue
        verdict = str(r.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            verdict = "unverifiable"  # ismeretlen verdikt → konzervatív
        by_idx[idx] = {
            "verdict": verdict,
            "reason": str(r.get("reason") or "").strip(),
            "source": str(r.get("source") or "").strip(),
        }
    meta = {
        "searches": searches,
        "in_tokens": in_tok,
        "out_tokens": out_tok,
        "rounds": rnd,
        "parsed_ok": bool(by_idx),
    }
    return by_idx, meta
