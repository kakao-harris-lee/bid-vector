"""Rollout preflight: manifest/signature/artifact/gate/calibration checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.ml_release.base import _MLReleaseBase
from app.services.ml_release.storage import RemoteObjectStorageClient


class _PreflightMixin(_MLReleaseBase):
    """Read-only preflight checks run before applying a release manifest."""

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

