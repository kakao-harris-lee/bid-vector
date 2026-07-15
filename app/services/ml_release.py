"""Helpers for promoting trained ML artifacts into runtime and embedding flows."""

from __future__ import annotations

import json
import os
import hashlib
import hmac
import shutil
import subprocess
import tarfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib import error, parse, request

from sqlalchemy.orm import Session

from app.ai.predictors.ensemble import load_ensemble_artifact
from app.ai.predictors.lstm import load_lstm_artifact
from app.core.config import settings
from app.core.time import utc_now


class RemoteObjectStorageClient:
    """Small object-storage adapter for file:// and optional S3 release storage."""

    def __init__(self, base_url: str | None) -> None:
        self.base_url = str(base_url or "").strip()
        self.parsed = parse.urlparse(self.base_url)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def describe(self) -> dict[str, Any]:
        """Return non-secret connection metadata for release diagnostics."""
        scheme = self.parsed.scheme.lower()
        provider = "file" if scheme in {"", "file"} else scheme
        description: dict[str, Any] = {
            "enabled": self.enabled,
            "provider": provider if self.enabled else None,
            "base_url": self._redacted_base_url() if self.enabled else "",
        }
        if provider == "s3":
            description["bucket"] = self.parsed.netloc
            description["prefix"] = self.parsed.path.strip("/")
        elif provider == "file" and self.enabled:
            description["path"] = str(self._local_base_path())
        return description

    def preflight(
        self,
        *,
        probe_write: bool = True,
        probe_name: str | None = None,
    ) -> dict[str, Any]:
        """Validate object-storage configuration and optional write permission."""
        checks: list[dict[str, Any]] = []
        description = self.describe()

        if not self.enabled:
            checks.append(
                self._check(
                    "object_storage_configured",
                    False,
                    "not_configured",
                    "ML_RELEASE_OBJECT_STORAGE_URL is not configured.",
                )
            )
            return self._finalize_preflight(description, checks)

        scheme = self.parsed.scheme.lower()
        if scheme not in {"", "file", "s3"}:
            checks.append(
                self._check(
                    "object_storage_provider",
                    False,
                    "unsupported",
                    f"Unsupported ML_RELEASE_OBJECT_STORAGE_URL scheme: {scheme or 'path'}.",
                    provider=scheme or "path",
                )
            )
            return self._finalize_preflight(description, checks)

        checks.append(
            self._check(
                "object_storage_configured",
                True,
                "passed",
                "Object storage URL is configured.",
                **description,
            )
        )
        if scheme in {"", "file"}:
            checks.extend(
                self._preflight_file_storage(
                    probe_write=probe_write, probe_name=probe_name
                )
            )
        elif scheme == "s3":
            checks.extend(
                self._preflight_s3_storage(
                    probe_write=probe_write, probe_name=probe_name
                )
            )
        return self._finalize_preflight(description, checks)

    def put_file(self, source_path: str | Path, *, object_name: str) -> dict[str, Any]:
        """Upload one local file under the configured object prefix."""
        if not self.enabled:
            return {"enabled": False, "uri": None, "object_name": object_name}

        source = Path(source_path).resolve()
        if not source.is_file():
            raise ValueError(f"Object storage upload source must be a file: {source}")

        scheme = self.parsed.scheme.lower()
        if scheme in {"", "file"}:
            destination = self._local_destination(object_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            return {
                "enabled": True,
                "provider": "file",
                "object_name": object_name,
                "uri": destination.as_uri(),
                "bytes": destination.stat().st_size,
            }
        if scheme == "s3":
            return self._put_s3(source, object_name=object_name)
        raise ValueError(
            f"Unsupported ML_RELEASE_OBJECT_STORAGE_URL scheme: {scheme or 'path'}"
        )

    def _preflight_file_storage(
        self,
        *,
        probe_write: bool,
        probe_name: str | None,
    ) -> list[dict[str, Any]]:
        """Check local or mounted object-storage path readiness."""
        checks: list[dict[str, Any]] = []
        base_path = self._local_base_path()
        if base_path.exists() and not base_path.is_dir():
            return [
                self._check(
                    "object_storage_target",
                    False,
                    "invalid_target",
                    f"Object storage target exists but is not a directory: {base_path}",
                    path=str(base_path),
                )
            ]

        checks.append(
            self._check(
                "object_storage_target",
                True,
                "passed",
                "File object storage target can be resolved.",
                path=str(base_path),
                exists=base_path.exists(),
            )
        )
        if not probe_write:
            checks.append(
                self._check(
                    "object_storage_write_probe",
                    True,
                    "skipped",
                    "Object storage write probe was skipped.",
                )
            )
            return checks

        object_name = probe_name or self._default_preflight_object_name()
        destination = self._local_destination(object_name)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(
                    {"probe": "ml-release-rollout", "created_at": utc_now().isoformat()}
                ),
                encoding="utf-8",
            )
            bytes_written = destination.stat().st_size
            destination.unlink(missing_ok=True)
        except OSError as exc:
            checks.append(
                self._check(
                    "object_storage_write_probe",
                    False,
                    "write_failed",
                    f"File object storage write probe failed: {exc}",
                    object_name=object_name,
                    path=str(destination),
                )
            )
            return checks

        checks.append(
            self._check(
                "object_storage_write_probe",
                True,
                "passed",
                "File object storage write/delete probe succeeded.",
                object_name=object_name,
                path=str(destination),
                bytes=bytes_written,
            )
        )
        return checks

    def _preflight_s3_storage(
        self,
        *,
        probe_write: bool,
        probe_name: str | None,
    ) -> list[dict[str, Any]]:
        """Check S3 bucket reachability and optional write permission."""
        checks: list[dict[str, Any]] = []
        bucket = self.parsed.netloc
        prefix = self.parsed.path.strip("/")
        if not bucket:
            return [
                self._check(
                    "object_storage_target",
                    False,
                    "invalid_target",
                    "S3 object storage URL must include a bucket name.",
                )
            ]

        client, client_checks = self._build_s3_client(bucket=bucket, prefix=prefix)
        checks.extend(client_checks)
        if client is None:
            return checks

        bucket_checks = self._preflight_s3_bucket_access(
            client,
            bucket=bucket,
            prefix=prefix,
        )
        checks.extend(bucket_checks)
        if not all(bool(check.get("passed")) for check in bucket_checks):
            return checks

        if not probe_write:
            checks.append(
                self._check(
                    "object_storage_write_probe",
                    True,
                    "skipped",
                    "Object storage write probe was skipped.",
                    bucket=bucket,
                    prefix=prefix,
                )
            )
            return checks

        checks.extend(
            self._preflight_s3_write_probe(
                client,
                bucket=bucket,
                prefix=prefix,
                probe_name=probe_name,
            )
        )
        return checks

    def _build_s3_client(
        self,
        *,
        bucket: str,
        prefix: str,
    ) -> tuple[Any | None, list[dict[str, Any]]]:
        try:
            import boto3  # type: ignore[import-not-found]
            from botocore.exceptions import (  # type: ignore[import-not-found]
                BotoCoreError,
                NoCredentialsError,
                PartialCredentialsError,
            )
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            return None, [
                self._check(
                    "object_storage_dependency",
                    False,
                    "dependency_missing",
                    "boto3 and botocore are required when ML_RELEASE_OBJECT_STORAGE_URL uses s3://.",
                    error=str(exc),
                )
            ]

        try:
            client = boto3.client("s3")
        except (
            NoCredentialsError,
            PartialCredentialsError,
        ) as exc:  # pragma: no cover - depends on deployed env
            return None, [
                self._check(
                    "object_storage_credentials",
                    False,
                    "credentials_missing",
                    "S3 credentials were not found or are incomplete for the active environment.",
                    error=str(exc),
                )
            ]
        except BotoCoreError as exc:  # pragma: no cover - depends on deployed env
            return None, [
                self._check(
                    "object_storage_target",
                    False,
                    "connection_failed",
                    f"S3 client initialization failed: {exc}",
                    bucket=bucket,
                    prefix=prefix,
                )
            ]
        return client, []

    def _preflight_s3_bucket_access(
        self,
        client: Any,
        *,
        bucket: str,
        prefix: str,
    ) -> list[dict[str, Any]]:
        from botocore.exceptions import (  # type: ignore[import-not-found]
            BotoCoreError,
            ClientError,
            NoCredentialsError,
            PartialCredentialsError,
        )

        checks: list[dict[str, Any]] = []
        try:
            client.head_bucket(Bucket=bucket)
        except (
            NoCredentialsError
        ) as exc:  # pragma: no cover - depends on deployed credential chain
            checks.append(
                self._check(
                    "object_storage_credentials",
                    False,
                    "credentials_missing",
                    "S3 credentials were not found for the active environment.",
                    error=str(exc),
                )
            )
            return checks
        except (
            PartialCredentialsError
        ) as exc:  # pragma: no cover - depends on deployed credential chain
            checks.append(
                self._check(
                    "object_storage_credentials",
                    False,
                    "credentials_incomplete",
                    "S3 credentials are incomplete for the active environment.",
                    error=str(exc),
                )
            )
            return checks
        except ClientError as exc:  # pragma: no cover - depends on remote AWS behavior
            checks.append(
                self._s3_client_error_check(
                    "object_storage_target", exc, bucket=bucket, prefix=prefix
                )
            )
            return checks
        except (
            BotoCoreError
        ) as exc:  # pragma: no cover - depends on remote AWS behavior
            checks.append(
                self._check(
                    "object_storage_target",
                    False,
                    "connection_failed",
                    f"S3 bucket preflight failed: {exc}",
                    bucket=bucket,
                    prefix=prefix,
                )
            )
            return checks

        return [
            self._check(
                "object_storage_target",
                True,
                "passed",
                "S3 bucket is reachable.",
                bucket=bucket,
                prefix=prefix,
            )
        ]

    def _preflight_s3_write_probe(
        self,
        client: Any,
        *,
        bucket: str,
        prefix: str,
        probe_name: str | None,
    ) -> list[dict[str, Any]]:
        from botocore.exceptions import (  # type: ignore[import-not-found]
            BotoCoreError,
            ClientError,
            NoCredentialsError,
            PartialCredentialsError,
        )

        checks: list[dict[str, Any]] = []
        object_name = probe_name or self._default_preflight_object_name()
        key = self._s3_key(object_name)
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=json.dumps(
                    {"probe": "ml-release-rollout", "created_at": utc_now().isoformat()}
                ).encode("utf-8"),
                ContentType="application/json",
            )
            client.delete_object(Bucket=bucket, Key=key)
        except (
            NoCredentialsError
        ) as exc:  # pragma: no cover - depends on deployed credential chain
            checks.append(
                self._check(
                    "object_storage_credentials",
                    False,
                    "credentials_missing",
                    "S3 credentials were not found for the active environment.",
                    error=str(exc),
                )
            )
            return checks
        except (
            PartialCredentialsError
        ) as exc:  # pragma: no cover - depends on deployed credential chain
            checks.append(
                self._check(
                    "object_storage_credentials",
                    False,
                    "credentials_incomplete",
                    "S3 credentials are incomplete for the active environment.",
                    error=str(exc),
                )
            )
            return checks
        except ClientError as exc:  # pragma: no cover - depends on remote AWS behavior
            checks.append(
                self._s3_client_error_check(
                    "object_storage_write_probe",
                    exc,
                    bucket=bucket,
                    prefix=prefix,
                    object_name=object_name,
                    key=key,
                )
            )
            return checks
        except (
            BotoCoreError
        ) as exc:  # pragma: no cover - depends on remote AWS behavior
            checks.append(
                self._check(
                    "object_storage_write_probe",
                    False,
                    "write_failed",
                    f"S3 write/delete probe failed: {exc}",
                    bucket=bucket,
                    prefix=prefix,
                    object_name=object_name,
                    key=key,
                )
            )
            return checks

        checks.append(
            self._check(
                "object_storage_write_probe",
                True,
                "passed",
                "S3 write/delete probe succeeded.",
                bucket=bucket,
                prefix=prefix,
                object_name=object_name,
                key=key,
            )
        )
        return checks

    def _s3_client_error_check(
        self,
        name: str,
        exc: Exception,
        **extra: Any,
    ) -> dict[str, Any]:
        """Normalize S3 ClientError details into a rollout check."""
        response = getattr(exc, "response", {}) if exc is not None else {}
        error_payload = response.get("Error", {}) if isinstance(response, dict) else {}
        metadata = (
            response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
        )
        code = str(error_payload.get("Code") or "unknown")
        status_code = (
            metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        )
        normalized_status = (
            "access_denied" if code in {"403", "AccessDenied"} else "request_failed"
        )
        if code in {"404", "NoSuchBucket", "NotFound"}:
            normalized_status = "not_found"
        return self._check(
            name,
            False,
            normalized_status,
            f"S3 object storage preflight failed with {code}.",
            error_code=code,
            http_status_code=status_code,
            **extra,
        )

    def _local_base_path(self) -> Path:
        """Resolve the configured file object-storage base path."""
        if self.parsed.scheme == "file":
            return Path(parse.unquote(self.parsed.path)).resolve()
        return Path(self.base_url).resolve()

    def _local_destination(self, object_name: str) -> Path:
        """Resolve a local object-storage destination."""
        return (self._local_base_path() / object_name).resolve()

    def _put_s3(self, source: Path, *, object_name: str) -> dict[str, Any]:
        """Upload one object to S3 when boto3 is available at runtime."""
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError(
                "boto3 is required when ML_RELEASE_OBJECT_STORAGE_URL uses s3://"
            ) from exc

        bucket = self.parsed.netloc
        key = self._s3_key(object_name)
        boto3.client("s3").upload_file(str(source), bucket, key)
        return {
            "enabled": True,
            "provider": "s3",
            "object_name": object_name,
            "uri": f"s3://{bucket}/{key}",
            "bytes": source.stat().st_size,
        }

    def _s3_key(self, object_name: str) -> str:
        """Build the S3 object key under the configured prefix."""
        prefix = self.parsed.path.strip("/")
        return f"{prefix}/{object_name}".strip("/")

    def _redacted_base_url(self) -> str:
        """Hide URL credentials before returning diagnostics."""
        if not self.base_url:
            return ""
        if not self.parsed.netloc or not (self.parsed.username or self.parsed.password):
            return self.base_url
        hostname = self.parsed.hostname or ""
        if self.parsed.port:
            hostname = f"{hostname}:{self.parsed.port}"
        return parse.urlunparse(
            (
                self.parsed.scheme,
                hostname,
                self.parsed.path,
                self.parsed.params,
                self.parsed.query,
                self.parsed.fragment,
            )
        )

    def _default_preflight_object_name(self) -> str:
        """Return a short-lived object name for rollout write probes."""
        return f"preflight/ml-release-{int(time.time())}-{os.getpid()}.json"

    def _check(
        self,
        name: str,
        passed: bool,
        status: str,
        detail: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build one object-storage preflight check."""
        return {
            "name": name,
            "passed": bool(passed),
            "status": status,
            "detail": detail,
            **extra,
        }

    def _finalize_preflight(
        self,
        description: dict[str, Any],
        checks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a compact object-storage preflight result."""
        passed = all(bool(check.get("passed")) for check in checks)
        return {
            **description,
            "status": "passed" if passed else "failed",
            "passed": passed,
            "checks": checks,
            "failure_reasons": [
                str(check.get("detail"))
                for check in checks
                if not bool(check.get("passed")) and check.get("detail")
            ],
        }


@dataclass(frozen=True, slots=True)
class MLReleasePromotionRequest:
    """Validated inputs for one manifest-backed ML release promotion."""

    release_tag: str
    embedding_model_path: str | None = None
    lstm_artifact_path: str | None = None
    ensemble_artifact_path: str | None = None
    predictor_backtest_report_path: str | None = None
    git_sha: str | None = None
    notes: str | None = None
    rebuild_limit: int = 100
    rebuild_offset: int = 0
    category: str | None = None
    project_status: str | None = None
    force_rebuild: bool = True
    publish_remote: bool = False


class MLReleasePromotionService:
    """Create, validate, and apply manifest-backed ML artifact promotions."""

    PREDICTOR_GATE_POLICY_PRESETS: dict[str, dict[str, Any]] = {
        "advisory": {
            "label": "Advisory rollout",
            "require_report": False,
            "min_sample_count": 1,
            "max_average_absolute_error_rate": 0.06,
            "max_guardrail_rate": 0.5,
            "max_fallback_rate": 0.5,
            "min_dataset_quality_status": "failed",
            "block_on_missing_dataset_quality": False,
        },
        "canary": {
            "label": "Canary rollout",
            "require_report": True,
            "min_sample_count": 3,
            "max_average_absolute_error_rate": 0.04,
            "max_guardrail_rate": 0.35,
            "max_fallback_rate": 0.35,
            "min_dataset_quality_status": "warning",
            "block_on_missing_dataset_quality": False,
        },
        "standard": {
            "label": "Standard rollout",
            "require_report": None,
            "min_sample_count": None,
            "max_average_absolute_error_rate": None,
            "max_guardrail_rate": None,
            "max_fallback_rate": None,
            "min_dataset_quality_status": "warning",
            "block_on_missing_dataset_quality": False,
        },
        "strict": {
            "label": "Strict rollout",
            "require_report": True,
            "min_sample_count": 10,
            "max_average_absolute_error_rate": 0.02,
            "max_guardrail_rate": 0.15,
            "max_fallback_rate": 0.1,
            "min_dataset_quality_status": "passed",
            "block_on_missing_dataset_quality": True,
        },
    }
    DATASET_QUALITY_ORDER = {
        "failed": 0,
        "warning": 1,
        "passed": 2,
    }

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = (
            Path(repo_root)
            if repo_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.repo_root = self.repo_root.resolve()

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

    def preflight_release_rollout(
        self,
        manifest_ref: str | Path | None = None,
        *,
        require_signature: bool | None = None,
        probe_write: bool = True,
    ) -> dict[str, Any]:
        """Validate manifest signature/artifacts and object-storage readiness before rollout."""
        checks: list[dict[str, Any]] = []
        manifest_summary: dict[str, Any] = {
            "configured": bool(manifest_ref),
            "path": None,
            "release_tag": None,
            "signature_status": None,
            "artifact_count": 0,
        }
        signature_required = (
            bool(settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE)
            if require_signature is None
            else bool(require_signature)
        )

        manifest, manifest_path = self._load_rollout_manifest(
            manifest_ref,
            manifest_summary=manifest_summary,
            checks=checks,
        )

        if manifest is not None and manifest_path is not None:
            self._append_manifest_signature_check(
                checks,
                manifest_summary=manifest_summary,
                manifest=manifest,
                manifest_path=manifest_path,
                signature_required=signature_required,
            )
            self._append_manifest_artifact_and_gate_checks(
                checks,
                manifest_summary=manifest_summary,
                manifest=manifest,
            )
            self._append_group_calibration_check(checks, manifest=manifest)

        storage_preflight = RemoteObjectStorageClient(
            settings.ML_RELEASE_OBJECT_STORAGE_URL
        ).preflight(
            probe_write=probe_write,
        )
        checks.extend(storage_preflight.get("checks", []))
        passed = all(bool(check.get("passed")) for check in checks)
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "signature_required": signature_required,
            "probe_write": bool(probe_write),
            "manifest": manifest_summary,
            "object_storage": {
                key: value
                for key, value in storage_preflight.items()
                if key not in {"checks", "failure_reasons"}
            },
            "checks": checks,
            "failure_reasons": [
                str(check.get("detail"))
                for check in checks
                if not bool(check.get("passed")) and check.get("detail")
            ],
        }

    def _load_rollout_manifest(
        self,
        manifest_ref: str | Path | None,
        *,
        manifest_summary: dict[str, Any],
        checks: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, Path | None]:
        if not manifest_ref:
            return None, None
        manifest_path = self._resolve_manifest_path(manifest_ref)
        manifest_summary["path"] = str(manifest_path)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            checks.append(
                self._rollout_check(
                    "manifest_load",
                    False,
                    "not_found",
                    f"Release manifest was not found: {manifest_path}",
                    error=str(exc),
                )
            )
            return None, manifest_path
        except json.JSONDecodeError as exc:
            checks.append(
                self._rollout_check(
                    "manifest_load",
                    False,
                    "invalid_json",
                    f"Release manifest is not valid JSON: {manifest_path}",
                    error=str(exc),
                )
            )
            return None, manifest_path
        if not isinstance(manifest, dict):
            checks.append(
                self._rollout_check(
                    "manifest_load",
                    False,
                    "invalid_json",
                    f"Release manifest must decode to a JSON object: {manifest_path}",
                )
            )
            return None, manifest_path
        manifest_summary["release_tag"] = str(
            manifest.get("release_tag") or manifest_path.stem
        )
        checks.append(
            self._rollout_check(
                "manifest_load",
                True,
                "passed",
                "Release manifest can be loaded.",
                manifest_path=str(manifest_path),
            )
        )
        return manifest, manifest_path

    def _append_manifest_signature_check(
        self,
        checks: list[dict[str, Any]],
        *,
        manifest_summary: dict[str, Any],
        manifest: dict[str, Any],
        manifest_path: Path,
        signature_required: bool,
    ) -> None:
        try:
            verification = self.verify_release_manifest(
                manifest,
                manifest_path=manifest_path,
                require_signature=signature_required,
            )
        except ValueError as exc:
            manifest_summary["signature_status"] = "invalid"
            checks.append(
                self._rollout_check(
                    "manifest_signature",
                    False,
                    "invalid",
                    str(exc),
                    required=signature_required,
                )
            )
            return
        signature_status = "verified" if verification.get("verified") else "missing"
        manifest_summary["signature_status"] = signature_status
        checks.append(
            self._rollout_check(
                "manifest_signature",
                True,
                signature_status,
                (
                    "Release manifest signature is verified."
                    if verification.get("verified")
                    else "Release manifest has no signature and signature is not required."
                ),
                required=signature_required,
                **verification,
            )
        )

    def _append_manifest_artifact_and_gate_checks(
        self,
        checks: list[dict[str, Any]],
        *,
        manifest_summary: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        artifact_checks = self._manifest_artifact_preflight_checks(manifest)
        manifest_summary["artifact_count"] = len(artifact_checks)
        checks.extend(artifact_checks)
        promotion_gate = self._resolve_manifest_promotion_gate(manifest)
        gate_passed = bool(promotion_gate.get("passed"))
        checks.append(
            self._rollout_check(
                "predictor_promotion_gate",
                gate_passed,
                str(
                    promotion_gate.get("status")
                    or ("passed" if gate_passed else "failed")
                ),
                self._promotion_gate_detail(promotion_gate, gate_passed=gate_passed),
                policy=(
                    promotion_gate.get("thresholds", {}).get("policy")
                    if isinstance(promotion_gate.get("thresholds"), dict)
                    else None
                ),
            )
        )

    def _promotion_gate_detail(
        self,
        promotion_gate: dict[str, Any],
        *,
        gate_passed: bool,
    ) -> str:
        if gate_passed:
            return "Predictor promotion gate passed."
        reasons = "; ".join(
            str(reason) for reason in promotion_gate.get("reasons", []) if reason
        )
        return f"Predictor promotion gate failed: {reasons}"

    def _append_group_calibration_check(
        self,
        checks: list[dict[str, Any]],
        *,
        manifest: dict[str, Any],
    ) -> None:
        calibration = (manifest.get("summary") or {}).get("group_calibration") or {}
        if not calibration:
            return
        min_samples = int(settings.GROUP_CALIBRATION_MIN_SAMPLES or 0)
        failing_groups = [
            (group, int((stats or {}).get("sample_count") or 0))
            for group, stats in calibration.items()
            if int((stats or {}).get("sample_count") or 0) < min_samples
        ]
        if failing_groups:
            detail = ", ".join(f"{group}={count}" for group, count in failing_groups)
            checks.append(
                self._rollout_check(
                    "group_calibration_sample_count",
                    False,
                    "failed",
                    f"group_calibration sample_count < {min_samples}: {detail}",
                    threshold=min_samples,
                    failing_groups={group: count for group, count in failing_groups},
                )
            )
            return
        checks.append(
            self._rollout_check(
                "group_calibration_sample_count",
                True,
                "passed",
                f"All group_calibration groups meet sample_count >= {min_samples}.",
                threshold=min_samples,
            )
        )

    def apply_release_manifest(
        self,
        db: Session | None,
        *,
        manifest_ref: str | Path,
        rebuild_embeddings: bool = False,
        limit: int | None = None,
        offset: int | None = None,
        category: str | None = None,
        project_status: str | None = None,
        force: bool | None = None,
        skip_promotion_gate: bool = False,
    ) -> dict[str, Any]:
        """Load a manifest, surface its runtime settings, and optionally rebuild embeddings."""
        manifest, manifest_path = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})
        promotion_gate = self._resolve_manifest_promotion_gate(manifest)
        if not skip_promotion_gate and not bool(promotion_gate.get("passed")):
            reasons = "; ".join(
                str(reason) for reason in promotion_gate.get("reasons", [])
            )
            raise ValueError(
                f"Release manifest failed predictor promotion gate: {reasons or 'unknown failure'}"
            )

        response: dict[str, Any] = {
            "release_tag": manifest.get("release_tag"),
            "manifest_path": str(manifest_path),
            "recommended_docker_target": manifest.get("recommended_docker_target"),
            "recommended_env": recommended_env,
            "promotion_gate": promotion_gate,
            "rebuild_requested": rebuild_embeddings,
            "rebuild_result": None,
        }

        if not rebuild_embeddings:
            return response
        if db is None:
            raise ValueError(
                "A database session is required when rebuild_embeddings=True."
            )
        if not recommended_env.get("CLASSIFIER_EMBEDDING_MODEL"):
            raise ValueError(
                "The selected manifest does not include a local embedding model path to rebuild from."
            )

        rebuild_settings = self._resolve_rebuild_settings(
            manifest,
            limit=limit,
            offset=offset,
            category=category,
            project_status=project_status,
            force=force,
        )

        temporary_settings = self._manifest_settings_overrides(recommended_env)

        try:
            from app.services.project_similarity import ProjectSimilarityService

            with self._temporary_settings(
                temporary_settings
            ), self._temporary_working_directory(self.repo_root):
                rebuild_result = ProjectSimilarityService().rebuild_project_embeddings(
                    db,
                    limit=int(rebuild_settings["limit"]),
                    offset=int(rebuild_settings["offset"]),
                    category=rebuild_settings["category"],
                    project_status=rebuild_settings["project_status"],
                    force=bool(rebuild_settings["force"]),
                )
                db.commit()
        except Exception:
            db.rollback()
            raise

        response["rebuild_result"] = rebuild_result
        response["rebuild_settings"] = rebuild_settings
        return response

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

    def publish_release_manifest(self, manifest_ref: str | Path) -> dict[str, Any]:
        """Publish a signed manifest and referenced artifacts to configured object storage."""
        client = RemoteObjectStorageClient(settings.ML_RELEASE_OBJECT_STORAGE_URL)
        if not client.enabled:
            return {
                "enabled": False,
                "status": "not_configured",
                "detail": "ML_RELEASE_OBJECT_STORAGE_URL is not configured.",
                "objects": [],
            }

        preflight = self.preflight_release_rollout(
            manifest_ref,
            require_signature=settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE,
            probe_write=True,
        )
        if not preflight["passed"]:
            reasons = "; ".join(
                str(reason) for reason in preflight.get("failure_reasons", []) if reason
            )
            return {
                "enabled": True,
                "status": "failed",
                "base_url": client.describe().get("base_url"),
                "release_tag": preflight.get("manifest", {}).get("release_tag"),
                "preflight": preflight,
                "object_count": 0,
                "objects": [],
                "failure_reasons": list(preflight.get("failure_reasons", [])),
                "detail": f"Release rollout preflight failed: {reasons or 'unknown failure'}",
            }

        manifest, manifest_path = self.load_release_manifest(manifest_ref)
        release_tag = str(manifest.get("release_tag") or manifest_path.stem)
        objects: list[dict[str, Any]] = []

        try:
            objects.append(
                client.put_file(
                    manifest_path, object_name=f"manifests/{manifest_path.name}"
                )
            )
            for artifact in self._iter_manifest_artifact_paths(manifest):
                artifact_path = self._resolve_portable_path(artifact["path"])
                if artifact_path.is_dir():
                    bundle_path = self._archive_artifact_directory(
                        artifact_path,
                        release_tag=release_tag,
                        artifact_key=artifact["key"],
                    )
                    object_name = f"artifacts/{release_tag}/{artifact['key']}.tar.gz"
                    objects.append(
                        client.put_file(bundle_path, object_name=object_name)
                    )
                elif artifact_path.is_file():
                    object_name = f"artifacts/{release_tag}/{artifact['key']}/{artifact_path.name}"
                    objects.append(
                        client.put_file(artifact_path, object_name=object_name)
                    )
        except Exception as exc:
            return {
                "enabled": True,
                "status": "failed",
                "base_url": settings.ML_RELEASE_OBJECT_STORAGE_URL,
                "release_tag": release_tag,
                "preflight": preflight,
                "object_count": len(objects),
                "objects": objects,
                "failure_reasons": [str(exc)],
                "detail": f"Release object upload failed: {exc}",
            }

        return {
            "enabled": True,
            "status": "passed",
            "base_url": settings.ML_RELEASE_OBJECT_STORAGE_URL,
            "release_tag": release_tag,
            "preflight": preflight,
            "object_count": len(objects),
            "objects": objects,
        }

    def enforce_manifest_retention(
        self, *, current_manifest_path: Path | None = None
    ) -> dict[str, Any]:
        """Archive older manifests according to the configured local retention limit."""
        retention_limit = int(settings.ML_RELEASE_MANIFEST_RETENTION_LIMIT or 0)
        manifest_dir = self._manifest_dir()
        archive_dir = self._manifest_archive_dir()
        if retention_limit <= 0 or not manifest_dir.exists():
            return {
                "enabled": retention_limit > 0,
                "retention_limit": retention_limit,
                "archived_count": 0,
                "archived_paths": [],
            }

        current_path = (
            current_manifest_path.resolve()
            if current_manifest_path is not None
            else None
        )
        candidates = [
            path
            for path in manifest_dir.glob("*.json")
            if path.is_file()
            and (current_path is None or path.resolve() != current_path)
        ]
        candidates.sort(
            key=lambda path: (path.stat().st_mtime, path.name), reverse=True
        )
        archive_candidates = candidates[max(retention_limit - 1, 0) :]
        archived_paths: list[str] = []
        if archive_candidates:
            archive_dir.mkdir(parents=True, exist_ok=True)

        for path in archive_candidates:
            destination = archive_dir / path.name
            if destination.exists():
                destination = (
                    archive_dir / f"{path.stem}-{int(time.time())}{path.suffix}"
                )
            path.rename(destination)
            archived_paths.append(str(destination))

        return {
            "enabled": True,
            "retention_limit": retention_limit,
            "archived_count": len(archived_paths),
            "archived_paths": archived_paths,
        }

    def restart_compose_services(
        self,
        *,
        services: list[str] | None = None,
        build: bool = True,
    ) -> dict[str, Any]:
        """Restart one or more compose services from the repository root."""
        resolved_services = [
            service for service in (services or ["api"]) if str(service or "").strip()
        ]
        command = ["docker", "compose", "up", "-d"]
        if build:
            command.append("--build")
        command.extend(resolved_services)

        completed = subprocess.run(
            command,
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "docker compose restart failed with exit code "
                f"{completed.returncode}.\nSTDOUT:\n{completed.stdout.strip()}\nSTDERR:\n{completed.stderr.strip()}"
            )

        return {
            "command": " ".join(command),
            "services": resolved_services,
            "build": build,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }

    def wait_for_http_ready(
        self,
        *,
        url: str,
        timeout_seconds: float = 120.0,
        interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Poll one HTTP endpoint until it responds successfully or times out."""
        started_at = time.monotonic()
        last_error: str | None = None

        while True:
            try:
                with request.urlopen(
                    url,
                    timeout=max(
                        1.0,
                        min(
                            timeout_seconds,
                            settings.ML_RELEASE_REMOTE_TRIGGER_PER_REQUEST_CAP_SECONDS,
                        ),
                    ),
                ) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    return {
                        "url": url,
                        "status_code": int(response.getcode()),
                        "body": body,
                        "elapsed_seconds": round(time.monotonic() - started_at, 2),
                    }
            except (
                Exception
            ) as exc:  # pragma: no cover - exercised via timeout/error branches in runtime use
                last_error = str(exc)

            if time.monotonic() - started_at >= timeout_seconds:
                raise RuntimeError(
                    f"Timed out waiting for HTTP readiness at {url} after {timeout_seconds:.1f}s. "
                    f"Last error: {last_error or 'unknown'}"
                )
            time.sleep(interval_seconds)

    def trigger_remote_embedding_rebuild(
        self,
        manifest_ref: str | Path,
        *,
        base_url: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        category: str | None = None,
        project_status: str | None = None,
        force: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Trigger the API-based embedding rebuild endpoint using one stored manifest's defaults."""
        if base_url is None:
            base_url = settings.ML_RELEASE_REMOTE_TRIGGER_BASE_URL
        if timeout_seconds is None:
            timeout_seconds = settings.ML_RELEASE_REMOTE_TRIGGER_TIMEOUT_SECONDS
        manifest, _ = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})
        if not recommended_env.get("CLASSIFIER_EMBEDDING_MODEL"):
            raise ValueError(
                "The selected manifest does not include an embedding model, so remote rebuild is unavailable."
            )

        rebuild_settings = self._resolve_rebuild_settings(
            manifest,
            limit=limit,
            offset=offset,
            category=category,
            project_status=project_status,
            force=force,
        )
        query = {
            "limit": int(rebuild_settings["limit"]),
            "offset": int(rebuild_settings["offset"]),
            "force": "true" if rebuild_settings["force"] else "false",
        }
        if rebuild_settings["category"]:
            query["category"] = str(rebuild_settings["category"])
        if rebuild_settings["project_status"]:
            query["project_status"] = str(rebuild_settings["project_status"])

        target_url = f"{str(base_url).rstrip('/')}/api/v1/ml/backfills/project-embeddings?{parse.urlencode(query)}"
        request_object = request.Request(target_url, method="POST")
        try:
            with request.urlopen(request_object, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed_body = json.loads(body) if body.strip() else None
                return {
                    "url": target_url,
                    "status_code": int(response.getcode()),
                    "response": parsed_body,
                    "rebuild_settings": rebuild_settings,
                }
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Remote embedding rebuild failed with HTTP {exc.code} at {target_url}: {error_body}"
            ) from exc

    def _load_predictor_backtest_report(
        self, raw_path: str | None
    ) -> dict[str, Any] | None:
        """Load an optional predictor backtest report JSON file for release gating."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=False)
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Predictor backtest report is not valid JSON: {path}"
            ) from exc
        if not isinstance(report, dict):
            raise ValueError(
                f"Predictor backtest report must decode to a JSON object: {path}"
            )
        report["report_path"] = self._to_portable_path(path)
        return report

    def _build_predictor_promotion_gate(
        self,
        backtest_report: dict[str, Any] | None,
        *,
        has_predictor_artifact: bool,
    ) -> dict[str, Any]:
        """Build a deterministic pass/fail gate from predictor backtest evidence."""
        thresholds = self._build_predictor_gate_thresholds()
        if not has_predictor_artifact:
            return {
                "status": "not_applicable",
                "passed": True,
                "thresholds": thresholds,
                "metrics": {},
                "best_predictor_key": None,
                "best_predictor_name": None,
                "reasons": ["No predictor artifact is included in this manifest."],
            }

        if backtest_report is None:
            passed = not thresholds["require_report"]
            return {
                "status": "missing_report" if not passed else "not_configured",
                "passed": passed,
                "thresholds": thresholds,
                "metrics": {},
                "best_predictor_key": None,
                "best_predictor_name": None,
                "reasons": [
                    (
                        "Predictor backtest report is required but was not provided."
                        if not passed
                        else "Predictor backtest report was not provided; gate is informational."
                    )
                ],
            }

        metrics = self._extract_predictor_gate_metrics(backtest_report)
        reasons: list[str] = []
        source_status = str(backtest_report.get("status") or "").strip() or "unknown"
        if source_status != "completed":
            reasons.append(f"Backtest status is {source_status}, expected completed.")
        if int(metrics["sample_count"]) < thresholds["min_sample_count"]:
            reasons.append(
                f"Backtest sample count {metrics['sample_count']} is below required {thresholds['min_sample_count']}."
            )

        average_error_rate = metrics["average_absolute_error_rate"]
        if average_error_rate is None:
            reasons.append("Backtest average absolute error rate is missing.")
        elif float(average_error_rate) > thresholds["max_average_absolute_error_rate"]:
            reasons.append(
                "Backtest average absolute error rate "
                f"{float(average_error_rate):.4f} exceeds {thresholds['max_average_absolute_error_rate']:.4f}."
            )

        guardrail_rate = metrics.get("guardrail_rate")
        if (
            guardrail_rate is not None
            and float(guardrail_rate) > thresholds["max_guardrail_rate"]
        ):
            reasons.append(
                f"Guardrail rate {float(guardrail_rate):.4f} exceeds {thresholds['max_guardrail_rate']:.4f}."
            )

        fallback_rate = metrics.get("fallback_rate")
        if (
            fallback_rate is not None
            and float(fallback_rate) > thresholds["max_fallback_rate"]
        ):
            reasons.append(
                f"Fallback rate {float(fallback_rate):.4f} exceeds {thresholds['max_fallback_rate']:.4f}."
            )

        dataset_quality_status = metrics.get("dataset_quality_status")
        dataset_quality_reason = self._dataset_quality_gate_reason(
            dataset_quality_status,
            min_status=str(thresholds["min_dataset_quality_status"]),
            block_on_missing=bool(thresholds["block_on_missing_dataset_quality"]),
        )
        if dataset_quality_reason:
            reasons.append(dataset_quality_reason)

        passed = not reasons
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "source_status": source_status,
            "thresholds": thresholds,
            "metrics": metrics,
            "best_predictor_key": metrics.get("best_predictor_key"),
            "best_predictor_name": metrics.get("best_predictor_name"),
            "report_path": backtest_report.get("report_path"),
            "reasons": reasons or ["Predictor backtest gate passed."],
        }

    def _extract_predictor_gate_metrics(
        self, backtest_report: dict[str, Any]
    ) -> dict[str, Any]:
        """Normalize supported backtest report shapes into promotion-gate metrics."""
        best_predictor_key = (
            str(backtest_report.get("best_predictor_key") or "").strip() or None
        )
        best_result = self._find_backtest_result(
            backtest_report, best_predictor_key=best_predictor_key
        )
        resolved_best_predictor_key = (
            best_predictor_key
            or (
                str(best_result.get("predictor_key") or "").strip()
                if best_result
                else None
            )
            or None
        )
        resolved_best_predictor_name = (
            str(backtest_report.get("best_predictor_name") or "").strip()
            or (
                str(best_result.get("predictor_name") or "").strip()
                if best_result
                else ""
            )
            or None
        )
        sample_count = self._first_int(
            backtest_report.get("sample_count"),
            best_result.get("sample_count") if best_result else None,
            backtest_report.get("holdout_size"),
        )
        average_error_rate = self._first_float(
            backtest_report.get("average_absolute_error_rate"),
            backtest_report.get("best_average_absolute_error_rate"),
            best_result.get("average_absolute_error_rate") if best_result else None,
        )
        guardrail_rate = self._first_float(backtest_report.get("guardrail_rate"))
        fallback_rate = self._first_float(backtest_report.get("fallback_rate"))
        dataset_quality = backtest_report.get("dataset_quality")
        dataset_quality = dataset_quality if isinstance(dataset_quality, dict) else {}
        dataset_quality_status = (
            str(backtest_report.get("dataset_quality_status") or "").strip().lower()
            or str(dataset_quality.get("status") or "").strip().lower()
            or None
        )
        dataset_quality_score = self._first_float(
            backtest_report.get("dataset_quality_score"),
            dataset_quality.get("score"),
        )
        return {
            "sample_count": sample_count,
            "average_absolute_error_rate": average_error_rate,
            "guardrail_rate": guardrail_rate,
            "fallback_rate": fallback_rate,
            "dataset_quality_status": dataset_quality_status,
            "dataset_quality_score": dataset_quality_score,
            "best_predictor_key": resolved_best_predictor_key,
            "best_predictor_name": resolved_best_predictor_name,
        }

    def _find_backtest_result(
        self,
        backtest_report: dict[str, Any],
        *,
        best_predictor_key: str | None,
    ) -> dict[str, Any] | None:
        """Return the backtest result row matching the selected predictor."""
        results = backtest_report.get("results")
        if not isinstance(results, list):
            return None
        for result in results:
            if not isinstance(result, dict):
                continue
            if (
                best_predictor_key
                and str(result.get("predictor_key") or "") == best_predictor_key
            ):
                return result
        completed_results = [
            result
            for result in results
            if isinstance(result, dict)
            and result.get("status") == "completed"
            and result.get("average_absolute_error_rate") is not None
        ]
        if not completed_results:
            return None
        return min(
            completed_results,
            key=lambda result: (
                float(result.get("average_absolute_error_rate") or 1.0),
                -int(result.get("sample_count") or 0),
            ),
        )

    def _build_predictor_gate_thresholds(self) -> dict[str, Any]:
        """Resolve effective predictor gate thresholds from policy and env settings."""
        policy = self._normalize_predictor_gate_policy(
            settings.ML_RELEASE_PREDICTOR_GATE_POLICY
        )
        preset = self.PREDICTOR_GATE_POLICY_PRESETS[policy]
        configured = {
            "require_report": bool(settings.ML_RELEASE_PREDICTOR_GATE_REQUIRE_REPORT),
            "min_sample_count": int(
                settings.ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT or 0
            ),
            "max_average_absolute_error_rate": float(
                settings.ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE
            ),
            "max_guardrail_rate": float(
                settings.ML_RELEASE_PREDICTOR_GATE_MAX_GUARDRAIL_RATE
            ),
            "max_fallback_rate": float(
                settings.ML_RELEASE_PREDICTOR_GATE_MAX_FALLBACK_RATE
            ),
        }
        if policy == "standard":
            effective = dict(configured)
        else:
            effective = {
                "require_report": bool(preset["require_report"]),
                "min_sample_count": int(preset["min_sample_count"]),
                "max_average_absolute_error_rate": float(
                    preset["max_average_absolute_error_rate"]
                ),
                "max_guardrail_rate": float(preset["max_guardrail_rate"]),
                "max_fallback_rate": float(preset["max_fallback_rate"]),
            }

        configured_min_dataset_quality = (
            str(settings.ML_RELEASE_PREDICTOR_GATE_MIN_DATASET_QUALITY_STATUS or "")
            .strip()
            .lower()
        )
        effective["policy"] = policy
        effective["policy_label"] = preset["label"]
        effective["configured_thresholds"] = configured
        effective["min_dataset_quality_status"] = (
            configured_min_dataset_quality
            if configured_min_dataset_quality in self.DATASET_QUALITY_ORDER
            else str(preset["min_dataset_quality_status"])
        )
        effective["block_on_missing_dataset_quality"] = bool(
            preset["block_on_missing_dataset_quality"]
        )
        return effective

    def _normalize_predictor_gate_policy(self, value: Any) -> str:
        """Normalize one release gate policy value into a supported preset key."""
        policy = str(value or "standard").strip().lower().replace("-", "_")
        aliases = {
            "default": "standard",
            "production": "standard",
            "prod": "standard",
            "permissive": "advisory",
            "informational": "advisory",
            "shadow": "advisory",
            "preview": "canary",
            "conservative": "strict",
        }
        policy = aliases.get(policy, policy)
        return policy if policy in self.PREDICTOR_GATE_POLICY_PRESETS else "standard"

    def _dataset_quality_gate_reason(
        self,
        dataset_quality_status: Any,
        *,
        min_status: str,
        block_on_missing: bool,
    ) -> str | None:
        """Return a failure reason when dataset quality is below the active gate policy."""
        required = str(min_status or "warning").strip().lower()
        required_rank = self.DATASET_QUALITY_ORDER.get(required)
        if required_rank is None:
            return None

        current = str(dataset_quality_status or "").strip().lower()
        if not current:
            if block_on_missing:
                return f"Dataset quality status is missing, but policy requires at least {required}."
            return None

        current_rank = self.DATASET_QUALITY_ORDER.get(current)
        if current_rank is None:
            if block_on_missing:
                return f"Dataset quality status '{current}' is unknown, but policy requires at least {required}."
            return None
        if current_rank < required_rank:
            return f"Dataset quality status '{current}' is below required '{required}'."
        return None

    def _resolve_manifest_promotion_gate(
        self, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """Read or reconstruct the predictor promotion gate from a manifest."""
        gate_container = manifest.get("promotion_gate")
        if isinstance(gate_container, dict):
            predictor_gate = gate_container.get("predictor_backtest")
            if isinstance(predictor_gate, dict):
                return predictor_gate

        artifacts = (
            manifest.get("artifacts")
            if isinstance(manifest.get("artifacts"), dict)
            else {}
        )
        predictors = artifacts.get("predictors") if isinstance(artifacts, dict) else {}
        has_predictor_artifact = False
        if isinstance(predictors, dict):
            has_predictor_artifact = any(
                isinstance(predictors.get(key), dict) for key in ("lstm", "ensemble")
            )
        return self._build_predictor_promotion_gate(
            None, has_predictor_artifact=has_predictor_artifact
        )

    def _first_int(self, *values: Any) -> int:
        """Return the first value that can be interpreted as a non-negative integer."""
        for value in values:
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    def _first_float(self, *values: Any) -> float | None:
        """Return the first value that can be interpreted as a float."""
        for value in values:
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

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

    def _sign_manifest(
        self, manifest_without_signature: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a deterministic HMAC signature for one manifest payload."""
        canonical_payload = self._canonical_manifest_payload(manifest_without_signature)
        signing_key = self._manifest_signing_key()
        digest = hmac.new(
            signing_key.encode("utf-8"), canonical_payload, hashlib.sha256
        ).hexdigest()
        return {
            "algorithm": "HMAC-SHA256",
            "key_id": settings.ML_RELEASE_MANIFEST_SIGNING_KEY_ID,
            "signed_at": utc_now().isoformat(),
            "payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
            "digest": digest,
        }

    def _canonical_manifest_payload(
        self, manifest_without_signature: dict[str, Any]
    ) -> bytes:
        """Serialize a manifest without incidental whitespace for signing."""
        return json.dumps(
            manifest_without_signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")

    def _manifest_signing_key(self) -> str:
        """Resolve the configured manifest signing key with a safe development fallback."""
        configured_key = str(settings.ML_RELEASE_MANIFEST_SIGNING_KEY or "").strip()
        if configured_key:
            return configured_key
        if settings.ENVIRONMENT == "production":
            raise ValueError(
                "ML_RELEASE_MANIFEST_SIGNING_KEY is required in production."
            )
        return str(settings.JWT_SECRET_KEY or "development-manifest-signing-key")

    def _iter_manifest_artifact_paths(
        self, manifest: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Return artifact path references from a release manifest."""
        artifacts = manifest.get("artifacts") or {}
        results: list[dict[str, str]] = []
        embedding_model = artifacts.get("embedding_model")
        if isinstance(embedding_model, dict) and embedding_model.get("path"):
            results.append(
                {"key": "embedding_model", "path": str(embedding_model["path"])}
            )

        predictors = artifacts.get("predictors") if isinstance(artifacts, dict) else {}
        if isinstance(predictors, dict):
            for predictor_key in ("lstm", "ensemble"):
                predictor = predictors.get(predictor_key)
                if isinstance(predictor, dict) and predictor.get("path"):
                    results.append(
                        {"key": predictor_key, "path": str(predictor["path"])}
                    )
                if (
                    predictor_key == "ensemble"
                    and isinstance(predictor, dict)
                    and predictor.get("resolved_lstm_artifact_path")
                ):
                    results.append(
                        {
                            "key": "linked_lstm",
                            "path": str(predictor["resolved_lstm_artifact_path"]),
                        }
                    )
        return results

    def _manifest_artifact_preflight_checks(
        self, manifest: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Verify that all manifest artifact references resolve before publish/apply rollout."""
        checks: list[dict[str, Any]] = []
        for artifact in self._iter_manifest_artifact_paths(manifest):
            artifact_path = self._resolve_portable_path(artifact["path"])
            exists = artifact_path.exists()
            path_type = (
                "directory"
                if artifact_path.is_dir()
                else "file" if artifact_path.is_file() else "missing"
            )
            checks.append(
                self._rollout_check(
                    f"artifact_path:{artifact['key']}",
                    exists,
                    "passed" if exists else "not_found",
                    (
                        f"Manifest artifact '{artifact['key']}' is available."
                        if exists
                        else f"Manifest artifact '{artifact['key']}' was not found: {artifact_path}"
                    ),
                    artifact_key=artifact["key"],
                    path=str(artifact_path),
                    path_type=path_type,
                )
            )
        if not checks:
            checks.append(
                self._rollout_check(
                    "artifact_path",
                    True,
                    "not_applicable",
                    "Release manifest does not reference local artifact paths.",
                )
            )
        return checks

    def _archive_artifact_directory(
        self, path: Path, *, release_tag: str, artifact_key: str
    ) -> Path:
        """Create a tarball for one directory artifact before object-storage upload."""
        bundle_dir = self._manifest_dir() / "bundles"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = bundle_dir / f"{release_tag}-{artifact_key}.tar.gz"
        with tarfile.open(bundle_path, "w:gz") as tar:
            tar.add(path, arcname=path.name)
        return bundle_path

    def _path_integrity_metadata(self, path: Path) -> dict[str, Any]:
        """Return deterministic integrity metadata for a file or directory artifact."""
        if path.is_file():
            return {
                "checksum_algorithm": "sha256",
                "sha256": self._sha256_file(path),
                "bytes": path.stat().st_size,
            }
        if path.is_dir():
            file_paths = sorted(item for item in path.rglob("*") if item.is_file())
            digest = hashlib.sha256()
            total_bytes = 0
            for file_path in file_paths:
                relative_name = file_path.relative_to(path).as_posix()
                digest.update(relative_name.encode("utf-8"))
                digest.update(b"\0")
                file_digest = self._sha256_file(file_path)
                digest.update(file_digest.encode("ascii"))
                total_bytes += file_path.stat().st_size
            return {
                "checksum_algorithm": "sha256-tree",
                "sha256": digest.hexdigest(),
                "bytes": total_bytes,
                "file_count": len(file_paths),
            }
        raise ValueError(f"Artifact path must be a file or directory: {path}")

    def _rollout_check(
        self,
        name: str,
        passed: bool,
        status: str,
        detail: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build one rollout preflight check payload."""
        return {
            "name": name,
            "passed": bool(passed),
            "status": status,
            "detail": detail,
            **extra,
        }

    def _sha256_file(self, path: Path) -> str:
        """Hash one file without loading it fully into memory."""
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _manifest_dir(self) -> Path:
        """Return the configured manifest directory."""
        configured_path = Path(settings.ML_RELEASE_MANIFEST_DIR)
        if not configured_path.is_absolute():
            configured_path = self.repo_root / configured_path
        return configured_path.resolve()

    def _manifest_archive_dir(self) -> Path:
        """Return the configured manifest archive directory."""
        configured_path = Path(settings.ML_RELEASE_MANIFEST_ARCHIVE_DIR)
        if not configured_path.is_absolute():
            configured_path = self.repo_root / configured_path
        return configured_path.resolve()

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

    def _stringify_env_value(self, value: Any) -> str:
        """Render manifest values into dotenv-friendly strings."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return ""
        return str(value)

    def _parse_env_key(self, line: str) -> str | None:
        """Extract an env key from one dotenv line while ignoring comments and blanks."""
        stripped_line = str(line or "").strip()
        if (
            not stripped_line
            or stripped_line.startswith("#")
            or "=" not in stripped_line
        ):
            return None
        key, _, _ = stripped_line.partition("=")
        normalized_key = key.strip()
        return normalized_key or None

    def _resolve_rebuild_settings(
        self,
        manifest: dict[str, Any],
        *,
        limit: int | None,
        offset: int | None,
        category: str | None,
        project_status: str | None,
        force: bool | None,
    ) -> dict[str, Any]:
        """Resolve rebuild settings by combining manifest defaults with explicit overrides."""
        rebuild_defaults = manifest.get("rebuild") or {}
        return {
            "limit": int(
                limit
                if limit is not None
                else rebuild_defaults.get("default_limit", 100)
            ),
            "offset": int(
                offset
                if offset is not None
                else rebuild_defaults.get("default_offset", 0)
            ),
            "category": (
                category
                if category is not None
                else rebuild_defaults.get("default_category")
            ),
            "project_status": (
                project_status
                if project_status is not None
                else rebuild_defaults.get("default_project_status")
            ),
            "force": bool(
                force
                if force is not None
                else rebuild_defaults.get("recommended_force", True)
            ),
        }

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

    def _normalize_release_tag(self, value: str) -> str:
        """Normalize and validate one manifest tag for safe filesystem usage."""
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("release_tag is required.")
        if any(token in normalized for token in ("/", "\\", "..")):
            raise ValueError("release_tag must not contain path separators or '..'.")
        return normalized

    def _manifest_path_for_tag(self, release_tag: str) -> Path:
        """Return the canonical manifest file path for one release tag."""
        return self._manifest_dir() / f"{release_tag}.json"

    def _resolve_manifest_path(self, manifest_ref: str | Path) -> Path:
        """Resolve either a direct manifest path or a release tag into a file path."""
        raw_value = str(manifest_ref).strip()
        if not raw_value:
            raise ValueError("manifest_ref is required.")

        candidate = Path(raw_value)
        if candidate.suffix.lower() == ".json" or candidate.exists():
            if not candidate.is_absolute():
                candidate = (self.repo_root / candidate).resolve()
            return candidate
        return self._manifest_path_for_tag(self._normalize_release_tag(raw_value))

    def _resolve_existing_path(self, raw_path: str, *, expect_directory: bool) -> Path:
        """Resolve one repo-relative or absolute path and ensure it exists."""
        path = self._resolve_portable_path(raw_path)
        if not path.exists():
            raise ValueError(f"Artifact path was not found: {path}")
        if expect_directory and not path.is_dir():
            raise ValueError(f"Expected a directory but found a file: {path}")
        if not expect_directory and not path.is_file():
            raise ValueError(f"Expected a file but found a directory: {path}")
        return path

    def _resolve_dependency_path(self, raw_path: str, *, base_path: Path) -> Path:
        """Resolve one nested artifact path relative to its parent file or the repository root."""
        candidate = Path(str(raw_path).strip())
        if candidate.is_absolute():
            if not candidate.exists():
                raise ValueError(f"Dependent artifact path was not found: {candidate}")
            return candidate.resolve()

        relative_to_parent = (base_path / candidate).resolve()
        if relative_to_parent.exists():
            return relative_to_parent

        relative_to_repo = (self.repo_root / candidate).resolve()
        if relative_to_repo.exists():
            return relative_to_repo

        raise ValueError(
            f"Dependent artifact path was not found relative to '{base_path}' or repo root: {raw_path}"
        )

    def _resolve_portable_path(self, raw_path: str) -> Path:
        """Resolve one manifest-friendly path string against the repository root."""
        candidate = Path(str(raw_path).strip())
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        return candidate.resolve()

    def _to_portable_path(self, path: Path) -> str:
        """Prefer repo-relative paths inside manifests when possible."""
        resolved_path = path.resolve()
        try:
            return resolved_path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return str(resolved_path)

    def _path_kind(self, path: Path) -> str:
        """Describe whether a manifest path is repo-relative or absolute."""
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(self.repo_root)
            return "repo-relative"
        except ValueError:
            return "absolute"

    @contextmanager
    def _temporary_settings(self, overrides: dict[str, Any]) -> Iterator[None]:
        """Temporarily override shared settings while applying one release manifest."""
        previous_values = {key: getattr(settings, key) for key in overrides}
        try:
            for key, value in overrides.items():
                setattr(settings, key, value)
            yield
        finally:
            for key, value in previous_values.items():
                setattr(settings, key, value)

    @contextmanager
    def _temporary_working_directory(self, path: Path) -> Iterator[None]:
        """Temporarily run inside the repository root so relative model paths resolve correctly."""
        previous_cwd = Path.cwd()
        try:
            os.chdir(path)
            yield
        finally:
            os.chdir(previous_cwd)
