"""Config resolution and rollout-check builders for release object storage.

``_ObjectStorageBase`` holds ``RemoteObjectStorageClient.__init__``, the
``enabled``/``describe`` connection metadata and the stateless leaf helpers
(local path resolution, S3 key building, URL redaction, probe naming and
rollout-check construction). Every member is moved verbatim from the original
``storage.py`` module."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib import parse


class _ObjectStorageBase:
    """Config resolution and check-building foundation for file:// and optional S3."""

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

    def _local_base_path(self) -> Path:
        """Resolve the configured file object-storage base path."""
        if self.parsed.scheme == "file":
            return Path(parse.unquote(self.parsed.path)).resolve()
        return Path(self.base_url).resolve()

    def _local_destination(self, object_name: str) -> Path:
        """Resolve a local object-storage destination."""
        return (self._local_base_path() / object_name).resolve()

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
