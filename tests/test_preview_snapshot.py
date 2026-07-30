"""preview 스냅샷 서비스·task·API 전환 가드 (설계 2026-07-30 §6 PR-B).

구 preview_cache(프로세스-로컬 single-flight+TTL, #315)의 행동 의도를 DB
스냅샷으로 승계한다: 스탬피드 방지 = 행 status DB 단일비행, 단기 TTL =
OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS 기반 stale 판정 + 자동 재계산 디스패치
(스냅샷은 즉시 서빙). fixture 는 test_scan_memory_hygiene 의 특성화 패턴을
재사용한다(운영자 구성 + 결정적 _analyze_project 스텁).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import OperatorPreviewSnapshot, Project
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.preview_snapshot import (
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_IDLE,
    SNAPSHOT_STATUS_RUNNING,
    PreviewSnapshotService,
)


def _configure_software_operator(client):
    """싱글턴 운영자 + software 감시 전략 (test_scan_memory_hygiene 패턴)."""
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


def _seed_matching_project(test_db, *, title: str = "서울 AI 데이터 통합 플랫폼 구축") -> Project:
    project = Project(
        title=title,
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


def _canned_analyze(self, db, project, **kwargs):
    """결정적 분석 스텁 — 임계값 통과 고정."""
    del db, kwargs
    return {
        "matched_score": 0.7,
        "probability_score": 0.8,
        "recommended_amount": 111_000_000.0,
        "deadline_hours_remaining": 8,
        "current_active_bids": 0,
        "max_active_bids": 3,
        "current_workload_score": 0.0,
        "workload_source": "auto",
        "analysis_summary": f"{project.title} 요약",
        "strengths": [],
        "risk_flags": [],
        "decision": {
            "pursue_bid": True,
            "action": "review",
            "priority_score": 0.9,
            "recommended_amount": 111_000_000.0,
            "probability_score": 0.8,
            "reasoning": "스냅샷 고정",
        },
    }


def _snapshot_row(test_db, operator_id: int, high_priority_only: bool = False):
    return (
        test_db.query(OperatorPreviewSnapshot)
        .filter(
            OperatorPreviewSnapshot.operator_id == operator_id,
            OperatorPreviewSnapshot.high_priority_only == high_priority_only,
        )
        .first()
    )


# ---------------------------------------------------------------------------
# 서비스 라이프사이클 (task body 관점)
# ---------------------------------------------------------------------------


def test_run_recompute_persists_top100_payload_and_meta(client, test_db, monkeypatch):
    """run_recompute 는 mark_running→계산→mark_completed 로 스냅샷을 영속화한다."""
    _configure_software_operator(client)
    project = _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    operator = ensure_operator_account(test_db)

    result = PreviewSnapshotService().run_recompute(
        test_db, operator_id=int(operator.id), high_priority_only=False, task_id="t-1"
    )

    row = _snapshot_row(test_db, int(operator.id))
    assert row is not None
    assert row.status == SNAPSHOT_STATUS_IDLE
    assert row.task_id == "t-1"
    assert row.computed_at is not None
    assert row.last_error is None
    stored = row.payload_json
    assert stored["evaluated_project_count"] == 1
    assert [c["project_id"] for c in stored["candidates"]] == [project.id]
    # JSON-safe: deadline 은 ISO 문자열로 저장된다 (이탈 노트 1)
    assert isinstance(stored["candidates"][0]["deadline"], str)
    assert result["snapshot_id"] == row.id
    assert result["candidate_count"] == 1


def test_run_recompute_failure_marks_failed_with_last_error(client, test_db, monkeypatch):
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)

    def boom(self, db, **kwargs):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(StrategyMonitoringService, "preview_candidates", boom)

    with pytest.raises(RuntimeError, match="scan exploded"):
        PreviewSnapshotService().run_recompute(
            test_db, operator_id=int(operator.id), high_priority_only=False, task_id="t-2"
        )

    row = _snapshot_row(test_db, int(operator.id))
    assert row.status == SNAPSHOT_STATUS_FAILED
    assert "scan exploded" in (row.last_error or "")


# ---------------------------------------------------------------------------
# DB 단일비행 클레임 (구 preview_cache single-flight 의 승계)
# ---------------------------------------------------------------------------


def test_claim_is_single_flight(test_db):
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )

    assert service._claim(test_db, row) is True
    assert service._claim(test_db, row) is False  # running 이면 스킵


def test_claim_reclaims_stale_running_row(test_db):
    """reconciler 임계를 넘긴 running(SIGKILL 고아)은 회수 후 재클레임된다."""
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    assert service._claim(test_db, row) is True
    # 고아 시뮬레이션: updated_at 을 임계 밖으로 밀어낸다
    test_db.query(OperatorPreviewSnapshot).filter(
        OperatorPreviewSnapshot.id == row.id
    ).update({"updated_at": utc_now() - timedelta(hours=2)}, synchronize_session=False)
    test_db.commit()

    assert service._claim(test_db, row) is True


# ---------------------------------------------------------------------------
# 디스패치 (API·전략 쓰기 트리거) + ops 큐 라우팅 + task 멱등
# ---------------------------------------------------------------------------


class _EnqueueRecorder:
    """jobs.enqueue_preview_snapshot_recompute 대역 — 실행 없이 디스패치만 기록."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []

    def __call__(self, *, operator_id: int, high_priority_only: bool):
        self.calls.append((int(operator_id), bool(high_priority_only)))
        return SimpleNamespace(id=f"stub-task-{len(self.calls)}")


