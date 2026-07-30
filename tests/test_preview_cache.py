"""Tests for the strategy preview short-TTL cache + single-flight guard.

The preview read (``preview_candidates``) runs inline ML analysis for tens of
seconds. A browser reload/re-click abandons the response but not the server
work, so without a guard concurrent duplicates stack up and starve each other.
These tests pin the two guarantees that stop that stampede — one computation per
key at a time, and short-lived reuse — plus the invalidation contract that keeps
a strategy edit from being masked by a stale entry.
"""

import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.single_user import ensure_operator_account, ensure_operator_strategy
from app.models.models import DecisionExperimentRun, Project
from app.schemas.schemas import (
    DecisionExperimentStrategyApplyRequest,
    DecisionExperimentThresholdApplyRequest,
)
from app.services.decision_experiments import DecisionExperimentService
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.opportunity_monitoring.preview_cache import (
    PreviewCacheKey,
    PreviewResultCache,
    preview_cache,
)


class _FakeClock:
    """Injectable monotonic clock so TTL expiry is tested without sleeping."""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _CountingCompute:
    """Compute callable that records how many times it actually ran."""

    def __init__(self, payload: dict | None = None) -> None:
        self.calls = 0
        self.payload = payload if payload is not None else {"candidates": [], "marker": "computed"}

    def __call__(self) -> dict:
        self.calls += 1
        return deepcopy(self.payload)


KEY = PreviewCacheKey(operator_id=7, limit=10, high_priority_only=False)


def test_second_read_within_ttl_reuses_cached_payload():
    """A re-click inside the TTL must return the cached payload without recomputing."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    compute = _CountingCompute({"returned_candidate_count": 3})

    first = cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)
    clock.advance(59.0)
    second = cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)

    assert compute.calls == 1
    assert first == second == {"returned_candidate_count": 3}


def test_read_after_ttl_expiry_recomputes():
    """Once the TTL has elapsed the next read pays for a fresh computation."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    compute = _CountingCompute()

    cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)
    clock.advance(60.5)
    cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)

    assert compute.calls == 2


def test_distinct_keys_do_not_share_entries():
    """Different operator / limit / priority knobs are different previews."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    compute = _CountingCompute()

    cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)
    cache.get_or_compute(
        PreviewCacheKey(operator_id=7, limit=20, high_priority_only=False),
        compute,
        ttl_seconds=60.0,
        now=clock,
    )
    cache.get_or_compute(
        PreviewCacheKey(operator_id=7, limit=10, high_priority_only=True),
        compute,
        ttl_seconds=60.0,
        now=clock,
    )
    cache.get_or_compute(
        PreviewCacheKey(operator_id=8, limit=10, high_priority_only=False),
        compute,
        ttl_seconds=60.0,
        now=clock,
    )

    assert compute.calls == 4


def _join_all(threads) -> None:
    """Start/join worker threads and fail loudly on a deadlocked single-flight."""
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads), "single-flight deadlocked"


def test_single_flight_computes_once_for_concurrent_callers():
    """Two callers racing on the same key share one computation, not two scans."""
    cache = PreviewResultCache()
    entered = threading.Event()
    release = threading.Event()
    calls = {"count": 0}

    def slow_compute() -> dict:
        calls["count"] += 1
        entered.set()
        # Hold the computation open so the second thread is guaranteed to arrive
        # while this one is still in flight (the live re-click scenario).
        release.wait(timeout=5)
        return {"marker": "single-flight"}

    outcomes: dict[str, dict] = {}

    def first_call() -> None:
        outcomes["first"] = cache.get_or_compute(KEY, slow_compute, ttl_seconds=60.0)

    def second_call() -> None:
        entered.wait(timeout=5)
        release.set()
        outcomes["second"] = cache.get_or_compute(KEY, slow_compute, ttl_seconds=60.0)

    _join_all([threading.Thread(target=first_call), threading.Thread(target=second_call)])

    assert calls["count"] == 1
    assert outcomes["first"] == outcomes["second"] == {"marker": "single-flight"}


def test_single_flight_holds_with_ttl_disabled():
    """``ttl_seconds=0`` disables reuse but must still collapse concurrent duplicates."""
    cache = PreviewResultCache()
    entered = threading.Event()
    release = threading.Event()
    calls = {"count": 0}

    def slow_compute() -> dict:
        calls["count"] += 1
        entered.set()
        release.wait(timeout=5)
        return {"marker": "no-ttl"}

    outcomes: dict[str, dict] = {}

    def first_call() -> None:
        outcomes["first"] = cache.get_or_compute(KEY, slow_compute, ttl_seconds=0.0)

    def second_call() -> None:
        entered.wait(timeout=5)
        release.set()
        outcomes["second"] = cache.get_or_compute(KEY, slow_compute, ttl_seconds=0.0)

    _join_all([threading.Thread(target=first_call), threading.Thread(target=second_call)])

    assert calls["count"] == 1
    assert outcomes["first"] == outcomes["second"] == {"marker": "no-ttl"}


def test_zero_ttl_recomputes_on_every_sequential_read():
    """With the TTL disabled a later (non-concurrent) read never reuses the entry."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    compute = _CountingCompute()

    cache.get_or_compute(KEY, compute, ttl_seconds=0.0, now=clock)
    cache.get_or_compute(KEY, compute, ttl_seconds=0.0, now=clock)

    assert compute.calls == 2


