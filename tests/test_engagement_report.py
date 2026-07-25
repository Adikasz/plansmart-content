"""Zero-network tests for src.core.storage.engagement_report (pure aggregation logic).

Deliberately hand-builds engagement_rows/posts_by_id dicts instead of touching Supabase --
this module never calls the DB itself, so every grouping/averaging/ranking edge case can be
exercised directly, including the ones explicitly called out in the Phase 21b spec: zero
logged posts, exactly one, and missing dimension data (hook_type / visual metadata / sent_at
never surfacing as hours_since_post).
"""

from __future__ import annotations

from src.core.storage import engagement_report as report


def _post(
    voice="david",
    hook_type="A",
    is_breaking=False,
    strategy_type="educational",
    visual_template="STAT_CARD",
    portrait_used=True,
):
    metadata = {}
    if strategy_type is not None:
        metadata["strategy_type"] = strategy_type
    if visual_template is not None:
        metadata["visual_template"] = visual_template
    if portrait_used is not None:
        metadata["portrait_used"] = portrait_used
    return {
        "voice": voice,
        "hook_type": hook_type,
        "is_breaking": is_breaking,
        "metadata": metadata,
    }


def _row(
    post_id,
    views=None,
    likes=None,
    comments=None,
    shares=None,
    hours_since_post=None,
    is_final_snapshot=False,
    measured_at="2026-07-01T00:00:00+00:00",
):
    return {
        "post_id": post_id,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "hours_since_post": hours_since_post,
        "is_final_snapshot": is_final_snapshot,
        "measured_at": measured_at,
    }


# ── representative_snapshot_per_post ────────────────────────────────────
def test_representative_snapshot_empty_list():
    assert report.representative_snapshot_per_post([]) == {}


def test_representative_snapshot_prefers_final_over_more_hours():
    rows = [
        _row("p1", views=100, hours_since_post=60, is_final_snapshot=False),
        _row("p1", views=50, hours_since_post=5, is_final_snapshot=True),
    ]
    best = report.representative_snapshot_per_post(rows)
    assert best["p1"]["views"] == 50  # a final=True győz, annak ellenére, hogy kevesebb óra telt el


def test_representative_snapshot_picks_max_hours_when_neither_final():
    rows = [
        _row("p1", views=10, hours_since_post=5, is_final_snapshot=False),
        _row("p1", views=20, hours_since_post=30, is_final_snapshot=False),
    ]
    best = report.representative_snapshot_per_post(rows)
    assert best["p1"]["views"] == 20


def test_representative_snapshot_none_hours_is_lowest_priority():
    rows = [
        _row("p1", views=10, hours_since_post=None, is_final_snapshot=False),
        _row("p1", views=20, hours_since_post=0, is_final_snapshot=False),
    ]
    best = report.representative_snapshot_per_post(rows)
    assert best["p1"]["views"] == 20


def test_representative_snapshot_keeps_posts_independent():
    rows = [_row("p1", views=10), _row("p2", views=20)]
    best = report.representative_snapshot_per_post(rows)
    assert set(best.keys()) == {"p1", "p2"}


# ── _group_stats / _avg via build_report's public surface ──────────────
def test_avg_ignores_none_and_rounds():
    assert report._avg([1, None, 2, None]) == 1.5
    assert report._avg([None, None]) is None
    assert report._avg([]) is None


def test_engagement_score_excludes_views():
    item = {"views": 100000, "likes": 0, "comments": 0, "shares": 0}
    assert report._engagement_score(item) == 0
    item2 = {"views": 1, "likes": 5, "comments": 2, "shares": 1}
    assert report._engagement_score(item2) == 8


# ── _content_type_of ────────────────────────────────────────────────────
def test_content_type_breaking_takes_priority_over_strategy_type():
    post = _post(is_breaking=True, strategy_type="educational")
    assert report._content_type_of(post) == "ai_news_breaking"


def test_content_type_uses_strategy_type_when_not_breaking():
    post = _post(is_breaking=False, strategy_type="case_study")
    assert report._content_type_of(post) == "case_study"


def test_content_type_falls_back_to_egyeb():
    post = _post(is_breaking=False, strategy_type=None)
    assert report._content_type_of(post) == "egyeb"


# ── build_report: the explicitly-requested edge cases ───────────────────
def test_build_report_zero_logged_posts():
    data = report.build_report({}, [])
    assert data["total_posts_with_engagement"] == 0
    assert data["by_voice"] == {}
    assert data["best_combo"] is None