@pytest.fixture
def enqueue_stub(monkeypatch) -> _EnqueueRecorder:
    from app.tasks import jobs

    recorder = _EnqueueRecorder()
    monkeypatch.setattr(jobs, "enqueue_preview_snapshot_recompute", recorder)
    return recorder


def test_recompute_task_is_routed_to_ops_queue():
    """워커 컨테이너(이미 monitor·g2 recheck 로 동일 스캔 실행 중)로 라우팅 (§6.3)."""
    from app.core.config import settings
    from app.tasks.celery_app import PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME, build_task_routes

    assert PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME == "jobs.recompute_preview_snapshot"
    assert build_task_routes()[PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME]["queue"] == settings.CELERY_OPS_QUEUE


def test_dispatch_recompute_is_single_flight(test_db, enqueue_stub):
    """running 중 재디스패치는 스킵 — 구 preview_cache 스탬피드 방지의 승계."""
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()

    first = service.dispatch_recompute(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    second = service.dispatch_recompute(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )

    assert first is not None
    assert first.status == SNAPSHOT_STATUS_RUNNING
    assert first.task_id == "stub-task-1"
    assert second is None
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_dispatch_for_strategy_write_targets_existing_keys_only(test_db, enqueue_stub):
    """기존 스냅샷 행이 있는 키만 재계산 — 미사용 키 변형 스캔 방지 (§6.3)."""
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    for key in (False, True):
        service._get_or_create_row(
            test_db, operator_id=int(operator.id), high_priority_only=key
        )

    dispatched = service.dispatch_for_strategy_write(test_db, operator_id=int(operator.id))

    assert dispatched == 2
    assert sorted(enqueue_stub.calls) == [(int(operator.id), False), (int(operator.id), True)]


def test_dispatch_for_strategy_write_defaults_to_false_key_when_no_rows(test_db, enqueue_stub):
    operator = ensure_operator_account(test_db)

    dispatched = PreviewSnapshotService().dispatch_for_strategy_write(
        test_db, operator_id=int(operator.id)
    )

    assert dispatched == 1
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_recompute_task_reuses_unique_row_idempotently(client, test_db, monkeypatch):
    """같은 키 재실행(재전달 상당)은 두 번째 행을 만들지 않는다 (celery_task_id 멱등)."""
    from app.tasks import jobs

    _configure_software_operator(client)
    _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    operator = ensure_operator_account(test_db)

    jobs.recompute_preview_snapshot.apply_async(
        kwargs={"operator_id": int(operator.id), "high_priority_only": False}
    )
    jobs.recompute_preview_snapshot.apply_async(
        kwargs={"operator_id": int(operator.id), "high_priority_only": False}
    )

    rows = (
        test_db.query(OperatorPreviewSnapshot)
        .filter(OperatorPreviewSnapshot.operator_id == int(operator.id))
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == SNAPSHOT_STATUS_IDLE
    assert rows[0].computed_at is not None


# ---------------------------------------------------------------------------
# GET /strategy/candidates — 순수 읽기 + 자동 디스패치 (§6.2)
# ---------------------------------------------------------------------------

_LEGACY_RESPONSE_FIELDS = {
    "operator_id", "evaluated_project_count", "returned_candidate_count",
    "high_priority_only", "candidates", "current_operator_id", "current_operator_username",
}


def test_get_candidates_bootstraps_empty_running_when_snapshot_missing(
    client, test_db, enqueue_stub
):
    """부재(최초) 시: 빈 후보 + snapshot_status=running + 단일비행 디스패치 (§6.2)."""
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    # 하위호환 superset (HARD): 기존 필드는 전부 그대로 존재한다
    assert _LEGACY_RESPONSE_FIELDS <= set(payload.keys())
    assert payload["candidates"] == []
    assert payload["evaluated_project_count"] == 0
    assert payload["returned_candidate_count"] == 0
    assert payload["snapshot_status"] == "running"
    assert payload["computed_at"] is None
    assert payload["stale"] is False
    assert enqueue_stub.calls == [(int(operator.id), False)]
    # 재조회는 running 스킵 — 디스패치가 늘지 않는다 (스탬피드 방지)
    client.get("/api/v1/operator/strategy/candidates", params={"high_priority_only": False})
    assert len(enqueue_stub.calls) == 1


def test_get_candidates_first_read_computes_inline_in_eager_mode(client, test_db, monkeypatch):
    """eager(테스트) 모드: 첫 GET 이 스냅샷을 인라인 계산해 반환하고, 재조회는
    스캔 없이 스냅샷을 서빙한다 — 구 repeat-read 스탬피드 테스트의 승계."""
    _configure_software_operator(client)
    project = _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    calls = {"count": 0}
    original = StrategyMonitoringService._collect_candidate_evaluations

    def counting(self, db, **kwargs):
        calls["count"] += 1
        return original(self, db, **kwargs)

    monkeypatch.setattr(StrategyMonitoringService, "_collect_candidate_evaluations", counting)
    # _configure 의 전략 PUT 은 이제 재계산을 디스패치한다(§6.3): project 시드 전에
    # 빈 스냅샷을 선계산했으므로, 첫 GET 이 시드 공고를 실제로 계산하도록 비운다.
    test_db.query(OperatorPreviewSnapshot).delete()
    test_db.commit()

    first = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )
    second = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    assert first.status_code == 200 and second.status_code == 200
    assert calls["count"] == 1  # 스캔은 task 1회뿐
    assert {c["project_id"] for c in first.json()["candidates"]} == {project.id}
    assert first.json()["candidates"] == second.json()["candidates"]
    assert second.json()["snapshot_status"] == "idle"
    assert second.json()["computed_at"] is not None
    assert second.json()["stale"] is False


def test_get_candidates_slices_stored_top100_by_requested_limit(client, test_db, enqueue_stub):
    """저장 top-100 을 요청 limit 으로 슬라이스 — limit 은 키 차원이 아니다 (§6.1)."""
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    stored_candidates = [
        {
            "project_id": index, "title": f"공고 {index}", "category": "software",
            "budget_estimate": 1.0, "deadline": None, "matched_score": 0.7,
            "probability_score": 0.8, "priority_score": 0.9, "action": "review",
            "recommended_amount": 1.0, "analysis_summary": "s", "strategy_reasons": ["r"],
        }
        for index in range(1, 4)
    ]
    service.mark_completed(
        test_db,
        row_id=int(row.id),
        payload={
            "operator_id": int(operator.id),
            "evaluated_project_count": 42,
            "returned_candidate_count": 3,
            "high_priority_only": False,
            "candidates": stored_candidates,
        },
    )
    enqueue_stub.calls.clear()  # _configure 의 전략 PUT 이 남긴 setup 디스패치 제거

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 2},
    )

    payload = response.json()
    assert [c["project_id"] for c in payload["candidates"]] == [1, 2]
    assert payload["returned_candidate_count"] == 2
    assert payload["evaluated_project_count"] == 42
    assert enqueue_stub.calls == []  # 신선한 스냅샷은 디스패치하지 않는다


