"""Declarative settlement-status derivation for paper-bidding overviews.

Extracted from the backtest router (CLAUDE.md §4.5.5 — keep routers thin) so the
settlement-status cascade lives as a first-match predicate table rather than an
open-coded ``if/elif`` chain. Given the settled / ready / waiting / before-deadline
counts (and their candidate timestamps) computed for a paper-bid run,
``derive_settlement_status`` walks the ordered rules and returns the first match —
byte-identical (status/label/detail/next_confirmable_at) to the original chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable


@dataclass(frozen=True)
class SettlementStatusContext:
    """Counts and candidate timestamps consumed by the settlement-status rules."""

    paper_bid_count: int
    settled_count: int
    unsettled_count: int
    ready_to_settle_count: int
    waiting_result_count: int
    before_deadline_count: int
    latest_settled_at: datetime | None
    next_ready_result_at: datetime | None
    oldest_waiting_deadline_at: datetime | None
    next_deadline_at: datetime | None


@dataclass(frozen=True)
class SettlementStatus:
    """Resolved settlement-status view for one paper-bid run."""

    status_key: str
    label: str
    detail: str
    next_confirmable_at: datetime | None


@dataclass(frozen=True)
class _SettlementStatusRule:
    """One first-match settlement rule: a predicate plus its declarative view."""

    predicate: Callable[[SettlementStatusContext], bool]
    status_key: str
    label: str
    detail_builder: Callable[[SettlementStatusContext], str]
    next_confirmable: Callable[[SettlementStatusContext], datetime | None]


# Ordered, first-match settlement machine. The rungs map 1:1 (in order) to the
# original router if/elif cascade; the final always-true rung is the "마감 미정"
# fallback. Labels and detail templates are carried verbatim.
_SETTLEMENT_STATUS_RULES: tuple[_SettlementStatusRule, ...] = (
    _SettlementStatusRule(
        predicate=lambda c: c.paper_bid_count == 0,
        status_key="no_paper_bids",
        label="검증 없음",
        detail_builder=lambda c: "페이퍼 투찰 항목이 없습니다.",
        next_confirmable=lambda c: None,
    ),
    _SettlementStatusRule(
        predicate=lambda c: c.unsettled_count == 0,
        status_key="settled",
        label="정산 완료",
        detail_builder=lambda c: f"{c.settled_count}건 모두 최종 결과로 정산되었습니다.",
        next_confirmable=lambda c: c.latest_settled_at,
    ),
    _SettlementStatusRule(
        predicate=lambda c: c.ready_to_settle_count > 0,
        status_key="ready_to_settle",
        label="정산 가능",
        detail_builder=lambda c: (
            f"{c.ready_to_settle_count}건은 최종 결과가 입수되어 승패 판정이 가능합니다."
        ),
        next_confirmable=lambda c: c.next_ready_result_at,
    ),
    _SettlementStatusRule(
        predicate=lambda c: c.waiting_result_count > 0,
        status_key="waiting_result",
        label="결과 대기",
        detail_builder=lambda c: (
            f"마감이 지난 {c.waiting_result_count}건은 최종 결과가 수집되면 정산됩니다."
        ),
        next_confirmable=lambda c: c.oldest_waiting_deadline_at,
    ),
    _SettlementStatusRule(
        predicate=lambda c: c.before_deadline_count > 0,
        status_key="before_deadline",
        label="마감 전",
        detail_builder=lambda c: (
            f"{c.before_deadline_count}건은 아직 마감 전입니다. 마감 후 결과가 수집되면 승패가 확정됩니다."
        ),
        next_confirmable=lambda c: c.next_deadline_at,
    ),
    _SettlementStatusRule(
        predicate=lambda c: True,
        status_key="deadline_missing",
        label="마감 미정",
        detail_builder=lambda c: "마감일이 없어 정산 예상 시점을 계산할 수 없습니다.",
        next_confirmable=lambda c: None,
    ),
)


def derive_settlement_status(context: SettlementStatusContext) -> SettlementStatus:
    """Return the first-match settlement-status view for a paper-bid run."""
    rule = next(rule for rule in _SETTLEMENT_STATUS_RULES if rule.predicate(context))
    return SettlementStatus(
        status_key=rule.status_key,
        label=rule.label,
        detail=rule.detail_builder(context),
        next_confirmable_at=rule.next_confirmable(context),
    )
