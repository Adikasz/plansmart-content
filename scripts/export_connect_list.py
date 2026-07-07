"""Phase 19 / PART 3 — egyszerű, jegyzet-mentes connect-lista export (OFFLINE, nem hív API-t).

A prospects táblából kiexportálja a NEM-flagelt (status != 'skipped') jelölteket gyors
manuális LinkedIn-connecthez, kategóriánként csoportosítva, néven belül ábécé-sorrendben:

    Name | Title | Company | Category | Suggested voice | Verified

A 'Verified' oszlop a data/verification_results.json alapján: 'verified' = független
web_search-ellenőrzés OK; üres = még nem ellenőrzött (a Part 1 pass nem ért oda / API-limit).
A flagelt (stale/unverifiable → status='skipped') sorok NEM kerülnek be.

Kimenet:
    data/prospects_connect_list.md   (markdown tábla)
    data/prospects_connect_list.csv  (táblázat-barát)

Használat:
    .venv/Scripts/python -m scripts.export_connect_list
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from src.storage import prospects as store

load_dotenv(override=False)

RESULTS_PATH = Path("data") / "verification_results.json"
MD_PATH = Path("data") / "prospects_connect_list.md"
CSV_PATH = Path("data") / "prospects_connect_list.csv"

EXCLUDE_STATUS = {"skipped"}           # a flagelt (stale/unverifiable) prospectek kimaradnak
VOICE_DISPLAY = {"david": "Dávid", "adam": "Ádám"}
CATEGORY_DISPLAY = {
    "hu_sme_owner": "Hungarian SME owners",
    "intl_sme_owner": "International SME owners",
    "ai_specialist": "AI / automation specialists",
    "industry_peer": "Industry peers",
}
CATEGORY_ORDER = ("hu_sme_owner", "intl_sme_owner", "ai_specialist", "industry_peer")


def _load_verdicts() -> dict[str, str]:
    if not RESULTS_PATH.exists():
        return {}
    try:
        return {r["id"]: r.get("verdict", "") for r in json.loads(RESULTS_PATH.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, KeyError):
        return {}


def _clean(v) -> str:
    return " ".join(str(v or "").split()).strip()


def build_rows() -> list[dict]:
    verdicts = _load_verdicts()
    rows = [r for r in store.all_full() if r.get("status") not in EXCLUDE_STATUS]
    out = []
    for r in rows:
        verdict = verdicts.get(r["id"], "")
        out.append({
            "name": _clean(r.get("name")),
            "title": _clean(r.get("title")) or "—",
            "company": _clean(r.get("company")) or "—",
            "category": r.get("category") or "",
            "voice": VOICE_DISPLAY.get(r.get("voice"), r.get("voice") or "—"),
            "verified": "✓ verified" if verdict == "verified" else "—",
        })
    return out


def write_markdown(rows: list[dict]) -> None:
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    total = len(rows)
    verified_n = sum(1 for r in rows if r["verified"].startswith("✓"))
    lines = [
        "# PlanSmart — LinkedIn connect-lista",
        "",
        f"Összesen **{total}** jelölt (flagelt/skipped nélkül). "
        f"Ebből **{verified_n}** független web_search-ellenőrzéssel megerősítve; "
        f"a többi még ellenőrizetlen (a Part 1 verifikáció az Anthropic API havi limitjébe "
        "ütközött — 2026-08-01 után folytatható).",
        "",
        "Kézi connecthez: keresd meg a profilt LinkedInen (bejelentkezve), küldj kapcsolatot "
        "a *Suggested voice* alapján (Dávid = builder / technikai, Ádám = stratéga / üzleti).",
        "",
    ]
    for cat in CATEGORY_ORDER:
        group = sorted(by_cat.get(cat, []), key=lambda r: r["name"].lower())
        if not group:
            continue
        lines.append(f"## {CATEGORY_DISPLAY.get(cat, cat)} ({len(group)})")
        lines.append("")
        lines.append("| Name | Title | Company | Suggested voice | Verified |")
        lines.append("|------|-------|---------|-----------------|----------|")
        for r in group:
            lines.append(
                f"| {r['name']} | {r['title']} | {r['company']} | {r['voice']} | {r['verified']} |"
            )
        lines.append("")
    MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_csv(rows: list[dict]) -> None:
    ordered = sorted(rows, key=lambda r: (CATEGORY_ORDER.index(r["category"])
                                          if r["category"] in CATEGORY_ORDER else 99, r["name"].lower()))
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["Name", "Title", "Company", "Category", "Suggested voice", "Verified"])
        for r in ordered:
            w.writerow([r["name"], r["title"], r["company"],
                        CATEGORY_DISPLAY.get(r["category"], r["category"]),
                        r["voice"], "verified" if r["verified"].startswith("✓") else ""])


def main() -> int:
    rows = build_rows()
    write_markdown(rows)
    write_csv(rows)
    by_cat = Counter(CATEGORY_DISPLAY.get(r["category"], r["category"]) for r in rows)
    verified_n = sum(1 for r in rows if r["verified"].startswith("✓"))
    print(f"Exportálva: {len(rows)} jelölt ({verified_n} verified) →")
    print(f"  {MD_PATH}")
    print(f"  {CSV_PATH}")
    for cat, n in by_cat.items():
        print(f"   - {cat}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