def test_get_candidates_serves_stale_snapshot_and_redispatches(client, test_db, enqueue_stub):
    """stale(>OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS) 시: 기존 payload 즉시 서빙
    + stale=true + 재계산 자동 디스패치 (§6.2)."""
    from app.core.config import settings

    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    row = service._get_or_create_row(
        test_db, operator_id=int(operator.id), high_priority_only=False
    )
    service.mark_completed(
        test_db,
        row_id=int(row.id),
        payload={
            "operator_id": int(operator.id), "evaluated_project_count": 1,
            "returned_candidate_count": 1, "high_priority_only": False,
            "candidates": [{
                "project_id": 7, "title": "오래된 후보", "category": "software",
                "budget_estimate": 1.0, "deadline": None, "matched_score": 0.7,
                "probability_score": 0.8, "priority_score": 0.9, "action": "review",
                "recommended_amount": 1.0, "analysis_summary": "s", "strategy_reasons": ["r"],
            }],
        },
    )
    stale_at = utc_now() - timedelta(
        seconds=int(settings.OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS) + 60
    )
    test_db.query(OperatorPreviewSnapshot).filter(
        OperatorPreviewSnapshot.id == row.id
    ).update({"computed_at": stale_at}, synchronize_session=False)
    test_db.commit()
    enqueue_stub.calls.clear()  # _configure 의 전략 PUT 이 남긴 setup 디스패치 제거

    response = client.get(
        "/api/v1/operator/strategy/candidates",
        params={"high_priority_only": False, "limit": 10},
    )

    payload = response.json()
    assert [c["project_id"] for c in payload["candidates"]] == [7]  # stale 즉시 서빙
    assert payload["stale"] is True
    assert payload["snapshot_status"] == "running"  # 재계산 클레임됨
    assert enqueue_stub.calls == [(int(operator.id), False)]


