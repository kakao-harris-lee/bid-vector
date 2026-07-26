"""Object-storage adapter for release artifacts (``file://`` and optional S3).

``RemoteObjectStorageClient`` is moved verbatim from the original module."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib import parse

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

