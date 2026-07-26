"""Manifest-backed ML artifact promotion — public surface.

``MLReleasePromotionService``, ``MLReleasePromotionRequest`` and
``RemoteObjectStorageClient`` keep their historical import path
(``from app.services.ml_release import ...``). The service body was
decomposed into responsibility mixins (base / signing / gate / preflight /
manifest / rollout); this ``__init__`` composes them. The split is a pure
move — every method body is the original ``MLReleasePromotionService``
member, relocated verbatim, so signing, gate judgement and sha256
verification are byte-identical and behaviour is unchanged."""

from __future__ import annotations

# ``app.services.ml_release.subprocess`` / ``.request`` are the historical
# monkeypatch surface: tests do ``setattr('app.services.ml_release.subprocess
# .run', ...)``. These re-exports keep those attributes resolvable on the
# package; the rollout mixin calls subprocess.run / request.urlopen on the
# same shared module objects, so the patches still take effect. Listed in
# ``__all__`` to mark them as the intentional re-export surface.
import subprocess
from urllib import request

from app.services.ml_release.base import MLReleasePromotionRequest
from app.services.ml_release.gate import _PromotionGateMixin
from app.services.ml_release.manifest import _ManifestLifecycleMixin
from app.services.ml_release.preflight import _PreflightMixin
from app.services.ml_release.rollout import _RolloutMixin
from app.services.ml_release.signing import _ManifestSigningMixin
from app.services.ml_release.storage import RemoteObjectStorageClient

__all__ = [
    "MLReleasePromotionRequest",
    "MLReleasePromotionService",
    "RemoteObjectStorageClient",
    "request",
    "subprocess",
]


class MLReleasePromotionService(
    _ManifestSigningMixin,
    _ManifestLifecycleMixin,
    _PromotionGateMixin,
    _PreflightMixin,
    _RolloutMixin,
):
    """Create, validate, and apply manifest-backed ML artifact promotions."""