def test_compute_failure_is_not_cached():
    """A failed computation propagates and leaves no entry behind to serve."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    calls = {"count": 0}

    def failing_compute() -> dict:
        calls["count"] += 1
        raise RuntimeError("analysis exploded")

    with pytest.raises(RuntimeError, match="analysis exploded"):
        cache.get_or_compute(KEY, failing_compute, ttl_seconds=60.0, now=clock)

    recovered = cache.get_or_compute(KEY, _CountingCompute({"ok": True}), ttl_seconds=60.0, now=clock)

    assert calls["count"] == 1
    assert recovered == {"ok": True}


def test_invalidate_drops_only_the_target_operator():
    """A strategy edit clears that operator's previews and leaves others alone."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    mine = _CountingCompute({"owner": "mine"})
    other_key = PreviewCacheKey(operator_id=8, limit=10, high_priority_only=False)
    other = _CountingCompute({"owner": "other"})

    cache.get_or_compute(KEY, mine, ttl_seconds=60.0, now=clock)
    cache.get_or_compute(
        PreviewCacheKey(operator_id=7, limit=25, high_priority_only=True),
        mine,
        ttl_seconds=60.0,
        now=clock,
    )
    cache.get_or_compute(other_key, other, ttl_seconds=60.0, now=clock)

    cache.invalidate(7, now=clock)

    cache.get_or_compute(KEY, mine, ttl_seconds=60.0, now=clock)
    cache.get_or_compute(other_key, other, ttl_seconds=60.0, now=clock)

    assert mine.calls == 3  # two seeded keys + one recompute after invalidation
    assert other.calls == 1  # untouched operator still served from cache


def test_invalidation_during_flight_is_not_stored():
    """A computation started before an edit must not be cached after it.

    The preview lives on the strategy edit screen and takes tens of seconds, so
    an operator can easily save while a scan is in flight. That scan is blind to
    the new rules; it may serve its own caller, but storing it would mask the
    edit for the whole TTL.
    """
    cache = PreviewResultCache()
    clock = _FakeClock()

    def compute_then_edit() -> dict:
        # Simulates the strategy PUT landing while this scan is still running.
        cache.invalidate(KEY.operator_id, now=clock)
        return {"stale": True}

    in_flight = cache.get_or_compute(KEY, compute_then_edit, ttl_seconds=60.0, now=clock)
    fresh = _CountingCompute({"stale": False})
    after = cache.get_or_compute(KEY, fresh, ttl_seconds=60.0, now=clock)

    assert in_flight == {"stale": True}  # the caller still gets its own result
    assert after == {"stale": False}  # but the next reader recomputes
    assert fresh.calls == 1


class _EntrySignallingClock(_FakeClock):
    """Fake clock that reports when a named thread reads it.

    ``get_or_compute`` samples the clock once on entry, so watching that read
    gives the test a deterministic handshake for "the waiter is now inside the
    call" without sleeping.
    """

    def __init__(self, watched_thread: str, entered: threading.Event, start: float = 1000.0) -> None:
        super().__init__(start)
        self._watched_thread = watched_thread
        self._entered = entered

    def __call__(self) -> float:
        value = super().__call__()
        if threading.current_thread().name == self._watched_thread:
            self._entered.set()
        return value


