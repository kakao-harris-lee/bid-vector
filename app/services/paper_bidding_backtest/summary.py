"""Run-level summary rollup over candidate and settlement items."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from app.schemas.paper_bidding_items import (
    PaperBiddingCandidateItem,
    PaperBiddingRunSummary,
    PaperBiddingSettlementItem,
    WouldHaveWonFinal,
)
from app.services.paper_bidding_backtest.base import (
    RATE_ERROR_BUCKETS,
    _PaperBiddingBase,
)

# 낙찰하한 게이트 판정 -> 요약 카운트 필드명. 종전에는 판정마다 같은 모양의 제너레이터
# 를 하나씩 복붙했다(4개). 표로 두면 새 판정 추가가 한 줄이다(§4.5-2).
_FINAL_VERDICT_COUNT_FIELDS: tuple[tuple[WouldHaveWonFinal, str], ...] = (
    ("eligible_favorable", "would_have_won_final_eligible_favorable_count"),
    ("eligible_but_outbid", "would_have_won_final_eligible_but_outbid_count"),
    ("disqualified", "would_have_won_final_disqualified_count"),
    ("unknown", "would_have_won_final_unknown_count"),
)
# 가격 근접 기반 추정 낙찰로 세는 판정(실제 낙찰이 아니다 — 정직 명세 §2).
_PLAUSIBLE_PRICE_ONLY_VERDICT = "plausible"


class _SummaryMixin(_PaperBiddingBase):
    """Aggregate candidate/settlement items into a run summary."""

    def _build_summary(
        self,
        *,
        candidate_items: Sequence[PaperBiddingCandidateItem],
        settlement_items: Sequence[PaperBiddingSettlementItem],
        skipped_by_strategy: int,
        action_counts: Counter[str],
        skipped_invalid: int = 0,
    ) -> PaperBiddingRunSummary:
        rate_errors = [
            float(item.absolute_bid_rate_error) for item in settlement_items
        ]
        amount_errors = [float(item.absolute_error_rate) for item in settlement_items]
        final_verdicts = Counter(item.would_have_won_final for item in settlement_items)
        return PaperBiddingRunSummary(
            candidate_count=len(candidate_items),
            paper_bid_count=int(
                action_counts.get("bid_now", 0) + action_counts.get("review", 0)
            ),
            review_count=int(action_counts.get("review", 0)),
            skip_count=int(action_counts.get("skip", 0)),
            skipped_by_strategy_count=skipped_by_strategy,
            skipped_invalid_count=skipped_invalid,
            settled_count=len(settlement_items),
            action_counts=dict(action_counts),
            average_absolute_bid_rate_error=self._average(rate_errors),
            average_absolute_amount_error_rate=self._average(amount_errors),
            price_close_count=sum(1 for item in settlement_items if item.price_close),
            price_competitive_count=sum(
                1 for item in settlement_items if item.price_competitive
            ),
            would_have_won_price_only_count=sum(
                1
                for item in settlement_items
                if item.would_have_won_price_only == _PLAUSIBLE_PRICE_ONLY_VERDICT
            ),
            **self._rate_error_bucket_counts(rate_errors),
            **{
                field: final_verdicts.get(verdict, 0)
                for verdict, field in _FINAL_VERDICT_COUNT_FIELDS
            },
        )

    @staticmethod
    def _rate_error_bucket_counts(rate_errors: Sequence[float]) -> dict[str, int]:
        """낙찰률 오차 허용폭별 카운트(``RATE_ERROR_BUCKETS`` 선언표 해석)."""
        return {
            field: sum(1 for value in rate_errors if value <= tolerance)
            for field, tolerance in RATE_ERROR_BUCKETS
        }
