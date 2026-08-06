"""Characterization tests for the bid-decision action gates.

These lock the behaviour of the three action-gate literals inside
``BidDecisionService.evaluate_opportunity`` BEFORE they are extracted into
``Settings`` (PR-2, CLAUDE.md §4.5.1). They must stay green both before and
after the extraction, proving the refactor changes zero behaviour.

The three gates being characterized (allocation.py ~:119-131):
  (a) capacity-hold: ``current_active_bids >= max_active_bids`` AND
      ``priority_score < 0.8`` -> action stays ``skip`` (conservative hold).
  (b) force-bid:     ``probability_score >= 0.8`` AND ``matched_score >= 0.7``
      -> action forced to ``bid_now`` (independent of the priority threshold).
  (c) the review/skip ladder below (``bid_now_threshold`` / ``review_threshold``
      come from OperatorStrategy and are already injected; not in this PR's
      scope — pinned here only as surrounding context).

All cases call ``evaluate_opportunity(..., db=None)`` so the strategy thresholds
fall back to the class defaults (bid_now=0.7, review=0.45) and the gate literals
are the only branch drivers. Reasons are compared byte-equal so the honesty-spec
wording ("가격 적합도(추정)", etc.) is frozen too.
"""

from __future__ import annotations

import pytest

from app.schemas.schemas import BidDecisionRequest
from app.services.allocation import BidDecisionService


def _request(**overrides) -> BidDecisionRequest:
    """Build a BidDecisionRequest with neutral, penalty-free defaults.

    Defaults zero out both penalties (no active load, no workload, low execution
    complexity) so ``priority_score`` equals the weighted opportunity score and
    each case can dial a single gate input.
    """
    defaults = dict(
        project_id=1,
        recommended_amount=100.0,
        probability_score=0.5,
        matched_score=0.5,
        deadline_hours_remaining=None,
        current_active_bids=0,
        max_active_bids=3,
        current_workload_score=0.0,
        budget_estimate=None,
        competitiveness_score=0.5,
        expected_margin_score=None,
        execution_complexity_score=None,
        workload_source="provided",
    )
    defaults.update(overrides)
    return BidDecisionRequest(**defaults)


def _evaluate(**overrides) -> dict:
    """Evaluate a request against the default (db-less) strategy thresholds."""
    return BidDecisionService().evaluate_opportunity(_request(**overrides), db=None)


# ---------------------------------------------------------------------------
# (b) force-bid gate: probability_score >= 0.8 AND matched_score >= 0.7
#
# priority_score is held inside the review band [0.45, 0.7) for every case, so
# the ONLY thing that can promote an action to bid_now is the force-bid gate.
# Crossing below either literal must demote bid_now -> review (never skip).
# ---------------------------------------------------------------------------

_FORCE_BID_BASE = dict(competitiveness_score=0.0, execution_complexity_score=0.0)


@pytest.mark.parametrize(
    "probability_score, matched_score, expected_action",
    [
        (0.80, 0.70, "bid_now"),  # both exactly at the literals -> forced bid
        (0.81, 0.71, "bid_now"),  # just above both -> forced bid
        (0.79, 0.70, "review"),   # probability just below 0.8 -> demoted
        (0.80, 0.69, "review"),   # matched just below 0.7 -> demoted
    ],
)
def test_force_bid_gate_boundaries(probability_score, matched_score, expected_action):
    """probability>=0.8 AND matched>=0.7 forces bid_now; dropping below demotes."""
    decision = _evaluate(
        probability_score=probability_score,
        matched_score=matched_score,
        **_FORCE_BID_BASE,
    )
    # priority stays in the review band so force-bid is the sole bid_now driver.
    assert 0.45 <= decision["priority_score"] < 0.70
    assert decision["action"] == expected_action


# ---------------------------------------------------------------------------
# (a) capacity-hold gate: at capacity AND priority_score < 0.8 -> hold (skip)
#
# Both cases are at capacity (2/2) with an unrounded priority that straddles the
# 0.8 literal (0.7992 vs 0.8008). Both round to 0.80 in the output, so the gate
# must be reading the UNROUNDED priority: the only difference is the < 0.8 test.
# Both priorities are >= the 0.7 bid_now threshold, so the hold is caused purely
# by the capacity gate, not by a low score.
# ---------------------------------------------------------------------------

_CAPACITY_BASE = dict(
    probability_score=1.0,
    matched_score=1.0,
    deadline_hours_remaining=6,
    budget_estimate=100.0,
    recommended_amount=100.0,
    expected_margin_score=1.0,
    execution_complexity_score=0.0,
    current_active_bids=2,
    max_active_bids=2,
)


def test_capacity_hold_below_threshold_holds():
    """At capacity with priority just under 0.8, the opportunity is held (skip)."""
    decision = _evaluate(competitiveness_score=0.74, **_CAPACITY_BASE)
    assert decision["priority_score"] == 0.8  # rounded output
    assert decision["action"] == "skip"
    assert decision["pursue_bid"] is False


