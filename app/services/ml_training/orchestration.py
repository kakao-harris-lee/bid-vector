"""Training-run orchestration for the price-predictor training service.

Owns the end-to-end ``train_price_predictor`` pipeline: dataset build, quality
report, summary, artifact build/write, holdout comparison, and optional signed
manifest creation. Delegates each step to the sibling mixins; method bodies are
moved verbatim from the original module.
"""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from sqlalchemy.orm import Session

from app.ai.predictors.ensemble import load_ensemble_artifact
from app.ai.predictors.lstm import load_lstm_artifact
from app.core.time import utc_now
from app.services.ml_release import MLReleasePromotionRequest, MLReleasePromotionService
from app.services.prediction_dataset import PredictionDatasetService

from .constants import TrainingRunOptions, TrainingRunPaths


class OrchestrationMixin:
    """End-to-end training-run pipeline and run-summary assembly."""

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
        # Settlement-derived labels for 낙찰-가능성 calibration. Built from PaperBid +
        # PaperBidSettlement (NOT from `dataset`), so settled outcomes can only ever
        # be labels here — never price-prediction features.
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
