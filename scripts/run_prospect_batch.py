"""Phase 19 / BLOCK A — prospect batch-orchestrator (ÉLES: web_search + Sonnet + Supabase).

Egy SUB-BATCH-et futtat végig, checkpoint-olva (körönként inkrementálisan ment, így egy
timeout sem visz veszendőbe munkát), PER-FULL-RUN de-duppal:

  research (web_search) → globális de-dup az EGÉSZ prospects tábla ellen (cégenként/emberenként
  max 1, a korábbi futásokat is beleértve) → voice-hozzárendelés → kapcsolat-jegyzet (Sonnet)
  → mentés status='note_drafted'.

⚠️ NEM CI-teszt: valós Anthropic web_search-t + Sonnet-et hív (pénz) és a Supabase-be ír.

Használat:
    # egy sub-batch:
    .venv/Scripts/python -m scripts.run_prospect_batch --category hu_sme_owner \
        --keywords "PR és marketing ügynökség" --target 24
    # kategória-eloszlás riport (nem hív API-t):
    .venv/Scripts/python -m scripts.run_prospect_batch --report
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from dotenv import load_dotenv

from src.outreach.note_generator import generate_note
from src.outreach.prospect_research import _norm_company, _norm_name, research_prospects
from src.outreach.prospect_verifier import verify_batch
from src.storage import prospects as store
from src.utils.logging import setup_logging

load_dotenv(override=False)

# Árazás (claude-sonnet-4-6: $3/$15 per 1M; web_search ~$10/1000 keresés).
SONNET_IN = 3.0 / 1_000_000
SONNET_OUT = 15.0 / 1_000_000
SEARCH_USD = 0.01
NOTE_IN_EST = 470  # a note_generator system+user promptja nagyjából ennyi input token

BUSINESS_HINTS = ("ceo", "founder", "owner", "managing", "marketing", "growth", "sales",
                  "business", "strateg", "operations", "commercial", "revenue", "gtm")
TECH_HINTS = ("cto", "engineer", "developer", "technical", "ml", "data", "builder", "builds",
              "architect", "automation", "n8n", "devrel", "software", "ai research")


def _peer_voice(cand: dict) -> str:
    """industry_peer: mix az alapító-profil szerint (business→adam, technical→david)."""
    text = f"{cand.get('title') or ''} {cand.get('relevance_notes') or ''}".lower()
    tech = any(h in text for h in TECH_HINTS)
    biz = any(h in text for h in BUSINESS_HINTS)
    if biz and not tech:
        return "adam"
    return "david"  # technikai vagy vegyes/ismeretlen → builder (Dávid)


def resolve_voice(category: str, cand: dict) -> str:
    """Voice-ajánlás best-fit szerint (a prospects.voice CHECK csak david|adam)."""
    if category in ("hu_sme_owner", "intl_sme_owner"):
        return "adam"          # üzleti döntéshozó → stratéga
    if category == "ai_specialist":
        return "david"         # technikai → builder
    return _peer_voice(cand)   # industry_peer → mix


def _load_seen() -> tuple[set[str], set[str], list[dict]]:
    """A már-lefedett cég- és név-kulcsok az EGÉSZ prospects táblából (per-full-run de-dup)."""
    rows = store.all_name_company()
    companies = {_norm_company(r.get("company")) for r in rows if _norm_company(r.get("company"))}
    names = {_norm_name(r.get("name")) for r in rows if _norm_name(r.get("name"))}
    return companies, names, rows


def _exclude_hint(kept: list[dict], db_rows: list[dict], category: str, cap: int = 40) -> list[str]:
    """Bounded kizárás-lista a promptnak: a mostani futás nevei/cégei + pár azonos-kategóriás DB-név."""
    terms: list[str] = []
    for c in kept[-30:]:
        terms += [t for t in (c.get("name"), c.get("company")) if t]
    db_same = [r.get("name") for r in db_rows if r.get("category") == category and r.get("name")]
    terms += db_same[:15]
    # egyediség + cap
    seen, out = set(), []
    for t in terms:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t)
    return out[:cap]


async def _gen_notes(prospects: list[dict], voice_by_id: dict[str, str]) -> tuple[int, int, int]:
    """Jegyzetek párhuzamosan (max 5), status→note_drafted. Visszaad: (ok, out_tokens_est, over_limit)."""
    sem = asyncio.Semaphore(5)
    ok = out_tok = over = 0

    async def one(p: dict) -> None:
        nonlocal ok, out_tok, over
        async with sem:
            try:
                res = await generate_note(p, voice=voice_by_id[p["id"]], persist=True)
                ok += 1
                out_tok += max(1, round(res["char_count"] / 3.5))
                over += 1 if res["over_limit"] else 0
            except Exception as exc:  # noqa: BLE001 — egy jegyzet hibája ne állítsa le a batch-et
                print(f"    ⚠️ jegyzet hiba ({p.get('name')}): {str(exc)[:100]}")

    await asyncio.gather(*(one(p) for p in prospects))
    return ok, out_tok, over


async def run_batch(category: str, keywords: str | None, target: int, per_call: int,
                    max_rounds: int, max_searches: int, verify: bool = False,
                    verify_searches: int = 8) -> dict:
    if not store.table_ready():
        raise RuntimeError("A prospects tábla nincs kész — futtasd a migration_18_prospects.sql-t.")

    seen_c, seen_n, db_rows = _load_seen()
    print(f"  DB-ben már: {len(db_rows)} prospect ({len(seen_c)} cég-kulcs). Cél: +{target} új."
          f"{'  [VERIFY-BEFORE-SAVE]' if verify else ''}")

    kept: list[dict] = []
    voice_by_id: dict[str, str] = {}
    in_tok = out_tok_r = searches = note_out_tok = notes_ok = notes_over = dropped = 0
    v_in = v_out = v_searches = v_rejected = 0
    low = 0

    for rnd in range(1, max_rounds + 1):
        if len(kept) >= target:
            break
        need = min(per_call, target - len(kept) + 4)
        hint = _exclude_hint(kept, db_rows, category)
        try:
            cands, meta = await research_prospects(
                category, keywords, count=need, persist=False, exclude_terms=hint,
                max_searches=max_searches,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ [kör {rnd}] research hiba: {str(exc)[:150]}")
            break
        in_tok += meta.get("in_tokens", 0)
        out_tok_r += meta.get("out_tokens", 0)
        searches += meta.get("searches", 0)

        # globális de-dup (cég ÉS név szerint, az egész DB + eddigi futás ellen)
        fresh: list[dict] = []
        for c in cands:
            ck, nk = _norm_company(c.get("company")), _norm_name(c.get("name"))
            if (nk and nk in seen_n) or (ck and ck in seen_c):
                dropped += 1
                continue
            if nk:
                seen_n.add(nk)
            if ck:
                seen_c.add(ck)
            fresh.append(c)

        print(f"  [kör {rnd}] talált {len(cands)}, új-egyedi {len(fresh)} "
              f"(keresés {meta.get('searches', 0)}, kör {meta.get('rounds', 0)})")
        if not fresh:
            low += 1
            if low >= 2:
                print("  (2 egymás utáni üres kör — leállás, nincs több valós jelölt)")
                break
            continue
        low = 0

        # VERIFY-BEFORE-SAVE: független web_search-ellenőrzés MENTÉS ELŐTT — csak a 'verified'
        # jelölteket tartjuk meg (a placeholder/kitalált/stale-cég nem kerül a DB-be).
        if verify:
            items = [{"idx": i, "name": c.get("name"), "title": c.get("title"),
                      "company": c.get("company"), "country": c.get("country")}
                     for i, c in enumerate(fresh)]
            try:
                verdicts, vmeta = await verify_batch(items, max_searches=verify_searches)
            except Exception as exc:  # noqa: BLE001 — verifikáció hiba ne veszejtse el a kört
                print(f"  ⚠️ [kör {rnd}] verifikáció hiba: {str(exc)[:120]} — kör kihagyva")
                continue
            v_in += vmeta.get("in_tokens", 0)
            v_out += vmeta.get("out_tokens", 0)
            v_searches += vmeta.get("searches", 0)
            verified_fresh = []
            for i, c in enumerate(fresh):
                v = verdicts.get(i) or {"verdict": "no_verdict", "reason": "no verdict"}
                if v["verdict"] == "verified":
                    verified_fresh.append(c)
                else:
                    v_rejected += 1
                    print(f"    ✗ verify elvetve: {c.get('name')} @ {c.get('company')} "
                          f"[{v['verdict']}] {v.get('reason','')[:90]}")
            print(f"  [kör {rnd}] verifikáció: {len(verified_fresh)}/{len(fresh)} verified "
                  f"(keresés {vmeta.get('searches', 0)})")
            fresh = verified_fresh
            if not fresh:
                low += 1
                if low >= 2:
                    print("  (2 üres kör verifikáció után — leállás)")
                    break
                continue
            low = 0

        # inkrementális mentés: insert + voice, majd jegyzetek — így timeout sem visz el munkát
        batch_saved = []
        for c in fresh:
            c["voice"] = resolve_voice(category, c)
            c["id"] = store.insert_prospect(c)
            voice_by_id[c["id"]] = c["voice"]
            batch_saved.append(c)
        nok, nout, nover = await _gen_notes(batch_saved, voice_by_id)
        notes_ok += nok
        note_out_tok += nout
        notes_over += nover
        kept.extend(batch_saved)
        print(f"  [kör {rnd}] mentve: {len(batch_saved)} (jegyzet ok: {nok}, 300+ kar: {nover})")

    research_usd = in_tok * SONNET_IN + out_tok_r * SONNET_OUT + searches * SEARCH_USD
    verify_usd = v_in * SONNET_IN + v_out * SONNET_OUT + v_searches * SEARCH_USD
    note_in_tok = notes_ok * NOTE_IN_EST
    note_usd = note_in_tok * SONNET_IN + note_out_tok * SONNET_OUT
    total_usd = research_usd + verify_usd + note_usd

    samples = [
        {"name": c.get("name"), "title": c.get("title"), "company": c.get("company"),
         "voice": c.get("voice"), "note": (store.get(c["id"]) or {}).get("connection_note_draft")}
        for c in kept[:3]
    ]
    return {
        "category": category, "keywords": keywords, "target": target,
        "kept": len(kept), "dropped_dupes": dropped, "verify_rejected": v_rejected,
        "notes_ok": notes_ok, "notes_over_limit": notes_over,
        "cost": {
            "research_in_tokens": in_tok, "research_out_tokens": out_tok_r, "searches": searches,
            "verify_searches": v_searches, "verify_in_tokens": v_in, "verify_out_tokens": v_out,
            "note_out_tokens_est": note_out_tok,
            "research_usd": round(research_usd, 4), "verify_usd": round(verify_usd, 4),
            "note_usd_est": round(note_usd, 4),
            "total_usd": round(total_usd, 4),
        },
        "samples": samples,
    }


def report() -> dict:
    """Kategória-eloszlás + státusz-összesítés a DB-ből (nem hív API-t)."""
    rows = store.all_name_company()
    by_cat = Counter(r.get("category") for r in rows)
    by_voice = Counter(r.get("voice") for r in rows)
    return {"total": len(rows), "by_category": dict(by_cat), "by_voice": dict(by_voice)}


def _print_result(res: dict) -> None:
    print("\n" + "═" * 72)
    print(f"SUB-BATCH KÉSZ: {res['category']} / {res.get('keywords') or '—'}")
    print(f"  új prospect: {res['kept']}  |  dupla eldobva: {res['dropped_dupes']}  |  "
          f"verify elvetve: {res.get('verify_rejected', 0)}  |  "
          f"jegyzet ok: {res['notes_ok']} (300+ kar: {res['notes_over_limit']})")
    c = res["cost"]
    print(f"  költség: ~${c['total_usd']}  (research ${c['research_usd']} + verify "
          f"${c.get('verify_usd', 0)} + jegyzet ~${c['note_usd_est']}; "
          f"keresések: research {c['searches']} / verify {c.get('verify_searches', 0)})")
    for i, s in enumerate(res["samples"], 1):
        note = (s.get("note") or "")[:200]
        print(f"  minta {i}: {s.get('name')} — {s.get('title') or '?'} @ {s.get('company') or '?'} "
              f"[{s.get('voice')}]\n           „{note}”")
    print("RESULT_JSON:" + json.dumps(res, ensure_ascii=False))


async def _amain(args) -> int:
    if args.report:
        print("RESULT_JSON:" + json.dumps(report(), ensure_ascii=False))
        return 0
    res = await run_batch(args.category, args.keywords, args.target, args.per_call,
                          args.max_rounds, args.max_searches, args.verify, args.verify_searches)
    _print_result(res)
    return 0


def main() -> int:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="hu_sme_owner")
    ap.add_argument("--keywords", default=None)
    ap.add_argument("--target", type=int, default=16)
    ap.add_argument("--per-call", type=int, default=14)
    ap.add_argument("--max-rounds", type=int, default=2)
    ap.add_argument("--max-searches", type=int, default=5,
                    help="web_search hívások max/kör — 5 a hatékony pont (3 túl kevés: 0 találat)")
    ap.add_argument("--verify", action="store_true",
                    help="MENTÉS ELŐTT független web_search-verifikáció — csak 'verified' jelölt kerül DB-be")
    ap.add_argument("--verify-searches", type=int, default=8, help="verify web_search max/kör")
    ap.add_argument("--report", action="store_true", help="csak kategória-eloszlás a DB-ből")
    return asyncio.run(_amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
