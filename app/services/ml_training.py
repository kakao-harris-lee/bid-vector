"""Queued ML training helpers kept out of the API request path."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TrainingRunOptions:
    release_tag: str
    category: str | None
    agency_name: str | None
    limit: int
    notes: str | None
    publish_remote: bool
    create_manifest: bool


@dataclass(frozen=True)
class TrainingRunPaths:
    training_dir: Path
    predictor_lstm_dir: Path
    predictor_ensemble_dir: Path
    dataset_path: Path
    summary_path: Path
    dataset_quality_path: Path
    comparison_report_path: Path
    lstm_artifact_path: Path
    ensemble_artifact_path: Path


class PricePredictionTrainingService:
    """Build lightweight predictor artifacts from historical bid-rate data."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
        self.repo_root = self.repo_root.resolve()

    def train_price_predictor(self, db: Session, request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a dataset snapshot, create predictor artifacts, and optionally publish a manifest."""
        options = self._training_run_options(request_payload)
        paths = self._training_run_paths(options.release_tag)
        self._ensure_training_run_dirs(paths)

        dataset, probability_calibration_dataset = self._build_training_datasets(db, options)
        paths.dataset_path.write_text(self._dump_json(dataset), encoding="utf-8")

        bid_rates = [float(item["bid_rate"]) for item in dataset["series"] if item.get("bid_rate") is not None]
        dataset_quality = self._build_dataset_quality_report(
            release_tag=options.release_tag,
            category=options.category,
            agency_name=options.agency_name,
            limit=options.limit,
            bid_rates=bid_rates,
            dataset=dataset,
        )
        paths.dataset_quality_path.write_text(self._dump_json(dataset_quality), encoding="utf-8")
        summary = self._build_training_summary(
            release_tag=options.release_tag,
            category=options.category,
            agency_name=options.agency_name,
            limit=options.limit,
            bid_rates=bid_rates,
            dataset=dataset,
            dataset_quality=dataset_quality,
            probability_calibration_dataset=probability_calibration_dataset,
        )
        paths.summary_path.write_text(self._dump_json(summary), encoding="utf-8")

        if not bid_rates:
            comparison_report = self._write_artifact_comparison_report(
                options=options,
                paths=paths,
                dataset=dataset,
                dataset_quality=dataset_quality,
                lstm_artifact=None,
                ensemble_artifact=None,
            )
            return self._skipped_training_result(
                options=options,
                paths=paths,
                summary=summary,
                dataset_quality=dataset_quality,
                comparison_report=comparison_report,
            )

        lstm_artifact, ensemble_artifact = self._build_and_write_predictor_artifacts(
            options=options,
            paths=paths,
            bid_rates=bid_rates,
            summary=summary,
        )
        comparison_report = self._write_artifact_comparison_report(
            options=options,
            paths=paths,
            dataset=dataset,
            dataset_quality=dataset_quality,
            lstm_artifact=lstm_artifact,
            ensemble_artifact=ensemble_artifact,
        )
        manifest, remote_storage = self._maybe_create_training_manifest(options=options, paths=paths)
        return self._completed_training_result(
            options=options,
            paths=paths,
            summary=summary,
            dataset_quality=dataset_quality,
            comparison_report=comparison_report,
            manifest=manifest,
            remote_storage=remote_storage,
        )

    def _training_run_options(
        self,
        request_payload: dict[str, Any] | None,
    ) -> TrainingRunOptions:
        request = dict(request_payload or {})
        return TrainingRunOptions(
            release_tag=self._resolve_release_tag(request.get("release_tag")),
            category=self._clean_optional(request.get("category")),
            agency_name=self._clean_optional(request.get("agency_name")),
            limit=max(1, min(int(request.get("limit") or 500), 5000)),
            notes=self._clean_optional(request.get("notes")),
            publish_remote=bool(request.get("publish_remote", True)),
            create_manifest=bool(request.get("create_manifest", True)),
        )

    def _training_run_paths(self, release_tag: str) -> TrainingRunPaths:
        training_dir = self.repo_root / "models" / "training-runs" / release_tag
        predictor_lstm_dir = self.repo_root / "models" / "predictors" / "lstm"
        predictor_ensemble_dir = self.repo_root / "models" / "predictors" / "ensemble"
        return TrainingRunPaths(
            training_dir=training_dir,
            predictor_lstm_dir=predictor_lstm_dir,
            predictor_ensemble_dir=predictor_ensemble_dir,
            dataset_path=training_dir / "dataset.json",
            summary_path=training_dir / "training-summary.json",
            dataset_quality_path=training_dir / "dataset-quality.json",
            comparison_report_path=training_dir / "artifact-comparison.json",
            lstm_artifact_path=predictor_lstm_dir / f"{release_tag}.json",
            ensemble_artifact_path=predictor_ensemble_dir / f"{release_tag}.json",
        )

    @staticmethod
    def _ensure_training_run_dirs(paths: TrainingRunPaths) -> None:
        paths.training_dir.mkdir(parents=True, exist_ok=True)
        paths.predictor_lstm_dir.mkdir(parents=True, exist_ok=True)
        paths.predictor_ensemble_dir.mkdir(parents=True, exist_ok=True)

    def _build_training_datasets(
        self,
        db: Session,
        options: TrainingRunOptions,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        dataset_service = PredictionDatasetService()
        dataset = dataset_service.build_training_dataset(
            db,
            category=options.category,
            agency_name=options.agency_name,
            limit=options.limit,
            explicit_bid_rate_only=True,
        )
        probability_calibration_dataset = dataset_service.build_probability_calibration_dataset(
            db,
            category=options.category,
            limit=options.limit * 5,
        )
        return dataset, probability_calibration_dataset

    def _build_and_write_predictor_artifacts(
        self,
        *,
        options: TrainingRunOptions,
        paths: TrainingRunPaths,
        bid_rates: list[float],
        summary: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        lstm_artifact = self._build_lstm_artifact(
            release_tag=options.release_tag,
            bid_rates=bid_rates,
        )
        ensemble_artifact = self._build_ensemble_artifact(
            release_tag=options.release_tag,
            lstm_artifact_path=paths.lstm_artifact_path,
            lstm_artifact=lstm_artifact,
            bid_rates=bid_rates,
        )
        self._inject_group_calibration(
            artifact=ensemble_artifact,
            group_calibration=summary.get("group_calibration") or {},
        )
        self._inject_summary_block(
            artifact=ensemble_artifact,
            key="probability_calibration",
            block=summary.get("probability_calibration") or {},
        )
        paths.lstm_artifact_path.write_text(self._dump_json(lstm_artifact), encoding="utf-8")
        paths.ensemble_artifact_path.write_text(self._dump_json(ensemble_artifact), encoding="utf-8")
        load_lstm_artifact(paths.lstm_artifact_path)
        load_ensemble_artifact(paths.ensemble_artifact_path)
        return lstm_artifact, ensemble_artifact

    def _write_artifact_comparison_report(
        self,
        *,
        options: TrainingRunOptions,
        paths: TrainingRunPaths,
        dataset: dict[str, Any],
        dataset_quality: dict[str, Any],
        lstm_artifact: dict[str, Any] | None,
        ensemble_artifact: dict[str, Any] | None,
    ) -> dict[str, Any]:
        comparison_report = self._build_artifact_comparison_report(
            release_tag=options.release_tag,
            category=options.category,
            agency_name=options.agency_name,
            dataset=dataset,
            dataset_quality=dataset_quality,
            lstm_artifact=lstm_artifact,
            ensemble_artifact=ensemble_artifact,
        )
        paths.comparison_report_path.write_text(self._dump_json(comparison_report), encoding="utf-8")
        return comparison_report

    def _maybe_create_training_manifest(
        self,
        *,
        options: TrainingRunOptions,
        paths: TrainingRunPaths,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not options.create_manifest:
            return None, None
        release_service = MLReleasePromotionService(repo_root=self.repo_root)
        manifest = release_service.create_release_manifest(
            MLReleasePromotionRequest(
                release_tag=options.release_tag,
                lstm_artifact_path=str(paths.lstm_artifact_path),
                ensemble_artifact_path=str(paths.ensemble_artifact_path),
                predictor_backtest_report_path=str(paths.comparison_report_path),
                notes=options.notes or f"Queued price-predictor training run for {options.release_tag}",
                rebuild_limit=100,
                force_rebuild=False,
            )
        )
        if options.publish_remote:
            return manifest, release_service.publish_release_manifest(manifest["manifest_path"])
        return manifest, None

    def _skipped_training_result(
        self,
        *,
        options: TrainingRunOptions,
        paths: TrainingRunPaths,
        summary: dict[str, Any],
        dataset_quality: dict[str, Any],
        comparison_report: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "release_tag": options.release_tag,
            "status": "skipped_insufficient_data",
            "detail": "No usable historical bid-rate samples were available for training.",
            "dataset_path": self._to_portable_path(paths.dataset_path),
            "summary_path": self._to_portable_path(paths.summary_path),
            "dataset_quality_path": self._to_portable_path(paths.dataset_quality_path),
            "comparison_report_path": self._to_portable_path(paths.comparison_report_path),
            "summary": summary,
            "dataset_quality": dataset_quality,
            "comparison_report": comparison_report,
            "manifest": None,
        }

    def _completed_training_result(
        self,
        *,
        options: TrainingRunOptions,
        paths: TrainingRunPaths,
        summary: dict[str, Any],
        dataset_quality: dict[str, Any],
        comparison_report: dict[str, Any],
        manifest: dict[str, Any] | None,
        remote_storage: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "release_tag": options.release_tag,
            "status": "completed",
            "dataset_path": self._to_portable_path(paths.dataset_path),
            "summary_path": self._to_portable_path(paths.summary_path),
            "dataset_quality_path": self._to_portable_path(paths.dataset_quality_path),
            "comparison_report_path": self._to_portable_path(paths.comparison_report_path),
            "lstm_artifact_path": self._to_portable_path(paths.lstm_artifact_path),
            "ensemble_artifact_path": self._to_portable_path(paths.ensemble_artifact_path),
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
        probability_calibration_dataset: dict[str, Any] | None = None,
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
            "probability_calibration": self._build_probability_calibration(
                probability_calibration_dataset or {}
            ),
        }

    def _build_probability_calibration(
        self, calibration_dataset: dict[str, Any]
    ) -> dict[str, Any]:
        """Fit a per-group logistic (Platt) calibration for the낙찰-가능성 score.

        Mirrors :meth:`_build_group_calibration` in spirit: it consumes the labeled
        settlement dataset produced by
        ``PredictionDatasetService.build_probability_calibration_dataset`` and emits a
        compact, audit-friendly dict keyed by ``business_group`` plus a ``__global__``
        fallback. Each entry stores Platt scale/bias over the heuristic probability
        signal so inference can map the raw heuristic onto observed win frequencies.

        **Label policy.** The primary label is the eligibility-gated
        ``eligible_favorable`` flag (1) vs. any other *adjudicated* eligibility
        verdict (0); ``unknown`` rows were already dropped upstream (``label is
        None``). If a group lacks both classes after that filter, it falls back to
        the price-only ``plausible`` label so a sparse, freshly-backfilled history
        still yields a usable curve. The fallback is recorded per group so reviewers
        can audit which curve used which label.

        **No external deps.** Uses numpy/stdlib only — a few Newton steps fit the
        1-parameter logistic, with an isotonic-free closed-form fallback for
        degenerate (single-class) groups.

        Returns ``{}`` when there is nothing usable so callers fall through to the
        legacy heuristic with no behavior change.
        """
        items = calibration_dataset.get("items") or []
        if not items:
            return {}

        groups: dict[str, list[tuple[float, int, int]]] = {}
        for item in items:
            features = item.get("features") or {}
            raw = self._raw_probability_signal(features)
            primary = item.get("label")
            price_only = item.get("price_close_label")
            try:
                price_only_int = int(price_only) if price_only is not None else None
            except (TypeError, ValueError):
                price_only_int = None
            group_key = str(features.get("business_group") or "__global__")
            primary_int: int | None
            try:
                primary_int = int(primary) if primary is not None else None
            except (TypeError, ValueError):
                primary_int = None
            # -1 marks a missing label (e.g. would_have_won_final == "unknown")
            # so the curve fitter can drop it without confusing it with class 0.
            row = (
                raw,
                primary_int if primary_int is not None else -1,
                price_only_int if price_only_int is not None else -1,
            )
            groups.setdefault(group_key, []).append(row)
            groups.setdefault("__global__", []).append(row)

        calibration: dict[str, Any] = {}
        for group_key, rows in groups.items():
            curve = self._fit_group_probability_curve(rows)
            if curve is not None:
                calibration[group_key] = curve
        return calibration

    @staticmethod
    def _raw_probability_signal(features: dict[str, Any]) -> float:
        """Build the Platt-curve input from inference-time features.

        Delegates to the single source of truth
        (:func:`app.ai.predictors.historical.calibration_raw_signal`) so training and
        serving feed the fitted curve a byte-identical raw value. Uses ONLY
        confidence/matched — ``historical_sample_size`` is excluded to avoid the
        train/serve skew (history is always 0 in the training dataset).
        """
        from app.ai.predictors.historical import calibration_raw_signal

        return calibration_raw_signal(
            features.get("confidence_score", 0.0),
            features.get("matched_score", 0.0),
        )

    def _fit_group_probability_curve(
        self, rows: list[tuple[float, int, int]]
    ) -> dict[str, Any] | None:
        """Fit a 1-parameter logistic over the raw heuristic for one group.

        ``rows`` is a list of ``(raw_signal, primary_label, price_only_label)`` where
        ``-1`` marks a missing label (e.g. ``would_have_won_final == "unknown"`` was
        dropped to ``None`` upstream and arrives here as ``-1``).
        """
        import math

        primary = [(raw, label) for raw, label, _ in rows if label in (0, 1)]
        label_source = "eligible_favorable"
        usable = primary
        if not primary or len({label for _, label in primary}) < 2:
            # Fall back to the price-only label when the eligibility-gated labels
            # are absent or single-class for this group.
            fallback = [(raw, label) for raw, _, label in rows if label in (0, 1)]
            if fallback and len({label for _, label in fallback}) >= 2:
                usable = fallback
                label_source = "price_close"
            elif primary:
                usable = primary
                label_source = "eligible_favorable"
            else:
                usable = fallback or primary

        if not usable:
            return None

        sample_count = len(usable)
        positives = sum(1 for _, label in usable if label == 1)
        base_rate = positives / sample_count if sample_count else 0.0

        # Degenerate single-class group: no slope is identifiable. Emit a flat curve
        # that simply reports the observed base rate (still better-calibrated than the
        # raw heuristic, and audit-transparent).
        if len({label for _, label in usable}) < 2:
            return {
                "method": "base_rate",
                "label_source": label_source,
                "scale": 0.0,
                "bias": self._logit(base_rate),
                "base_rate": round(base_rate, 6),
                "sample_count": sample_count,
                "positive_count": positives,
            }

        # Platt scaling: fit p = sigmoid(scale * raw + bias) via a few Newton steps
        # on the log-loss. 2 parameters, tiny data — this converges in <10 iters.
        scale, bias = 1.0, 0.0
        for _ in range(50):
            g0 = g1 = 0.0
            h00 = h01 = h11 = 0.0
            for raw, label in usable:
                z = scale * raw + bias
                p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
                err = p - label
                g0 += err * raw
                g1 += err
                w = p * (1.0 - p)
                h00 += w * raw * raw
                h01 += w * raw
                h11 += w
            # Ridge term keeps the Hessian invertible for thin/collinear data.
            h00 += 1e-6
            h11 += 1e-6
            det = h00 * h11 - h01 * h01
            if abs(det) < 1e-12:
                break
            d_scale = (g0 * h11 - g1 * h01) / det
            d_bias = (g1 * h00 - g0 * h01) / det
            scale -= d_scale
            bias -= d_bias
            if abs(d_scale) < 1e-6 and abs(d_bias) < 1e-6:
                break

        return {
            "method": "platt",
            "label_source": label_source,
            "scale": round(float(scale), 6),
            "bias": round(float(bias), 6),
            "base_rate": round(base_rate, 6),
            "sample_count": sample_count,
            "positive_count": positives,
        }

    @staticmethod
    def _logit(p: float) -> float:
        import math

        clamped = min(1.0 - 1e-6, max(1e-6, float(p)))
        return round(math.log(clamped / (1.0 - clamped)), 6)

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
                "p25": sorted_values[(n - 1) * 1 // 4],
                "p75": sorted_values[(n - 1) * 3 // 4],
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

    @staticmethod
    def _inject_group_calibration(
        artifact: dict[str, Any],
        group_calibration: dict[str, dict[str, float | int]],
    ) -> None:
        """Mutate the in-memory ensemble artifact to embed summary.group_calibration.

        Call this BEFORE writing the artifact to disk so that the on-disk file and
        the in-memory dict are always in sync — no race window, no divergence.
        """
        if not group_calibration:
            return
        summary = artifact.setdefault("summary", {})
        if not isinstance(summary, dict):
            summary = {}
            artifact["summary"] = summary
        summary["group_calibration"] = group_calibration

    @staticmethod
    def _inject_summary_block(
        artifact: dict[str, Any],
        *,
        key: str,
        block: dict[str, Any],
    ) -> None:
        """Embed an arbitrary ``summary.<key>`` block into the in-memory artifact.

        Same write-before-disk contract as :meth:`_inject_group_calibration`: call
        BEFORE serializing so the on-disk sha256 (and the signed manifest) covers it.
        A falsy ``block`` is a no-op so empty calibration never bloats the artifact.
        """
        if not block:
            return
        summary = artifact.setdefault("summary", {})
        if not isinstance(summary, dict):
            summary = {}
            artifact["summary"] = summary
        summary[key] = block

    def _inject_group_calibration_into_ensemble_artifact(
        self,
        *,
        ensemble_artifact_path: "Path | str",
        group_calibration: dict[str, dict[str, float | int]],
    ) -> None:
        """Patch the ensemble artifact JSON with summary.group_calibration in place.

        .. deprecated::
            Use :meth:`_inject_group_calibration` (in-memory variant) instead.
            This file-based helper is retained for backward compatibility only.

        Best-effort: if the artifact doesn't exist or isn't JSON, log and return —
        this is a runtime feature enhancement, not a training prerequisite.
        """
        path = Path(ensemble_artifact_path)
        if not group_calibration or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        self._inject_group_calibration(payload, group_calibration)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _relative_path_from(self, path: Path, *, base_path: Path) -> str:
        return Path(os.path.relpath(path.resolve(), start=base_path.resolve())).as_posix()
