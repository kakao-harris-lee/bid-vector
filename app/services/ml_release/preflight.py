"""Rollout preflight: manifest/signature/artifact/gate/calibration checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.services.ml_release.base import _MLReleaseBase, build_preflight_check
from app.services.ml_release.contracts import (
    MLReleaseJsonDocument,
    is_json_decode_error,
    json_document_error_detail,
)
from app.services.ml_release.storage import RemoteObjectStorageClient


class _PreflightMixin(_MLReleaseBase):
    """Read-only preflight checks run before applying a release manifest."""

    def preflight_release_rollout(
        self, manifest_ref: str | Path | None = None, *,
        require_signature: bool | None = None,
        probe_write: bool = True, production: bool = False,
        expected_git_sha: str | None = None,
    ) -> dict[str, Any]:
        """Validate manifest signature/artifacts and object-storage readiness before rollout."""
        checks: list[dict[str, Any]] = []
        manifest_summary: dict[str, Any] = dict(
            configured=bool(manifest_ref), path=None, release_tag=None,
            signature_status=None, artifact_count=0,
        )
        signature_required = (
            bool(settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE)
            if require_signature is None
            else bool(require_signature)
        )

        manifest, manifest_path = self._load_rollout_manifest(
            manifest_ref, manifest_summary=manifest_summary, checks=checks
        )

        if manifest is not None and manifest_path is not None:
            self._append_manifest_signature_check(
                checks, manifest_summary=manifest_summary, manifest=manifest,
                manifest_path=manifest_path,
                signature_required=signature_required,
            )
            self._append_manifest_artifact_and_gate_checks(
                checks, manifest_summary=manifest_summary, manifest=manifest,
                production=production,
            )
            checks.append(self._manifest_git_sha_check(
                manifest_git_sha=manifest.get("git_sha"),
                expected_git_sha=expected_git_sha, required=production,
            ))
            self._append_group_calibration_check(checks, manifest=manifest)

        storage_preflight = RemoteObjectStorageClient(
            settings.ML_RELEASE_OBJECT_STORAGE_URL
        ).preflight(
            probe_write=probe_write,
        )
        checks.extend(storage_preflight.get("checks", []))
        return self._build_preflight_result(
            checks=checks, manifest_summary=manifest_summary,
            storage_preflight=storage_preflight,
            signature_required=signature_required, probe_write=probe_write,
            production=production,
        )

    @staticmethod
    def _build_preflight_result(
        *,
        checks: list[dict[str, Any]],
        manifest_summary: dict[str, Any],
        storage_preflight: dict[str, Any],
        signature_required: bool,
        probe_write: bool,
        production: bool,
    ) -> dict[str, Any]:
        """Assemble the preflight result payload from the accumulated checks/summaries.

        ``passed`` is the AND of every check; ``failure_reasons`` mirrors the failing
        checks' details. Field set and pass/fail semantics are unchanged from the inline
        return it replaces.
        """
        passed = all(bool(check.get("passed")) for check in checks)
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "signature_required": signature_required,
            "probe_write": bool(probe_write),
            "production": bool(production),
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

    def _manifest_git_sha_check(
        self,
        *,
        manifest_git_sha: str | None,
        expected_git_sha: str | None,
        required: bool,
    ) -> dict[str, bool | str | None]:
        expected = str(expected_git_sha or "").strip()
        actual = str(manifest_git_sha or "").strip()
        if not expected and not required:
            return build_preflight_check(
                "manifest_git_sha", True, "not_required",
                "Manifest git SHA comparison was not requested.",
            )
        passed = bool(expected and actual and actual == expected)
        return build_preflight_check(
            "manifest_git_sha", passed, "passed" if passed else "mismatch",
            (
                "Release manifest git SHA matches the deployment SHA."
                if passed
                else "Production release requires an exact manifest/deployment git SHA match."
            ),
            expected_git_sha=expected or None,
            manifest_git_sha=actual or None,
        )

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
            manifest_text = manifest_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            checks.append(
                build_preflight_check(
                    "manifest_load",
                    False,
                    "not_found",
                    f"Release manifest was not found: {manifest_path}",
                    error=str(exc),
                )
            )
            return None, manifest_path
        # 원문 매핑 그대로 복원한다 — 이 뒤에서 서명을 재계산해 비교하므로 키가 하나라도
        # 추가/누락되면 검증이 깨진다(contracts.MLReleaseJsonDocument 참고).
        try:
            manifest = MLReleaseJsonDocument.model_validate_json(manifest_text).root
        except ValidationError as exc:
            if is_json_decode_error(exc):
                checks.append(
                    build_preflight_check(
                        "manifest_load",
                        False,
                        "invalid_json",
                        f"Release manifest is not valid JSON: {manifest_path}",
                        error=json_document_error_detail(exc),
                    )
                )
                return None, manifest_path
            checks.append(
                build_preflight_check(
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
            build_preflight_check(
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
                build_preflight_check(
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
            build_preflight_check(
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
        production: bool,
    ) -> None:
        artifact_checks = self._manifest_artifact_preflight_checks(
            manifest, require_integrity=production
        )
        manifest_summary["artifact_count"] = len(artifact_checks)
        checks.extend(artifact_checks)
        promotion_gate = self._resolve_manifest_promotion_gate(manifest)
        gate_passed = bool(promotion_gate.get("passed"))
        checks.append(
            build_preflight_check(
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
        if production:
            checks.append(
                self._production_predictor_backtest_check(
                    manifest=MLReleaseJsonDocument(root=manifest),
                    promotion_gate=MLReleaseJsonDocument(root=promotion_gate),
                ).root
            )

    def _production_predictor_backtest_check(
        self, *, manifest: MLReleaseJsonDocument,
        promotion_gate: MLReleaseJsonDocument,
    ) -> MLReleaseJsonDocument:
        manifest_payload, gate_payload = manifest.root, promotion_gate.root
        artifacts = manifest_payload.get("artifacts")
        artifacts = artifacts if isinstance(artifacts, dict) else {}
        predictors = artifacts.get("predictors")
        predictors = predictors if isinstance(predictors, dict) else {}
        has_predictor = any(isinstance(predictors.get(key), dict)
                            for key in ("lstm", "ensemble"))
        if not has_predictor:
            return MLReleaseJsonDocument(root=build_preflight_check(
                "production_predictor_backtest",
                True,
                "not_applicable",
                "No predictor artifact is included in this production release.",
            ))
        metrics = gate_payload.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        report_integrity = gate_payload.get("report_integrity")
        report_sha256 = (
            str(report_integrity.get("sha256") or "") or None
            if isinstance(report_integrity, dict) else None
        )
        sample_count = int(metrics.get("sample_count") or 0)
        dataset_quality_status = (
            str(metrics.get("dataset_quality_status") or "") or None
        )
        passed = bool(
            gate_payload.get("passed") and gate_payload.get("status") == "passed"
            and gate_payload.get("report_path")
            and report_sha256 and sample_count > 0
            and metrics.get("average_absolute_error_rate") is not None
            and metrics.get("guardrail_rate") is not None
            and metrics.get("fallback_rate") is not None
            and dataset_quality_status
        )
        return MLReleaseJsonDocument(root=build_preflight_check(
            "production_predictor_backtest",
            passed,
            "passed" if passed else "missing_or_unverified",
            (
                "Production predictor release has a passing, checksummed backtest."
                if passed
                else "Production predictor release requires complete quality, error, guardrail, fallback, sample, and checksum evidence."
            ),
            sample_count=sample_count,
            dataset_quality_status=dataset_quality_status,
        ))

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
                build_preflight_check(
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
            build_preflight_check(
                "group_calibration_sample_count",
                True,
                "passed",
                f"All group_calibration groups meet sample_count >= {min_samples}.",
                threshold=min_samples,
            )
        )

    def _manifest_artifact_preflight_checks(
        self,
        manifest: dict[str, Any],
        *,
        require_integrity: bool = False,
    ) -> list[dict[str, Any]]:
        """Verify that all manifest artifact references resolve before publish/apply rollout."""
        checks: list[dict[str, Any]] = []
        for artifact in self._iter_manifest_artifact_paths(manifest):
            artifact_path = self._resolve_portable_path(artifact["path"])
            exists = artifact_path.exists()
            expected_integrity = artifact.get("integrity")
            integrity_present = bool(
                isinstance(expected_integrity, dict)
                and expected_integrity.get("sha256")
            )
            actual_integrity = (
                self._path_integrity_metadata(artifact_path) if exists else None
            )
            integrity_matches = (
                not require_integrity
                if not integrity_present
                else actual_integrity == expected_integrity
            )
            path_type = (
                "directory"
                if artifact_path.is_dir()
                else "file" if artifact_path.is_file() else "missing"
            )
            checks.append(
                build_preflight_check(
                    f"artifact_path:{artifact['key']}",
                    exists and integrity_matches,
                    (
                        "passed"
                        if exists and integrity_matches
                        else "checksum_missing"
                        if exists and not integrity_present
                        else "checksum_mismatch" if exists else "not_found"
                    ),
                    (
                        f"Manifest artifact '{artifact['key']}' is available and matches its checksum."
                        if exists and integrity_matches
                        else f"Manifest artifact '{artifact['key']}' has no signed checksum."
                        if exists and not integrity_present
                        else f"Manifest artifact '{artifact['key']}' checksum does not match the signed manifest."
                        if exists
                        else f"Manifest artifact '{artifact['key']}' was not found: {artifact_path}"
                    ),
                    artifact_key=artifact["key"],
                    path=str(artifact_path),
                    path_type=path_type,
                    expected_sha256=(
                        expected_integrity.get("sha256")
                        if isinstance(expected_integrity, dict)
                        else None
                    ),
                    actual_sha256=(
                        actual_integrity.get("sha256")
                        if isinstance(actual_integrity, dict)
                        else None
                    ),
                )
            )
        if not checks:
            checks.append(
                build_preflight_check(
                    "artifact_path",
                    True,
                    "not_applicable",
                    "Release manifest does not reference local artifact paths.",
                )
            )
        return checks
