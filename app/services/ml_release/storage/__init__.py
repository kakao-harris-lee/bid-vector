"""Object-storage adapter for release artifacts (``file://`` and optional S3).

``RemoteObjectStorageClient`` keeps its historical import path
(``from app.services.ml_release.storage import RemoteObjectStorageClient``).
The single client body was decomposed into responsibility mixins (base config
resolution / preflight validation / object transfer); this ``__init__``
composes them. The split is a pure move — every method body is the original
``RemoteObjectStorageClient`` member, relocated verbatim, so the file:// and
S3 describe/preflight/upload behaviour is byte-identical."""

from __future__ import annotations

from app.services.ml_release.storage.preflight import _ObjectStoragePreflightMixin
from app.services.ml_release.storage.transfer import _ObjectStorageTransferMixin

__all__ = ["RemoteObjectStorageClient"]


class RemoteObjectStorageClient(
    _ObjectStoragePreflightMixin,
    _ObjectStorageTransferMixin,
):
    """Small object-storage adapter for file:// and optional S3 release storage."""
