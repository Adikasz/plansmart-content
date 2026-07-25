"""Phase 19 — SME-tulajdonos EXPANZIÓS kampány (ÉLES: web_search kutatás + verify + Supabase).

Determinisztikus, SZEKVENCIÁLIS kampány-harness (nem párhuzamos agent-fan-out — a globális
de-dup és a költség-plafon így korrekt):

  régiónként × iparáganként:  research_sme (diszkréciós szögek, NEM LinkedIn)
    → globális de-dup az EGÉSZ prospects tábla + eddigi mentések ellen
    → verify_batch (MENTÉS ELŐTT, csak 'verified' marad)
    → mentés (category = ország szerint hu_/intl_sme_owner, voice=adam, jegyzet NÉLKÜL)
    → results.json 'verified' jelölés (az export ebből színez)

Biztonságok:
  • --max-usd fölött nem indít több al-batchet (kemény plafon).
  • Régiónként 2 egymás utáni ZÉRÓ-hozamú al-batch (0 új verified) → a régió leáll.
  • Al-batchenként kiír egy sort + inkrementálisan ment (timeout sem visz el munkát).

⚠️ NEM CI-teszt: valós web_search-t + Sonnet-et hív (pénz) és a Supabase-be ír.

Használat:
    .venv/Scripts/python -m scripts.run_sme_campaign --regions poland,czechia,slovakia --max-usd 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from src.ai.outreach.prospect_research import _norm_company, _norm_name
from src.ai.outreach.prospect_verifier import verify_batch
from src.ai.outreach.sme_research import research_sme
from src.core.storage import prospects as store
from src.utils.logging import setup_logging

load_dotenv(override=False)

SONNET_IN = 3.0 / 1_000_000
SONNET_OUT = 15.0 / 1_000_000
SEARCH_USD = 0.01
RESULTS_PATH = Path("data") / "verification_results.json"

# Iparágak — a legnagyobb publikus-jelenlét (press/díj/podcast) valószínűség szerint előre.
INDUSTRIES = [
    "professional services (accounting, legal, consulting, marketing agencies)",
    "creative / design / branding agencies",
    "hospitality & tourism (hotels, restaurants, travel)",
    "food & beverage production and brands",
    "retail & e-commerce",
    "manufacturing & industrial SMEs",
    "construction & real estate",
    "healthcare & wellness clinics",
    "fitness & wellness studios",
    "beauty & personal care",
    "automotive services",
    "agriculture / agtech",
    "event management",
    "logistics & transport (new companies)",
]

# Régió-kulcs → a promptba menő emberi címke.
REGION_LABELS = {
    # Tier 1 — CEE
    "hungary": "Hungary",
    "poland": "Poland",
    "czechia": "Czech Republic",
    "slovakia": "Slovakia",
    "romania": "Romania",
    "croatia": "Croatia",
    "estonia": "Estonia",
    "latvia": "Latvia",
    "lithuania": "Lithuania",
    "slovenia": "Slovenia",
    "bulgaria": "Bulgaria",
    "serbia": "Serbia",
    # Tier 2 — USA (régiónként, nem csak nagyvárosok)
    "usa-northeast": "the United States — Northeast (NY, NJ, PA, New England)",
    "usa-southeast": "the United States — Southeast (FL, GA, NC, TN, VA)",
    "usa-midwest": "the United States — Midwest (OH, MI, IL, WI, MN)",
    "usa-south": "the United States — South Central (TX, OK, LA)",
    "usa-mountain": "the United States — Mountain West (CO, UT, AZ, ID)",
    "usa-west": "the United States — West Coast (CA, OR, WA)",
    # Tier 3 — broader international
    "germany": "Germany", "austria": "Austria", "netherlands": "the Netherlands",
    "france": "France", "spain": "Spain", "italy": "Italy",
    "nordics": "the Nordics (Sweden, Denmark, Norway, Finland)",
    "uk": "the United Kingdom", "ireland": "Ireland",
    "canada": "Canada", "australia": "Australia",
}

HU_HINTS = ("hungary", "magyar", "hungária", "budapest")


def _is_hu(cand: dict, region_key: str) -> bool:
    if region_key == "hungary":
        blob = f"{cand.get('country') or ''} {cand.get('city') or ''}".lower()
        return "hungary" in blob or "magyar" in blob or not (cand.get("country"))
    blob = f"{cand.get('country') or ''} {cand.get('city') or ''}".lower()
    return any(h in blob for h in HU_HINTS)


def _load_results() -> dict[str, dict]:
    if RESULTS_PATH.exists():
        try:
            return {r["id"]: r for r in json.loads(RESULTS_PATH.read_text(encoding="utf-8"))}
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def _save_results(by_id: dict[str, dict]) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(list(by_id.values()), ensure_ascii=False, indent=2),
                            encoding="utf-8")


class Cost:
    def __init__(self) -> None:
        self.in_tok = self.out_tok = self.searches = 0

    def add(self, meta: dict) -> None:
        self.in_tok += meta.get("in_tokens", 0)
        self.out_tok += meta.get("out_tokens", 0)
        self.searches += meta.get("searches", 0)

    @property
    def usd(self) -> float:
        return self.in_tok * SONNET_IN + self.out_tok * SONNET_OUT + self.searches * SEARCH_USD


def _exclude_hint(recent: list[dict], region_label: str, cap: int = 40) -> list[str]:
    terms: list[str] = []
    for c in recent[-30:]:
        terms += [t for t in (c.get("name"), c.get("company")) if t]
    seen, out = set(), []
    for t in terms:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t)
    return out[:cap]


async def run(regions: list[str], industries: list[str], target: int, research_searches: int,
              verify_searches: int, max_usd: float, max_industries: int) -> dict:
    if not store.table_ready():
        raise RuntimeError("A prospects tábla nincs kész.")

    db_rows = store.all_name_company()
    seen_c = {_norm_company(r.get("company")) for r in db_rows if _norm_company(r.get("company"))}
    seen_n = {_norm_name(r.get("name")) for r in db_rows if _norm_name(r.get("name"))}
    results = _load_results()
    cost = Cost()
    recent_saved: list[dict] = []
    per_combo: list[dict] = []
    saved_total = rejected_total = 0
    stopped_reason = "completed"

    print(f"  START — régiók: {regions} | DB már: {len(db_rows)} prospect | max_usd ${max_usd}")

    for region_key in regions:
        region_label = REGION_LABELS.get(region_key, region_key)
        zero_streak = 0
        region_saved = 0
        for industry in industries[:max_industries]:
            if cost.usd >= max_usd:
                stopped_reason = f"max_usd (${cost.usd:.2f})"
                print(f"  ⛔ költség-plafon elérve (${cost.usd:.2f}) — kampány leáll.")
                break

            hint = _exclude_hint(recent_saved, region_label)
            try:
                cands, rmeta = await research_sme(region_label, industry, count=target,
                                                  exclude_terms=hint, max_searches=research_searches)
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠️ [{region_key}/{industry[:22]}] research hiba: {str(exc)[:110]}")
                per_combo.append({"region": region_key, "industry": industry, "saved": 0, "note": "research_error"})
                continue
            cost.add(rmeta)

            fresh = []
            for c in cands:
                ck, nk = _norm_company(c.get("company")), _norm_name(c.get("name"))
                if (nk and nk in seen_n) or (ck and ck in seen_c):
                    continue
                fresh.append(c)

            verified: list[dict] = []
            vsearch = 0
            if fresh:
                items = [{"idx": j, "name": c.get("name"), "title": c.get("title"),
                          "company": c.get("company"), "country": c.get("country")}
                         for j, c in enumerate(fresh)]
                try:
                    verdicts, vmeta = await verify_batch(items, max_searches=verify_searches)
                    cost.add(vmeta)
                    vsearch = vmeta.get("searches", 0)
                    for j, c in enumerate(fresh):
                        v = verdicts.get(j) or {"verdict": "no_verdict"}
                        if v["verdict"] == "verified":
                            c["_verify"] = v
                            verified.append(c)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ⚠️ [{region_key}/{industry[:22]}] verify hiba: {str(exc)[:110]}")

            # mentés (verified) — category ország szerint, voice=adam, jegyzet NÉLKÜL
            batch_saved = 0
            for c in verified:
                nk, ck = _norm_name(c.get("name")), _norm_company(c.get("company"))
                if (nk and nk in seen_n) or (ck and ck in seen_c):
                    continue
                is_hu = _is_hu(c, region_key)
                row = {
                    "name": c["name"], "title": c.get("title"), "company": c.get("company"),
                    "company_size_estimate": c.get("company_size_estimate"),
                    "country": c.get("country"), "city": c.get("city"),
                    "linkedin_url": None,
                    "category": "hu_sme_owner" if is_hu else "intl_sme_owner",
                    "voice": "adam",
                    "relevance_notes": c.get("relevance_notes"),
                    "note_language": "hu" if is_hu else "en",
                    "source": c.get("source"), "status": "researched",
                }
                pid = store.insert_prospect(row)
                if nk:
                    seen_n.add(nk)
                if ck:
                    seen_c.add(ck)
                recent_saved.append(c)
                results[pid] = {
                    "id": pid, "name": c["name"], "title": c.get("title"), "company": c.get("company"),
                    "category": row["category"], "voice": "adam", "prev_status": "researched",
                    "verdict": "verified", "reason": (c.get("_verify") or {}).get("reason")
                    or "SME expansion — verified before save.",
                    "source": (c.get("_verify") or {}).get("source") or c.get("source") or "",
                }
                batch_saved += 1
            rej = len(fresh) - len(verified)
            rejected_total += rej
            saved_total += batch_saved
            region_saved += batch_saved
            if batch_saved:
                _save_results(results)

            per_combo.append({"region": region_key, "industry": industry, "found": len(cands),
                              "fresh": len(fresh), "saved": batch_saved, "rejected": rej})
            print(f"  [{region_key}/{industry.split('(')[0].strip()[:26]}] talált {len(cands)}, "
                  f"friss {len(fresh)}, verified-mentve {batch_saved}, elvetve {rej} "
                  f"(keresés r{rmeta.get('searches',0)}/v{vsearch}; össz ~${cost.usd:.2f})")

            if batch_saved == 0:
                zero_streak += 1
                if zero_streak >= 2:
                    print(f"  ↳ {region_key}: 2 egymás utáni zéró-hozam — régió leáll.")
                    break
            else:
                zero_streak = 0

        print(f"  == {region_key} kész: +{region_saved} verified SME ==")
        if cost.usd >= max_usd:
            break

    by_region: Counter = Counter()
    for pc in per_combo:
        by_region[pc["region"]] += pc.get("saved", 0)
    return {
        "regions": regions, "saved_total": saved_total, "rejected_total": rejected_total,
        "stopped_reason": stopped_reason,
        "by_region": dict(by_region),
        "per_combo": per_combo,
        "cost": {"in_tokens": cost.in_tok, "out_tokens": cost.out_tok, "searches": cost.searches,
                 "total_usd": round(cost.usd, 4)},
    }


async def _amain(a) -> int:
    regions = [r.strip() for r in a.regions.split(",") if r.strip()]
    unknown = [r for r in regions if r not in REGION_LABELS]
    if unknown:
        print(f"  ⚠️ ismeretlen régió-kulcs(ok): {unknown} — választható: {list(REGION_LABELS)}")
        regions = [r for r in regions if r in REGION_LABELS]
    res = await run(regions, INDUSTRIES, a.target, a.research_searches, a.verify_searches,
                    a.max_usd, a.max_industries)
    print("\n" + "═" * 72)
    print(f"SME KAMPÁNY KÉSZ: +{res['saved_total']} verified SME mentve "
          f"(elvetve {res['rejected_total']}; leállás: {res['stopped_reason']})")
    print(f"  régiónként: {res['by_region']}")
    print(f"  költség: ~${res['cost']['total_usd']} "
          f"(keresés {res['cost']['searches']}, in/out {res['cost']['in_tokens']}/{res['cost']['out_tokens']})")
    print("RESULT_JSON:" + json.dumps(res, ensure_ascii=False))
    return 0


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", required=True, help="vesszős régió-kulcsok (pl. poland,czechia)")
    ap.add_argument("--target", type=int, default=12, help="jelölt / al-batch (research)")
    ap.add_argument("--research-searches", type=int, default=6)
    ap.add_argument("--verify-searches", type=int, default=10)
    ap.add_argument("--max-usd", type=float, default=10.0, help="kemény költség-plafon a kampányra")
    ap.add_argument("--max-industries", type=int, default=8, help="max iparág / régió (költség-korlát)")
    return asyncio.run(_amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
