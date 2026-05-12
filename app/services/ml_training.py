"""Queued ML training helpers kept out of the API request path."""

from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from sqlalchemy.orm import Session

from app.ai.predictors.ensemble import load_ensemble_artifact
from app.ai.predictors.lstm import load_lstm_artifact
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
        )
        training_dir = self.repo_root / "models" / "training-runs" / release_tag
        predictor_lstm_dir = self.repo_root / "models" / "predictors" / "lstm"
        predictor_ensemble_dir = self.repo_root / "models" / "predictors" / "ensemble"
        training_dir.mkdir(parents=True, exist_ok=True)
        predictor_lstm_dir.mkdir(parents=True, exist_ok=True)
        predictor_ensemble_dir.mkdir(parents=True, exist_ok=True)

        dataset_path = training_dir / "dataset.json"
        summary_path = training_dir / "training-summary.json"
        dataset_path.write_text(self._dump_json(dataset), encoding="utf-8")

        bid_rates = [float(item["bid_rate"]) for item in dataset["series"] if item.get("bid_rate") is not None]
        summary = self._build_training_summary(
            release_tag=release_tag,
            category=category,
            agency_name=agency_name,
            limit=limit,
            bid_rates=bid_rates,
            dataset=dataset,
        )
        summary_path.write_text(self._dump_json(summary), encoding="utf-8")

        if not bid_rates:
            return {
                "release_tag": release_tag,
                "status": "skipped_insufficient_data",
                "detail": "No usable historical bid-rate samples were available for training.",
                "dataset_path": self._to_portable_path(dataset_path),
                "summary_path": self._to_portable_path(summary_path),
                "summary": summary,
                "manifest": None,
            }

        lstm_artifact_path = predictor_lstm_dir / f"{release_tag}.json"
        ensemble_artifact_path = predictor_ensemble_dir / f"{release_tag}.json"
        lstm_artifact = self._build_lstm_artifact(release_tag=release_tag, bid_rates=bid_rates)
        ensemble_artifact = self._build_ensemble_artifact(
            release_tag=release_tag,
            lstm_artifact_path=lstm_artifact_path,
            bid_rates=bid_rates,
        )
        lstm_artifact_path.write_text(self._dump_json(lstm_artifact), encoding="utf-8")
        ensemble_artifact_path.write_text(self._dump_json(ensemble_artifact), encoding="utf-8")
        load_lstm_artifact(lstm_artifact_path)
        load_ensemble_artifact(ensemble_artifact_path)

        manifest = None
        remote_storage = None
        if create_manifest:
            release_service = MLReleasePromotionService(repo_root=self.repo_root)
            manifest = release_service.create_release_manifest(
                MLReleasePromotionRequest(
                    release_tag=release_tag,
                    lstm_artifact_path=str(lstm_artifact_path),
                    ensemble_artifact_path=str(ensemble_artifact_path),
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
            "lstm_artifact_path": self._to_portable_path(lstm_artifact_path),
            "ensemble_artifact_path": self._to_portable_path(ensemble_artifact_path),
            "summary": summary,
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
        }

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
        bid_rates: list[float],
    ) -> dict[str, Any]:
        """Create an ensemble artifact that links to the generated LSTM artifact."""
        std_bid_rate = pstdev(bid_rates) if len(bid_rates) > 1 else 0.0
        return {
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
