"""Preflight validation for release object storage (file:// and optional S3).

``_ObjectStoragePreflightMixin`` owns ``preflight`` and its file/S3 helpers:
target readiness, boto3 client construction, bucket-access probing, write/
delete probes and ``ClientError`` normalization. Every method body is moved
verbatim from the original ``storage.py`` module."""

from __future__ import annotations

import json
from typing import Any

from app.core.time import utc_now
from app.services.ml_release.storage.base import _ObjectStorageBase


class _ObjectStoragePreflightMixin(_ObjectStorageBase):
    """Validate object-storage configuration and optional write permission."""

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
