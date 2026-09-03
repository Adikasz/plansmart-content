"""Zero-network tests for the Fázis 23 consolidated Ádám digest.

Covers the three things that must hold after the cadence change:
  1. the digest itself picks the right candidate and sends EXACTLY ONE message,
  2. adam is gone from the morning / breaking / video-idea triggers (no double-fire),
  3. david + plansmart are untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.strategy import cadence
from src.core.workers import adam_digest_worker as worker
from src.core.workers import breaking_news_worker as bnw
from src.core.workers import morning_post_worker as mpw
from src.core.workers import video_idea_worker as viw

NOW = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)


def _row(item_id: str, score: int, *, hours_ago: int = 1, title: str = "T", **extra):
    published = (NOW - timedelta(hours=hours_ago)).isoformat()
    row = {
        "id": item_id,
        "title": title,
        "content": "body",
        "url": f"https://example.com/{item_id}",
        "source_name": "TestSource",
        "score": score,
        "published_at": published,
        "fetched_at": published,
        "voice_fit": {"adam": True},
        "status": "filtered",
    }
    row.update(extra)
    return row


@pytest.fixture
def no_digest_voices(monkeypatch):
    """Kikapcsolja a digest-kizárást — a régi worker-logika önmagában tesztelhető."""
    monkeypatch.setattr(cadence, "digest_voices", lambda: set())


# ── cadence: ki tartozik a digestbe ───────────────────────────────────
def test_adam_is_a_digest_voice_by_default():
    assert cadence.is_digest_voice("adam") is True
    assert cadence.is_digest_voice("ADAM") is True


def test_david_and_plansmart_are_not_digest_voices():
    assert cadence.is_digest_voice("david") is False
    assert cadence.is_digest_voice("plansmart") is False


def test_digest_voices_can_be_emptied(no_digest_voices):
    assert cadence.is_digest_voice("adam") is False


# ── 1) morning worker: adam kiesett, a másik kettő maradt ─────────────
def test_morning_accounts_excludes_adam_but_keeps_the_others():
    assert mpw.morning_accounts() == ["david", "plansmart"]


def test_morning_accounts_restores_adam_when_config_changes(no_digest_voices):
    assert mpw.morning_accounts() == ["david", "adam", "plansmart"]


@pytest.mark.asyncio
async def test_morning_run_never_touches_adam(monkeypatch):
    """A reggeli ciklus alapból SEM generál, SEM küld Ádámnak."""
    monkeypatch.setattr(mpw, "get_client", lambda use_service_key=False: object())
    monkeypatch.setattr(mpw.feed_store, "get_recent_top", lambda *a, **k: [])
    monkeypatch.setattr(mpw.posts_store, "weekly_content_type_counts", lambda *a, **k: {})
    monkeypatch.setattr(mpw, "_build_attempts", lambda *a, **k: [])

    seen: list[str] = []

    async def fake_generate(account, attempts):
        seen.append(account)
        return None, None, None, None

    monkeypatch.setattr(mpw, "_generate_from_attempts", fake_generate)

    summary = await mpw.run_morning_posts(dry_run=True, send=False)
    assert summary["accounts"] == ["david", "plansmart"]
    assert "adam" not in seen


# ── 2) breaking worker: nem tüzel Ádámra ──────────────────────────────
@pytest.mark.asyncio
async def test_breaking_check_is_a_noop_for_a_digest_voice(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a breaking worker NEM nyúlhat a DB-hez digest-hangnál")

    monkeypatch.setattr(bnw, "get_client", boom)

    result = await bnw.run_breaking_check(dry_run=True, send=False)
    assert result["sent"] == 0
    assert result["skipped_reason"] == "digest_voice"


@pytest.mark.asyncio
async def test_breaking_check_still_runs_for_a_non_digest_voice(monkeypatch, no_digest_voices):
    """A kapu CSAK a digest-hangot állítja meg — a worker maga változatlan."""
    monkeypatch.setattr(bnw, "get_client", lambda use_service_key=False: object())
    monkeypatch.setattr(bnw.posts_store, "breaking_count_since", lambda *a, **k: 0)
    monkeypatch.setattr(bnw.feed_store, "get_breaking_candidates", lambda *a, **k: [])

    result = await bnw.run_breaking_check(dry_run=True, send=False)
    assert result["enabled"] is True
    assert "skipped_reason" not in result


# ── 3) video-idea worker: nem fut önálló heti ütemezésen ──────────────
@pytest.mark.asyncio
async def test_video_idea_check_is_a_noop_for_a_digest_voice(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a videó worker NEM nyúlhat a DB-hez digest-hangnál")

    monkeypatch.setattr(viw, "get_client", boom)

    result = await viw.run_video_idea_check(dry_run=True, send=False)
    assert result["produced"] is None
    assert result["skipped_reason"] == "digest_voice"


# ── 4) ütemezés: nincs duplikált/árva adam trigger ────────────────────
def test_schedule_has_exactly_one_adam_trigger():
    from src.core.workers import main

    job_ids = [job_id for job_id, _desc, _trigger in main._build_schedule()]
    assert "adam_digest" in job_ids
    # A breaking és a videó-ötlet EGYETLEN hangja Ádám -> a jobjuk be sem kerül.
    assert "breaking" not in job_ids
    assert "video_idea" not in job_ids
    assert job_ids.count("adam_digest") == 1


def test_schedule_restores_the_old_jobs_without_digest_voices(no_digest_voices):
    from src.core.workers import main

    job_ids = [job_id for job_id, _desc, _trigger in main._build_schedule()]
    assert "breaking" in job_ids
    assert "video_idea" in job_ids
    assert "adam_digest" not in job_ids


# ── 5) jelöltgyűjtés + rangsor ────────────────────────────────────────
def test_collect_candidates_merges_lanes_and_ranks_by_score_then_recency(monkeypatch):
    top = _row("a", 10, hours_ago=5)
    fresher_same_score = _row("b", 10, hours_ago=1)
    weak = _row("c", 6, hours_ago=1)

    monkeypatch.setattr(
        worker.feed_store, "get_recent_top", lambda *a, **k: [top, fresher_same_score, weak]
    )
    monkeypatch.setattr(worker.feed_store, "get_breaking_candidates", lambda *a, **k: [top])
    monkeypatch.setattr(worker.feed_store, "get_video_idea_candidates", lambda *a, **k: [top])
    monkeypatch.setattr(worker.bnw, "_qualifies", lambda row, now: True)

    ranked = worker.collect_candidates(object(), "2026-09-01T00:00:00+00:00", NOW, 48)

    # Ugyanaz a hír HÁROM sávból is jött -> EGY jelölt, mindhárom kind-dal.
    assert [c["row"]["id"] for c in ranked] == ["b", "a", "c"]
    a = next(c for c in ranked if c["row"]["id"] == "a")
    assert a["kinds"] == {"post", "breaking", "video_idea"}


def test_kind_order_prefers_video_then_breaking_then_post():
    kinds = {"post", "breaking", "video_idea"}
    assert worker._kind_order(kinds, video_allowed=True) == ["video_idea", "breaking", "post"]
    assert worker._kind_order(kinds, video_allowed=False) == ["breaking", "post"]
    assert worker._kind_order({"video_idea"}, video_allowed=False) == []


# ── 6) ablak + kadencia-kapu ──────────────────────────────────────────
def test_window_start_uses_last_send_when_present():
    last = NOW - timedelta(hours=50)
    assert worker._window_start(last, NOW) == last


def test_window_start_defaults_to_48h_without_a_previous_send():
    assert worker._window_start(None, NOW) == NOW - timedelta(hours=48)


def test_window_start_is_clamped_after_a_long_outage():
    ancient = NOW - timedelta(days=90)
    assert worker._window_start(ancient, NOW) == NOW - timedelta(
        hours=worker.DIGEST_MAX_LOOKBACK_HOURS
    )


def test_last_digest_sent_at_takes_the_newest_of_both_sources(monkeypatch):
    monkeypatch.setattr(
        worker.posts_store,
        "recent_sent_for_voice",
        lambda *a, **k: [
            {"sent_at": (NOW - timedelta(hours=3)).isoformat(), "metadata": {}},
            {"sent_at": (NOW - timedelta(hours=10)).isoformat(), "metadata": {"digest": True}},
        ],
    )
    monkeypatch.setattr(
        worker.video_store,
        "latest_created_at",
        lambda *a, **k: (NOW - timedelta(hours=4)).isoformat(),
    )
    # A 3 órás poszt NEM digest -> a digest-jelölt (10h) és a videó-ötlet (4h) közül a 4h nyer.
    assert worker.last_digest_sent_at(object()) == NOW - timedelta(hours=4)


def test_last_digest_sent_at_is_none_on_a_clean_slate(monkeypatch):
    monkeypatch.setattr(worker.posts_store, "recent_sent_for_voice", lambda *a, **k: [])
    monkeypatch.setattr(worker.video_store, "latest_created_at", lambda *a, **k: None)
    assert worker.last_digest_sent_at(object()) is None


@pytest.mark.asyncio
async def test_digest_skips_the_day_after_a_send(monkeypatch):
    monkeypatch.setattr(worker, "get_client", lambda use_service_key=False: object())
    monkeypatch.setattr(
        worker, "last_digest_sent_at", lambda client: worker._now_utc() - timedelta(hours=24)
    )

    def boom(*_a, **_k):
        raise AssertionError("a kihagyott napon SEMMILYEN jelöltgyűjtés nem futhat")

    monkeypatch.setattr(worker, "collect_candidates", boom)

    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["sent"] == 0
    assert result["skipped_reason"] == "too_soon"


@pytest.mark.asyncio
async def test_digest_sends_again_two_days_later(monkeypatch):
    """A teljes 2 napos ciklus zárása: nap 1 küld, nap 2 kihagy, nap 3 ismét küld."""
    _stub_digest_env(monkeypatch, [_row("a", 9)])
    monkeypatch.setattr(
        worker, "last_digest_sent_at", lambda client: worker._now_utc() - timedelta(hours=48)
    )
    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result.get("skipped_reason") is None
    assert result["winner"]["feed_item_id"] == "a"


@pytest.mark.asyncio
async def test_a_missed_morning_self_heals_the_next_day(monkeypatch):
    """Kimaradt futás (pl. misfire) után a KÖVETKEZŐ reggel kiküldi — nem vár újabb 2 napot."""
    _stub_digest_env(monkeypatch, [_row("a", 9)])
    monkeypatch.setattr(
        worker, "last_digest_sent_at", lambda client: worker._now_utc() - timedelta(hours=72)
    )
    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result.get("skipped_reason") is None
    assert result["winner"] is not None


@pytest.mark.asyncio
async def test_force_overrides_the_min_interval_gate(monkeypatch):
    _stub_digest_env(monkeypatch, candidates=[])
    monkeypatch.setattr(
        worker, "last_digest_sent_at", lambda client: worker._now_utc() - timedelta(hours=2)
    )

    async def no_seed(client):
        return None, None

    monkeypatch.setattr(worker, "_build_seed_post", no_seed)
    result = await worker.run_adam_digest(dry_run=True, send=False, force=True)
    assert result.get("skipped_reason") is None


# ── 7) teljes ciklus: PONTOSAN EGY üzenet ─────────────────────────────
def _stub_digest_env(monkeypatch, candidates, *, video_allowed=False):
    """Minden külső határ (DB, Claude, Telegram) lecserélve — nulla hálózat."""
    monkeypatch.setattr(worker, "get_client", lambda use_service_key=False: object())
    monkeypatch.setattr(worker, "last_digest_sent_at", lambda client: None)
    monkeypatch.setattr(worker, "_video_idea_allowed", lambda client, now: video_allowed)
    monkeypatch.setattr(worker.feed_store, "get_recent_top", lambda *a, **k: candidates)
    monkeypatch.setattr(worker.feed_store, "get_breaking_candidates", lambda *a, **k: [])
    monkeypatch.setattr(worker.feed_store, "get_video_idea_candidates", lambda *a, **k: [])
    monkeypatch.setattr(worker.posts_store, "has_post_for_feed_item", lambda *a, **k: False)
    monkeypatch.setattr(worker.video_store, "has_video_idea_for_feed_item", lambda *a, **k: False)

    async def fake_optimize(post, content_type, hook_bias=None):
        post["hook_type"] = "C"
        post["estimated_engagement_tier"] = "high"
        return post

    monkeypatch.setattr(worker, "_optimize_post", fake_optimize)

    async def fake_generator(item):
        return {"linkedin": {"content": f"POST for {item.id}", "hashtags": ["#ai"]}}

    monkeypatch.setattr(worker, "GENERATORS", {"adam": fake_generator})


@pytest.mark.asyncio
async def test_digest_picks_the_highest_scored_candidate(monkeypatch):
    _stub_digest_env(monkeypatch, [_row("low", 7), _row("best", 10), _row("mid", 8)])
    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["winner"]["feed_item_id"] == "best"
    assert result["winner"]["kind"] == "post"
    assert result["candidates"] == 3


@pytest.mark.asyncio
async def test_digest_tiebreaks_equal_scores_by_recency(monkeypatch):
    _stub_digest_env(monkeypatch, [_row("older", 10, hours_ago=20), _row("newer", 10, hours_ago=2)])
    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["winner"]["feed_item_id"] == "newer"


@pytest.mark.asyncio
async def test_digest_sends_exactly_one_telegram_message(monkeypatch):
    _stub_digest_env(monkeypatch, [_row("a", 9), _row("b", 8), _row("c", 7)])
    monkeypatch.setattr(worker.posts_store, "insert_post", lambda post, client=None: "p1")
    monkeypatch.setattr(worker.posts_store, "mark_sent", lambda *a, **k: None)
    monkeypatch.setattr(worker.feed_store, "mark_generated", lambda *a, **k: None)

    async def fake_attach(post):
        return post

    monkeypatch.setattr(worker.tb, "_attach_visual", fake_attach)

    sent: list[tuple] = []

    async def fake_send(post, chat_id, header=""):
        sent.append((post["id"], chat_id, header))

    monkeypatch.setattr(worker.tb, "send_for_approval", fake_send)

    async def fake_video_send(*a, **k):
        raise AssertionError("poszt-győztesnél nem mehet ki videó-ötlet üzenet is")

    monkeypatch.setattr(worker.vb, "send_video_idea_for_approval", fake_video_send)

    result = await worker.run_adam_digest(dry_run=False, send=True)
    assert len(sent) == 1  # HÁROM jelölt, EGY üzenet
    assert result["sent"] == 1
    assert sent[0][1] == worker.tb.POSTS_CHAT_ID


@pytest.mark.asyncio
async def test_digest_marks_the_post_so_the_next_run_can_find_it(monkeypatch):
    _stub_digest_env(monkeypatch, [_row("a", 9)])
    inserted: list[dict] = []
    monkeypatch.setattr(
        worker.posts_store, "insert_post", lambda post, client=None: inserted.append(post) or "p1"
    )
    monkeypatch.setattr(worker.posts_store, "mark_sent", lambda *a, **k: None)
    monkeypatch.setattr(worker.feed_store, "mark_generated", lambda *a, **k: None)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(worker.tb, "_attach_visual", noop)
    monkeypatch.setattr(worker.tb, "send_for_approval", noop)

    await worker.run_adam_digest(dry_run=False, send=True)
    assert inserted[0]["metadata"]["digest"] is True
    assert inserted[0]["metadata"]["digest_kind"] == "post"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_and_sends_nothing(monkeypatch):
    _stub_digest_env(monkeypatch, [_row("a", 9)])

    def boom(*_a, **_k):
        raise AssertionError("dry_run: SEMMILYEN DB-írás/kiküldés")

    monkeypatch.setattr(worker.posts_store, "insert_post", boom)
    monkeypatch.setattr(worker.tb, "send_for_approval", boom)

    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["sent"] == 0
    assert result["winner"] is not None


@pytest.mark.asyncio
async def test_video_idea_wins_when_it_is_due(monkeypatch):
    row = _row("v", 9)
    _stub_digest_env(monkeypatch, [row], video_allowed=True)
    monkeypatch.setattr(worker.feed_store, "get_video_idea_candidates", lambda *a, **k: [row])

    async def fake_idea(row, voice):
        return {"hook": "HOOK", "talking_points": ["x"], "fabrication_risk": False}

    monkeypatch.setattr(worker, "generate_video_idea", fake_idea)

    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["winner"]["kind"] == "video_idea"


@pytest.mark.asyncio
async def test_video_idea_in_cooldown_falls_back_to_a_post(monkeypatch):
    row = _row("v", 9)
    _stub_digest_env(monkeypatch, [row], video_allowed=False)
    monkeypatch.setattr(worker.feed_store, "get_video_idea_candidates", lambda *a, **k: [row])

    async def boom(*_a, **_k):
        raise AssertionError("cooldownban nem szabad videó-ötletet generálni")

    monkeypatch.setattr(worker, "generate_video_idea", boom)

    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["winner"]["kind"] == "post"


@pytest.mark.asyncio
async def test_breaking_candidate_wins_over_plain_post_for_the_same_item(monkeypatch):
    row = _row("b", 10)
    _stub_digest_env(monkeypatch, [row])
    monkeypatch.setattr(worker.feed_store, "get_breaking_candidates", lambda *a, **k: [row])
    monkeypatch.setattr(worker.bnw, "_qualifies", lambda r, now: True)

    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["winner"]["kind"] == "breaking"


@pytest.mark.asyncio
async def test_falls_back_to_a_strategy_seed_when_no_candidates(monkeypatch):
    _stub_digest_env(monkeypatch, [])

    async def fake_seed(client):
        return {"content": "SEED POST", "voice": "adam", "seed_key": "topic-x"}, "educational"

    monkeypatch.setattr(worker, "_build_seed_post", fake_seed)

    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["winner"]["kind"] == "seed"
    assert result["winner"]["strategy_type"] == "educational"


@pytest.mark.asyncio
async def test_no_message_when_everything_skips(monkeypatch):
    _stub_digest_env(monkeypatch, [_row("a", 9)])

    async def skipping_generator(item):
        return None

    monkeypatch.setattr(worker, "GENERATORS", {"adam": skipping_generator})

    async def no_seed(client):
        return None, None

    monkeypatch.setattr(worker, "_build_seed_post", no_seed)

    result = await worker.run_adam_digest(dry_run=True, send=False)
    assert result["winner"] is None
    assert result["sent"] == 0
