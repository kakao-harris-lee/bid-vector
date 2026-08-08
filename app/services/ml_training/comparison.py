"""Rolling-holdout artifact comparison for the price-predictor training service.

Evaluates the generated ensemble artifact against the historical baseline on a
rolling holdout and picks the best predictor for the comparison report. The
holdout budget/bid-rate resolution (#261 alignment) is delegated to the shared
helpers unchanged.
"""

from __future__ import annotations

from typing import Any

from app.ai.predictors.base import PricePredictionContext, serialize_prediction_result
from app.ai.predictors.ensemble import build_ensemble_prediction_payload, load_ensemble_artifact
from app.ai.predictors.historical import HistoricalStatisticalPredictor
from app.core.config import settings
from app.core.time import utc_now


class ComparisonMixin:
    """Artifact-vs-holdout comparison and per-predictor evaluation."""

    def _build_artifact_comparison_report(
        self,
        *,
        release_tag: str,
        category: str | None,
        agency_name: str | None,
        dataset: dict[str, Any],
        dataset_quality: dict[str, Any],
        ensemble_artifact: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Compare generated artifacts against a rolling holdout and the historical baseline."""
        series = self._sort_dataset_series(dataset.get("series") if isinstance(dataset.get("series"), list) else [])
        min_training_size = max(1, int(settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES or 1))
        configured_holdout_size = max(
            1,
            int(settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE or 1),
            int(settings.ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT or 1),
        )
        holdout_size = min(configured_holdout_size, max(0, len(series) - min_training_size))

        base_report: dict[str, Any] = {
            "report_version": "1",
            "release_tag": release_tag,
            "created_at": utc_now().isoformat(),
            "status": "insufficient_data",
            "category": category,
            "agency_name": agency_name,
            "dataset_quality_status": dataset_quality.get("status"),
            "holdout_size": holdout_size,
            "min_training_size": min_training_size,
            "sample_count": 0,
            "average_absolute_error_rate": None,
            "best_predictor_key": None,
            "best_predictor_name": None,
            "best_average_absolute_error_rate": None,
            "results": [],
        }
        if holdout_size <= 0:
            base_report["detail"] = "Not enough dataset rows for rolling holdout comparison."
            return base_report

        training_prefix = series[:-holdout_size]
        holdout_records = series[-holdout_size:]
        predictor_results = [
            self._evaluate_historical_predictor(
                training_prefix=training_prefix,
                holdout_records=holdout_records,
                category=category,
                agency_name=agency_name,
                min_training_size=min_training_size,
            ),
            self._evaluate_ensemble_predictor(
                artifact=ensemble_artifact,
                training_prefix=training_prefix,
                holdout_records=holdout_records,
                category=category,
                agency_name=agency_name,
                min_training_size=min_training_size,
            ),
        ]
        eligible_results = [
            result
            for result in predictor_results
            if result["status"] == "completed" and result["average_absolute_error_rate"] is not None
        ]
        best_result = min(
            eligible_results,
            key=lambda result: (
                float(result["average_absolute_error_rate"]),
                -int(result["sample_count"]),
                str(result["predictor_key"]),
            ),
            default=None,
        )
        historical_result = next(
            (result for result in predictor_results if result.get("predictor_key") == "historical"),
            {},
        )
        historical_error = historical_result.get("average_absolute_error_rate")
        for result in predictor_results:
            result_error = result.get("average_absolute_error_rate")
            result["average_error_delta_vs_historical"] = (
                round(float(result_error) - float(historical_error), 6)
                if result_error is not None and historical_error is not None
                else None
            )

        return {
            **base_report,
            "status": "completed" if best_result else "no_eligible_predictor",
            "sample_count": int(best_result.get("sample_count", 0) or 0) if best_result else 0,
            "average_absolute_error_rate": best_result.get("average_absolute_error_rate") if best_result else None,
            "guardrail_rate": best_result.get("guardrail_rate") if best_result else None,
            "fallback_rate": best_result.get("fallback_rate") if best_result else None,
            "best_predictor_key": best_result.get("predictor_key") if best_result else None,
            "best_predictor_name": best_result.get("predictor_name") if best_result else None,
            "best_average_absolute_error_rate": best_result.get("average_absolute_error_rate") if best_result else None,
            "training_sample_count": len(training_prefix),
            "results": predictor_results,
        }

    def _evaluate_historical_predictor(
        self,
        *,
        training_prefix: list[dict[str, Any]],
        holdout_records: list[dict[str, Any]],
        category: str | None,
        agency_name: str | None,
        min_training_size: int,
    ) -> dict[str, Any]:
        """Evaluate the current historical baseline on the training holdout."""
        predictor = HistoricalStatisticalPredictor()
        return self._evaluate_predictor(
            predictor_key="historical",
            predictor_name=predictor.name,
            predictor_family=predictor.family,
            training_prefix=training_prefix,
            holdout_records=holdout_records,
            category=category,
            agency_name=agency_name,
            min_training_size=min_training_size,
            # ``predict`` returns the typed PredictionResult; the shared evaluator
            # below is still dict-based (the artifact predictors feed it raw payload
            # builders), so demote at this adapter seam only.
            predict=lambda context: serialize_prediction_result(predictor.predict(context)),
        )

    def _evaluate_ensemble_predictor(
        self,
        *,
        artifact: dict[str, Any] | None,
        training_prefix: list[dict[str, Any]],
        holdout_records: list[dict[str, Any]],
        category: str | None,
        agency_name: str | None,
        min_training_size: int,
    ) -> dict[str, Any]:
        """Evaluate the generated ensemble artifact on the training holdout."""
        if artifact is None:
            return self._skipped_predictor_result(
                predictor_key="ensemble",
                predictor_name="ensemble_blend",
                predictor_family="ensemble",
                reason="No ensemble artifact was generated.",
            )
        loaded_artifact = load_ensemble_artifact(artifact)
        return self._evaluate_predictor(
            predictor_key="ensemble",
            predictor_name="ensemble_blend",
            predictor_family="ensemble",
            training_prefix=training_prefix,
            holdout_records=holdout_records,
            category=category,
            agency_name=agency_name,
            min_training_size=min_training_size,
            predict=lambda context: build_ensemble_prediction_payload(context, artifact=loaded_artifact),
        )

    def _evaluate_predictor(
        self,
        *,
        predictor_key: str,
        predictor_name: str,
        predictor_family: str,
        training_prefix: list[dict[str, Any]],
        holdout_records: list[dict[str, Any]],
        category: str | None,
        agency_name: str | None,
        min_training_size: int,
        predict,
    ) -> dict[str, Any]:
        """Evaluate one predictor over a rolling holdout."""
        errors: list[float] = []
        skipped_reasons: list[str] = []
        sample_rows: list[dict[str, Any]] = []
        fallback_count = 0
        guardrail_count = 0
        rolling_records = list(training_prefix)

        for holdout_record in holdout_records:
            actual_bid_rate = self._resolve_dataset_bid_rate(holdout_record)
            budget = self._resolve_dataset_budget(holdout_record)
            if len(rolling_records) < min_training_size:
                skipped_reasons.append("not enough training samples before holdout")
                rolling_records.append(holdout_record)
                continue
            if actual_bid_rate is None or budget <= 0:
                skipped_reasons.append("holdout record has no usable bid rate or budget")
                rolling_records.append(holdout_record)
                continue

            context = PricePredictionContext(
                budget=budget,
                category=str(holdout_record.get("category") or category or "other"),
                description=str(holdout_record.get("notice_number") or "training holdout"),
                historical_records=tuple(rolling_records),
                agency_name=str(holdout_record.get("agency_name") or agency_name or "") or None,
            )
            try:
                prediction = predict(context)
            except Exception as exc:
                skipped_reasons.append(f"prediction failed: {exc}")
                rolling_records.append(holdout_record)
                continue

            predicted_bid_rate = self._resolve_prediction_bid_rate(prediction, budget=budget)
            if predicted_bid_rate is None:
                skipped_reasons.append("prediction returned no usable bid rate")
                rolling_records.append(holdout_record)
                continue

            absolute_error = abs(predicted_bid_rate - actual_bid_rate)
            errors.append(absolute_error)
            fallback_count += 1 if prediction.get("fallback_reason") else 0
            guardrail_count += 1 if prediction.get("guardrail_applied") else 0
            sample_rows.append({
                "notice_number": holdout_record.get("notice_number"),
                "agency_name": holdout_record.get("agency_name"),
                "actual_bid_rate": round(float(actual_bid_rate), 6),
                "predicted_bid_rate": round(float(predicted_bid_rate), 6),
                "absolute_error_rate": round(float(absolute_error), 6),
            })
            rolling_records.append(holdout_record)

        sample_count = len(errors)
        return {
            "predictor_key": predictor_key,
            "predictor_name": predictor_name,
            "predictor_family": predictor_family,
            "status": "completed" if sample_count else "skipped",
            "sample_count": sample_count,
            "average_absolute_error_rate": self._average(errors),
            "max_absolute_error_rate": round(max(errors), 6) if errors else None,
            "fallback_rate": round(self._safe_ratio(fallback_count, sample_count), 6) if sample_count else None,
            "guardrail_rate": round(self._safe_ratio(guardrail_count, sample_count), 6) if sample_count else None,
            "skipped_count": len(skipped_reasons),
            "skipped_reasons": self._top_reason_counts(skipped_reasons),
            "samples": sample_rows,
        }

    def _skipped_predictor_result(
        self,
        *,
        predictor_key: str,
        predictor_name: str,
        predictor_family: str,
        reason: str,
    ) -> dict[str, Any]:
        """Return a stable skipped result for a predictor that could not be evaluated."""
        return {
            "predictor_key": predictor_key,
            "predictor_name": predictor_name,
            "predictor_family": predictor_family,
            "status": "skipped",
            "sample_count": 0,
            "average_absolute_error_rate": None,
            "max_absolute_error_rate": None,
            "fallback_rate": None,
            "guardrail_rate": None,
            "skipped_count": 1,
            "skipped_reasons": [{"reason": reason, "count": 1}],
            "samples": [],
        }
