"""Prediction observability and model-performance reporting."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import ensure_utc, utc_now
from app.domain.aggregates import average, error_rate, rate
from app.models.models import PricePrediction, TenderResult, User
from app.services.query_predicates import settled_with_amount


class PredictionReportingService:
    """Aggregate persisted prediction metadata for operator reporting."""

    def build_observability(
        self,
        db: Session,
        *,
        days: int = 90,
        trend_bucket_days: int = 14,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Summarize predictor selection, guardrails, fallback, and result accuracy."""
        if operator is None:
            operator = ensure_operator_account(db)
        now = utc_now()
        date_from = now - timedelta(days=days)
        resolved_trend_bucket_days = max(1, int(trend_bucket_days or 14))

        predictions = (
            db.query(PricePrediction)
            .filter(
                PricePrediction.user_id == operator.id,
                PricePrediction.created_at >= date_from,
            )
            .order_by(PricePrediction.created_at.desc(), PricePrediction.id.desc())
            .all()
        )
        tender_results_by_project = self._load_latest_tender_results(
            db,
            project_ids=[int(prediction.project_id) for prediction in predictions if prediction.project_id is not None],
        )

        total_count = len(predictions)
        fallback_count = 0
        guardrail_count = 0
        error_rates: list[float] = []
        fallback_reason_breakdown: dict[str, int] = {}
        guardrail_reason_breakdown: dict[str, int] = {}
        predictor_groups: dict[str, dict[str, Any]] = {}
        pricing_mode_groups: dict[str, dict[str, Any]] = {}
        trend_groups: dict[int, dict[str, Any]] = {}

        for prediction in predictions:
            predictor_name = self._clean_text(prediction.predictor_name, fallback="historical_statistical")
            predictor_family = self._clean_text(prediction.predictor_family, fallback="statistical")
            pricing_mode = self._clean_text(prediction.pricing_mode, fallback="heuristic")
            fallback_reason = self._clean_text(prediction.fallback_reason, fallback="")
            guardrail_reason = self._clean_text(prediction.guardrail_reason, fallback="")
            has_fallback = bool(fallback_reason)
            has_guardrail = bool(prediction.guardrail_applied)

            if has_fallback:
                fallback_count += 1
                fallback_reason_breakdown[fallback_reason] = fallback_reason_breakdown.get(fallback_reason, 0) + 1
            if has_guardrail:
                guardrail_count += 1
                if guardrail_reason:
                    guardrail_reason_breakdown[guardrail_reason] = guardrail_reason_breakdown.get(guardrail_reason, 0) + 1

            error_rate = self._resolve_prediction_error_rate(prediction, tender_results_by_project)
            if error_rate is not None:
                error_rates.append(error_rate)
            trend_group = self._trend_group_for_prediction(
                trend_groups,
                prediction=prediction,
                date_from=date_from,
                now=now,
                bucket_days=resolved_trend_bucket_days,
            )
            self._accumulate_trend_metrics(
                trend_group,
                prediction=prediction,
                error_rate=error_rate,
                has_fallback=has_fallback,
                has_guardrail=has_guardrail,
            )

            predictor_group = predictor_groups.setdefault(
                predictor_name,
                self._empty_predictor_group(predictor_name=predictor_name, predictor_family=predictor_family),
            )
            self._accumulate_group_metrics(
                predictor_group,
                prediction=prediction,
                error_rate=error_rate,
                has_fallback=has_fallback,
                has_guardrail=has_guardrail,
            )

            pricing_mode_group = pricing_mode_groups.setdefault(
                pricing_mode,
                {"pricing_mode": pricing_mode, "prediction_count": 0, "fallback_count": 0, "guardrail_count": 0},
            )
            pricing_mode_group["prediction_count"] += 1
            pricing_mode_group["fallback_count"] += int(has_fallback)
            pricing_mode_group["guardrail_count"] += int(has_guardrail)

        return {
            "operator_id": operator.id,
            "period_days": days,
            "prediction_count": total_count,
            "fallback_count": fallback_count,
            "fallback_rate": self._rate(fallback_count, total_count),
            "guardrail_count": guardrail_count,
            "guardrail_rate": self._rate(guardrail_count, total_count),
            "accuracy_sample_count": len(error_rates),
            "average_absolute_error_rate": self._average(error_rates),
            "within_1_percent_count": sum(1 for value in error_rates if value <= 0.01),
            "within_3_percent_count": sum(1 for value in error_rates if value <= 0.03),
            "predictor_breakdown": [
                self._serialize_predictor_group(group, total_count=total_count)
                for group in sorted(predictor_groups.values(), key=lambda item: (-item["prediction_count"], item["predictor_name"]))
            ],
            "pricing_mode_breakdown": [
                self._serialize_pricing_mode_group(group, total_count=total_count)
                for group in sorted(pricing_mode_groups.values(), key=lambda item: (-item["prediction_count"], item["pricing_mode"]))
            ],
            "performance_trend": [
                self._serialize_trend_group(group)
                for group in sorted(trend_groups.values(), key=lambda item: item["bucket_start"])
            ],
            "fallback_reason_breakdown": dict(sorted(fallback_reason_breakdown.items(), key=lambda item: (-item[1], item[0]))),
            "guardrail_reason_breakdown": dict(sorted(guardrail_reason_breakdown.items(), key=lambda item: (-item[1], item[0]))),
        }

    def _load_latest_tender_results(self, db: Session, *, project_ids: list[int]) -> dict[int, TenderResult]:
        """Return the latest usable tender result per project."""
        if not project_ids:
            return {}

        tender_results = (
            db.query(TenderResult)
            .filter(
                TenderResult.project_id.in_(project_ids),
                settled_with_amount(),
            )
            .order_by(TenderResult.project_id.asc(), TenderResult.announced_at.desc(), TenderResult.created_at.desc(), TenderResult.id.desc())
            .all()
        )
        latest_by_project: dict[int, TenderResult] = {}
        for tender_result in tender_results:
            if tender_result.project_id is None:
                continue
            latest_by_project.setdefault(int(tender_result.project_id), tender_result)
        return latest_by_project

    def _resolve_prediction_error_rate(
        self,
        prediction: PricePrediction,
        tender_results_by_project: dict[int, TenderResult],
    ) -> float | None:
        """Return absolute prediction error rate when actual result data is available."""
        if prediction.project_id is None:
            return None
        tender_result = tender_results_by_project.get(int(prediction.project_id))
        if tender_result is None or float(tender_result.winning_amount or 0.0) <= 0:
            return None
        return error_rate(
            float(prediction.predicted_price or 0.0),
            float(tender_result.winning_amount),
        )

    def _empty_predictor_group(self, *, predictor_name: str, predictor_family: str) -> dict[str, Any]:
        """Return accumulator state for one predictor."""
        return {
            "predictor_name": predictor_name,
            "predictor_family": predictor_family,
            "prediction_count": 0,
            "fallback_count": 0,
            "guardrail_count": 0,
            "accuracy_error_rates": [],
            "confidence_scores": [],
            "training_window_sizes": [],
            "predicted_bid_rates": [],
        }

    def _accumulate_group_metrics(
        self,
        group: dict[str, Any],
        *,
        prediction: PricePrediction,
        error_rate: float | None,
        has_fallback: bool,
        has_guardrail: bool,
    ) -> None:
        """Accumulate metric inputs for one prediction row."""
        group["prediction_count"] += 1
        group["fallback_count"] += int(has_fallback)
        group["guardrail_count"] += int(has_guardrail)
        if error_rate is not None:
            group["accuracy_error_rates"].append(error_rate)
        if prediction.confidence_score is not None:
            group["confidence_scores"].append(float(prediction.confidence_score))
        if prediction.training_window_size is not None:
            group["training_window_sizes"].append(float(prediction.training_window_size))
        if prediction.predicted_bid_rate is not None and float(prediction.predicted_bid_rate or 0.0) > 0:
            group["predicted_bid_rates"].append(float(prediction.predicted_bid_rate))

    def _serialize_predictor_group(self, group: dict[str, Any], *, total_count: int) -> dict[str, Any]:
        """Convert one predictor accumulator into API-ready metrics."""
        prediction_count = int(group["prediction_count"])
        error_rates = group["accuracy_error_rates"]
        return {
            "predictor_name": group["predictor_name"],
            "predictor_family": group["predictor_family"],
            "prediction_count": prediction_count,
            "selection_rate": self._rate(prediction_count, total_count),
            "fallback_count": int(group["fallback_count"]),
            "fallback_rate": self._rate(int(group["fallback_count"]), prediction_count),
            "guardrail_count": int(group["guardrail_count"]),
            "guardrail_rate": self._rate(int(group["guardrail_count"]), prediction_count),
            "accuracy_sample_count": len(error_rates),
            "average_absolute_error_rate": self._average(error_rates),
            "within_1_percent_count": sum(1 for value in error_rates if value <= 0.01),
            "within_3_percent_count": sum(1 for value in error_rates if value <= 0.03),
            "average_confidence_score": self._average(group["confidence_scores"]),
            "average_training_window_size": self._average(group["training_window_sizes"]),
            "average_predicted_bid_rate": self._average(group["predicted_bid_rates"]),
        }

    def _serialize_pricing_mode_group(self, group: dict[str, Any], *, total_count: int) -> dict[str, Any]:
        """Convert one pricing-mode accumulator into API-ready metrics."""
        prediction_count = int(group["prediction_count"])
        return {
            "pricing_mode": group["pricing_mode"],
            "prediction_count": prediction_count,
            "selection_rate": self._rate(prediction_count, total_count),
            "fallback_count": int(group["fallback_count"]),
            "fallback_rate": self._rate(int(group["fallback_count"]), prediction_count),
            "guardrail_count": int(group["guardrail_count"]),
            "guardrail_rate": self._rate(int(group["guardrail_count"]), prediction_count),
        }

    def _trend_group_for_prediction(
        self,
        trend_groups: dict[int, dict[str, Any]],
        *,
        prediction: PricePrediction,
        date_from: datetime,
        now: datetime,
        bucket_days: int,
    ) -> dict[str, Any]:
        """Return the trend bucket accumulator for one prediction timestamp."""
        created_at = ensure_utc(prediction.created_at)
        elapsed_seconds = max(0.0, (created_at - date_from).total_seconds())
        bucket_seconds = max(1, bucket_days) * 86400
        bucket_index = int(elapsed_seconds // bucket_seconds)
        bucket_start = date_from + timedelta(days=bucket_index * bucket_days)
        bucket_end = min(now, bucket_start + timedelta(days=bucket_days))
        return trend_groups.setdefault(
            bucket_index,
            {
                "bucket_start": bucket_start,
                "bucket_end": bucket_end,
                "prediction_count": 0,
                "fallback_count": 0,
                "guardrail_count": 0,
                "accuracy_error_rates": [],
                "backtest_error_rates": [],
                "backtest_sample_counts": [],
            },
        )

    def _accumulate_trend_metrics(
        self,
        group: dict[str, Any],
        *,
        prediction: PricePrediction,
        error_rate: float | None,
        has_fallback: bool,
        has_guardrail: bool,
    ) -> None:
        """Accumulate one prediction into a time-bucket trend group."""
        group["prediction_count"] += 1
        group["fallback_count"] += int(has_fallback)
        group["guardrail_count"] += int(has_guardrail)
        if error_rate is not None:
            group["accuracy_error_rates"].append(error_rate)
        if prediction.backtest_average_absolute_error_rate is not None:
            group["backtest_error_rates"].append(float(prediction.backtest_average_absolute_error_rate))
        if prediction.backtest_sample_count is not None and int(prediction.backtest_sample_count or 0) > 0:
            group["backtest_sample_counts"].append(float(prediction.backtest_sample_count))

    def _serialize_trend_group(self, group: dict[str, Any]) -> dict[str, Any]:
        """Convert one time bucket into API-ready trend metrics."""
        prediction_count = int(group["prediction_count"])
        error_rates = group["accuracy_error_rates"]
        return {
            "bucket_start": group["bucket_start"],
            "bucket_end": group["bucket_end"],
            "prediction_count": prediction_count,
            "fallback_rate": self._rate(int(group["fallback_count"]), prediction_count),
            "guardrail_rate": self._rate(int(group["guardrail_count"]), prediction_count),
            "accuracy_sample_count": len(error_rates),
            "average_absolute_error_rate": self._average(error_rates),
            "backtest_sample_count": int(sum(group["backtest_sample_counts"])),
            "average_backtest_error_rate": self._average(group["backtest_error_rates"]),
        }

    def _clean_text(self, value: Any, *, fallback: str) -> str:
        """Normalize optional string-ish values for grouping."""
        cleaned_value = str(value or "").strip()
        return cleaned_value or fallback

    def _average(self, values: list[float]) -> float | None:
        """Return a rounded average while preserving empty sets."""
        return average(values, digits=4)

    def _rate(self, numerator: int, denominator: int) -> float:
        """Return a stable ratio rounded for dashboard display."""
        return rate(numerator, denominator, digits=4)
