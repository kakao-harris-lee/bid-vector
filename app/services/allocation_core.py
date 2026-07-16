"""Pure decision core for the single-user bid pursuit service (§4.7.4).

``BidDecisionService.evaluate_opportunity`` used to interleave three concerns:
resolving persisted strategy thresholds (DB I/O), computing the weighted scores,
and running the action-gate ladder while assembling the human-facing reasons.

This module isolates the last concern into a pure :func:`decide` that takes the
already-computed :class:`DecisionSignals` plus the resolved
:class:`AllocationThresholds` and returns the ``action`` and ``reasons`` verbatim.
The gate literals (capacity-hold / force-bid) are read once at the boundary via
:meth:`AllocationThresholds.from_settings`, so the ladder here reads no settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.operator_strategy_tuning import (
    DEFAULT_AUTO_WORKLOAD_PENALTY_MULTIPLIER,
)


@dataclass(frozen=True)
class AllocationThresholds:
    """Boundary snapshot of the gate thresholds the decision ladder reads.

    ``bid_now_threshold`` / ``review_threshold`` come from the persisted operator
    strategy (already resolved by the caller); the three gate literals come from
    settings and are collected here so :func:`decide` stays I/O-free.
    """

    bid_now_threshold: float
    review_threshold: float
    capacity_hold_priority_threshold: float
    force_bid_probability_threshold: float
    force_bid_matched_threshold: float

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        bid_now_threshold: float,
        review_threshold: float,
    ) -> "AllocationThresholds":
        return cls(
            bid_now_threshold=bid_now_threshold,
            review_threshold=review_threshold,
            capacity_hold_priority_threshold=settings.ALLOCATION_CAPACITY_HOLD_PRIORITY_THRESHOLD,
            force_bid_probability_threshold=settings.ALLOCATION_FORCE_BID_PROBABILITY_THRESHOLD,
            force_bid_matched_threshold=settings.ALLOCATION_FORCE_BID_MATCHED_THRESHOLD,
        )


@dataclass(frozen=True)
class DecisionSignals:
    """The computed scores/context the reason assembly and gate ladder consume."""

    probability_score: float
    matched_score: float
    urgency_score: float
    competitiveness_score: float
    budget_capture_score: float
    expected_margin_score: float
    execution_complexity_score: float
    budget_estimate: float | None
    workload_source: str
    current_workload_score: float
    auto_workload_penalty_multiplier: float
    load_penalty: float
    complexity_penalty: float
    priority_score: float
    current_active_bids: int
    max_active_bids: int


@dataclass(frozen=True)
class Decision:
    action: str
    reasons: list[str]


def decide(signals: DecisionSignals, thresholds: AllocationThresholds) -> Decision:
    """Assemble the decision reasons and pick the action via the gate ladder.

    Moved verbatim from ``evaluate_opportunity`` so the reasons stay byte-equal
    (honesty-spec wording) and the ladder keeps its first-match ordering:
    capacity-hold -> force-bid/bid_now -> review -> skip.
    """
    reasons = [
        f"가격 적합도(추정) 점수 {signals.probability_score:.2f}를 반영했습니다.",
        f"공고 적합도 점수 {signals.matched_score:.2f}를 반영했습니다.",
        f"마감 임박도 점수 {signals.urgency_score:.2f}를 반영했습니다.",
        f"시장 경쟁력 점수 {signals.competitiveness_score:.2f}를 반영했습니다.",
    ]

    if signals.budget_estimate and signals.budget_estimate > 0:
        reasons.append(f"예산 대비 추천가 유지율 {signals.budget_capture_score:.2f}를 반영했습니다.")
    reasons.append(f"예상 수익성 점수 {signals.expected_margin_score:.2f}를 반영했습니다.")

    if signals.workload_source == "auto":
        reasons.append(f"현재 진행 중인 입찰 현황을 바탕으로 업무부하 점수 {signals.current_workload_score:.2f}를 자동 산정했습니다.")
        if round(signals.auto_workload_penalty_multiplier, 4) != DEFAULT_AUTO_WORKLOAD_PENALTY_MULTIPLIER:
            reasons.append(
                f"자동 업무부하 감점 배율 {signals.auto_workload_penalty_multiplier:.2f}를 적용했습니다."
            )
    elif signals.current_workload_score > 0:
        reasons.append(f"입력된 업무부하 점수 {signals.current_workload_score:.2f}를 반영했습니다.")

    if signals.load_penalty > 0:
        reasons.append(f"현재 진행 중인 입찰/업무 부담으로 {signals.load_penalty:.2f} 감점을 적용했습니다.")
    if signals.complexity_penalty > 0:
        reasons.append(
            f"실행 복잡도 점수 {signals.execution_complexity_score:.2f}를 반영해 {signals.complexity_penalty:.2f} 추가 감점을 적용했습니다."
        )

    action = "skip"
    if (
        signals.current_active_bids >= signals.max_active_bids
        and signals.priority_score < thresholds.capacity_hold_priority_threshold
    ):
        reasons.append("현재 동시 관리 중인 입찰 수가 한도에 가까워 보수적으로 보류했습니다.")
    elif signals.priority_score >= thresholds.bid_now_threshold or (
        signals.probability_score >= thresholds.force_bid_probability_threshold
        and signals.matched_score >= thresholds.force_bid_matched_threshold
    ):
        action = "bid_now"
        reasons.append("우선순위가 높아 바로 투찰 검토 대상으로 올렸습니다.")
    elif signals.priority_score >= thresholds.review_threshold:
        action = "review"
        reasons.append("즉시 투찰까지는 아니지만 추가 검토 가치가 있어 검토 대기열에 올렸습니다.")
    else:
        reasons.append("현재 기준에서는 우선순위가 낮아 보류하는 편이 유리합니다.")

    return Decision(action=action, reasons=reasons)
