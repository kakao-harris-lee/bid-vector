"""Run-level summary rollup over candidate and settlement items."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.services.paper_bidding_backtest.base import _PaperBiddingBase


class _SummaryMixin(_PaperBiddingBase):
    """Aggregate candidate/settlement items into a run summary dict."""

    def _build_summary(
        self,
        *,
        candidate_items: list[dict[str, Any]],
        settlement_items: list[dict[str, Any]],
        skipped_by_strategy: int,
        action_counts: Counter[str],
        skipped_invalid: int = 0,
    ) -> dict[str, Any]:
        rate_errors = [
            float(item["absolute_bid_rate_error"]) for item in settlement_items
        ]
        amount_errors = [
            float(item["absolute_error_rate"]) for item in settlement_items
        ]
        return {
            "candidate_count": len(candidate_items),
            "paper_bid_count": int(
                action_counts.get("bid_now", 0) + action_counts.get("review", 0)
            ),
            "review_count": int(action_counts.get("review", 0)),
            "skip_count": int(action_counts.get("skip", 0)),
            "skipped_by_strategy_count": skipped_by_strategy,
            "skipped_invalid_count": skipped_invalid,
            "settled_count": len(settlement_items),
            "action_counts": dict(action_counts),
            "average_absolute_bid_rate_error": self._average(rate_errors),
            "average_absolute_amount_error_rate": self._average(amount_errors),
            "within_0_1pct_count": sum(1 for value in rate_errors if value <= 0.001),
            "within_0_3pct_count": sum(1 for value in rate_errors if value <= 0.003),
            "within_1pct_count": sum(1 for value in rate_errors if value <= 0.01),
            "price_close_count": sum(
                1 for item in settlement_items if item["price_close"]
            ),
            "price_competitive_count": sum(
                1 for item in settlement_items if item["price_competitive"]
            ),
            "would_have_won_price_only_count": sum(
                1
                for item in settlement_items
                if item["would_have_won_price_only"] == "plausible"
            ),
            "would_have_won_final_eligible_favorable_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "eligible_favorable"
            ),
            "would_have_won_final_eligible_but_outbid_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "eligible_but_outbid"
            ),
            "would_have_won_final_disqualified_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "disqualified"
            ),
            "would_have_won_final_unknown_count": sum(
                1
                for item in settlement_items
                if item.get("would_have_won_final") == "unknown"
            ),
        }