# ---------------------------------------------------------------------------
# 모든 전략 쓰기는 재계산을 디스패치한다 (구 preview_cache.invalidate 5경로 대체)
# ---------------------------------------------------------------------------


def test_strategy_put_dispatches_recompute_for_existing_keys(client, test_db, enqueue_stub):
    """웹 PUT: 기존 스냅샷 키(양쪽)만 재계산 디스패치 (§6.3)."""
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)
    service = PreviewSnapshotService()
    for key in (False, True):
        service._get_or_create_row(
            test_db, operator_id=int(operator.id), high_priority_only=key
        )
    # _configure 의 전략 PUT 이 False 키를 running 으로 남긴다(단일비행). 이 테스트는
    # "양쪽 기존 키가 idle 일 때 PUT 이 둘 다 재계산 디스패치" 를 검증하므로 idle 리셋.
    test_db.query(OperatorPreviewSnapshot).filter(
        OperatorPreviewSnapshot.operator_id == int(operator.id)
    ).update({"status": SNAPSHOT_STATUS_IDLE}, synchronize_session=False)
    test_db.commit()
    enqueue_stub.calls.clear()

    update = client.put("/api/v1/operator/strategy", json={"exclude_keywords": ["데이터"]})

    assert update.status_code == 200
    assert sorted(enqueue_stub.calls) == [(int(operator.id), False), (int(operator.id), True)]


def test_telegram_strategy_edit_dispatches_recompute(test_db, enqueue_stub):
    """텔레그램 set/clear/버튼은 _persist_strategy_edit 단일 seam 을 지난다."""
    from app.core.single_user import ensure_operator_strategy
    from app.services.notifications.telegram_strategy import TelegramStrategyCommandProcessor

    strategy = ensure_operator_strategy(test_db)
    reply = TelegramStrategyCommandProcessor()._handle_set(test_db, ["categories=software"])

    assert "전략이 업데이트되었습니다" in reply
    assert enqueue_stub.calls == [(int(strategy.user_id), False)]


def test_experiment_threshold_apply_dispatches_recompute(test_db, enqueue_stub):
    from app.models.models import DecisionExperimentRun
    from app.schemas.schemas import DecisionExperimentThresholdApplyRequest
    from app.services.decision_experiments import DecisionExperimentService
    from app.core.single_user import ensure_operator_strategy

    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = DecisionExperimentRun(
        operator_id=operator.id,
        experiment_key="exp-review-threshold-tighten",
        recommendation_key="rec-exp-review-threshold-tighten",
        status="completed",
        outcome="success",
        title="threshold tuning",
    )
    test_db.add(run)
    test_db.commit()

    result = DecisionExperimentService().apply_threshold_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentThresholdApplyRequest(dry_run=False),
        operator=operator,
    )

    assert result["applied"] is True
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_experiment_threshold_dry_run_does_not_dispatch(test_db, enqueue_stub):
    from app.models.models import DecisionExperimentRun
    from app.schemas.schemas import DecisionExperimentThresholdApplyRequest
    from app.services.decision_experiments import DecisionExperimentService
    from app.core.single_user import ensure_operator_strategy

    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = DecisionExperimentRun(
        operator_id=operator.id,
        experiment_key="exp-review-threshold-tighten",
        recommendation_key="rec-exp-review-threshold-tighten",
        status="completed",
        outcome="success",
        title="threshold tuning",
    )
    test_db.add(run)
    test_db.commit()

    result = DecisionExperimentService().apply_threshold_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentThresholdApplyRequest(dry_run=True),
        operator=operator,
    )

    assert result["applied"] is False
    assert enqueue_stub.calls == []