def test_capacity_hold_at_threshold_releases_to_bid():
    """At capacity with priority just at/over 0.8, the hold releases to bid_now."""
    decision = _evaluate(competitiveness_score=0.76, **_CAPACITY_BASE)
    assert decision["priority_score"] == 0.8  # same rounded output as the hold case
    assert decision["action"] == "bid_now"
    assert decision["pursue_bid"] is True


def test_capacity_hold_requires_at_capacity():
    """Below capacity, a sub-0.8 priority is NOT force-held; it bids on its score."""
    # Same score profile as the hold case but with headroom (1/2) -> no hold; the
    # priority clears the 0.7 bid_now threshold so it bids.
    decision = _evaluate(
        competitiveness_score=0.74,
        **{**_CAPACITY_BASE, "current_active_bids": 1},
    )
    assert decision["action"] == "bid_now"


# ---------------------------------------------------------------------------
# (c) surrounding review/skip ladder (context — thresholds are injected, not in
# this PR's scope, but pinned so the extraction cannot disturb the ladder).
# ---------------------------------------------------------------------------


def test_mid_priority_lands_in_review():
    """A mid priority (>= review 0.45, < bid_now 0.7) with no force-bid -> review."""
    decision = _evaluate(probability_score=0.6, matched_score=0.6)
    assert decision["priority_score"] == 0.54
    assert decision["action"] == "review"


def test_low_priority_skips():
    """A low priority (< review 0.45), not at capacity -> skip."""
    decision = _evaluate(
        probability_score=0.2,
        matched_score=0.2,
        competitiveness_score=0.0,
        execution_complexity_score=0.0,
    )
    assert decision["priority_score"] == 0.24
    assert decision["action"] == "skip"
    assert decision["pursue_bid"] is False


# ---------------------------------------------------------------------------
# reasons byte-equal snapshots — freeze the honesty-spec wording and the exact
# gate reason sentences for one representative case per branch.
# ---------------------------------------------------------------------------


def test_reasoning_snapshot_force_bid():
    decision = _evaluate(probability_score=0.80, matched_score=0.70, **_FORCE_BID_BASE)
    assert decision["reasoning"] == (
        "가격 적합도(추정) 점수 0.80를 반영했습니다. "
        "공고 적합도 점수 0.70를 반영했습니다. "
        "마감 임박도 점수 0.30를 반영했습니다. "
        "시장 경쟁력 점수 0.00를 반영했습니다. "
        "예상 수익성 점수 0.50를 반영했습니다. "
        "우선순위가 높아 바로 투찰 검토 대상으로 올렸습니다."
    )


def test_reasoning_snapshot_capacity_hold():
    decision = _evaluate(competitiveness_score=0.74, **_CAPACITY_BASE)
    assert decision["reasoning"] == (
        "가격 적합도(추정) 점수 1.00를 반영했습니다. "
        "공고 적합도 점수 1.00를 반영했습니다. "
        "마감 임박도 점수 1.00를 반영했습니다. "
        "시장 경쟁력 점수 0.74를 반영했습니다. "
        "기초금액 대비 추천가 유지율 1.00를 반영했습니다. "
        "예상 수익성 점수 1.00를 반영했습니다. "
        "현재 진행 중인 입찰/업무 부담으로 0.18 감점을 적용했습니다. "
        "현재 동시 관리 중인 입찰 수가 한도에 가까워 보수적으로 보류했습니다."
    )


def test_reasoning_snapshot_review():
    decision = _evaluate(probability_score=0.6, matched_score=0.6)
    assert decision["reasoning"] == (
        "가격 적합도(추정) 점수 0.60를 반영했습니다. "
        "공고 적합도 점수 0.60를 반영했습니다. "
        "마감 임박도 점수 0.30를 반영했습니다. "
        "시장 경쟁력 점수 0.50를 반영했습니다. "
        "예상 수익성 점수 0.50를 반영했습니다. "
        "즉시 투찰까지는 아니지만 추가 검토 가치가 있어 검토 대기열에 올렸습니다."
    )


def test_reasoning_snapshot_skip():
    decision = _evaluate(
        probability_score=0.2,
        matched_score=0.2,
        competitiveness_score=0.0,
        execution_complexity_score=0.0,
    )
    assert decision["reasoning"] == (
        "가격 적합도(추정) 점수 0.20를 반영했습니다. "
        "공고 적합도 점수 0.20를 반영했습니다. "
        "마감 임박도 점수 0.30를 반영했습니다. "
        "시장 경쟁력 점수 0.00를 반영했습니다. "
        "예상 수익성 점수 0.50를 반영했습니다. "
        "현재 기준에서는 우선순위가 낮아 보류하는 편이 유리합니다."
    )
