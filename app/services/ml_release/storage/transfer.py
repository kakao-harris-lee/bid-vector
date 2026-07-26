"""Object transfer for release object storage (file:// copy and optional S3 upload).

``_ObjectStorageTransferMixin`` owns ``put_file`` and its ``_put_s3`` helper.
Every method body is moved verbatim from the original ``storage.py`` module."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.services.ml_release.storage.base import _ObjectStorageBase


class _ObjectStorageTransferMixin(_ObjectStorageBase):
    """Upload local files under the configured file:// or S3 object prefix."""

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
