"""Engagement report — tiszta aggregáció (Phase 21b, Part 2+3).

build_report() posts + engagement_metrics adatból épít egy csoportosított riportot -- NEM hív
DB-t saját maga (a hívó, src/bots/engagement_bot.py tölti be az adatot és adja át). Ez teszi
lehetővé, hogy a csoportosítási/átlagolási/rangsorolási logika saját, zero-network tesztekkel
ellenőrizhető legyen a valódi Supabase-től függetlenül.
"""
from __future__ import annotations

from typing import Any, Callable

# Part 2: "n < 5" -> "kevés adat" felirat MINDEN dimenzió-sornál.
MIN_TRUSTWORTHY_N = 5
# Part 3: a best-combo advisory-hoz explicit magasabb küszöb (a user spec szó szerint ezt kéri
# a "vonj le következtetést" lépéshez -- szándékosan SZIGORÚBB, mint a fenti megjelenítési küszöb).
ADVISORY_MIN_N = "15-20"

HOOK_LABEL = {
    "A": "kontrariánus", "B": "kíváncsiság-rés", "C": "adat/konkrét szám",
    "D": "személyes sztori", "E": "gyakorlati ígéret",
}
METRICS: tuple[str, ...] = ("views", "likes", "comments", "shares")


def _avg(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 1)


def _engagement_score(item: dict[str, Any]) -> float:
    """Rangsoroláshoz (best-combo): likes+comments+shares összeg. A views szándékosan
    KIMARAD -- más nagyságrend (elérés, nem interakció), összeadva eltorzítaná a rangsort."""
    return sum((item.get(k) or 0) for k in ("likes", "comments", "shares"))


def _snapshot_priority(row: dict[str, Any]) -> tuple:
    """Nagyobb = jobb jelölt a poszt reprezentatív pillanatfelvételének."""
    is_final = bool(row.get("is_final_snapshot"))
    hours = row.get("hours_since_post")
    hours_key = hours if hours is not None else -1
    measured = row.get("measured_at") or ""
    return (is_final, hours_key, measured)


def representative_snapshot_per_post(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Posztonként EGY sort választ ki -- egy több-pillanatfelvételes poszt (pl. 24h + 48h
    mérve) ne számítson duplán / torzítva bele az átlagokba.

    Prioritás: is_final_snapshot=True előnyben > nagyobb hours_since_post (None a
    legalacsonyabb) > frissebb measured_at dönt döntetlennél.
    """
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = row.get("post_id")
        if not pid:
            continue
        cur = best.get(pid)
        if cur is None or _snapshot_priority(row) > _snapshot_priority(cur):
            best[pid] = row
    return best


def _group_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(items)
    stats: dict[str, Any] = {"n": n}
    for m in METRICS:
        stats[f"avg_{m}"] = _avg([it.get(m) for it in items])
    stats["low_confidence"] = n < MIN_TRUSTWORTHY_N
    return stats


def _group_by(
    items: list[dict[str, Any]], key: Callable[[dict[str, Any]], str | None]
) -> tuple[dict[str, dict[str, Any]], int]:
    """Visszaad (csoportok, kihagyott-elemek-száma) -- a kihagyottak azok, ahol key() None-t ad."""
    groups: dict[str, list[dict[str, Any]]] = {}
    excluded = 0
    for it in items:
        k = key(it)
        if k is None:
            excluded += 1
            continue
        groups.setdefault(k, []).append(it)
    return {k: _group_stats(v) for k, v in groups.items()}, excluded


def _content_type_of(post: dict[str, Any]) -> str:
    """breaking hírek + a strategy-vezérelt típusok (educational/workshop_promo/case_study/
    ai_news) + minden más (manuális /create, régi poszt strategy_type nélkül) -> 'egyeb'."""
    if post.get("is_breaking"):
        return "ai_news_breaking"
    strategy_type = (post.get("metadata") or {}).get("strategy_type")
    return strategy_type or "egyeb"


def _best_combo(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    combos: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for it in items:
        key = (it["voice"], it.get("hook_type") or "ismeretlen", it["content_type"])
        combos.setdefault(key, []).append(it)

    def _rank(kv: tuple[tuple, list[dict[str, Any]]]) -> float:
        return _avg([_engagement_score(it) for it in kv[1]]) or 0.0

    best_key, best_items = max(combos.items(), key=_rank)
    voice, hook_type, content_type = best_key
    return {
        "voice": voice,
        "hook_type": hook_type,
        "content_type": content_type,
        "n": len(best_items),
        "avg_engagement_score": _avg([_engagement_score(it) for it in best_items]),
    }


def build_report(
    posts_by_id: dict[str, dict[str, Any]], engagement_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """A teljes /engagement_report adatcsomagja.

    posts_by_id: {post_id: post_row} -- CSAK az engagement_rows-ban ténylegesen hivatkozott
        posztok (a hívó tölti be, id-lista alapján -- lásd engagement_bot.py). Egy post_id,
        ami engagement_rows-ban szerepel, de posts_by_id-ban nem (törölt poszt?), kimarad és
        a "skipped_missing_post" számlálóba kerül -- SOSEM dob KeyError-t.
    engagement_rows: MINDEN engagement_metrics sor (több pillanatfelvétel/poszt is lehet).
    """
    representative = representative_snapshot_per_post(engagement_rows)

    merged: list[dict[str, Any]] = []
    skipped_missing_post = 0
    for post_id, row in representative.items():
        post = posts_by_id.get(post_id)
        if post is None:
            skipped_missing_post += 1
            continue
        item = dict(row)
        item["post_id"] = post_id
        item["voice"] = post.get("voice") or row.get("voice") or "ismeretlen"
        item["hook_type"] = post.get("hook_type") or None
        item["content_type"] = _content_type_of(post)
        metadata = post.get("metadata") or {}
        item["visual_template"] = metadata.get("visual_template")
        item["portrait_used"] = metadata.get("portrait_used")
        merged.append(item)

    by_voice, _ = _group_by(merged, key=lambda it: it["voice"])
    by_hook_type, hook_type_missing_n = _group_by(merged, key=lambda it: it["hook_type"])
    by_content_type, _ = _group_by(merged, key=lambda it: it["content_type"])

    with_portrait = [it for it in merged if it["portrait_used"] is True]
    without_portrait = [it for it in merged if it["portrait_used"] is False]
    visual_data_missing_n = len(merged) - len(with_portrait) - len(without_portrait)
    by_template, _ = _group_by(
        [it for it in merged if it.get("visual_template")], key=lambda it: it["visual_template"]
    )

    return {
        "total_posts_with_engagement": len(merged),
        "skipped_missing_post": skipped_missing_post,
        "by_voice": by_voice,
        "by_hook_type": by_hook_type,
        "hook_type_missing_n": hook_type_missing_n,
        "by_content_type": by_content_type,
        "by_visual": {
            "with_portrait": _group_stats(with_portrait),
            "without_portrait": _group_stats(without_portrait),
            "by_template": by_template,
            "missing_n": visual_data_missing_n,
        },
        "best_combo": _best_combo(merged),
    }
