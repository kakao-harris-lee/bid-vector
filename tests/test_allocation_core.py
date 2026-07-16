"""Direct unit tests for the pure allocation decision core (PR-16, §4.7.4).

These exercise ``allocation_core.decide`` standalone with explicit thresholds —
no settings, no DB — proving the gate ladder and reason assembly were extracted
as an I/O-free core. The end-to-end byte-equal reason snapshots live in
``tests/test_allocation_action_gates.py``.
"""

from __future__ import annotations

import pytest

from app.services import allocation_core as ac


# Fixed thresholds mirroring the service/settings defaults so the gate literals
# are the only branch drivers.
_THRESHOLDS = ac.AllocationThresholds(
    bid_now_threshold=0.7,
    review_threshold=0.45,
    capacity_hold_priority_threshold=0.8,
    force_bid_probability_threshold=0.8,
    force_bid_matched_threshold=0.7,
)


def _signals(**overrides) -> ac.DecisionSignals:
    defaults = dict(
        probability_score=0.5,
        matched_score=0.5,
        urgency_score=0.3,
        competitiveness_score=0.5,
        budget_capture_score=0.5,
        expected_margin_score=0.5,
        execution_complexity_score=0.35,
        budget_estimate=None,
        workload_source="provided",
        current_workload_score=0.0,
        auto_workload_penalty_multiplier=1.0,
        load_penalty=0.0,
        complexity_penalty=0.0,
        priority_score=0.5,
        current_active_bids=0,
        max_active_bids=3,
    )
    defaults.update(overrides)
    return ac.DecisionSignals(**defaults)


@pytest.mark.parametrize(
    "priority_score, probability_score, matched_score, expected_action",
    [
        (0.9, 0.5, 0.5, "bid_now"),   # priority clears bid_now threshold
        (0.5, 0.8, 0.7, "bid_now"),   # force-bid gate fires despite mid priority
        (0.5, 0.79, 0.7, "review"),   # probability below force literal -> review band
        (0.24, 0.2, 0.2, "skip"),     # below review threshold -> skip
    ],
)
def test_decide_gate_ladder(priority_score, probability_score, matched_score, expected_action):
    decision = ac.decide(
        _signals(
            priority_score=priority_score,
            probability_score=probability_score,
            matched_score=matched_score,
        ),
        _THRESHOLDS,
    )
    assert decision.action == expected_action


def test_decide_capacity_hold_overrides_high_priority():
    """At capacity with priority under the hold literal, the action holds at skip."""
    decision = ac.decide(
        _signals(priority_score=0.79, current_active_bids=2, max_active_bids=2),
        _THRESHOLDS,
    )
    assert decision.action == "skip"
    assert decision.reasons[-1] == "현재 동시 관리 중인 입찰 수가 한도에 가까워 보수적으로 보류했습니다."


def test_decide_reasons_include_conditional_segments():
    """budget/auto-workload/penalty reasons appear only when their inputs warrant."""
    decision = ac.decide(
        _signals(
            budget_estimate=100.0,
            budget_capture_score=1.0,
            workload_source="auto",
            current_workload_score=0.4,
            auto_workload_penalty_multiplier=1.5,
            load_penalty=0.18,
            complexity_penalty=0.05,
            execution_complexity_score=0.83,
            priority_score=0.5,
        ),
        _THRESHOLDS,
    )
    joined = " ".join(decision.reasons)
    assert "예산 대비 추천가 유지율 1.00를 반영했습니다." in joined
    assert "업무부하 점수 0.40를 자동 산정했습니다." in joined
    assert "자동 업무부하 감점 배율 1.50를 적용했습니다." in joined
    assert "0.18 감점을 적용했습니다." in joined
    assert "0.05 추가 감점을 적용했습니다." in joined
