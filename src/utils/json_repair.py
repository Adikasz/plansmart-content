"""Robusztus LLM-JSON parse + repair — a modellek néha nem-szabványos JSON-t adnak.

Korábban a src/generators/base_generator.py-ban lakott; ide emeltük, mert HAT modul
(base_generator, optimization.text_evaluator / visual_eval / linkedin_optimizer,
outreach.prospect_research, visuals.visual_generator) használja. A base_generator
visszafelé kompatibilisen re-exportálja a `_repair_and_parse` (+ társai) neveket, így a
meglévő `from src.generators.base_generator import _repair_and_parse` importok érintetlenek.

A repair lépések (a modell tipikus hibáira hangolva) változatlanul kerültek át.
"""
from __future__ import annotations

import json
import re
from typing import Any

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_fences(text: str) -> str:
    """Markdown ```json ... ``` fence eltávolítása."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def _escape_inner_quotes(s: str) -> str:
    """A string-értékeken belüli, nem-escape-elt ASCII idézőjelek escape-elése.

    A modell néha emfázisra straight " jelet tesz a tartalomba (pl. „átmásolja"),
    ami idő előtt lezárja a JSON stringet -> "Expecting ',' delimiter". Egy kis
    állapotgéppel megkülönböztetjük a szerkezeti idézőjelet a tartalmitól: ha egy
    string belsejében lévő " után (whitespace-t átugorva) NEM szerkezeti karakter
    (, : } ]) jön, akkor az tartalmi -> escape-eljük.
    """
    out: list[str] = []
    i, n, in_str = 0, len(s), False
    while i < n:
        c = s[i]
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
        elif c == "\\":  # meglévő escape-pár érintetlenül
            out.append(c)
            if i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
        elif c == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            nxt = s[j] if j < n else ""
            if nxt in ",:}]" or nxt == "":
                out.append(c)        # szerkezeti zárás
                in_str = False
            else:
                out.append('\\"')    # tartalmi idézőjel -> escape
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _repair_and_parse(text: str) -> dict[str, Any] | None:
    """Robusztus JSON parse repair lépésekkel. Sikertelenség esetén None.

    Lépések minden jelöltön (teljes szöveg, majd az első {..} utolsó } blokk):
      a) json.loads (strict)
      b) json.loads(strict=False) — megengedi a string-en belüli kontrollkaraktert (pl. \\n)
      c) trailing-comma javítás után újra (strict=False)
      d) string-en belüli nem-escape-elt idézőjelek escape-elése után újra (strict=False)
    """
    t = _strip_fences(text)
    if not t:
        return None

    candidates = [t]
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        block = t[start : end + 1]
        if block != t:
            candidates.append(block)

    def _try(s: str) -> dict[str, Any] | None:
        for strict in (True, False):
            try:
                parsed = json.loads(s, strict=strict)
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                continue
        return None

    for cand in candidates:
        for variant in (cand, _TRAILING_COMMA_RE.sub(r"\1", cand), _escape_inner_quotes(cand)):
            parsed = _try(variant)
            if parsed is not None:
                return parsed
    return None


# Publikus aliasok — a privát nevek a base_generator történeti (import-kompatibilis) API-ja.
strip_fences = _strip_fences
escape_inner_quotes = _escape_inner_quotes
repair_and_parse = _repair_and_parse
