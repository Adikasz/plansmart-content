"""Phase 19 / PART 1 — a már-mentett prospectek FÜGGETLEN valóság-ellenőrzése (ÉLES web_search).

Végigmegy a prospects táblán, adagokban független web_search-ös verifikációt futtat
(prospect_verifier.verify_batch), és:
  • verified  → marad, ahogy volt (note_drafted / approved_to_send),
  • stale / unverifiable → FLAG: status='skipped' (visszafordítható, nem törlünk),
    az indok a data/verification_results.json-be kerül a review-hoz.

Költség-védelem: --max-usd fölött NEM indít több adagot (a maradék prospect 'no_verdict'
marad, nem flag-eljük). Adagonként inkrementálisan ír JSON-t → egy timeout sem visz el munkát.

⚠️ NEM CI-teszt: valós Anthropic web_search-t hív (pénz) és a Supabase-be ír.

Használat:
    .venv/Scripts/python -m scripts.run_prospect_verification --limit 8      # próba-adag
    .venv/Scripts/python -m scripts.run_prospect_verification --max-usd 4.0  # teljes pass
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from src.outreach.prospect_verifier import verify_batch
from src.storage import prospects as store
from src.utils.logging import setup_logging

load_dotenv(override=False)

SONNET_IN = 3.0 / 1_000_000
SONNET_OUT = 15.0 / 1_000_000
SEARCH_USD = 0.01

RESULTS_PATH = Path("data") / "verification_results.json"
FLAG_STATUS = "skipped"                       # a flagelt prospectek státusza
FLAG_VERDICTS = {"stale", "unverifiable"}     # CSAK ezek flag-elnek (verified/no_verdict soha)
SETTLED_VERDICTS = {"verified", "stale", "unverifiable"}  # ezeket --only-unchecked kihagyja
# 'no_verdict' = a modell nem adott verdiktet (csonkolás/kihagyás) → NEM flag, újra kell nézni.


def _load_results() -> dict[str, dict]:
    if RESULTS_PATH.exists():
        try:
            return {r["id"]: r for r in json.loads(RESULTS_PATH.read_text(encoding="utf-8"))}
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def _save_results(by_id: dict[str, dict]) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(list(by_id.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run(batch_size: int, max_searches: int, max_usd: float,
              limit: int | None, offset: int, apply: bool, only_unchecked: bool) -> dict:
    if not store.table_ready():
        raise RuntimeError("A prospects tábla nincs kész.")
    rows = store.all_full()
    rows.sort(key=lambda r: (r.get("category") or "", r.get("name") or ""))

    results = _load_results()
    if only_unchecked:
        # csak a MÁR ELDÖNTÖTT (settled) id-ket hagyjuk ki; a no_verdict/hiányzó → újranézzük
        rows = [r for r in rows
                if results.get(r["id"], {}).get("verdict") not in SETTLED_VERDICTS]
    rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]

    total_target = len(rows)
    print(f"  Ellenőrzésre kijelölve: {total_target} prospect "
          f"(adag={batch_size}, max_searches={max_searches}, max_usd=${max_usd}, apply={apply})")

    in_tok = out_tok = searches = checked = flagged = stopped_at = 0
    verdict_counts: Counter = Counter()

    for start in range(0, total_target, batch_size):
        cost_so_far = in_tok * SONNET_IN + out_tok * SONNET_OUT + searches * SEARCH_USD
        if cost_so_far >= max_usd:
            stopped_at = start
            print(f"  ⛔ költség-limit (${cost_so_far:.2f} ≥ ${max_usd}) — leállás, "
                  f"{total_target - start} prospect ellenőrizetlen marad.")
            break

        chunk = rows[start:start + batch_size]
        items = [{"idx": i, "name": r.get("name"), "title": r.get("title"),
                  "company": r.get("company"), "country": r.get("country")}
                 for i, r in enumerate(chunk)]
        try:
            verdicts, meta = await verify_batch(items, max_searches=max_searches)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ [adag {start//batch_size+1}] verifikáció hiba: {str(exc)[:150]}")
            continue
        in_tok += meta.get("in_tokens", 0)
        out_tok += meta.get("out_tokens", 0)
        searches += meta.get("searches", 0)

        batch_flag = 0
        for i, r in enumerate(chunk):
            v = verdicts.get(i) or {"verdict": "no_verdict", "reason": "no verdict returned", "source": ""}
            verdict_counts[v["verdict"]] += 1
            checked += 1
            # prev_status: az EREDETI (verifikáció előtti) státusz — ne írjuk felül egy korábbi
            # (esetleg téves) flag-gel; ha van korábbi eredmény, abból örököljük.
            prev = results.get(r["id"], {}).get("prev_status") or r.get("status")
            results[r["id"]] = {
                "id": r["id"], "name": r.get("name"), "title": r.get("title"),
                "company": r.get("company"), "category": r.get("category"), "voice": r.get("voice"),
                "prev_status": prev,
                "verdict": v["verdict"], "reason": v["reason"], "source": v["source"],
            }
            if v["verdict"] in FLAG_VERDICTS:      # csak valódi stale/unverifiable flag-el
                batch_flag += 1
                flagged += 1
                if apply:
                    try:
                        store.set_status(r["id"], FLAG_STATUS)
                    except Exception as exc:  # noqa: BLE001
                        print(f"    ⚠️ flag mentés hiba ({r.get('name')}): {str(exc)[:80]}")
        _save_results(results)

        bcost = meta.get("in_tokens", 0) * SONNET_IN + meta.get("out_tokens", 0) * SONNET_OUT \
            + meta.get("searches", 0) * SEARCH_USD
        run_cost = in_tok * SONNET_IN + out_tok * SONNET_OUT + searches * SEARCH_USD
        print(f"  [adag {start//batch_size+1}] {len(chunk)} ellenőrizve, {batch_flag} flag-elve "
              f"(keresés {meta.get('searches',0)}, ~${bcost:.2f}; összesen ~${run_cost:.2f})")

    total_usd = in_tok * SONNET_IN + out_tok * SONNET_OUT + searches * SEARCH_USD
    flagged_rows = [r for r in results.values() if r["verdict"] in FLAG_VERDICTS]
    return {
        "checked": checked, "verified": verdict_counts.get("verified", 0),
        "stale": verdict_counts.get("stale", 0), "unverifiable": verdict_counts.get("unverifiable", 0),
        "no_verdict": verdict_counts.get("no_verdict", 0),
        "flagged": flagged, "stopped_early_at": stopped_at,
        "cost": {"in_tokens": in_tok, "out_tokens": out_tok, "searches": searches,
                 "total_usd": round(total_usd, 4)},
        "flagged_list": [{"name": r["name"], "company": r["company"], "category": r["category"],
                          "verdict": r["verdict"], "reason": r["reason"]} for r in flagged_rows],
        "results_path": str(RESULTS_PATH),
    }


async def _amain(a) -> int:
    res = await run(a.batch_size, a.max_searches, a.max_usd, a.limit, a.offset,
                    not a.dry_run, a.only_unchecked)
    print("\n" + "═" * 72)
    print(f"VERIFIKÁCIÓ KÉSZ: {res['checked']} ellenőrizve → "
          f"verified {res['verified']}, stale {res['stale']}, unverifiable {res['unverifiable']}, "
          f"no_verdict {res.get('no_verdict', 0)} (újranézendő)")
    print(f"  flag-elve (skipped): {res['flagged']}  |  költség: ~${res['cost']['total_usd']} "
          f"(keresés {res['cost']['searches']}, in/out {res['cost']['in_tokens']}/{res['cost']['out_tokens']})")
    if res["flagged_list"]:
        print("  FLAG-ELT (kézi review):")
        for f in res["flagged_list"]:
            print(f"   - {f['name']} @ {f['company'] or '?'} [{f['category']}] — {f['verdict']}: {f['reason']}")
    print("RESULT_JSON:" + json.dumps(res, ensure_ascii=False))
    return 0


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--max-searches", type=int, default=8, help="web_search hívások max / adag")
    ap.add_argument("--max-usd", type=float, default=4.0, help="e fölött nem indít több adagot")
    ap.add_argument("--limit", type=int, default=None, help="csak az első N (próbához)")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--only-unchecked", action="store_true",
                    help="a már verifikált id-ket (results.json) kihagyja — folytatáshoz")
    ap.add_argument("--dry-run", action="store_true", help="ne írjon DB flag-et, csak jelentsen")
    return asyncio.run(_amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
