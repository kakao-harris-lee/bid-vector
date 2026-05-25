"""Queued ML training helpers kept out of the API request path."""

from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from sqlalchemy.orm import Session

from app.ai.predictors.base import PricePredictionContext
from app.ai.predictors.ensemble import build_ensemble_prediction_payload, load_ensemble_artifact
from app.ai.predictors.historical import HistoricalStatisticalPredictor
from app.ai.predictors.lstm import build_lstm_prediction_payload, infer_lstm_sequence_signal, load_lstm_artifact
from app.core.config import settings
from app.core.time import utc_now
from app.services.ml_release import MLReleasePromotionRequest, MLReleasePromotionService
from app.services.prediction_dataset import PredictionDatasetService


class PricePredictionTrainingService:
    """Build lightweight predictor artifacts from historical bid-rate data."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
        self.repo_root = self.repo_root.resolve()

    def train_price_predictor(self, db: Session, request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a dataset snapshot, create predictor artifacts, and optionally publish a manifest."""
        request = dict(request_payload or {})
        release_tag = self._resolve_release_tag(request.get("release_tag"))
        category = self._clean_optional(request.get("category"))
        agency_name = self._clean_optional(request.get("agency_name"))
        limit = max(1, min(int(request.get("limit") or 500), 5000))
        notes = self._clean_optional(request.get("notes"))
        publish_remote = bool(request.get("publish_remote", True))
        create_manifest = bool(request.get("create_manifest", True))

        dataset = PredictionDatasetService().build_training_dataset(
            db,
            category=category,
            agency_name=agency_name,
            limit=limit,
            explicit_bid_rate_only=True,
        )
        training_dir = self.repo_root / "models" / "training-runs" / release_tag
        predictor_lstm_dir = self.repo_root / "models" / "predictors" / "lstm"
        predictor_ensemble_dir = self.repo_root / "models" / "predictors" / "ensemble"
        training_dir.mkdir(parents=True, exist_ok=True)
        predictor_lstm_dir.mkdir(parents=True, exist_ok=True)
        predictor_ensemble_dir.mkdir(parents=True, exist_ok=True)

        dataset_path = training_dir / "dataset.json"
        summary_path = training_dir / "training-summary.json"
        dataset_quality_path = training_dir / "dataset-quality.json"
        comparison_report_path = training_dir / "artifact-comparison.json"
        dataset_path.write_text(self._dump_json(dataset), encoding="utf-8")

        bid_rates = [float(item["bid_rate"]) for item in dataset["series"] if item.get("bid_rate") is not None]
        dataset_quality = self._build_dataset_quality_report(
            release_tag=release_tag,
            category=category,
            agency_name=agency_name,
            limit=limit,
            bid_rates=bid_rates,
            dataset=dataset,
        )
        dataset_quality_path.write_text(self._dump_json(dataset_quality), encoding="utf-8")
        summary = self._build_training_summary(
            release_tag=release_tag,
            category=category,
            agency_name=agency_name,
            limit=limit,
            bid_rates=bid_rates,
            dataset=dataset,
            dataset_quality=dataset_quality,
        )
        summary_path.write_text(self._dump_json(summary), encoding="utf-8")

        if not bid_rates:
            comparison_report = self._build_artifact_comparison_report(
                release_tag=release_tag,
                category=category,
                agency_name=agency_name,
                dataset=dataset,
                dataset_quality=dataset_quality,
                lstm_artifact=None,
                ensemble_artifact=None,
            )
            comparison_report_path.write_text(self._dump_json(comparison_report), encoding="utf-8")
            return {
                "release_tag": release_tag,
                "status": "skipped_insufficient_data",
                "detail": "No usable historical bid-rate samples were available for training.",
                "dataset_path": self._to_portable_path(dataset_path),
                "summary_path": self._to_portable_path(summary_path),
                "dataset_quality_path": self._to_portable_path(dataset_quality_path),
                "comparison_report_path": self._to_portable_path(comparison_report_path),
                "summary": summary,
                "dataset_quality": dataset_quality,
                "comparison_report": comparison_report,
                "manifest": None,
            }

        lstm_artifact_path = predictor_lstm_dir / f"{release_tag}.json"
        ensemble_artifact_path = predictor_ensemble_dir / f"{release_tag}.json"
        lstm_artifact = self._build_lstm_artifact(release_tag=release_tag, bid_rates=bid_rates)
        ensemble_artifact = self._build_ensemble_artifact(
            release_tag=release_tag,
            lstm_artifact_path=lstm_artifact_path,
            lstm_artifact=lstm_artifact,
            bid_rates=bid_rates,
        )
        lstm_artifact_path.write_text(self._dump_json(lstm_artifact), encoding="utf-8")
        ensemble_artifact_path.write_text(self._dump_json(ensemble_artifact), encoding="utf-8")
        load_lstm_artifact(lstm_artifact_path)
        load_ensemble_artifact(ensemble_artifact_path)
        comparison_report = self._build_artifact_comparison_report(
            release_tag=release_tag,
            category=category,
            agency_name=agency_name,
            dataset=dataset,
            dataset_quality=dataset_quality,
            lstm_artifact=lstm_artifact,
            ensemble_artifact=ensemble_artifact,
        )
        comparison_report_path.write_text(self._dump_json(comparison_report), encoding="utf-8")

        manifest = None
        remote_storage = None
        if create_manifest:
            release_service = MLReleasePromotionService(repo_root=self.repo_root)
            manifest = release_service.create_release_manifest(
                MLReleasePromotionRequest(
                    release_tag=release_tag,
                    lstm_artifact_path=str(lstm_artifact_path),
                    ensemble_artifact_path=str(ensemble_artifact_path),
                    predictor_backtest_report_path=str(comparison_report_path),
                    notes=notes or f"Queued price-predictor training run for {release_tag}",
                    rebuild_limit=100,
                    force_rebuild=False,
                )
            )
            if publish_remote:
                remote_storage = release_service.publish_release_manifest(manifest["manifest_path"])

        return {
            "release_tag": release_tag,
            "status": "completed",
            "dataset_path": self._to_portable_path(dataset_path),
            "summary_path": self._to_portable_path(summary_path),
            "dataset_quality_path": self._to_portable_path(dataset_quality_path),
            "comparison_report_path": self._to_portable_path(comparison_report_path),
            "lstm_artifact_path": self._to_portable_path(lstm_artifact_path),
            "ensemble_artifact_path": self._to_portable_path(ensemble_artifact_path),
            "summary": summary,
            "dataset_quality": dataset_quality,
            "comparison_report": comparison_report,
            "manifest": manifest,
            "remote_storage": remote_storage,
        }

    def _build_training_summary(
        self,
        *,
        release_tag: str,
        category: str | None,
        agency_name: str | None,
        limit: int,
        bid_rates: list[float],
        dataset: dict[str, Any],
        dataset_quality: dict[str, Any],
    ) -> dict[str, Any]:
        """Build an auditable summary for the training run."""
        sample_count = len(bid_rates)
        average_bid_rate = mean(bid_rates) if bid_rates else None
        std_bid_rate = pstdev(bid_rates) if len(bid_rates) > 1 else 0.0
        return {
            "release_tag": release_tag,
            "trained_at": utc_now().isoformat(),
            "category": category,
            "agency_name": agency_name,
            "limit": limit,
            "sample_count": sample_count,
            "average_bid_rate": round(float(average_bid_rate), 6) if average_bid_rate is not None else None,
            "std_bid_rate": round(float(std_bid_rate), 6),
            "dataset_summary": dataset.get("summary", {}),
            "dataset_quality": {
                "status": dataset_quality.get("status"),
                "score": dataset_quality.get("score"),
                "blocking_issue_count": dataset_quality.get("blocking_issue_count", 0),
                "warning_count": dataset_quality.get("warning_count", 0),
            },
            "group_calibration": self._build_group_calibration(dataset),
        }

    def _build_group_calibration(self, dataset: dict[str, Any]) -> dict[str, dict[str, float | int]]:
        """Aggregate per-group winning_rate stats for inclusion in the release manifest."""
        import statistics

        # Support both "items" (test fixture) and "series" (production dataset builder)
        items = dataset.get("items") or dataset.get("series") or []
        groups: dict[str, list[float]] = {}
        for item in items:
            group = item.get("business_group")
            rate = item.get("winning_rate")
            if not group or rate in (None, ""):
                continue
            try:
                groups.setdefault(group, []).append(float(rate))
            except (TypeError, ValueError):
                continue
        calibration: dict[str, dict[str, float | int]] = {}
        for group, values in groups.items():
            if not values:
                continue
            sorted_values = sorted(values)
            n = len(sorted_values)
            calibration[group] = {
                "median_rate": round(statistics.median(sorted_values), 6),
                "std": round(statistics.pstdev(sorted_values), 6) if n > 1 else 0.0,
                "p25": sorted_values[max(0, n // 4)],
                "p75": sorted_values[min(n - 1, 3 * n // 4)],
                "sample_count": n,
            }
        return calibration

    def _build_dataset_quality_report(
        self,
        *,
        release_tag: str,
        category: str | None,
        agency_name: str | None,
        limit: int,
        bid_rates: list[float],
        dataset: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate whether the dataset is strong enough for release-gated training."""
        summary = dataset.get("summary") if isinstance(dataset.get("summary"), dict) else {}
        sample_count = int(summary.get("sample_count") or 0)
        project_count = int(summary.get("project_count") or 0)
        agency_count = int(summary.get("agency_count") or 0)
        linked_result_count = int(summary.get("linked_result_count") or 0)
        reserve_pattern_sample_count = int(summary.get("reserve_pattern_sample_count") or 0)
        linked_result_coverage = self._safe_ratio(linked_result_count, sample_count)
        reserve_pattern_coverage = self._safe_ratio(reserve_pattern_sample_count, sample_count)
        std_bid_rate = pstdev(bid_rates) if len(bid_rates) > 1 else 0.0
        min_training_samples = max(1, int(settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES or 1))
        min_gate_samples = max(1, int(settings.ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT or 1))
        configured_holdout_size = max(1, int(settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE or 1), min_gate_samples)
        required_samples = min_training_samples + configured_holdout_size

        checks: list[dict[str, Any]] = []
        self._append_quality_check(
            checks,
            name="sample_depth",
            passed=sample_count >= required_samples,
            severity="blocking",
            value=sample_count,
            threshold=required_samples,
            detail="Dataset needs enough rows for both training prefix and release-gate holdout samples.",
        )
        self._append_quality_check(
            checks,
            name="project_diversity",
            passed=project_count >= min(3, max(1, sample_count)),
            severity="warning",
            value=project_count,
            threshold=min(3, max(1, sample_count)),
            detail="Dataset should include more than one project before release comparison is trusted.",
        )
        self._append_quality_check(
            checks,
            name="agency_diversity",
            passed=bool(agency_name) or agency_count >= min(2, max(1, sample_count)),
            severity="warning",
            value=agency_count,
            threshold=min(2, max(1, sample_count)),
            detail="Global training should include multiple agencies; agency-scoped runs are exempt.",
        )
        self._append_quality_check(
            checks,
            name="linked_result_coverage",
            passed=linked_result_coverage >= 0.25,
            severity="warning",
            value=round(linked_result_coverage, 4),
            threshold=0.25,
            detail="Linked tender results improve post-training auditability.",
        )
        self._append_quality_check(
            checks,
            name="reserve_pattern_coverage",
            passed=reserve_pattern_coverage >= 0.25,
            severity="warning",
            value=round(reserve_pattern_coverage, 4),
            threshold=0.25,
            detail="Reserve-price samples improve scenario spread validation.",
        )
        self._append_quality_check(
            checks,
            name="bid_rate_variance",
            passed=sample_count < 2 or std_bid_rate <= 0.08,
            severity="warning",
            value=round(float(std_bid_rate), 6),
            threshold=0.08,
            detail="Very high bid-rate variance should be reviewed before promotion.",
        )

        blocking_issue_count = sum(1 for check in checks if not check["passed"] and check["severity"] == "blocking")
        warning_count = sum(1 for check in checks if not check["passed"] and check["severity"] == "warning")
        score = max(0, 100 - (blocking_issue_count * 40) - (warning_count * 10))
        status = "failed" if blocking_issue_count else ("warning" if warning_count else "passed")

        return {
            "report_version": "1",
            "release_tag": release_tag,
            "created_at": utc_now().isoformat(),
            "category": category,
            "agency_name": agency_name,
            "limit": limit,
            "status": status,
            "score": score,
            "blocking_issue_count": blocking_issue_count,
            "warning_count": warning_count,
            "metrics": {
                "sample_count": sample_count,
                "required_sample_count": required_samples,
                "required_holdout_sample_count": configured_holdout_size,
                "project_count": project_count,
                "agency_count": agency_count,
                "linked_result_count": linked_result_count,
                "linked_result_coverage": round(linked_result_coverage, 4),
                "reserve_pattern_sample_count": reserve_pattern_sample_count,
                "reserve_pattern_coverage": round(reserve_pattern_coverage, 4),
                "average_bid_rate": round(float(mean(bid_rates)), 6) if bid_rates else None,
                "std_bid_rate": round(float(std_bid_rate), 6),
                "started_at": summary.get("started_at"),
                "ended_at": summary.get("ended_at"),
            },
            "checks": checks,
        }

    def _build_artifact_comparison_report(
        self,
        *,
        release_tag: str,
        category: str | None,
        agency_name: str | None,
        dataset: dict[str, Any],
        dataset_quality: dict[str, Any],
        lstm_artifact: dict[str, Any] | None,
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
            self._evaluate_lstm_predictor(
                artifact=lstm_artifact,
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
            predict=lambda context: predictor.predict(context),
        )

    def _evaluate_lstm_predictor(
        self,
        *,
        artifact: dict[str, Any] | None,
        training_prefix: list[dict[str, Any]],
        holdout_records: list[dict[str, Any]],
        category: str | None,
        agency_name: str | None,
        min_training_size: int,
    ) -> dict[str, Any]:
        """Evaluate the generated LSTM artifact on the training holdout."""
        if artifact is None:
            return self._skipped_predictor_result(
                predictor_key="lstm",
                predictor_name="lstm_sequence",
                predictor_family="sequence_model",
                reason="No LSTM artifact was generated.",
            )
        loaded_artifact = load_lstm_artifact(artifact)
        return self._evaluate_predictor(
            predictor_key="lstm",
            predictor_name="lstm_sequence",
            predictor_family="sequence_model",
            training_prefix=training_prefix,
            holdout_records=holdout_records,
            category=category,
            agency_name=agency_name,
            min_training_size=min_training_size,
            predict=lambda context: build_lstm_prediction_payload(
                context,
                artifact=loaded_artifact,
                signal=infer_lstm_sequence_signal(context, artifact=loaded_artifact),
            ),
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

    def _append_quality_check(
        self,
        checks: list[dict[str, Any]],
        *,
        name: str,
        passed: bool,
        severity: str,
        value: Any,
        threshold: Any,
        detail: str,
    ) -> None:
        """Append one normalized dataset quality check."""
        checks.append({
            "name": name,
            "passed": bool(passed),
            "severity": severity,
            "value": value,
            "threshold": threshold,
            "detail": detail,
        })

    def _sort_dataset_series(self, series: list[Any]) -> list[dict[str, Any]]:
        """Return dataset rows in oldest-first order."""
        normalized_rows = [dict(item) for item in series if isinstance(item, dict)]

        def sort_key(item: dict[str, Any]) -> tuple[str, int]:
            opened_at = item.get("opened_at") or ""
            if hasattr(opened_at, "isoformat"):
                opened_at = opened_at.isoformat()
            return str(opened_at), int(item.get("historical_data_id") or 0)

        return sorted(normalized_rows, key=sort_key)

    def _resolve_dataset_bid_rate(self, item: dict[str, Any]) -> float | None:
        """Resolve a usable bid rate from a dataset row."""
        try:
            bid_rate = float(item.get("bid_rate") or 0.0)
        except (TypeError, ValueError):
            bid_rate = 0.0
        if bid_rate <= 0:
            try:
                predicted_price = float(item.get("predicted_price") or 0.0)
                base_amount = float(item.get("base_amount") or 0.0)
            except (TypeError, ValueError):
                predicted_price = 0.0
                base_amount = 0.0
            if predicted_price > 0 and base_amount > 0:
                bid_rate = predicted_price / base_amount
        if 0.5 <= bid_rate <= 1.5:
            return float(bid_rate)
        return None

    def _resolve_dataset_budget(self, item: dict[str, Any]) -> float:
        """Resolve the base amount used for one holdout prediction."""
        try:
            budget = float(item.get("base_amount") or 0.0)
        except (TypeError, ValueError):
            budget = 0.0
        if budget > 0:
            return budget
        bid_rate = self._resolve_dataset_bid_rate(item)
        try:
            predicted_price = float(item.get("predicted_price") or 0.0)
        except (TypeError, ValueError):
            predicted_price = 0.0
        if bid_rate and predicted_price > 0:
            return predicted_price / bid_rate
        return 0.0

    def _resolve_prediction_bid_rate(self, prediction: dict[str, Any], *, budget: float) -> float | None:
        """Resolve a predicted bid rate from one predictor response."""
        try:
            bid_rate = float(prediction.get("predicted_bid_rate") or 0.0)
        except (TypeError, ValueError):
            bid_rate = 0.0
        if bid_rate <= 0 and budget > 0:
            try:
                bid_rate = float(prediction.get("predicted_price") or 0.0) / budget
            except (TypeError, ValueError):
                bid_rate = 0.0
        if 0.0 < bid_rate < 2.0:
            return float(bid_rate)
        return None

    def _average(self, values: list[float]) -> float | None:
        """Return a rounded average while preserving empty sets."""
        if not values:
            return None
        return round(sum(values) / len(values), 6)

    def _safe_ratio(self, numerator: int | float, denominator: int | float) -> float:
        """Return a zero-safe ratio."""
        denominator_value = float(denominator or 0.0)
        if denominator_value <= 0:
            return 0.0
        return float(numerator or 0.0) / denominator_value

    def _top_reason_counts(self, reasons: list[str], *, limit: int = 3) -> list[dict[str, Any]]:
        """Return top skip reasons with counts."""
        counts: dict[str, int] = {}
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
        return [
            {"reason": reason, "count": count}
            for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    def _build_lstm_artifact(self, *, release_tag: str, bid_rates: list[float]) -> dict[str, Any]:
        """Create a valid lightweight LSTM artifact from dataset statistics."""
        average_bid_rate = mean(bid_rates)
        std_bid_rate = max(pstdev(bid_rates) if len(bid_rates) > 1 else 0.025, 0.01)
        sequence_length = max(3, min(len(bid_rates), 12))
        return {
            "artifact_version": "1",
            "model_version": f"{release_tag}-lstm",
            "sequence_length": sequence_length,
            "input_center": round(float(average_bid_rate), 6),
            "input_scale": round(float(std_bid_rate), 6),
            "output_scale": round(float(std_bid_rate) * 0.35, 6),
            "output_bias": round(float(average_bid_rate), 6),
            "scenario_spread_multiplier": 1.0,
            "confidence_bias": min(0.08, len(bid_rates) / 1000),
            "blend_weights": {
                "lstm": 0.6,
                "historical": 0.3,
                "trend": 0.1,
            },
            "weights": {
                "W_i": [[0.7]],
                "U_i": [[0.1]],
                "b_i": [2.0],
                "W_f": [[0.2]],
                "U_f": [[0.05]],
                "b_f": [2.0],
                "W_o": [[0.4]],
                "U_o": [[0.1]],
                "b_o": [1.5],
                "W_c": [[0.8]],
                "U_c": [[0.15]],
                "b_c": [0.0],
                "dense_W": [0.6],
                "dense_b": [0.0],
            },
        }

    def _build_ensemble_artifact(
        self,
        *,
        release_tag: str,
        lstm_artifact_path: Path,
        lstm_artifact: dict[str, Any] | None,
        bid_rates: list[float],
    ) -> dict[str, Any]:
        """Create an ensemble artifact that links to the generated LSTM artifact."""
        std_bid_rate = pstdev(bid_rates) if len(bid_rates) > 1 else 0.0
        artifact = {
            "artifact_version": "1",
            "model_version": f"{release_tag}-ensemble",
            "sequence_length": max(3, min(len(bid_rates), 12)),
            "momentum_window": max(3, min(len(bid_rates), 6)),
            "scenario_spread_multiplier": 1.0 if std_bid_rate < 0.04 else 1.15,
            "confidence_bias": min(0.06, len(bid_rates) / 1200),
            "component_weights": {
                "historical": 0.5,
                "momentum": 0.2,
                "mean_reversion": 0.15,
                "lstm": 0.15,
            },
            "lstm_artifact_path": self._relative_path_from(
                lstm_artifact_path,
                base_path=self.repo_root / "models" / "predictors" / "ensemble",
            ),
        }
        if lstm_artifact is not None:
            artifact["lstm_artifact"] = lstm_artifact
        return artifact

    def _resolve_release_tag(self, value: Any) -> str:
        cleaned = self._clean_optional(value)
        release_tag = cleaned or f"price-predictor-{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
        if any(token in release_tag for token in ("/", "\\", "..")):
            raise ValueError("release_tag must not contain path separators or '..'.")
        return release_tag

    def _clean_optional(self, value: Any) -> str | None:
        cleaned = str(value or "").strip()
        return cleaned or None

    def _dump_json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"

    def _to_portable_path(self, path: Path) -> str:
        resolved_path = path.resolve()
        try:
            return resolved_path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return str(resolved_path)

    def _relative_path_from(self, path: Path, *, base_path: Path) -> str:
        return Path(os.path.relpath(path.resolve(), start=base_path.resolve())).as_posix()
