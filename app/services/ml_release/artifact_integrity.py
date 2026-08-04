"""Manifest artifact integrity verdicts — declarative statuses and detail text.

Rollout preflight resolves every manifest artifact path and, when the manifest
carries a signed ``integrity`` block, recomputes the artifact's sha256 to compare
with it. This module owns what those outcomes are *called* and what they are
allowed to *claim*.

The detail text asserts only what was actually verified: an artifact whose
checksum was never recomputed must not be reported as "matches its checksum", or
the rollout audit trail records a verification that never happened. A missing
integrity block is therefore its own verdict (``checksum_missing``) which passes
only when the rollout does not require integrity.

Pure functions (no I/O).
"""

from __future__ import annotations

from dataclasses import dataclass

ARTIFACT_STATUS_PASSED = "passed"
ARTIFACT_STATUS_CHECKSUM_MISSING = "checksum_missing"
ARTIFACT_STATUS_CHECKSUM_MISMATCH = "checksum_mismatch"
ARTIFACT_STATUS_NOT_FOUND = "not_found"

# 문구 템플릿(선언). ``{key}`` 는 아티팩트 키, ``{path}`` 는 해석된 경로다.
_DETAIL_PASSED = (
    "Manifest artifact '{key}' is available and matches its signed checksum."
)
_DETAIL_CHECKSUM_OPTIONAL = (
    "Manifest artifact '{key}' is available but has no signed checksum "
    "(integrity is not required for this rollout)."
)
_DETAIL_CHECKSUM_REQUIRED = (
    "Manifest artifact '{key}' has no signed checksum, which this rollout requires."
)
_DETAIL_MISMATCH = (
    "Manifest artifact '{key}' checksum does not match the signed manifest."
)
_DETAIL_NOT_FOUND = "Manifest artifact '{key}' was not found: {path}"


@dataclass(frozen=True, slots=True)
class ArtifactIntegrityVerdict:
    """한 아티팩트의 존재·체크섬 판정과 그 문구.

    ``checksum_verified`` 는 체크섬을 실제로 재계산해 비교했는지를 남긴다 — 통과
    여부와 별개 축이라, 무결성이 요구되지 않아 통과한 건과 검증하고 통과한 건이
    증적에서 구분된다.
    """

    status: str
    passed: bool
    checksum_verified: bool
    detail_template: str


def resolve_artifact_integrity_verdict(
    *,
    exists: bool,
    integrity_present: bool,
    integrity_matches: bool,
    require_integrity: bool,
) -> ArtifactIntegrityVerdict:
    """Resolve one artifact's preflight verdict from what was actually verified."""
    if not exists:
        return ArtifactIntegrityVerdict(
            ARTIFACT_STATUS_NOT_FOUND, False, False, _DETAIL_NOT_FOUND
        )
    if not integrity_present:
        return ArtifactIntegrityVerdict(
            ARTIFACT_STATUS_CHECKSUM_MISSING,
            not require_integrity,
            False,
            (
                _DETAIL_CHECKSUM_REQUIRED
                if require_integrity
                else _DETAIL_CHECKSUM_OPTIONAL
            ),
        )
    if not integrity_matches:
        return ArtifactIntegrityVerdict(
            ARTIFACT_STATUS_CHECKSUM_MISMATCH, False, True, _DETAIL_MISMATCH
        )
    return ArtifactIntegrityVerdict(
        ARTIFACT_STATUS_PASSED, True, True, _DETAIL_PASSED
    )
