"""Consolidated 추천 vs 실제 낙찰가 정확도 리포트.

This service reuses :class:`PredictionFeedbackService.build_feedback` -- the
single source of the per-settled-result prediction/recommendation-vs-actual
join -- and only post-aggregates its already-computed ``items``. It does NOT
re-implement the prediction<->tender join, and inherits its leakage safety:
``build_feedback`` filters to settled results (winning_amount present), so no
unsettled/future data can enter here.

The metric is a real comparison: error = |추천가 - 실제낙찰가| / 실제낙찰가, over
정산 완료 건만 (단일 운영자/canonical 기준). This is distinct from the
would_have_won / price-only *estimated* win metrics elsewhere in the codebase.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import ensure_utc
from app.models.models import User
from app.services.prediction_feedback import PredictionFeedbackService

_UNCATEGORIZED_LABEL = "(미분류)"

# Upper bounds (inclusive) for the recommendation error-rate distribution bins.
# Assignment rule: the first bin uses ``rate <= upper``; every subsequent bin
# uses ``lower < rate <= upper`` (so a 0.03 error lands in the 1-3% bin, not
# the 3-5% bin). The final bin has no upper bound.
_ERROR_BINS: list[tuple[str, float, float | None]] = [
    ("<1%", 0.0, 0.01),
    ("1-3%", 0.01, 0.03),
    ("3-5%", 0.03, 0.05),
    ("5-10%", 0.05, 0.10),
    ("≥10%", 0.10, None),
]

_NOTES: list[str] = [
    "오차 = |추천가 - 실제낙찰가| / 실제낙찰가 (추천가와 실제 낙찰가의 실측 비교).",
    "정산 완료(실제 낙찰가 보유) 건만 포함하며 미정산 공고는 제외됩니다.",
    "현재 선택된 단일 운영자 기준 집계이며, 운영자 간 합산 비교가 아닙니다.",
    "표본이 적으면 분포·카테고리·추세 통계의 변동이 큽니다.",
    "would_have_won / price-only 추정 낙찰 지표와 달리, 본 리포트는 실측 낙찰가와의 비교입니다.",
]


class AccuracyIntegrationService:
    """Build the consolidated recommendation-vs-actual accuracy report."""

    def build_accuracy_report(
        self,
        db: Session,
        *,
        days: int = 90,
        limit: int = 500,
        trend_bucket_days: int = 7,
        category_limit: int = 12,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Aggregate the prediction-feedback items into report breakdowns.

        Passes the report's own ``limit`` (default 500) to ``build_feedback`` so
        the aggregates cover the full window rather than its default 20.
        """
        feedback = PredictionFeedbackService().build_feedback(
            db, days=days, limit=limit, operator=operator
        )
        items = feedback["items"]
        # ``items`` is already pre-capped at ``limit`` by build_feedback (which
        # stops appending at the limit, newest-first). Track that cap so the
        # summary can honestly flag truncation of older comparisons.
        matched_sample_count = int(feedback["result_count"])
        truncated = matched_sample_count >= limit

        recommendation_error_rates = [
            float(item["recommendation_error_rate"])
            for item in items
            if item.get("recommendation_error_rate") is not None
        ]
        sample_count = len(recommendation_error_rates)

        summary = self._build_summary(
            feedback,
            recommendation_error_rates,
            matched_sample_count=matched_sample_count,
            truncated=truncated,
            limit=limit,
        )
        error_distribution = self._build_error_distribution(
            recommendation_error_rates, sample_count
        )
        per_category = self._build_per_category(items, category_limit=category_limit)
        time_trend = self._build_time_trend(items, trend_bucket_days=trend_bucket_days)

        # items are already <= limit (pre-capped by build_feedback), so no slice.
        notes = list(_NOTES)
        if truncated:
            notes.append(
                f"표본이 상한({limit}건)에 도달해 가장 최근 건만 집계됐습니다 "
                "— 더 긴 기간/상한이 필요할 수 있습니다."
            )

        return {
            "operator_id": int(feedback["operator_id"]),
            "summary": summary,
            "error_distribution": error_distribution,
            "per_category": per_category,
            "time_trend": time_trend,
            "items": items,
            "notes": notes,
        }

    # -- summary ---------------------------------------------------------------

    def _build_summary(
        self,
        feedback: dict[str, Any],
        recommendation_error_rates: list[float],
        *,
        matched_sample_count: int,
        truncated: bool,
        limit: int,
    ) -> dict[str, Any]:
        sample_count = len(recommendation_error_rates)
        # Compute all within_* thresholds uniformly from the local rates so the
        # three are symmetric (build_feedback only exposes 1%/3%, not 5%).
        within_1 = sum(1 for value in recommendation_error_rates if value <= 0.01)
        within_3 = sum(1 for value in recommendation_error_rates if value <= 0.03)
        within_5 = sum(1 for value in recommendation_error_rates if value <= 0.05)

        return {
            "period_days": int(feedback["period_days"]),
            "matched_sample_count": matched_sample_count,
            "truncated": truncated,
            "limit": int(limit),
            "recommendation_sample_count": sample_count,
            "prediction_sample_count": int(feedback.get("prediction_sample_count", 0)),
            "average_recommendation_error_rate": feedback.get(
                "average_recommendation_error_rate"
            ),
            "average_prediction_error_rate": feedback.get(
                "average_prediction_error_rate"
            ),
            "recommendation_better_than_prediction_count": int(
                feedback.get("recommendation_better_than_prediction_count", 0)
            ),
            "within_1pct_count": int(within_1),
            "within_3pct_count": int(within_3),
            "within_5pct_count": int(within_5),
            "within_1pct_rate": self._rate(int(within_1), sample_count),
            "within_3pct_rate": self._rate(int(within_3), sample_count),
            "within_5pct_rate": self._rate(int(within_5), sample_count),
        }

    # -- error distribution ----------------------------------------------------

    def _build_error_distribution(
        self,
        recommendation_error_rates: list[float],
        sample_count: int,
    ) -> list[dict[str, Any]]:
        bins: list[dict[str, Any]] = []
        for bin_label, lower, upper in _ERROR_BINS:
            count = sum(
                1
                for rate in recommendation_error_rates
                if self._in_bin(rate, lower, upper)
            )
            bins.append(
                {
                    "bin_label": bin_label,
                    "lower": lower,
                    "upper": upper,
                    "count": count,
                    "rate": self._rate(count, sample_count),
                }
            )
        return bins

    @staticmethod
    def _in_bin(rate: float, lower: float, upper: float | None) -> bool:
        """Assign ``rate`` to a bin via ``lower < rate <= upper`` (first bin: ``<= upper``)."""
        if upper is None:
            return rate > lower
        if lower <= 0.0:
            return rate <= upper
        return lower < rate <= upper

    # -- per category ----------------------------------------------------------

    def _build_per_category(
        self,
        items: list[dict[str, Any]],
        *,
        category_limit: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[float]] = {}
        for item in items:
            if item.get("recommendation_error_rate") is None:
                continue
            label = item.get("category") or _UNCATEGORIZED_LABEL
            grouped.setdefault(label, []).append(float(item["recommendation_error_rate"]))

        rows: list[dict[str, Any]] = []
        for label, rates in grouped.items():
            sample_count = len(rates)
            within_3 = sum(1 for rate in rates if rate <= 0.03)
            rows.append(
                {
                    "category": label,
                    "sample_count": sample_count,
                    "average_recommendation_error_rate": self._average(rates),
                    "within_3pct_rate": self._rate(within_3, sample_count),
                }
            )

        rows.sort(key=lambda row: (-row["sample_count"], row["category"]))
        return rows[:category_limit]

    # -- time trend ------------------------------------------------------------

    def _build_time_trend(
        self,
        items: list[dict[str, Any]],
        *,
        trend_bucket_days: int,
    ) -> list[dict[str, Any]]:
        bucket_span = timedelta(days=max(int(trend_bucket_days), 1))
        dated: list[tuple[datetime, float]] = []
        for item in items:
            if item.get("recommendation_error_rate") is None:
                continue
            announced_at = item.get("announced_at")
            if announced_at is None:
                continue
            dated.append(
                (ensure_utc(announced_at), float(item["recommendation_error_rate"]))
            )

        if not dated:
            return []

        earliest = min(moment for moment, _ in dated)
        buckets: dict[int, list[float]] = {}
        for moment, rate in dated:
            index = int((moment - earliest) // bucket_span)
            buckets.setdefault(index, []).append(rate)

        trend: list[dict[str, Any]] = []
        for index in sorted(buckets):
            period_start = earliest + (bucket_span * index)
            period_end = period_start + bucket_span
            rates = buckets[index]
            trend.append(
                {
                    "period_start": period_start,
                    "period_end": period_end,
                    "sample_count": len(rates),
                    "average_recommendation_error_rate": self._average(rates),
                }
            )
        return trend

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _rate(count: int, total: int) -> float | None:
        if total <= 0:
            return None
        return round(count / total, 4)

    @staticmethod
    def _average(values: list[float]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 4)
