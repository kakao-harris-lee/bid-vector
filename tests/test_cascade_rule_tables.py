"""Characterization tests for the four cascades re-expressed as declarative tables.

These lock the exact first-match boundary behaviour of four open-coded
``if/elif`` cascades BEFORE (and after) they are moved into declarative rule /
bucket tables. Every branch is exercised at the input that fires it, plus the
±1 boundary where a neighbouring branch takes over, so a ``>=`` -> ``>`` drift,
a reordered rung, or a changed literal/label would fail loudly. They must stay
green both before and after the conversion (byte-identical outputs).

* Target 1 — ``DecisionAnalyticsService._build_history_adjustment`` (experiment
  priority cascade: promoted/deprioritized/neutral + priority_delta).
* Target 2 — ``_build_settlement_overview`` settlement-status cascade (driven
  end-to-end so the pre-extraction inline elif chain is locked).
* Target 3 — ``BidDecisionService._compute_urgency_score`` (deadline-hours ladder).
* Target 4 — ``describe_company_capability`` revenue/award band ladders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.backtests import _build_settlement_overview
from app.models.models import CompanyProfile
from app.services.allocation import BidDecisionService
from app.services.classification.semantic import describe_company_capability
from app.services.decision_analytics import DecisionAnalyticsService


# ---------------------------------------------------------------------------
# Target 1 — _build_history_adjustment (experiment priority cascade)
# ---------------------------------------------------------------------------


def _history_summary(
    *,
    success_count: int = 0,
    rollback_count: int = 0,
    failed_count: int = 0,
    pending_count: int = 0,
    applied_count: int = 0,
    run_count: int = 1,
) -> dict[str, int]:
    return {
        "run_count": run_count,
        "success_count": success_count,
        "rollback_count": rollback_count,
        "failed_count": failed_count,
        "pending_count": pending_count,
        "applied_count": applied_count,
    }


@pytest.mark.parametrize(
    ("summary", "expected_status", "expected_delta", "reason_fragment"),
    [
        # Branch 1: applied>0 AND success>0 -> promoted 14.0 (highest precedence).
        (_history_summary(applied_count=1, success_count=1), "promoted", 14.0, "적용된 실험 이력"),
        # Branch 2: negative>=2 AND success==0 -> deprioritized -55.0 (boundary =2).
        (_history_summary(rollback_count=2), "deprioritized", -55.0, "반복 실패"),
        (_history_summary(rollback_count=1, failed_count=1), "deprioritized", -55.0, "반복 실패"),
        # Branch 3: negative>success but not branch 2 (negative==1<2, or success>0).
        (_history_summary(rollback_count=1), "deprioritized", -35.0, "성공 이력보다 많아"),
        (_history_summary(rollback_count=2, success_count=1, applied_count=0), "deprioritized", -35.0, "성공 이력보다 많아"),
        # Branch 4: pending>=2 AND success==0, no negatives (boundary =2).
        (_history_summary(pending_count=2), "deprioritized", -20.0, "보류 또는 표본 부족"),
        # Branch 5: success>0, none of the above -> promoted 8.0.
        (_history_summary(success_count=2), "promoted", 8.0, "성공한 실험 이력"),
        (_history_summary(success_count=1, applied_count=0), "promoted", 8.0, "성공한 실험 이력"),
        # Branch 6 (else): no actionable signal -> neutral 0.0.
        (_history_summary(pending_count=1), "neutral", 0.0, "충분한 신호가 없습니다"),
        (_history_summary(run_count=1), "neutral", 0.0, "충분한 신호가 없습니다"),
    ],
)
def test_build_history_adjustment_cascade(summary, expected_status, expected_delta, reason_fragment):
    adjustment = DecisionAnalyticsService()._build_history_adjustment(summary)

    assert adjustment["status"] == expected_status
    assert adjustment["priority_delta"] == expected_delta
    assert reason_fragment in adjustment["reason"]
    # Count passthrough must be preserved verbatim.
    assert adjustment["success_count"] == summary["success_count"]
    assert adjustment["applied_count"] == summary["applied_count"]
    assert adjustment["recent_run_count"] == summary["run_count"]


@pytest.mark.parametrize("empty", [None, {}])
def test_build_history_adjustment_empty_history(empty):
    adjustment = DecisionAnalyticsService()._build_history_adjustment(empty)

    assert adjustment["status"] == "neutral"
    assert adjustment["priority_delta"] == 0.0
    assert "실험 이력이 없습니다" in adjustment["reason"]
    assert adjustment["recent_run_count"] == 0


# ---------------------------------------------------------------------------
# Target 2 — _build_settlement_overview status cascade (end-to-end)
# ---------------------------------------------------------------------------


def _paper_bid(*, settlement=None, project=None):
    return SimpleNamespace(settlement=settlement, project=project)


def _run(paper_bids):
    return SimpleNamespace(paper_bids=paper_bids)


def _project(*, deadline=None, tender_results=None):
    return SimpleNamespace(deadline=deadline, tender_results=tender_results or [])


def _tender_result(*, winning_amount, announced_at=None, created_at=None):
    return SimpleNamespace(
        winning_amount=winning_amount,
        announced_at=announced_at,
        created_at=created_at,
    )


def test_settlement_overview_no_paper_bids():
    overview = _build_settlement_overview(_run([]))
    assert overview.status == "no_paper_bids"
    assert overview.label == "검증 없음"
    assert overview.detail == "페이퍼 투찰 항목이 없습니다."
    assert overview.next_confirmable_at is None


def test_settlement_overview_settled():
    settled_at = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
    pb = _paper_bid(settlement=SimpleNamespace(settled_at=settled_at))
    overview = _build_settlement_overview(_run([pb]))
    assert overview.status == "settled"
    assert overview.label == "정산 완료"
    assert overview.detail == "1건 모두 최종 결과로 정산되었습니다."
    assert overview.next_confirmable_at == settled_at


def test_settlement_overview_ready_to_settle():
    announced = datetime(2026, 6, 2, 5, 0, tzinfo=UTC)
    project = _project(tender_results=[_tender_result(winning_amount=1000.0, announced_at=announced)])
    pb = _paper_bid(project=project)
    overview = _build_settlement_overview(_run([pb]))
    assert overview.status == "ready_to_settle"
    assert overview.label == "정산 가능"
    assert overview.detail == "1건은 최종 결과가 입수되어 승패 판정이 가능합니다."
    assert overview.next_confirmable_at == announced


def test_settlement_overview_waiting_result():
    past_deadline = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    project = _project(deadline=past_deadline, tender_results=[])
    pb = _paper_bid(project=project)
    overview = _build_settlement_overview(_run([pb]))
    assert overview.status == "waiting_result"
    assert overview.label == "결과 대기"
    assert overview.detail == "마감이 지난 1건은 최종 결과가 수집되면 정산됩니다."
    assert overview.next_confirmable_at == past_deadline


def test_settlement_overview_before_deadline():
    future_deadline = datetime.now(UTC) + timedelta(days=30)
    project = _project(deadline=future_deadline, tender_results=[])
    pb = _paper_bid(project=project)
    overview = _build_settlement_overview(_run([pb]))
    assert overview.status == "before_deadline"
    assert overview.label == "마감 전"
    assert "1건은 아직 마감 전입니다" in overview.detail
    assert overview.next_confirmable_at == future_deadline


def test_settlement_overview_deadline_missing():
    project = _project(deadline=None, tender_results=[])
    pb = _paper_bid(project=project)
    overview = _build_settlement_overview(_run([pb]))
    assert overview.status == "deadline_missing"
    assert overview.label == "마감 미정"
    assert overview.detail == "마감일이 없어 정산 예상 시점을 계산할 수 없습니다."
    assert overview.next_confirmable_at is None


# ---------------------------------------------------------------------------
# Target 3 — _compute_urgency_score (deadline-hours ladder)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (None, 0.3),   # unknown deadline
        (0, 1.0),      # <= 6
        (6, 1.0),      # boundary
        (7, 0.8),      # <= 24
        (24, 0.8),     # boundary
        (25, 0.55),    # <= 72
        (72, 0.55),    # boundary
        (73, 0.25),    # else
        (1000, 0.25),  # far out
    ],
)
def test_compute_urgency_score_ladder(hours, expected):
    assert BidDecisionService()._compute_urgency_score(hours) == expected


# ---------------------------------------------------------------------------
# Target 4 — describe_company_capability revenue/award band ladders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("annual_revenue", "expected_band"),
    [
        (1_000_000_000, "대형 프로젝트 대응 가능"),      # >= 1e9 boundary
        (1_500_000_000, "대형 프로젝트 대응 가능"),
        (999_999_999, "중대형 프로젝트 대응 가능"),      # just below 1e9
        (300_000_000, "중대형 프로젝트 대응 가능"),      # >= 3e8 boundary
        (299_999_999, "중형 프로젝트 대응 가능"),        # just below 3e8, still > 0
        (1, "중형 프로젝트 대응 가능"),                  # smallest positive
        (0, "매출 정보 부족"),                           # zero -> not > 0
    ],
)
def test_describe_company_capability_revenue_band(annual_revenue, expected_band):
    profile = CompanyProfile(annual_revenue=annual_revenue, total_awards=0, capacity_score=None)
    assert expected_band in describe_company_capability(profile)


@pytest.mark.parametrize(
    ("total_awards", "expected_band"),
    [
        (5, "낙찰 실적 다수"),      # >= 5 boundary
        (9, "낙찰 실적 다수"),
        (4, "낙찰 실적 보유"),      # just below 5
        (2, "낙찰 실적 보유"),      # >= 2 boundary
        (1, "낙찰 실적 제한적"),    # just below 2
        (0, "낙찰 실적 제한적"),
    ],
)
def test_describe_company_capability_award_band(total_awards, expected_band):
    profile = CompanyProfile(annual_revenue=0, total_awards=total_awards, capacity_score=None)
    assert expected_band in describe_company_capability(profile)
