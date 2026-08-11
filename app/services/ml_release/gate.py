"""Predictor promotion gate: backtest-report evaluation and threshold policy."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.core.constants import AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
from app.services.ml_release.base import MANIFEST_PREDICTOR_KEYS, _MLReleaseBase
from app.services.ml_release.contracts import (
    MLReleaseJsonDocument,
    is_json_decode_error,
)


class _PromotionGateMixin(_MLReleaseBase):
    """Evaluate predictor backtest reports against promotion-gate thresholds."""

    def _promotion_gate_detail(
        self,
        promotion_gate: dict[str, Any],
        *,
        gate_passed: bool,
    ) -> str:
        if gate_passed:
            return "Predictor promotion gate passed."
        reasons = "; ".join(
            str(reason) for reason in promotion_gate.get("reasons", []) if reason
        )
        return f"Predictor promotion gate failed: {reasons}"

    def _load_predictor_backtest_report(
        self, raw_path: str | None
    ) -> dict[str, Any] | None:
        """Load an optional predictor backtest report JSON file for release gating."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=False)
        try:
            report = MLReleaseJsonDocument.model_validate_json(
                path.read_text(encoding="utf-8")
            ).root
        except ValidationError as exc:
            if is_json_decode_error(exc):
                raise ValueError(
                    f"Predictor backtest report is not valid JSON: {path}"
                ) from exc
            raise ValueError(
                f"Predictor backtest report must decode to a JSON object: {path}"
            ) from exc
        report["report_path"] = self._to_portable_path(path)
        report["report_integrity"] = self._path_integrity_metadata(path)
        return report

    def _build_predictor_promotion_gate(
        self,
        backtest_report: dict[str, Any] | None,
        *,
        has_predictor_artifact: bool,
    ) -> dict[str, Any]:
        """Build a deterministic pass/fail gate from predictor backtest evidence."""
        thresholds = self._build_predictor_gate_thresholds()
        if not has_predictor_artifact:
            return {
                "status": "not_applicable",
                "passed": True,
                "thresholds": thresholds,
                "metrics": {},
                "best_predictor_key": None,
                "best_predictor_name": None,
                "reasons": ["No predictor artifact is included in this manifest."],
            }

        if backtest_report is None:
            passed = not thresholds["require_report"]
            return {
                "status": "missing_report" if not passed else "not_configured",
                "passed": passed,
                "thresholds": thresholds,
                "metrics": {},
                "best_predictor_key": None,
                "best_predictor_name": None,
                "reasons": [
                    (
                        "Predictor backtest report is required but was not provided."
                        if not passed
                        else "Predictor backtest report was not provided; gate is informational."
                    )
                ],
            }

        metrics = self._extract_predictor_gate_metrics(backtest_report)
        reasons: list[str] = []
        source_status = str(backtest_report.get("status") or "").strip() or "unknown"
        if source_status != "completed":
            reasons.append(f"Backtest status is {source_status}, expected completed.")
        if int(metrics["sample_count"]) < thresholds["min_sample_count"]:
            reasons.append(
                f"Backtest sample count {metrics['sample_count']} is below required {thresholds['min_sample_count']}."
            )

        average_error_rate = metrics["average_absolute_error_rate"]
        if average_error_rate is None:
            reasons.append("Backtest average absolute error rate is missing.")
        elif float(average_error_rate) > thresholds["max_average_absolute_error_rate"]:
            reasons.append(
                "Backtest average absolute error rate "
                f"{float(average_error_rate):.4f} exceeds {thresholds['max_average_absolute_error_rate']:.4f}."
            )

        guardrail_rate = metrics.get("guardrail_rate")
        if (
            guardrail_rate is not None
            and float(guardrail_rate) > thresholds["max_guardrail_rate"]
        ):
            reasons.append(
                f"Guardrail rate {float(guardrail_rate):.4f} exceeds {thresholds['max_guardrail_rate']:.4f}."
            )

        fallback_rate = metrics.get("fallback_rate")
        if (
            fallback_rate is not None
            and float(fallback_rate) > thresholds["max_fallback_rate"]
        ):
            reasons.append(
                f"Fallback rate {float(fallback_rate):.4f} exceeds {thresholds['max_fallback_rate']:.4f}."
            )

        dataset_quality_status = metrics.get("dataset_quality_status")
        dataset_quality_reason = self._dataset_quality_gate_reason(
            dataset_quality_status,
            min_status=str(thresholds["min_dataset_quality_status"]),
            block_on_missing=bool(thresholds["block_on_missing_dataset_quality"]),
        )
        if dataset_quality_reason:
            reasons.append(dataset_quality_reason)

        passed = not reasons
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "source_status": source_status,
            "thresholds": thresholds,
            "metrics": metrics,
            "best_predictor_key": metrics.get("best_predictor_key"),
            "best_predictor_name": metrics.get("best_predictor_name"),
            "report_path": backtest_report.get("report_path"),
            "report_integrity": backtest_report.get("report_integrity"),
            "reasons": reasons or ["Predictor backtest gate passed."],
        }

    def _extract_predictor_gate_metrics(
        self, backtest_report: dict[str, Any]
    ) -> dict[str, Any]:
        """Normalize supported backtest report shapes into promotion-gate metrics.

        자동 승격 제외 키(AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS)가 리포트의 best 로
        선언돼 있으면 **그 arm 에서 유도되는 값 전부**(key·name·best 오차·top-level
        guardrail/fallback rate)를 불신하고, 제외되지 않은 completed 결과에서 다시
        고른다 — 서빙되지 않을 엔진의 성적으로 pass/fail 도장이 찍히는 것을 막는다
        (리뷰 K4). fresh 리포트는 상류가 이미 거르므로 이 분기는 수제·스테일 리포트
        전용 방어다.
        """
        best_predictor_key = (
            str(backtest_report.get("best_predictor_key") or "").strip() or None
        )
        best_key_excluded = best_predictor_key in AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
        if best_key_excluded:
            best_predictor_key = None
        best_result = self._find_backtest_result(
            backtest_report, best_predictor_key=best_predictor_key
        )
        resolved_best_predictor_key = (
            best_predictor_key
            or (
                str(best_result.get("predictor_key") or "").strip()
                if best_result
                else None
            )
            or None
        )
        resolved_best_predictor_name = (
            (
                ""
                if best_key_excluded
                else str(backtest_report.get("best_predictor_name") or "").strip()
            )
            or (
                str(best_result.get("predictor_name") or "").strip()
                if best_result
                else ""
            )
            or None
        )
        sample_count = self._first_int(
            None if best_key_excluded else backtest_report.get("sample_count"),
            best_result.get("sample_count") if best_result else None,
            backtest_report.get("holdout_size"),
        )
        average_error_rate = self._first_float(
            None if best_key_excluded else backtest_report.get("average_absolute_error_rate"),
            None if best_key_excluded else backtest_report.get("best_average_absolute_error_rate"),
            best_result.get("average_absolute_error_rate") if best_result else None,
        )
        guardrail_rate = self._first_float(
            None if best_key_excluded else backtest_report.get("guardrail_rate")
        )
        fallback_rate = self._first_float(
            None if best_key_excluded else backtest_report.get("fallback_rate")
        )
        dataset_quality = backtest_report.get("dataset_quality")
        dataset_quality = dataset_quality if isinstance(dataset_quality, dict) else {}
        report_settings = backtest_report.get("settings")
        report_settings = report_settings if isinstance(report_settings, dict) else {}
        dataset_quality_status = (
            str(backtest_report.get("dataset_quality_status") or "").strip().lower()
            or str(dataset_quality.get("status") or "").strip().lower()
            or None
        )
        dataset_quality_score = self._first_float(
            backtest_report.get("dataset_quality_score"),
            dataset_quality.get("score"),
        )
        return {
            "sample_count": sample_count,
            "average_absolute_error_rate": average_error_rate,
            "guardrail_rate": guardrail_rate,
            "fallback_rate": fallback_rate,
            "dataset_quality_status": dataset_quality_status,
            "dataset_quality_score": dataset_quality_score,
            "base_amount_basis": (
                str(report_settings.get("base_amount_basis") or "").strip() or None
            ),
            "best_predictor_key": resolved_best_predictor_key,
            "best_predictor_name": resolved_best_predictor_name,
        }

    def _find_backtest_result(
        self,
        backtest_report: dict[str, Any],
        *,
        best_predictor_key: str | None,
    ) -> dict[str, Any] | None:
        """Return the backtest result row matching the selected predictor."""
        results = backtest_report.get("results")
        if not isinstance(results, list):
            return None
        for result in results:
            if not isinstance(result, dict):
                continue
            if (
                best_predictor_key
                and str(result.get("predictor_key") or "") == best_predictor_key
            ):
                return result
        completed_results = [
            result
            for result in results
            if isinstance(result, dict)
            and result.get("status") == "completed"
            and result.get("average_absolute_error_rate") is not None
            # 자동 승격 제외 arm 은 results 에 남아 있어도(비교 증적) 폴백 best 후보로
            # 승격되면 안 된다(리뷰 K4).
            and str(result.get("predictor_key") or "")
            not in AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
        ]
        if not completed_results:
            return None
        return min(
            completed_results,
            key=lambda result: (
                float(result.get("average_absolute_error_rate") or 1.0),
                -int(result.get("sample_count") or 0),
            ),
        )

    def _build_predictor_gate_thresholds(self) -> dict[str, Any]:
        """Resolve effective predictor gate thresholds from policy and env settings."""
        policy = self._normalize_predictor_gate_policy(
            settings.ML_RELEASE_PREDICTOR_GATE_POLICY
        )
        preset = self.PREDICTOR_GATE_POLICY_PRESETS[policy]
        configured = {
            "require_report": bool(settings.ML_RELEASE_PREDICTOR_GATE_REQUIRE_REPORT),
            "min_sample_count": int(
                settings.ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT or 0
            ),
            "max_average_absolute_error_rate": float(
                settings.ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE
            ),
            "max_guardrail_rate": float(
                settings.ML_RELEASE_PREDICTOR_GATE_MAX_GUARDRAIL_RATE
            ),
            "max_fallback_rate": float(
                settings.ML_RELEASE_PREDICTOR_GATE_MAX_FALLBACK_RATE
            ),
        }
        if policy == "standard":
            effective = dict(configured)
        else:
            effective = {
                "require_report": bool(preset["require_report"]),
                "min_sample_count": int(preset["min_sample_count"]),
                "max_average_absolute_error_rate": float(
                    preset["max_average_absolute_error_rate"]
                ),
                "max_guardrail_rate": float(preset["max_guardrail_rate"]),
                "max_fallback_rate": float(preset["max_fallback_rate"]),
            }

        configured_min_dataset_quality = (
            str(settings.ML_RELEASE_PREDICTOR_GATE_MIN_DATASET_QUALITY_STATUS or "")
            .strip()
            .lower()
        )
        effective["policy"] = policy
        effective["policy_label"] = preset["label"]
        effective["configured_thresholds"] = configured
        effective["min_dataset_quality_status"] = (
            configured_min_dataset_quality
            if configured_min_dataset_quality in self.DATASET_QUALITY_ORDER
            else str(preset["min_dataset_quality_status"])
        )
        effective["block_on_missing_dataset_quality"] = bool(
            preset["block_on_missing_dataset_quality"]
        )
        return effective

    def _normalize_predictor_gate_policy(self, value: Any) -> str:
        """Normalize one release gate policy value into a supported preset key."""
        policy = str(value or "standard").strip().lower().replace("-", "_")
        aliases = {
            "default": "standard",
            "production": "standard",
            "prod": "standard",
            "permissive": "advisory",
            "informational": "advisory",
            "shadow": "advisory",
            "preview": "canary",
            "conservative": "strict",
        }
        policy = aliases.get(policy, policy)
        return policy if policy in self.PREDICTOR_GATE_POLICY_PRESETS else "standard"

    def _dataset_quality_gate_reason(
        self,
        dataset_quality_status: Any,
        *,
        min_status: str,
        block_on_missing: bool,
    ) -> str | None:
        """Return a failure reason when dataset quality is below the active gate policy."""
        required = str(min_status or "warning").strip().lower()
        required_rank = self.DATASET_QUALITY_ORDER.get(required)
        if required_rank is None:
            return None

        current = str(dataset_quality_status or "").strip().lower()
        if not current:
            if block_on_missing:
                return f"Dataset quality status is missing, but policy requires at least {required}."
            return None

        current_rank = self.DATASET_QUALITY_ORDER.get(current)
        if current_rank is None:
            if block_on_missing:
                return f"Dataset quality status '{current}' is unknown, but policy requires at least {required}."
            return None
        if current_rank < required_rank:
            return f"Dataset quality status '{current}' is below required '{required}'."
        return None

    def _resolve_manifest_promotion_gate(
        self, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """Read or reconstruct the predictor promotion gate from a manifest."""
        gate_container = manifest.get("promotion_gate")
        if isinstance(gate_container, dict):
            predictor_gate = gate_container.get("predictor_backtest")
            if isinstance(predictor_gate, dict):
                return predictor_gate

        artifacts = (
            manifest.get("artifacts")
            if isinstance(manifest.get("artifacts"), dict)
            else {}
        )
        predictors = artifacts.get("predictors") if isinstance(artifacts, dict) else {}
        has_predictor_artifact = False
        if isinstance(predictors, dict):
            has_predictor_artifact = any(
                isinstance(predictors.get(key), dict) for key in MANIFEST_PREDICTOR_KEYS
            )
        return self._build_predictor_promotion_gate(
            None, has_predictor_artifact=has_predictor_artifact
        )