def test_waiter_result_from_a_pre_invalidation_closure_is_not_stored():
    """A waiter that entered before a strategy edit must not store its stale scan.

    The waiter captured its compute closure (strategy rows read before the edit)
    when it entered, then blocked behind the leader's scan. If the invalidation
    guard were keyed on when the waiter *starts computing* — after the edit — its
    pre-edit result would look fresh and mask the edit for the whole TTL. The
    guard is keyed on call entry instead, so only the waiter's own caller sees it.
    """
    cache = PreviewResultCache()
    waiter_entered = threading.Event()
    clock = _EntrySignallingClock("preview-waiter", waiter_entered)
    leader_computing = threading.Event()
    leader_may_finish = threading.Event()
    outcomes: dict[str, dict] = {}

    def leader_compute() -> dict:
        leader_computing.set()
        leader_may_finish.wait(timeout=5)
        return {"generation": "leader-pre-edit"}

    def waiter_compute() -> dict:
        # The waiter's closure was captured before the edit, so it rescans with
        # the old strategy — exactly what must not be published to later readers.
        return {"generation": "waiter-pre-edit"}

    def run_leader() -> None:
        outcomes["leader"] = cache.get_or_compute(KEY, leader_compute, ttl_seconds=60.0, now=clock)

    def run_waiter() -> None:
        outcomes["waiter"] = cache.get_or_compute(KEY, waiter_compute, ttl_seconds=60.0, now=clock)

    leader = threading.Thread(target=run_leader, name="preview-leader")
    waiter = threading.Thread(target=run_waiter, name="preview-waiter")
    leader.start()
    assert leader_computing.wait(timeout=5)
    waiter.start()
    assert waiter_entered.wait(timeout=5)  # waiter is inside the call, blocked on the leader

    # The strategy PUT lands while the leader scans and the waiter queues, then
    # time moves on before either computation stores its result.
    cache.invalidate(KEY.operator_id, now=clock)
    clock.advance(1.0)
    leader_may_finish.set()
    leader.join(timeout=10)
    waiter.join(timeout=10)
    assert not leader.is_alive() and not waiter.is_alive()

    assert outcomes["leader"] == {"generation": "leader-pre-edit"}
    assert outcomes["waiter"] == {"generation": "waiter-pre-edit"}
    assert cache.entry_count() == 0  # neither pre-edit scan was published

    post_edit = _CountingCompute({"generation": "post-edit"})
    assert cache.get_or_compute(KEY, post_edit, ttl_seconds=60.0, now=clock) == {
        "generation": "post-edit"
    }
    assert post_edit.calls == 1


def test_caller_mutation_does_not_poison_the_cache():
    """Callers own their payload — the API layer adds context fields to it."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    compute = _CountingCompute({"candidates": [{"project_id": 1}]})

    first = cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)
    first["current_operator_id"] = 7
    first["candidates"].append({"project_id": 999})

    second = cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)

    assert compute.calls == 1
    assert second == {"candidates": [{"project_id": 1}]}


def test_expired_entries_do_not_accumulate():
    """Stale entries are evicted on the next store so key sprawl cannot grow memory."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    compute = _CountingCompute()

    for limit in (1, 2, 3):
        cache.get_or_compute(
            PreviewCacheKey(operator_id=7, limit=limit, high_priority_only=False),
            compute,
            ttl_seconds=60.0,
            now=clock,
        )
    assert cache.entry_count() == 3

    clock.advance(120.0)
    cache.get_or_compute(
        PreviewCacheKey(operator_id=7, limit=4, high_priority_only=False),
        compute,
        ttl_seconds=60.0,
        now=clock,
    )

    assert cache.entry_count() == 1


def test_clear_removes_every_entry():
    """``clear`` resets the whole process-local cache (used by the test fixture)."""
    cache = PreviewResultCache()
    clock = _FakeClock()
    compute = _CountingCompute()

    cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)
    cache.clear()
    cache.get_or_compute(KEY, compute, ttl_seconds=60.0, now=clock)

    assert compute.calls == 2


# ---------------------------------------------------------------------------
# Integration — the preview endpoint/service actually goes through the cache
# ---------------------------------------------------------------------------


