"""ML release / backtest manifest reporting mixin."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.ml_release import MLReleasePromotionService


class _MLReleaseReportMixin:
    """ML release / backtest manifest summary and dashboard cards."""

    def _build_ml_release_summary(self, *, recent_limit: int) -> dict[str, Any]:
        """Summarize local ML release manifests and predictor promotion gates."""
        manifest_dir = self._ml_manifest_dir()
        manifest_paths = list(manifest_dir.glob("*.json") if manifest_dir.exists() else [])
        manifest_summaries = [self._read_manifest_summary(path) for path in manifest_paths]
        manifest_summaries.sort(key=self._manifest_recency_key, reverse=True)
        recent_manifests = manifest_summaries[:recent_limit]
        latest = recent_manifests[0] if recent_manifests else None
        status, detail = self._ml_release_status(latest, manifest_count=len(manifest_paths))
        backtest_status, backtest_detail = self._ml_backtest_status(latest)
        return {
            "manifest_dir": str(manifest_dir),
            "manifest_count": len(manifest_paths),
            "remote_storage_configured": bool(settings.ML_RELEASE_OBJECT_STORAGE_URL),
            "remote_auto_publish": bool(settings.ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH),
            "retention_limit": int(settings.ML_RELEASE_MANIFEST_RETENTION_LIMIT or 0),
            "status": status,
            "detail": detail,
            "latest_release_tag": latest.get("release_tag") if latest else None,
            "latest_manifest_path": latest.get("manifest_path") if latest else None,
            "latest_validated_on": latest.get("validated_on") if latest else None,
            "latest_signature_status": latest.get("signature_status") if latest else "missing",
            "latest_gate_status": latest.get("gate_status") if latest else "missing",
            "latest_gate_passed": latest.get("gate_passed") if latest else None,
            "latest_gate_policy": latest.get("gate_policy") if latest else None,
            "latest_best_predictor_key": latest.get("best_predictor_key") if latest else None,
            "latest_dataset_quality_status": latest.get("dataset_quality_status") if latest else None,
            "latest_backtest_sample_count": int(latest.get("backtest_sample_count") or 0) if latest else 0,
            "latest_backtest_average_absolute_error_rate": (
                latest.get("backtest_average_absolute_error_rate") if latest else None
            ),
            "backtest_status": backtest_status,
            "backtest_detail": backtest_detail,
            "recent_manifests": recent_manifests,
        }

    @staticmethod
    def _ml_release_dashboard_cards(
        ml_release_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "key": "ml_release_gate",
                "label": "ML release gate",
                "value": 1 if ml_release_summary["status"] == "healthy" else 0,
                "unit": "count",
                "status": ml_release_summary["status"],
                "detail": ml_release_summary["detail"],
            },
            {
                "key": "ml_backtest_samples",
                "label": "Backtest samples",
                "value": ml_release_summary["latest_backtest_sample_count"],
                "unit": "count",
                "status": ml_release_summary["backtest_status"],
                "detail": ml_release_summary["backtest_detail"],
            },
        ]

    def _ml_manifest_dir(self) -> Path:
        """Resolve the local release manifest directory."""
        raw_path = Path(settings.ML_RELEASE_MANIFEST_DIR)
        if raw_path.is_absolute():
            return raw_path
        return Path(__file__).resolve().parents[3] / raw_path

    def _manifest_recency_key(self, summary: dict[str, Any]) -> tuple[float, str]:
        """Sort manifests by validation timestamp, falling back to the release tag."""
        validated_on = summary.get("validated_on")
        timestamp = 0.0
        if validated_on:
            try:
                parsed = datetime.fromisoformat(str(validated_on).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                timestamp = parsed.timestamp()
            except ValueError:
                timestamp = 0.0
        return timestamp, str(summary.get("release_tag") or summary.get("manifest_path") or "")

    def _read_manifest_summary(self, path: Path) -> dict[str, Any]:
        """Read one release manifest into a compact operations summary."""
        summary: dict[str, Any] = {
            "manifest_path": str(path),
            "release_tag": path.stem,
            "validated_on": None,
            "signature_status": "missing",
            "gate_status": "missing",
            "gate_passed": None,
            "gate_policy": None,
            "backtest_sample_count": 0,
            "backtest_average_absolute_error_rate": None,
            "dataset_quality_status": None,
            "best_predictor_key": None,
            "best_predictor_name": None,
            "detail": "",
        }
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            summary.update({
                "signature_status": "invalid",
                "gate_status": "invalid",
                "gate_passed": False,
                "detail": f"Manifest could not be read: {exc}",
            })
            return summary
        if not isinstance(manifest, dict):
            summary.update({
                "signature_status": "invalid",
                "gate_status": "invalid",
                "gate_passed": False,
                "detail": "Manifest JSON is not an object.",
            })
            return summary

        summary["release_tag"] = str(manifest.get("release_tag") or path.stem)
        summary["validated_on"] = manifest.get("validated_on")
        summary["recommended_docker_target"] = manifest.get("recommended_docker_target")
        summary["remote_storage_enabled"] = bool((manifest.get("remote_storage") or {}).get("enabled"))
        summary["signature_status"] = self._manifest_signature_status(manifest, path)

        gate_container = manifest.get("promotion_gate") if isinstance(manifest.get("promotion_gate"), dict) else {}
        gate = gate_container.get("predictor_backtest") if isinstance(gate_container, dict) else {}
        gate = gate if isinstance(gate, dict) else {}
        metrics = gate.get("metrics") if isinstance(gate.get("metrics"), dict) else {}
        summary.update({
            "gate_status": str(gate.get("status") or "missing"),
            "gate_passed": bool(gate.get("passed")) if "passed" in gate else None,
            "gate_policy": (gate.get("thresholds") or {}).get("policy") if isinstance(gate.get("thresholds"), dict) else None,
            "backtest_sample_count": int(metrics.get("sample_count") or 0),
            "backtest_average_absolute_error_rate": metrics.get("average_absolute_error_rate"),
            "dataset_quality_status": metrics.get("dataset_quality_status"),
            "best_predictor_key": gate.get("best_predictor_key") or metrics.get("best_predictor_key"),
            "best_predictor_name": gate.get("best_predictor_name") or metrics.get("best_predictor_name"),
            "detail": "; ".join(str(reason) for reason in gate.get("reasons", []) if reason) if gate else "",
        })
        return summary

    def _manifest_signature_status(self, manifest: dict[str, Any], path: Path) -> str:
        """Return verified/missing/invalid for one release manifest signature."""
        if not isinstance(manifest.get("signature"), dict):
            return "invalid" if settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE else "missing"
        try:
            MLReleasePromotionService().verify_release_manifest(manifest, manifest_path=path)
        except ValueError:
            return "invalid"
        return "verified"

    def _ml_release_status(self, latest: dict[str, Any] | None, *, manifest_count: int) -> tuple[str, str]:
        """Convert release manifest state into a dashboard status."""
        if manifest_count == 0 or latest is None:
            return "watch", "No ML release manifest was found."
        if latest.get("signature_status") == "invalid":
            if settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE:
                return "critical", f"Latest manifest {latest.get('release_tag')} has an invalid signature."
            return "watch", f"Latest manifest {latest.get('release_tag')} has an invalid optional signature."
        if latest.get("gate_passed") is False:
            return "critical", f"Latest manifest {latest.get('release_tag')} failed the predictor promotion gate."
        if latest.get("signature_status") == "missing":
            return "watch", f"Latest manifest {latest.get('release_tag')} is not signed."
        return "healthy", f"Latest manifest {latest.get('release_tag')} is signed and passed its release gate."

    def _ml_backtest_status(self, latest: dict[str, Any] | None) -> tuple[str, str]:
        """Convert latest predictor backtest metadata into a dashboard status."""
        if latest is None:
            return "info", "No predictor backtest metadata is available."
        sample_count = int(latest.get("backtest_sample_count") or 0)
        gate_passed = latest.get("gate_passed")
        if gate_passed is False:
            return "critical", "Latest predictor promotion gate failed."
        if sample_count <= 0:
            return "info", "Latest manifest has no predictor backtest samples."
        required = int(settings.ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT or 0)
        if sample_count < required:
            return "watch", f"Latest backtest has {sample_count} sample(s), below required {required}."
        error_rate = latest.get("backtest_average_absolute_error_rate")
        if error_rate is None:
            return "watch", f"Latest backtest has {sample_count} sample(s), but no error-rate metric."
        return "healthy", f"Latest backtest has {sample_count} sample(s) at {float(error_rate):.4f} average error."
