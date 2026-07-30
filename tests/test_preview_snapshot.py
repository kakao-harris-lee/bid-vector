"""preview 스냅샷 서비스·task·API 전환 가드 (설계 2026-07-30 §6 PR-B).

구 preview_cache(프로세스-로컬 single-flight+TTL, #315)의 행동 의도를 DB
스냅샷으로 승계한다: 스탬피드 방지 = 행 status DB 단일비행, 단기 TTL =
OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS 기반 stale 판정 + 자동 재계산 디스패치
(스냅샷은 즉시 서빙). fixture 는 test_scan_memory_hygiene 의 특성화 패턴을
재사용한다(운영자 구성 + 결정적 _analyze_project 스텁).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import OperatorPreviewSnapshot, Project
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.preview_snapshot import (
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_IDLE,
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