def _configure_software_operator(client):
    """Configure the singleton operator + a software watch strategy."""
    client.put(
        "/api/v1/operator/profile",
        json={
            "business_type": "software",
            "license_codes": ["SW001"],
            "region_codes": ["서울특별시", "전국"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.95,
            "total_awards": 9,
        },
    )
    client.put(
        "/api/v1/operator/strategy",
        json={
            "focus_categories": ["software"],
            "focus_regions": ["서울특별시"],
            "required_keywords": ["AI", "데이터"],
            "minimum_match_score": 0.6,
            "minimum_probability_score": 0.55,
            "notify_only_high_priority": False,
            "max_recommended_candidates": 10,
        },
    )


def _seed_matching_project(test_db):
    project = Project(
        title="서울 AI 데이터 통합 플랫폼 구축",
        description="서울특별시 대상 AI 데이터 분석과 대시보드 자동화 구축",
        requirements="SW001 보유 업체, 서울특별시 수행 가능, 데이터 연계 포함",
        budget_estimate=130000000.0,
        category="software",
        status="open",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _count_collect_calls(monkeypatch) -> dict:
    """Count how many times the expensive candidate scan actually runs."""
    calls = {"count": 0}
    original = StrategyMonitoringService._collect_candidate_evaluations

    def counting(self, db, **kwargs):
        calls["count"] += 1
        return original(self, db, **kwargs)

    monkeypatch.setattr(StrategyMonitoringService, "_collect_candidate_evaluations", counting)
    return calls


def test_preview_candidates_repeat_read_skips_the_expensive_scan(client, test_db, monkeypatch):
    """Back-to-back preview reads (reload / re-click) run one scan, not two."""
    _configure_software_operator(client)
    project = _seed_matching_project(test_db)
    calls = _count_collect_calls(monkeypatch)

    first = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )
    second = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1
    assert first.json()["candidates"] == second.json()["candidates"]
    assert {item["project_id"] for item in second.json()["candidates"]} == {project.id}
    # Cached reads keep the operator-context fields the API layer adds.
    assert second.json()["current_operator_id"] == first.json()["current_operator_id"]


def test_preview_cache_ttl_setting_is_declared():
    """The TTL is a declared setting (§4.5.1), not a literal inside the service."""
    assert settings.OPERATOR_STRATEGY_PREVIEW_CACHE_TTL_SECONDS >= 0
    assert isinstance(preview_cache, PreviewResultCache)


# ---------------------------------------------------------------------------
# Every live strategy write must invalidate — not just the web PUT
# ---------------------------------------------------------------------------


def _seed_preview_entry(operator_id: int) -> None:
    """Put one preview payload in the process-local cache for ``operator_id``."""
    preview_cache.get_or_compute(
        PreviewCacheKey(operator_id=operator_id, limit=10, high_priority_only=False),
        _CountingCompute({"candidates": [], "operator_id": operator_id}),
        60.0,
    )
    assert preview_cache.entry_count() == 1


def test_telegram_strategy_set_invalidates_preview(test_db):
    """``/strategy_set`` writes the same strategy row as the web PUT, so it must invalidate."""
    from app.services.notifications.telegram_strategy import TelegramStrategyCommandProcessor

    processor = TelegramStrategyCommandProcessor()
    strategy = ensure_operator_strategy(test_db)
    _seed_preview_entry(int(strategy.user_id))

    reply = processor._handle_set(test_db, ["categories=software"])

    assert "전략이 업데이트되었습니다" in reply
    assert preview_cache.entry_count() == 0


def test_telegram_strategy_clear_invalidates_preview(test_db):
    """``/strategy_clear`` resets watch rules through the same commit path."""
    from app.services.notifications.telegram_strategy import TelegramStrategyCommandProcessor

    processor = TelegramStrategyCommandProcessor()
    strategy = ensure_operator_strategy(test_db)
    _seed_preview_entry(int(strategy.user_id))

    reply = processor._handle_clear(test_db, ["categories"])

    assert "전략 항목을 초기화했습니다" in reply
    assert preview_cache.entry_count() == 0


def _seed_successful_experiment_run(test_db, *, operator, experiment_key: str):
    run = DecisionExperimentRun(
        operator_id=operator.id,
        experiment_key=experiment_key,
        recommendation_key=f"rec-{experiment_key}",
        status="completed",
        outcome="success",
        title="threshold tuning",
    )
    test_db.add(run)
    test_db.commit()
    test_db.refresh(run)
    return run


def test_experiment_threshold_apply_invalidates_preview(test_db):
    """Applying an experiment's threshold adjustment persists strategy thresholds."""
    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = _seed_successful_experiment_run(
        test_db, operator=operator, experiment_key="exp-review-threshold-tighten"
    )
    _seed_preview_entry(int(operator.id))

    result = DecisionExperimentService().apply_threshold_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentThresholdApplyRequest(dry_run=False),
        operator=operator,
    )

    assert result["applied"] is True
    assert preview_cache.entry_count() == 0


def test_experiment_threshold_dry_run_keeps_preview(test_db):
    """A dry run writes nothing, so it must not churn the cache either."""
    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = _seed_successful_experiment_run(
        test_db, operator=operator, experiment_key="exp-review-threshold-tighten"
    )
    _seed_preview_entry(int(operator.id))

    result = DecisionExperimentService().apply_threshold_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentThresholdApplyRequest(dry_run=True),
        operator=operator,
    )

    assert result["applied"] is False
    assert preview_cache.entry_count() == 1


def test_experiment_strategy_apply_invalidates_preview(test_db):
    """Workload/category tuning is a strategy write on the same row — invalidate too."""
    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = _seed_successful_experiment_run(
        test_db, operator=operator, experiment_key="exp-workload-auto-calibration"
    )
    _seed_preview_entry(int(operator.id))

    result = DecisionExperimentService().apply_strategy_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentStrategyApplyRequest(dry_run=False),
        operator=operator,
    )

    assert result["applied"] is True
    assert preview_cache.entry_count() == 0
