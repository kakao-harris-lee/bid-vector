"""Manifest signing: HMAC signature, canonical payload, and key resolution."""

from __future__ import annotations

import json
import hashlib
import hmac
from typing import Any

from app.core.config import settings
from app.core.time import utc_now
from app.services.ml_release.base import _MLReleaseBase


class _ManifestSigningMixin(_MLReleaseBase):
    """HMAC-SHA256 manifest signing and signing-key resolution."""

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