def test_experiment_strategy_apply_dispatches_recompute(test_db, enqueue_stub):
    """workload/category 튜닝 적용도 같은 행을 쓰는 전략 쓰기 — 재계산 디스패치
    (lifecycle.apply_strategy_adjustments 경로, 구 preview_cache.invalidate 대체)."""
    from app.models.models import DecisionExperimentRun
    from app.schemas.schemas import DecisionExperimentStrategyApplyRequest
    from app.services.decision_experiments import DecisionExperimentService
    from app.core.single_user import ensure_operator_strategy

    operator = ensure_operator_account(test_db)
    ensure_operator_strategy(test_db)
    run = DecisionExperimentRun(
        operator_id=operator.id,
        experiment_key="exp-workload-auto-calibration",
        recommendation_key="rec-exp-workload-auto-calibration",
        status="completed",
        outcome="success",
        title="workload tuning",
    )
    test_db.add(run)
    test_db.commit()

    result = DecisionExperimentService().apply_strategy_adjustments(
        test_db,
        run_id=int(run.id),
        request=DecisionExperimentStrategyApplyRequest(dry_run=False),
        operator=operator,
    )

    assert result["applied"] is True
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_strategy_update_refreshes_preview_end_to_end(client, test_db, monkeypatch):
    """(eager) 저장 → 재계산 → 다음 GET 은 새 규칙 반영 — 구
    test_preview_candidates_recomputes_after_strategy_update 의 승계."""
    _configure_software_operator(client)
    _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)
    calls = {"count": 0}
    original = StrategyMonitoringService._collect_candidate_evaluations

    def counting(self, db, **kwargs):
        calls["count"] += 1
        return original(self, db, **kwargs)

    monkeypatch.setattr(StrategyMonitoringService, "_collect_candidate_evaluations", counting)
    # _configure 의 전략 PUT 이 (project 시드 전에) 빈 스냅샷을 선계산했다. 첫 GET 이
    # 실제로 계산하고 이후 PUT 트리거가 재계산하도록 setup 잔여 스냅샷을 비운다.
    test_db.query(OperatorPreviewSnapshot).delete()
    test_db.commit()

    client.get("/api/v1/operator/strategy/candidates", params={"high_priority_only": False})
    update = client.put("/api/v1/operator/strategy", json={"exclude_keywords": ["데이터"]})
    after = client.get(
        "/api/v1/operator/strategy/candidates", params={"high_priority_only": False}
    )

    assert update.status_code == 200
    assert calls["count"] == 2  # 최초 1 + 전략 저장 트리거 1 (GET 재조회는 0)
    assert after.json()["candidates"] == []  # 새 규칙이 시드 공고를 배제


# ---------------------------------------------------------------------------
# POST /candidates/refresh (202) + sync monitor 위임 (§6.2)
# ---------------------------------------------------------------------------


def test_candidates_refresh_returns_202_envelope(client, test_db, enqueue_stub):
    _configure_software_operator(client)
    operator = ensure_operator_account(test_db)

    response = client.post(
        "/api/v1/operator/strategy/candidates/refresh",
        params={"high_priority_only": False},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["task_id"] == "stub-task-1"
    assert payload["operator_id"] == int(operator.id)
    assert payload["high_priority_only"] is False
    assert payload["snapshot_status"] == "running"
    assert payload["poll_url"] == "/api/v1/operator/strategy/candidates"
    assert enqueue_stub.calls == [(int(operator.id), False)]


def test_candidates_refresh_single_flight_reuses_running_task(client, test_db, enqueue_stub):
    _configure_software_operator(client)

    first = client.post("/api/v1/operator/strategy/candidates/refresh")
    second = client.post("/api/v1/operator/strategy/candidates/refresh")

    assert first.status_code == 202 and second.status_code == 202
    assert len(enqueue_stub.calls) == 1  # 단일비행: 두 번째는 디스패치 없음
    assert second.json()["task_id"] == first.json()["task_id"]


def test_sync_monitor_route_returns_202_async_envelope(client, test_db, monkeypatch):
    """(eager) POST /monitor 는 202 async envelope 을 반환하고 결과는 poll_url 로 읽는다."""
    _configure_software_operator(client)
    project = _seed_matching_project(test_db)
    monkeypatch.setattr(StrategyMonitoringService, "_analyze_project", _canned_analyze)

    kickoff = client.post(
        "/api/v1/operator/strategy/monitor",
        json={"high_priority_only": False, "limit": 5},
    )

    assert kickoff.status_code == 202
    envelope = kickoff.json()
    assert envelope["task_name"] == "jobs.monitor_operator_strategy"
    assert envelope["poll_url"].endswith(envelope["task_id"])

    status_payload = client.get(envelope["poll_url"]).json()
    assert status_payload["status"] == "completed"
    assert status_payload["result"]["results"][0]["project_id"] == project.id
    assert (
        status_payload["result"]["trigger_source"]
        == StrategyMonitoringService.ASYNC_TRIGGER_SOURCE
    )
