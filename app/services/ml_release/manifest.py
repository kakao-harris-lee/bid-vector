"""Manifest lifecycle: create/load/verify, env updates, and artifact validation."""

from __future__ import annotations

import json
import hmac
from pathlib import Path
from typing import Any

from app.ai.predictors.ensemble import load_ensemble_artifact
from app.ai.predictors.lstm import load_lstm_artifact
from app.core.config import settings
from app.core.time import utc_now
from app.services.ml_release.base import MLReleasePromotionRequest, _MLReleaseBase


class _ManifestLifecycleMixin(_MLReleaseBase):
    """Create, load and verify release manifests and validate their artifacts."""

    def create_release_manifest(
        self, request: MLReleasePromotionRequest
    ) -> dict[str, Any]:
        """Validate provided artifacts and persist one release manifest."""
        release_tag = self._normalize_release_tag(request.release_tag)
        embedding_metadata = self._validate_embedding_model_path(
            request.embedding_model_path
        )
        lstm_metadata = self._validate_lstm_artifact(request.lstm_artifact_path)
        ensemble_metadata = self._validate_ensemble_artifact(
            request.ensemble_artifact_path
        )

        if (
            embedding_metadata is None
            and lstm_metadata is None
            and ensemble_metadata is None
        ):
            raise ValueError(
                "At least one embedding or predictor artifact path must be provided."
            )

        auto_lstm_path = None
        if lstm_metadata is None and ensemble_metadata is not None:
            auto_lstm_path = ensemble_metadata.get("resolved_lstm_artifact_path")

        recommended_env: dict[str, Any] = {}
        if embedding_metadata is not None:
            recommended_env["CLASSIFIER_EMBEDDING_MODEL"] = embedding_metadata["path"]
            recommended_env["CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY"] = True

        effective_lstm_path = (
            lstm_metadata["path"] if lstm_metadata is not None else auto_lstm_path
        )
        if effective_lstm_path or ensemble_metadata is not None:
            recommended_env["PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS"] = True
        if effective_lstm_path:
            recommended_env["PRICE_PREDICTION_LSTM_MODEL_PATH"] = effective_lstm_path
        if ensemble_metadata is not None:
            recommended_env["PRICE_PREDICTION_ENSEMBLE_MODEL_PATH"] = ensemble_metadata[
                "path"
            ]

        predictor_backtest_report = self._load_predictor_backtest_report(
            request.predictor_backtest_report_path
        )
        predictor_promotion_gate = self._build_predictor_promotion_gate(
            predictor_backtest_report,
            has_predictor_artifact=bool(
                effective_lstm_path or ensemble_metadata is not None
            ),
        )
        best_predictor_key = str(
            predictor_promotion_gate.get("best_predictor_key") or ""
        ).strip()
        if best_predictor_key:
            recommended_env["PRICE_PREDICTION_PREFERRED_PREDICTOR"] = best_predictor_key

        manifest = {
            "manifest_schema_version": "2",
            "release_tag": release_tag,
            "git_sha": str(request.git_sha).strip() or None,
            "validated_on": utc_now().isoformat(),
            "notes": str(request.notes).strip() or None,
            "recommended_docker_target": (
                "api-embedding" if embedding_metadata is not None else "api-runtime"
            ),
            "artifacts": {
                "embedding_model": embedding_metadata,
                "predictors": {
                    "lstm": lstm_metadata,
                    "ensemble": ensemble_metadata,
                },
            },
            "recommended_env": recommended_env,
            "promotion_gate": {
                "predictor_backtest": predictor_promotion_gate,
            },
            "rebuild": {
                "recommended_force": bool(request.force_rebuild),
                "default_limit": int(request.rebuild_limit),
                "default_offset": int(request.rebuild_offset),
                "default_category": request.category,
                "default_project_status": request.project_status,
                "task_endpoint": "/api/v1/ml/backfills/project-embeddings",
            },
            "storage_policy": self._build_storage_policy(),
        }
        manifest["signature"] = self._sign_manifest(manifest)

        manifest_path = self._manifest_path_for_tag(release_tag)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["manifest_path"] = str(manifest_path)
        manifest["retention"] = self.enforce_manifest_retention(
            current_manifest_path=manifest_path
        )
        if request.publish_remote or settings.ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH:
            manifest["remote_storage"] = self.publish_release_manifest(manifest_path)
        return manifest

    def load_release_manifest(
        self, manifest_ref: str | Path
    ) -> tuple[dict[str, Any], Path]:
        """Load one persisted release manifest by path or release tag."""
        manifest_path = self._resolve_manifest_path(manifest_ref)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(
                f"Release manifest was not found: {manifest_path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Release manifest is not valid JSON: {manifest_path}"
            ) from exc

        if not isinstance(manifest, dict):
            raise ValueError(
                f"Release manifest must decode to a JSON object: {manifest_path}"
            )
        self.verify_release_manifest(manifest, manifest_path=manifest_path)
        return manifest, manifest_path

    def verify_release_manifest(
        self,
        manifest: dict[str, Any],
        *,
        manifest_path: Path | None = None,
        require_signature: bool | None = None,
    ) -> dict[str, Any]:
        """Verify a manifest signature when present or required by policy."""
        signature_required = (
            bool(settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE)
            if require_signature is None
            else bool(require_signature)
        )
        signature = manifest.get("signature")
        if not isinstance(signature, dict):
            if signature_required:
                location = f": {manifest_path}" if manifest_path is not None else ""
                raise ValueError(
                    f"Release manifest is missing a required signature{location}"
                )
            return {"verified": False, "reason": "signature_missing"}

        expected = self._sign_manifest(
            {key: value for key, value in manifest.items() if key != "signature"}
        )
        if not hmac.compare_digest(
            str(signature.get("digest") or ""), str(expected.get("digest") or "")
        ):
            location = f": {manifest_path}" if manifest_path is not None else ""
            raise ValueError(
                f"Release manifest signature verification failed{location}"
            )
        return {
            "verified": True,
            "algorithm": signature.get("algorithm"),
            "key_id": signature.get("key_id"),
            "payload_sha256": signature.get("payload_sha256"),
        }

    def build_manifest_env_updates(
        self,
        manifest_ref: str | Path,
        *,
        include_docker_target: bool = True,
    ) -> dict[str, str]:
        """Build a stringified env mapping from one stored release manifest."""
        manifest, _ = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})

        env_updates = {
            key: self._stringify_env_value(value)
            for key, value in recommended_env.items()
        }
        if include_docker_target:
            docker_target = str(manifest.get("recommended_docker_target") or "").strip()
            if docker_target:
                env_updates["API_DOCKER_TARGET"] = docker_target
        return env_updates

    def write_manifest_env_file(
        self,
        manifest_ref: str | Path,
        *,
        env_file_path: str | Path = ".env",
        include_docker_target: bool = True,
    ) -> dict[str, Any]:
        """Apply one manifest's recommended env values into a dotenv-style file."""
        env_updates = self.build_manifest_env_updates(
            manifest_ref,
            include_docker_target=include_docker_target,
        )
        target_path = Path(env_file_path)
        if not target_path.is_absolute():
            target_path = self.repo_root / target_path
        target_path = target_path.resolve()
        file_previously_existed = target_path.exists()

        existing_lines = (
            target_path.read_text(encoding="utf-8").splitlines()
            if file_previously_existed
            else []
        )
        updated_lines: list[str] = []
        applied_keys: list[str] = []

        for line in existing_lines:
            key = self._parse_env_key(line)
            if key is not None and key in env_updates:
                updated_lines.append(f"{key}={env_updates[key]}")
                applied_keys.append(key)
            else:
                updated_lines.append(line)

        missing_keys = [key for key in env_updates if key not in applied_keys]
        if missing_keys and updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        for key in missing_keys:
            updated_lines.append(f"{key}={env_updates[key]}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            "\n".join(updated_lines).rstrip() + "\n", encoding="utf-8"
        )

        return {
            "env_file_path": str(target_path),
            "created": not file_previously_existed,
            "updated_keys": list(env_updates.keys()),
            "applied_env": env_updates,
        }

    def _build_storage_policy(self) -> dict[str, Any]:
        """Describe the release manifest storage and retention policy."""
        return {
            "local_manifest_dir": self._to_portable_path(self._manifest_dir()),
            "local_archive_dir": self._to_portable_path(self._manifest_archive_dir()),
            "retention_limit": int(settings.ML_RELEASE_MANIFEST_RETENTION_LIMIT or 0),
            "remote_storage_configured": bool(settings.ML_RELEASE_OBJECT_STORAGE_URL),
            "remote_auto_publish": bool(
                settings.ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH
            ),
        }

    def _manifest_settings_overrides(
        self, recommended_env: dict[str, Any]
    ) -> dict[str, Any]:
        """Map one manifest's env recommendation into in-process setting overrides."""
        overrides = {
            "ENABLE_SEMANTIC_CLASSIFICATION": True,
            "CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY": bool(
                recommended_env.get("CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY", True)
            ),
        }

        if settings.ENVIRONMENT == "test":
            overrides["ENVIRONMENT"] = "development"
        for key in (
            "CLASSIFIER_EMBEDDING_MODEL",
            "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS",
            "PRICE_PREDICTION_LSTM_MODEL_PATH",
            "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH",
        ):
            if key in recommended_env:
                overrides[key] = recommended_env[key]
        return overrides

    def _validate_embedding_model_path(
        self, raw_path: str | None
    ) -> dict[str, Any] | None:
        """Validate one local sentence-transformer snapshot directory."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=True)
        top_level_entries = list(path.iterdir())
        if not top_level_entries:
            raise ValueError(f"Embedding model directory is empty: {path}")

        config_candidates = sorted(
            entry.name
            for entry in top_level_entries
            if entry.is_file() and entry.suffix.lower() == ".json"
        )
        return {
            "path": self._to_portable_path(path),
            "path_kind": self._path_kind(path),
            "directory_name": path.name,
            "entry_count": len(top_level_entries),
            "config_files": config_candidates,
            "integrity": self._path_integrity_metadata(path),
        }

    def _validate_lstm_artifact(self, raw_path: str | None) -> dict[str, Any] | None:
        """Validate one persisted LSTM JSON artifact."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=False)
        artifact = load_lstm_artifact(path)
        return {
            "path": self._to_portable_path(path),
            "path_kind": self._path_kind(path),
            "artifact_version": artifact["artifact_version"],
            "model_version": artifact["model_version"],
            "sequence_length": int(artifact["sequence_length"]),
            "integrity": self._path_integrity_metadata(path),
        }

    def _validate_ensemble_artifact(
        self, raw_path: str | None
    ) -> dict[str, Any] | None:
        """Validate one persisted ensemble JSON artifact and its optional LSTM linkage."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=False)
        artifact = load_ensemble_artifact(path)
        resolved_lstm_artifact_path = None
        raw_lstm_artifact_path = (
            str(artifact.get("lstm_artifact_path") or "").strip() or None
        )

        if raw_lstm_artifact_path:
            dependent_path = self._resolve_dependency_path(
                raw_lstm_artifact_path, base_path=path.parent
            )
            load_lstm_artifact(dependent_path)
            resolved_lstm_artifact_path = self._to_portable_path(dependent_path)

        return {
            "path": self._to_portable_path(path),
            "path_kind": self._path_kind(path),
            "artifact_version": artifact["artifact_version"],
            "model_version": artifact["model_version"],
            "sequence_length": int(artifact["sequence_length"]),
            "momentum_window": int(artifact["momentum_window"]),
            "has_embedded_lstm": isinstance(artifact.get("lstm_artifact"), dict),
            "resolved_lstm_artifact_path": resolved_lstm_artifact_path,
            "integrity": self._path_integrity_metadata(path),
        }