def test_build_report_exactly_one_post():
    posts_by_id = {"p1": _post(voice="david", hook_type="B", strategy_type="ai_news")}
    rows = [_row("p1", views=100, likes=10, comments=2, shares=1, hours_since_post=24)]
    data = report.build_report(posts_by_id, rows)

    assert data["total_posts_with_engagement"] == 1
    assert data["by_voice"]["david"]["n"] == 1
    assert data["by_voice"]["david"]["avg_views"] == 100
    assert data["by_voice"]["david"]["low_confidence"] is True  # n=1 < 5
    assert data["best_combo"]["n"] == 1
    assert data["best_combo"]["voice"] == "david"


def test_build_report_skips_engagement_row_with_no_matching_post():
    # p1 engagement exists, but p1 is NOT in posts_by_id (e.g. a since-deleted post) -- must
    # not raise, must not appear in any grouping, and must be counted as skipped.
    rows = [_row("p1", views=100)]
    data = report.build_report({}, rows)
    assert data["skipped_missing_post"] == 1
    assert data["total_posts_with_engagement"] == 0


def test_build_report_missing_hook_type_excluded_but_counted():
    posts_by_id = {
        "p1": _post(hook_type=None),
        "p2": _post(hook_type="A"),
    }
    rows = [_row("p1", views=10), _row("p2", views=20)]
    data = report.build_report(posts_by_id, rows)

    assert data["hook_type_missing_n"] == 1
    assert "A" in data["by_hook_type"]
    assert sum(v["n"] for v in data["by_hook_type"].values()) == 1
    # de a hiányzó hook_type-ú poszt MÉG SZEREPEL a by_voice-ban -- csak EGY dimenzióból esik ki.
    assert data["by_voice"]["david"]["n"] == 2


def test_build_report_missing_visual_metadata_excluded_but_counted():
    posts_by_id = {
        "p1": _post(visual_template=None, portrait_used=None),  # régi poszt, e feature előtt
        "p2": _post(visual_template="QUOTE_STYLE", portrait_used=False),
    }
    rows = [_row("p1", views=10), _row("p2", views=20)]
    data = report.build_report(posts_by_id, rows)

    assert data["by_visual"]["missing_n"] == 1
    assert data["by_visual"]["with_portrait"]["n"] == 0
    assert data["by_visual"]["without_portrait"]["n"] == 1
    assert "QUOTE_STYLE" in data["by_visual"]["by_template"]
    # de a by_voice-ban mindkettő szerepel.
    assert data["by_voice"]["david"]["n"] == 2


def test_build_report_multiple_snapshots_same_post_counted_once():
    posts_by_id = {"p1": _post()}
    rows = [
        _row("p1", views=50, hours_since_post=10, is_final_snapshot=False),
        _row("p1", views=80, hours_since_post=48, is_final_snapshot=True),
    ]
    data = report.build_report(posts_by_id, rows)
    assert data["total_posts_with_engagement"] == 1
    assert data["by_voice"]["david"]["avg_views"] == 80  # a final snapshotot használja, nem 50-et


def test_build_report_best_combo_ranks_by_interactions_not_reach():
    posts_by_id = {
        "p1": _post(voice="david", hook_type="A", strategy_type="educational"),
        "p2": _post(voice="adam", hook_type="C", strategy_type="ai_news"),
    }
    rows = [
        # p1: hatalmas elérés, de senki nem lépett interakcióba.
        _row("p1", views=50000, likes=0, comments=0, shares=0),
        # p2: szerényebb elérés, de valódi interakció.
        _row("p2", views=200, likes=30, comments=10, shares=5),
    ]
    data = report.build_report(posts_by_id, rows)
    assert data["best_combo"]["voice"] == "adam"
    assert data["best_combo"]["hook_type"] == "C"


def test_build_report_low_confidence_flag_at_threshold_boundary():
    posts_by_id = {f"p{i}": _post() for i in range(5)}
    rows = [_row(f"p{i}", views=10) for i in range(5)]
    data = report.build_report(posts_by_id, rows)
    assert data["by_voice"]["david"]["n"] == 5
    assert data["by_voice"]["david"]["low_confidence"] is False  # n=5, a küszöb NEM inkluzív alul

    posts_by_id_4 = {f"p{i}": _post() for i in range(4)}
    rows_4 = [_row(f"p{i}", views=10) for i in range(4)]
    data_4 = report.build_report(posts_by_id_4, rows_4)
    assert data_4["by_voice"]["david"]["low_confidence"] is True
