"""``BidDecisionRequest.budget_estimate`` must always carry 기초금액-basis.

WHY
---
``budget_capture_score = recommended_amount / budget_estimate``. The numerator is
the predictor's recommendation, which every path computes against the 기초금액/
사업금액 (``resolve_notice_bid_base``, #162). When the denominator is the 추정가격
(ex-VAT ``Project.budget_estimate``) instead, a 과세 공고 gets
``capture ≈ rate × 1.1`` — clamped to 1.0 — so the SAME notice scores differently
depending on which path evaluated it, inflating opportunity/priority and the
"예산 대비 추천가 유지율" reason line.

These tests pin every production construction site of ``BidDecisionRequest`` /
``BidDecisionSaveRequest`` to the 기초금액 base with an explicit 과세 공고 value
table (추정가격 100,000,000 / 기초금액 110,000,000), so a future path that reaches
for ``project.budget_estimate`` again fails here instead of in a live 실격.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.domain.money import BaseAmount
from app.models.models import HistoricalData, Project
from app.schemas.schemas import BidDecisionSaveRequest
from app.services import allocation_core as ac
from app.services.allocation import BidDecisionService
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.paper_bidding_backtest.base import CandidatePredictionContext
from app.services.opportunity_monitoring import StrategyMonitoringService

# 과세 공고 value table: 기초금액 = 추정가격 × 1.1.
_BUDGET_ESTIMATE = 100_000_000.0
_BASE_AMOUNT = 110_000_000.0
_RECOMMENDED_AMOUNT = 99_000_000.0  # 0.90 × 기초금액

# The two capture ratios the basis choice produces.
_CAPTURE_ON_BASE = _RECOMMENDED_AMOUNT / _BASE_AMOUNT  # 0.90 — correct
_CAPTURE_ON_ESTIMATE = _RECOMMENDED_AMOUNT / _BUDGET_ESTIMATE  # 0.99 — the bug


def _vat_project(db, *, category: str = "construction") -> Project:
    project = Project(
        title="basis 정합 검증 공고",
        description="투찰 기준금액 basis 정합",
        requirements="",
        budget_estimate=_BUDGET_ESTIMATE,
        category=category,
        status="open",
    )
    db.add(project)
    db.flush()
    db.add(
        HistoricalData(
            project_id=project.id,
            base_amount=_BASE_AMOUNT,
            bid_rate=0.0,
            category=category,
        )
    )
    db.commit()
    db.refresh(project)
    return project


# --------------------------------------------------------------------------- #
# The pure kernel (allocation_core) — the single arithmetic that mixes basis
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "recommended_amount, bid_base, expected",
    [
        (_RECOMMENDED_AMOUNT, BaseAmount(_BASE_AMOUNT), _CAPTURE_ON_BASE),
        # No usable base → the neutral 0.5 default (unchanged behaviour).
        (_RECOMMENDED_AMOUNT, None, 0.5),
        (_RECOMMENDED_AMOUNT, BaseAmount(0.0), 0.5),
        (_RECOMMENDED_AMOUNT, BaseAmount(-1.0), 0.5),
        # Clamped into the 0-1 unit range at both ends.
        (2 * _BASE_AMOUNT, BaseAmount(_BASE_AMOUNT), 1.0),
        (0.0, BaseAmount(_BASE_AMOUNT), 0.0),
    ],
)
def test_budget_capture_score_value_table(recommended_amount, bid_base, expected):
    assert ac.budget_capture_score(recommended_amount, bid_base) == pytest.approx(expected)


def test_budget_capture_score_separates_the_two_bases():
    """The 과세 공고 gap this whole PR exists for: 0.90 (base) vs 0.99 (추정가격)."""
    on_base = ac.budget_capture_score(_RECOMMENDED_AMOUNT, BaseAmount(_BASE_AMOUNT))
    on_estimate = ac.budget_capture_score(_RECOMMENDED_AMOUNT, BaseAmount(_BUDGET_ESTIMATE))

    assert on_base == pytest.approx(_CAPTURE_ON_BASE)
    assert on_estimate == pytest.approx(_CAPTURE_ON_ESTIMATE)
    assert on_estimate > on_base


# --------------------------------------------------------------------------- #
# Site 1: allocation.save_decision fallback (API path, client omits the field)
# --------------------------------------------------------------------------- #


def test_save_decision_fallback_uses_bid_base_not_budget_estimate(client, test_db):
    """A client that omits ``budget_estimate`` must fall back to 기초금액, not 추정가격."""
    project = _vat_project(test_db)

    record = BidDecisionService().save_decision(
        test_db,
        BidDecisionSaveRequest(
            project_id=project.id,
            recommended_amount=_RECOMMENDED_AMOUNT,
            probability_score=0.7,
            matched_score=0.7,
            budget_estimate=None,
        ),
    )

    assert record.budget_capture_score == pytest.approx(_CAPTURE_ON_BASE, abs=1e-6)
    assert record.budget_capture_score != pytest.approx(_CAPTURE_ON_ESTIMATE, abs=1e-6)
    # The capture signal persisted into score_breakdown agrees (rounded to 2dp).
    assert json.loads(record.score_breakdown)["budget_capture_signal"] == pytest.approx(0.9)


def test_save_decision_respects_explicit_budget_estimate(client, test_db):
    """An explicitly supplied value is still honoured — the fallback only fills a gap."""
    project = _vat_project(test_db)

    record = BidDecisionService().save_decision(
        test_db,
        BidDecisionSaveRequest(
            project_id=project.id,
            recommended_amount=_RECOMMENDED_AMOUNT,
            probability_score=0.7,
            matched_score=0.7,
            budget_estimate=_BASE_AMOUNT,
        ),
    )

    assert record.budget_capture_score == pytest.approx(_CAPTURE_ON_BASE, abs=1e-6)


# --------------------------------------------------------------------------- #
# Site 2: opportunity_monitoring (scan → persisted decision)
# --------------------------------------------------------------------------- #


def test_monitor_decision_inputs_resolve_the_notice_bid_base(test_db):
    """The monitor persists a decision scored on 기초금액, matching direct analysis."""
    project = _vat_project(test_db)
    analysis = {
        "recommended_amount": _RECOMMENDED_AMOUNT,
        "probability_score": 0.7,
        "matched_score": 0.7,
        "deadline_hours_remaining": 24,
        "strengths": [],
        "risk_flags": [],
        "analysis_summary": "",
        "market_insights": {"competitiveness_score": 0.5},
        "decision": {"expected_margin_score": 0.5, "execution_complexity_score": 0.35},
    }

    inputs = StrategyMonitoringService()._build_candidate_decision_inputs(
        db=test_db,
        project=project,
        analysis=analysis,
        max_active_bids=3,
        current_workload_score=0.0,
    )

    assert inputs.budget_estimate == pytest.approx(_BASE_AMOUNT)
    assert inputs.budget_estimate != pytest.approx(_BUDGET_ESTIMATE)


# --------------------------------------------------------------------------- #
# Site 3: paper_bidding_backtest (accuracy measurement must match live)
# --------------------------------------------------------------------------- #


def test_backtest_decision_request_uses_bid_base(test_db, monkeypatch):
    """The backtest scores capture on the SAME base it priced the paper bid against.

    ``budget`` (추정가격) stays the reporting/strategy-band field; the decision
    denominator is ``bid_base`` so a backtest action distribution is comparable to
    the live path it is supposed to measure.
    """
    project = _vat_project(test_db)
    service = PaperBiddingBacktestService()

    captured: dict[str, object] = {}

    def _capture(request, db=None, **kwargs):
        captured["budget_estimate"] = request.budget_estimate
        return {
            "action": "review",
            "priority_score": 0.5,
            "budget_capture_score": 0.5,
            "pursue_bid": False,
            "reasoning": "",
        }

    monkeypatch.setattr(service.decision_service, "evaluate_opportunity", _capture)

    context = CandidatePredictionContext(
        budget=_BUDGET_ESTIMATE,
        bid_base=BaseAmount(_BASE_AMOUNT),
        data_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
        history=[],
        business_group=None,
        prediction={
            "predicted_price": _RECOMMENDED_AMOUNT,
            "predicted_bid_rate": 0.9,
            "bid_rate_candidates": [
                {"label": "base", "bid_rate": 0.9, "predicted_price": _RECOMMENDED_AMOUNT}
            ],
        },
    )

    service._build_candidate_decision_context(
        test_db,
        project=project,
        prediction_context=context,
        scenario="base",
        profile=None,
    )

    assert captured["budget_estimate"] == pytest.approx(_BASE_AMOUNT)
    assert captured["budget_estimate"] != pytest.approx(_BUDGET_ESTIMATE)
