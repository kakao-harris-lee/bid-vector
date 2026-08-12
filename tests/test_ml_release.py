"""Tests for manifest-backed ML artifact promotion helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.models import Project
from app.services.classifier import NoticeClassifierService
from app.services.ml_release import MLReleasePromotionRequest, MLReleasePromotionService
from app.services.ml_release.base import build_preflight_check


def _write_embedding_snapshot(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(
        json.dumps({"model_type": "bert"}), encoding="utf-8"
    )
    (path / "modules.json").write_text(
        json.dumps([{"idx": 0, "name": "0"}]), encoding="utf-8"
    )
    return path


def _write_ensemble_artifact(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_version": "1",
        "model_version": "v2.0-ensemble",
        "sequence_length": 8,
        "momentum_window": 5,
        "scenario_spread_multiplier": 1.05,
        "confidence_bias": 0.02,
        "component_weights": {
            "historical": 0.5,
            "momentum": 0.2,
            "mean_reversion": 0.15,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_predictor_backtest_report(
    path: Path,
    *,
    status: str = "completed",
    sample_count: int = 6,
    average_error_rate: float = 0.012,
    best_predictor_key: str = "ensemble",
    dataset_quality_status: str | None = None,
    guardrail_rate: float | None = 0.0,
    fallback_rate: float | None = 0.0,
    base_amount_basis: str = "clean",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": status,
                "holdout_size": sample_count,
                "best_predictor_key": best_predictor_key,
                "best_predictor_name": f"{best_predictor_key}_blend",
                "best_average_absolute_error_rate": average_error_rate,
                "guardrail_rate": guardrail_rate,
                "fallback_rate": fallback_rate,
                "settings": {"base_amount_basis": base_amount_basis},
                **(
                    {"dataset_quality_status": dataset_quality_status}
                    if dataset_quality_status is not None
                    else {}
                ),
                "results": [
                    {
                        "predictor_key": best_predictor_key,
                        "predictor_name": f"{best_predictor_key}_blend",
                        "predictor_family": best_predictor_key,
                        "status": "completed",
                        "sample_count": sample_count,
                        "average_absolute_error_rate": average_error_rate,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_create_release_manifest_writes_repo_relative_paths(tmp_path):
    """Manifest creation should validate artifacts and store repo-relative runtime paths."""
    repo_root = tmp_path / "repo"
    embedding_dir = _write_embedding_snapshot(
        repo_root / "models" / "embeddings" / "ko-sbert-v3"
    )
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "release-1.json"
    )
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "release-1-backtest.json"
    )

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-release-1",
            embedding_model_path=str(embedding_dir),
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(backtest_report_path),
            git_sha="abc123def",
            rebuild_limit=250,
            category="software",
        )
    )

    manifest_path = repo_root / "models" / "manifests" / "2026-05-11-release-1.json"
    assert manifest_path.exists()
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_path"] == str(manifest_path)
    assert persisted["recommended_docker_target"] == "api-embedding"
    assert (
        persisted["recommended_env"]["CLASSIFIER_EMBEDDING_MODEL"]
        == "models/embeddings/ko-sbert-v3"
    )
    assert (
        persisted["recommended_env"]["PRICE_PREDICTION_ENSEMBLE_MODEL_PATH"]
        == "models/predictors/ensemble/release-1.json"
    )
    assert (
        persisted["recommended_env"]["PRICE_PREDICTION_PREFERRED_PREDICTOR"]
        == "ensemble"
    )
    assert persisted["promotion_gate"]["predictor_backtest"]["passed"] is True
    assert (
        persisted["promotion_gate"]["predictor_backtest"]["thresholds"]["policy"]
        == "standard"
    )
    assert (
        persisted["promotion_gate"]["predictor_backtest"]["metrics"]["sample_count"]
        == 6
    )
    assert persisted["promotion_gate"]["predictor_backtest"]["metrics"][
        "average_absolute_error_rate"
    ] == pytest.approx(0.012)
    assert "lstm" not in persisted["artifacts"]["predictors"]
    assert persisted["rebuild"]["default_limit"] == 250
    assert persisted["rebuild"]["default_category"] == "software"
    assert persisted["signature"]["algorithm"] == "HMAC-SHA256"
    assert (
        persisted["artifacts"]["embedding_model"]["integrity"]["checksum_algorithm"]
        == "sha256-tree"
    )


def test_release_manifest_signature_detects_tampering(tmp_path):
    """Manifest loading should reject signed payloads that were modified after creation."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "signed.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-signed",
            ensemble_artifact_path=str(ensemble_path),
        )
    )

    manifest_path = Path(manifest["manifest_path"])
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted["recommended_env"][
        "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH"
    ] = "models/predictors/ensemble/tampered.json"
    manifest_path.write_text(
        json.dumps(persisted, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="signature verification failed"):
        service.load_release_manifest("2026-05-11-signed")


def test_apply_release_manifest_blocks_failed_predictor_promotion_gate(
    tmp_path, monkeypatch
):
    """Applying predictor artifacts should fail when the embedded backtest gate does not pass."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "weak.json"
    )
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "weak-backtest.json",
        sample_count=3,
        average_error_rate=0.06,
        best_predictor_key="ensemble",
    )
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT", 5)
    monkeypatch.setattr(
        settings, "ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE", 0.03
    )

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-weak-predictor",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(backtest_report_path),
        )
    )

    assert manifest["promotion_gate"]["predictor_backtest"]["passed"] is False
    with pytest.raises(ValueError, match="failed predictor promotion gate"):
        service.apply_release_manifest(None, manifest_ref="2026-05-11-weak-predictor")

    bypassed = service.apply_release_manifest(
        None,
        manifest_ref="2026-05-11-weak-predictor",
        skip_promotion_gate=True,
    )
    assert bypassed["promotion_gate"]["passed"] is False


def test_predictor_promotion_gate_blocks_failed_dataset_quality(tmp_path):
    """Training comparison reports with failed dataset quality should block standard rollout."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "dataset-quality.json"
    )
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "failed-dataset-quality.json",
        sample_count=6,
        average_error_rate=0.012,
        best_predictor_key="ensemble",
        dataset_quality_status="failed",
    )

    manifest = MLReleasePromotionService(repo_root=repo_root).create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-failed-dataset-quality",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(backtest_report_path),
        )
    )

    gate = manifest["promotion_gate"]["predictor_backtest"]
    assert gate["passed"] is False
    assert gate["thresholds"]["min_dataset_quality_status"] == "warning"
    assert gate["metrics"]["dataset_quality_status"] == "failed"
    assert any(
        "Dataset quality status 'failed'" in reason for reason in gate["reasons"]
    )


def test_predictor_promotion_gate_canary_policy_uses_release_tier_thresholds(
    tmp_path, monkeypatch
):
    """Canary rollout policy should expose and apply its own gate preset."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "canary.json"
    )
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "canary-backtest.json",
        sample_count=3,
        average_error_rate=0.035,
        best_predictor_key="ensemble",
        dataset_quality_status="warning",
    )
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_POLICY", "canary")

    manifest = MLReleasePromotionService(repo_root=repo_root).create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-canary-predictor",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(backtest_report_path),
        )
    )

    gate = manifest["promotion_gate"]["predictor_backtest"]
    assert gate["passed"] is True
    assert gate["thresholds"]["policy"] == "canary"
    assert gate["thresholds"]["require_report"] is True
    assert gate["thresholds"]["min_sample_count"] == 3
    assert gate["thresholds"]["max_average_absolute_error_rate"] == pytest.approx(0.04)
    assert gate["metrics"]["dataset_quality_status"] == "warning"


def test_publish_release_manifest_to_file_object_storage(tmp_path, monkeypatch):
    """Configured file object storage should receive the signed manifest and artifact objects."""
    repo_root = tmp_path / "repo"
    object_store = tmp_path / "object-store"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "remote.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-remote-storage",
            ensemble_artifact_path=str(ensemble_path),
        )
    )

    monkeypatch.setattr(
        settings, "ML_RELEASE_OBJECT_STORAGE_URL", object_store.as_uri()
    )
    result = service.publish_release_manifest(manifest["manifest_path"])

    assert result["enabled"] is True
    assert result["status"] == "passed"
    assert result["preflight"]["passed"] is True
    assert result["object_count"] == 2
    assert (object_store / "manifests" / "2026-05-11-remote-storage.json").exists()
    assert (
        object_store
        / "artifacts"
        / "2026-05-11-remote-storage"
        / "ensemble"
        / "remote.json"
    ).exists()


def test_preflight_release_rollout_checks_file_storage_and_signature(
    tmp_path, monkeypatch
):
    """Rollout preflight should validate signature, artifacts, and write access before publish."""
    repo_root = tmp_path / "repo"
    object_store = tmp_path / "object-store"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "preflight.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-preflight",
            ensemble_artifact_path=str(ensemble_path),
        )
    )
    monkeypatch.setattr(
        settings, "ML_RELEASE_OBJECT_STORAGE_URL", object_store.as_uri()
    )

    result = service.preflight_release_rollout(
        manifest["manifest_path"],
        require_signature=True,
    )

    assert result["passed"] is True
    assert result["signature_required"] is True
    assert result["manifest"]["signature_status"] == "verified"
    assert result["manifest"]["artifact_count"] >= 1
    assert result["object_storage"]["provider"] == "file"
    assert not list(object_store.glob("preflight/*.json"))
    check_names = {check["name"] for check in result["checks"]}
    assert "manifest_signature" in check_names
    assert "artifact_path:ensemble" in check_names
    assert "object_storage_write_probe" in check_names


def test_preflight_release_rollout_rejects_artifact_checksum_mismatch(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "tampered.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-04-tampered-artifact",
            ensemble_artifact_path=str(ensemble_path),
        )
    )
    ensemble_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    result = service.preflight_release_rollout(
        manifest["manifest_path"], require_signature=True, probe_write=False
    )

    artifact_check = next(
        check for check in result["checks"] if check["name"] == "artifact_path:ensemble"
    )
    assert result["passed"] is False
    assert artifact_check["status"] == "checksum_mismatch"
    assert artifact_check["expected_sha256"] != artifact_check["actual_sha256"]


def _write_unsigned_artifact_manifest(repo_root: Path, *, release_tag: str) -> Path:
    """Craft a manifest whose artifact carries no ``integrity`` block (legacy shape).

    은퇴한 ``lstm`` 키를 일부러 그대로 쓴다. 이미 서명된 과거 manifest 가 디스크에 남아
    있고, 은퇴를 이유로 그 키를 읽지 않으면 해당 릴리스가 "predictor 없음"으로 판정되어
    무결성 검사가 조용히 건너뛰어진다. 여기서 고정하는 것이 그 회귀 방지다.
    """
    artifact_path = repo_root / "models" / "predictors" / "lstm" / f"{release_tag}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps({"model_version": f"{release_tag}-retired"}), encoding="utf-8"
    )
    manifest_dir = repo_root / "models" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{release_tag}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "release_tag": release_tag,
                "artifacts": {"predictors": {"lstm": {"path": str(artifact_path)}}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_preflight_reports_unverified_artifact_as_checksum_missing(
    tmp_path, monkeypatch
):
    """무결성 없는 아티팩트는 통과하되 '체크섬이 일치한다'고 단언하지 않는다."""
    repo_root = tmp_path / "repo"
    manifest_path = _write_unsigned_artifact_manifest(
        repo_root, release_tag="2026-08-04-unsigned-artifact"
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")
    service = MLReleasePromotionService(repo_root=repo_root)

    result = service.preflight_release_rollout(
        str(manifest_path), require_signature=False, probe_write=False
    )

    # 은퇴한 키의 아티팩트도 계속 검사 대상이어야 한다(게이트 약화 방지).
    artifact_check = next(
        check for check in result["checks"] if check["name"] == "artifact_path:lstm"
    )
    assert artifact_check["passed"] is True
    assert artifact_check["status"] == "checksum_missing"
    assert artifact_check["checksum_verified"] is False
    assert artifact_check["expected_sha256"] is None
    assert "matches its" not in artifact_check["detail"]
    assert "has no signed checksum" in artifact_check["detail"]


def test_production_preflight_rejects_artifact_without_signed_checksum(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    manifest_path = _write_unsigned_artifact_manifest(
        repo_root, release_tag="2026-08-04-unsigned-production"
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")
    service = MLReleasePromotionService(repo_root=repo_root)

    result = service.preflight_release_rollout(
        str(manifest_path),
        require_signature=False,
        probe_write=False,
        production=True,
    )

    artifact_check = next(
        check for check in result["checks"] if check["name"] == "artifact_path:lstm"
    )
    assert result["passed"] is False
    assert artifact_check["passed"] is False
    assert artifact_check["status"] == "checksum_missing"
    assert "requires" in artifact_check["detail"]


# ---------------------------------------------------------------------------
# 은퇴 키(lstm) 레거시 매니페스트 — 게이트 약화 방지
#
# lstm predictor 는 2026-08-09 은퇴했지만 이미 서명된 과거 manifest 는 디스크에 남아
# 있다. predictor 키 목록을 ``("ensemble",)`` 로 좁히면 그 릴리스들이 "predictor
# 아티팩트 없음"으로 읽혀 promotion gate 와 production preflight 가 조용히
# not_applicable 로 넘어간다 — 검사가 사라지는데 결과는 여전히 통과로 보인다.
# ---------------------------------------------------------------------------
def test_promotion_gate_still_recognizes_a_retired_key_legacy_manifest(tmp_path):
    """gate.py: 은퇴 키만 가진 레거시 manifest 도 predictor 보유로 읽혀야 한다."""
    service = MLReleasePromotionService(repo_root=tmp_path / "repo")

    legacy_gate = service._resolve_manifest_promotion_gate(
        {
            "release_tag": "2026-05-11-legacy",
            "artifacts": {"predictors": {"lstm": {"path": "models/predictors/lstm/x.json"}}},
        }
    )
    no_predictor_gate = service._resolve_manifest_promotion_gate(
        {"release_tag": "2026-05-11-none", "artifacts": {"predictors": {}}}
    )

    assert no_predictor_gate["status"] == "not_applicable"
    # 은퇴 키를 읽지 않으면 이 값도 not_applicable 로 붕괴한다 — 그것이 조용한 약화다.
    assert legacy_gate["status"] != "not_applicable"


def test_legacy_ensemble_to_lstm_link_is_still_integrity_checked(tmp_path):
    """base.py: 은퇴 이전 manifest 의 ensemble → lstm 링크도 검사 대상에 남는다.

    신규 manifest 는 ``resolved_lstm_artifact_path`` 를 더 이상 쓰지 않으므로 이
    분기는 **레거시 문서로만 도달 가능**하다. 테스트가 없으면 "도달 불가 코드"로 보여
    지우기 쉬운데, 지우면 과거 릴리스의 링크 아티팩트가 무결성 검사에서 조용히 빠진다
    ("레거시를 계속 읽는다" 논거의 나머지 절반).
    """
    service = MLReleasePromotionService(repo_root=tmp_path / "repo")

    targets = service._iter_manifest_artifact_paths(
        {
            "release_tag": "2026-05-16-legacy-linked",
            "artifacts": {
                "predictors": {
                    "ensemble": {
                        "path": "models/predictors/ensemble/2026-05-16-price-v1.json",
                        "integrity": {"sha256": "e" * 64},
                        "resolved_lstm_artifact_path": (
                            "models/predictors/lstm/2026-05-16-price-v1.json"
                        ),
                        "resolved_lstm_artifact_integrity": {"sha256": "l" * 64},
                    }
                }
            },
        }
    )

    keys = {target["key"] for target in targets}
    assert "ensemble" in keys
    assert "linked_lstm" in keys

    linked = next(target for target in targets if target["key"] == "linked_lstm")
    assert linked["path"] == "models/predictors/lstm/2026-05-16-price-v1.json"
    assert linked["integrity"] == {"sha256": "l" * 64}


def test_new_manifests_no_longer_emit_a_linked_lstm_target(tmp_path):
    """반대쪽 절반: 새로 만드는 manifest 는 그 링크를 더 이상 싣지 않는다."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "fresh.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-09-fresh",
            ensemble_artifact_path=str(ensemble_path),
        )
    )

    targets = service._iter_manifest_artifact_paths(manifest)

    assert "linked_lstm" not in {target["key"] for target in targets}


def test_production_preflight_still_gates_a_retired_key_legacy_manifest(
    tmp_path, monkeypatch
):
    """preflight.py: 은퇴 키 릴리스의 production predictor 검사가 건너뛰어지지 않는다."""
    repo_root = tmp_path / "repo"
    manifest_path = _write_unsigned_artifact_manifest(
        repo_root, release_tag="2026-08-09-retired-key-production"
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")
    service = MLReleasePromotionService(repo_root=repo_root)

    result = service.preflight_release_rollout(
        str(manifest_path),
        require_signature=False,
        probe_write=False,
        production=True,
    )

    predictor_check = next(
        check
        for check in result["checks"]
        if check["name"] == "production_predictor_backtest"
    )
    assert predictor_check["status"] != "not_applicable"
    assert predictor_check["passed"] is False


def test_preflight_reports_verified_artifact_checksum(tmp_path, monkeypatch):
    """서명된 체크섬을 실제로 재계산해 맞춘 아티팩트만 일치를 단언한다."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "verified.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-04-verified-artifact",
            ensemble_artifact_path=str(ensemble_path),
        )
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    result = service.preflight_release_rollout(
        manifest["manifest_path"], require_signature=True, probe_write=False
    )

    artifact_check = next(
        check for check in result["checks"] if check["name"] == "artifact_path:ensemble"
    )
    assert artifact_check["passed"] is True
    assert artifact_check["status"] == "passed"
    assert artifact_check["checksum_verified"] is True
    assert artifact_check["expected_sha256"] == artifact_check["actual_sha256"]
    assert "matches its signed checksum" in artifact_check["detail"]


def test_production_preflight_requires_checksummed_predictor_backtest(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "no-backtest.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-04-no-backtest",
            ensemble_artifact_path=str(ensemble_path),
        )
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    result = service.preflight_release_rollout(
        manifest["manifest_path"],
        require_signature=True,
        probe_write=False,
        production=True,
    )

    check = next(
        item
        for item in result["checks"]
        if item["name"] == "production_predictor_backtest"
    )
    assert result["passed"] is False
    assert check["status"] == "missing_or_unverified"


def test_production_preflight_accepts_checksummed_predictor_backtest(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    object_store = tmp_path / "object-store"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "backtested.json"
    )
    report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "backtested.json",
        dataset_quality_status="warning",
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-04-backtested",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(report_path),
            git_sha="abc123",
        )
    )
    monkeypatch.setattr(
        settings, "ML_RELEASE_OBJECT_STORAGE_URL", object_store.as_uri()
    )

    result = service.preflight_release_rollout(
        manifest["manifest_path"],
        require_signature=True,
        probe_write=False,
        production=True,
        expected_git_sha="abc123",
    )

    assert result["passed"] is True
    check_names = {item["name"] for item in result["checks"]}
    assert "artifact_path:predictor_backtest" in check_names
    check = next(
        item
        for item in result["checks"]
        if item["name"] == "production_predictor_backtest"
    )
    assert check["status"] == "passed"


def test_production_preflight_rejects_incomplete_predictor_metrics(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "incomplete.json"
    )
    report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "incomplete.json",
        dataset_quality_status=None,
        guardrail_rate=None,
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-04-incomplete-backtest",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(report_path),
            git_sha="abc123",
        )
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    result = service.preflight_release_rollout(
        manifest["manifest_path"], require_signature=True, probe_write=False,
        production=True, expected_git_sha="abc123",
    )

    check = next(
        item for item in result["checks"]
        if item["name"] == "production_predictor_backtest"
    )
    assert result["passed"] is False
    assert check["status"] == "missing_or_unverified"


def test_production_preflight_rejects_non_clean_backtest_provenance(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "mixed-basis.json"
    )
    report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "mixed-basis.json",
        dataset_quality_status="warning",
        base_amount_basis="any",
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-04-mixed-basis",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(report_path),
            git_sha="abc123",
        )
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    result = service.preflight_release_rollout(
        manifest["manifest_path"], require_signature=True, probe_write=False,
        production=True, expected_git_sha="abc123",
    )

    check = next(item for item in result["checks"]
                 if item["name"] == "production_predictor_backtest")
    assert result["passed"] is False
    assert check["base_amount_basis"] == "any"


def test_production_preflight_rejects_manifest_deployment_sha_mismatch(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "sha.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-04-sha",
            ensemble_artifact_path=str(ensemble_path),
            git_sha="manifest-sha",
        )
    )
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    result = service.preflight_release_rollout(
        manifest["manifest_path"],
        require_signature=True,
        probe_write=False,
        production=True,
        expected_git_sha="deployment-sha",
    )

    check = next(
        item for item in result["checks"] if item["name"] == "manifest_git_sha"
    )
    assert check["status"] == "mismatch"
    assert check["manifest_git_sha"] == "manifest-sha"
    assert check["expected_git_sha"] == "deployment-sha"


def test_preflight_release_rollout_reports_missing_required_signature(
    tmp_path, monkeypatch
):
    """Required-signature preflight should fail with a structured reason before rollout."""
    repo_root = tmp_path / "repo"
    object_store = tmp_path / "object-store"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "unsigned.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-unsigned-preflight",
            ensemble_artifact_path=str(ensemble_path),
        )
    )
    manifest_path = Path(manifest["manifest_path"])
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted.pop("signature")
    manifest_path.write_text(
        json.dumps(persisted, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(
        settings, "ML_RELEASE_OBJECT_STORAGE_URL", object_store.as_uri()
    )

    result = service.preflight_release_rollout(
        "2026-05-11-unsigned-preflight",
        require_signature=True,
        probe_write=False,
    )

    assert result["passed"] is False
    assert result["manifest"]["signature_status"] == "invalid"
    assert any(
        "missing a required signature" in reason for reason in result["failure_reasons"]
    )
    signature_check = next(
        check for check in result["checks"] if check["name"] == "manifest_signature"
    )
    assert signature_check["required"] is True
    assert signature_check["status"] == "invalid"


def test_preflight_release_rollout_reports_unsupported_object_storage_scheme(
    tmp_path, monkeypatch
):
    """Unsupported object-storage URLs should be surfaced as preflight failures."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "unsupported.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-unsupported-storage",
            ensemble_artifact_path=str(ensemble_path),
        )
    )
    monkeypatch.setattr(
        settings, "ML_RELEASE_OBJECT_STORAGE_URL", "gs://bucket/releases"
    )

    result = service.preflight_release_rollout(
        "2026-05-11-unsupported-storage",
        probe_write=False,
    )

    assert result["passed"] is False
    assert result["object_storage"]["provider"] == "gs"
    assert any(
        "Unsupported ML_RELEASE_OBJECT_STORAGE_URL scheme" in reason
        for reason in result["failure_reasons"]
    )

    publish_result = service.publish_release_manifest("2026-05-11-unsupported-storage")
    assert publish_result["status"] == "failed"
    assert publish_result["object_count"] == 0
    assert publish_result["preflight"]["passed"] is False
    assert any(
        "Unsupported ML_RELEASE_OBJECT_STORAGE_URL scheme" in reason
        for reason in publish_result["failure_reasons"]
    )


class _FakeEmbeddingModel:
    def encode(self, texts, normalize_embeddings=True):
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_apply_release_manifest_can_rebuild_embeddings_with_temporary_overrides(
    test_db, tmp_path, monkeypatch
):
    """Applying a manifest should temporarily switch embedding settings and rebuild vectors."""
    repo_root = tmp_path / "repo"
    embedding_dir = _write_embedding_snapshot(
        repo_root / "models" / "embeddings" / "ko-sbert-v4"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-embedding-v4",
            embedding_model_path=str(embedding_dir),
            rebuild_limit=20,
        )
    )

    project = Project(
        title="Manifest rebuild target",
        description="로컬 embedding snapshot으로 재색인할 프로젝트",
        requirements="임베딩 재빌드 테스트",
        budget_estimate=95000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()

    original_model_name = settings.CLASSIFIER_EMBEDDING_MODEL
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ENABLE_SEMANTIC_CLASSIFICATION", True)
    monkeypatch.setattr(
        NoticeClassifierService,
        "_get_embedding_model",
        lambda self: _FakeEmbeddingModel(),
    )

    result = service.apply_release_manifest(
        test_db,
        manifest_ref="2026-05-11-embedding-v4",
        rebuild_embeddings=True,
    )

    refreshed = test_db.query(Project).filter(Project.id == project.id).first()
    assert result["rebuild_result"]["processed_count"] == 1
    assert result["rebuild_settings"]["limit"] == 20
    assert refreshed is not None
    assert refreshed.embedding_model == "models/embeddings/ko-sbert-v4"
    assert refreshed.embedding_updated_at is not None
    assert settings.CLASSIFIER_EMBEDDING_MODEL == original_model_name


def test_write_manifest_env_file_updates_existing_keys_and_appends_missing_ones(
    tmp_path,
):
    """Applying a manifest to a dotenv file should replace known keys and append missing ones."""
    repo_root = tmp_path / "repo"
    embedding_dir = _write_embedding_snapshot(
        repo_root / "models" / "embeddings" / "ko-sbert-v5"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-env-apply",
            embedding_model_path=str(embedding_dir),
        )
    )

    env_path = repo_root / ".env"
    env_path.write_text(
        "# Existing config\n"
        "API_DOCKER_TARGET=api-runtime\n"
        "CLASSIFIER_EMBEDDING_MODEL=old/model\n"
        "ENABLE_SEMANTIC_CLASSIFICATION=true\n",
        encoding="utf-8",
    )

    result = service.write_manifest_env_file(
        "2026-05-11-env-apply",
        env_file_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert result["env_file_path"] == str(env_path)
    assert result["created"] is False
    assert "API_DOCKER_TARGET=api-embedding" in content
    assert "CLASSIFIER_EMBEDDING_MODEL=models/embeddings/ko-sbert-v5" in content
    assert "CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY=true" in content
    assert "ENABLE_SEMANTIC_CLASSIFICATION=true" in content


def test_write_manifest_env_file_can_create_new_dotenv(tmp_path):
    """When the target dotenv file does not exist yet, the service should create it."""
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "release-2.json"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-new-env",
            ensemble_artifact_path=str(ensemble_path),
        )
    )

    env_path = repo_root / ".generated.env"
    result = service.write_manifest_env_file(
        "2026-05-11-new-env",
        env_file_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert result["created"] is True
    assert "API_DOCKER_TARGET=api-runtime" in content
    assert "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS=true" in content
    assert (
        "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH=models/predictors/ensemble/release-2.json"
        in content
    )


def test_restart_compose_services_runs_expected_command(tmp_path, monkeypatch):
    """Compose rollout should use docker compose up -d --build from the repository root."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    service = MLReleasePromotionService(repo_root=repo_root)
    captured: dict[str, object] = {}

    def fake_run(command, cwd, check, capture_output, text):
        captured["command"] = command
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=0, stdout="compose ok\n", stderr="")

    monkeypatch.setattr("app.services.ml_release.subprocess.run", fake_run)

    result = service.restart_compose_services(services=["api"], build=True)

    assert captured["command"] == ["docker", "compose", "up", "-d", "--build", "api"]
    assert captured["cwd"] == repo_root
    assert result["services"] == ["api"]
    assert result["build"] is True


def test_trigger_remote_embedding_rebuild_uses_manifest_defaults(tmp_path, monkeypatch):
    """Remote rebuild should call the API endpoint with manifest-derived default query parameters."""
    repo_root = tmp_path / "repo"
    embedding_dir = _write_embedding_snapshot(
        repo_root / "models" / "embeddings" / "ko-sbert-v6"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-remote-rebuild",
            embedding_model_path=str(embedding_dir),
            rebuild_limit=25,
            rebuild_offset=5,
            category="software",
            project_status="open",
            force_rebuild=False,
        )
    )

    captured: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"processed_count": 3}).encode("utf-8")

        def getcode(self):
            return 200

    def fake_urlopen(request_object, timeout):
        captured["url"] = request_object.full_url
        captured["method"] = request_object.get_method()
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("app.services.ml_release.request.urlopen", fake_urlopen)

    result = service.trigger_remote_embedding_rebuild(
        "2026-05-11-remote-rebuild",
        base_url="http://example.test",
    )

    assert captured["method"] == "POST"
    assert captured["timeout"] == 120.0
    assert captured["url"] == (
        "http://example.test/api/v1/ml/backfills/project-embeddings?"
        "limit=25&offset=5&force=false&category=software&project_status=open"
    )
    assert result["response"]["processed_count"] == 3
    assert result["rebuild_settings"] == {
        "limit": 25,
        "offset": 5,
        "category": "software",
        "project_status": "open",
        "force": False,
    }


def test_ml_release_remote_trigger_settings_defaults():
    """신규 remote-trigger Settings 키 기본값이 기존 리터럴과 동일한지 확인."""
    assert settings.ML_RELEASE_REMOTE_TRIGGER_BASE_URL == "http://localhost:3000"
    assert settings.ML_RELEASE_REMOTE_TRIGGER_TIMEOUT_SECONDS == 120.0
    assert settings.ML_RELEASE_HTTP_READY_PER_REQUEST_CAP_SECONDS == 10.0


def test_trigger_remote_embedding_rebuild_defaults_use_settings(tmp_path, monkeypatch):
    """base_url/timeout 인자를 생략하면 Settings 값이 반영되어야 한다."""
    repo_root = tmp_path / "repo"
    embedding_dir = _write_embedding_snapshot(
        repo_root / "models" / "embeddings" / "ko-sbert-v6"
    )
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-remote-rebuild-settings",
            embedding_model_path=str(embedding_dir),
            rebuild_limit=25,
            rebuild_offset=5,
            category="software",
            project_status="open",
            force_rebuild=False,
        )
    )

    monkeypatch.setattr(
        settings, "ML_RELEASE_REMOTE_TRIGGER_BASE_URL", "http://injected.test"
    )
    monkeypatch.setattr(settings, "ML_RELEASE_REMOTE_TRIGGER_TIMEOUT_SECONDS", 33.0)

    captured: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"processed_count": 1}).encode("utf-8")

        def getcode(self):
            return 200

    def fake_urlopen(request_object, timeout):
        captured["url"] = request_object.full_url
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("app.services.ml_release.request.urlopen", fake_urlopen)

    service.trigger_remote_embedding_rebuild("2026-05-11-remote-rebuild-settings")

    assert captured["timeout"] == 33.0
    assert captured["url"].startswith("http://injected.test/api/v1/ml/backfills/")


# ---------------------------------------------------------------------------
# Task 18 — Group calibration sample-count preflight gate
# ---------------------------------------------------------------------------


def _write_manifest_with_calibration(path: Path, calibration: dict) -> Path:
    """Write a minimal manifest JSON with the given group_calibration block."""
    manifest = {
        "version": "test",
        "release_tag": path.stem,
        "summary": {
            "group_calibration": calibration,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_evaluate_preflight_gate_rejects_when_group_below_threshold(
    tmp_path, monkeypatch
):
    """manifest의 group_calibration sample_count가 임계 미만이면 preflight 실패."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROUP_CALIBRATION_MIN_SAMPLES", 100)
    path = _write_manifest_with_calibration(
        tmp_path / "manifest.json",
        {
            "construction": {"median_rate": 0.90, "sample_count": 500},
            "service": {"median_rate": 0.88, "sample_count": 10},  # below 100
        },
    )
    from scripts.promote_ml_release import evaluate_preflight_gate

    result = evaluate_preflight_gate(path)
    assert result.ok is False
    assert result.reason is not None
    assert "service" in result.reason


def test_evaluate_preflight_gate_accepts_when_all_groups_meet_threshold(
    tmp_path, monkeypatch
):
    """全 group sample_count가 임계 이상이면 preflight 통과."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROUP_CALIBRATION_MIN_SAMPLES", 100)
    path = _write_manifest_with_calibration(
        tmp_path / "manifest.json",
        {
            "construction": {"median_rate": 0.90, "sample_count": 500},
            "service": {"median_rate": 0.88, "sample_count": 200},
        },
    )
    from scripts.promote_ml_release import evaluate_preflight_gate

    result = evaluate_preflight_gate(path)
    assert result.ok is True


def test_preflight_rollout_includes_group_calibration_check_when_below_threshold(
    tmp_path, monkeypatch
):
    """preflight_release_rollout이 group_calibration 미달 그룹을 실패 check로 포함해야 함."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROUP_CALIBRATION_MIN_SAMPLES", 100)
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    repo_root = tmp_path / "repo"
    service = MLReleasePromotionService(repo_root=repo_root)

    # Craft a manifest directly (bypass create_release_manifest to control summary).
    # Use require_signature=False so we don't need a real signature.
    manifest_dir = repo_root / "models" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "test-calib-fail.json"
    raw = {
        "release_tag": "test-calib-fail",
        "summary": {
            "group_calibration": {
                "construction": {"median_rate": 0.90, "sample_count": 500},
                "service": {"median_rate": 0.88, "sample_count": 5},  # below 100
            }
        },
        "artifacts": {},
    }
    manifest_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = service.preflight_release_rollout(
        str(manifest_path), require_signature=False, probe_write=False
    )

    check_names = [check["name"] for check in result["checks"]]
    assert "group_calibration_sample_count" in check_names, (
        f"group_calibration_sample_count check missing; got: {check_names}"
    )
    calib_check = next(
        c for c in result["checks"] if c["name"] == "group_calibration_sample_count"
    )
    assert calib_check["passed"] is False
    assert "service" in calib_check["detail"]
    assert result["passed"] is False


def test_preflight_rollout_group_calibration_check_passes_when_sufficient(
    tmp_path, monkeypatch
):
    """全 group sample_count가 임계 이상이면 group_calibration_sample_count check가 통과."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "GROUP_CALIBRATION_MIN_SAMPLES", 100)
    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", "")

    repo_root = tmp_path / "repo"
    service = MLReleasePromotionService(repo_root=repo_root)

    manifest_dir = repo_root / "models" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "test-calib-pass.json"
    raw = {
        "release_tag": "test-calib-pass",
        "summary": {
            "group_calibration": {
                "construction": {"median_rate": 0.90, "sample_count": 500},
                "service": {"median_rate": 0.88, "sample_count": 200},
            }
        },
        "artifacts": {},
    }
    manifest_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = service.preflight_release_rollout(
        str(manifest_path), require_signature=False, probe_write=False
    )

    check_names = [check["name"] for check in result["checks"]]
    assert "group_calibration_sample_count" in check_names
    calib_check = next(
        c for c in result["checks"] if c["name"] == "group_calibration_sample_count"
    )
    assert calib_check["passed"] is True


def test_default_repo_root_points_at_repository_root():
    """The parameterless service must resolve ``repo_root`` to the repository root.

    ``repo_root`` is derived by counting ``parents`` from this module's file, so
    any package decomposition that changes ``base.py``'s directory depth silently
    shifts the target (#284/#286 moved it into ``app/services/ml_release/`` and the
    byte-identical ``parents[2]`` started resolving to ``/app/app``). Deriving the
    expectation independently from the ``app`` package pins the arithmetic: if the
    file depth changes again, this assertion catches it.
    """
    import app

    expected_repo_root = Path(app.__file__).resolve().parents[1]

    assert MLReleasePromotionService().repo_root == expected_repo_root


# --- preflight check payload -------------------------------------------------
# Release preflight (``_MLReleaseBase._rollout_check``) and object-storage
# preflight (``_ObjectStorageBase._check``) built this payload separately with
# byte-identical bodies. Both results are consumed as one ``checks`` array
# (``_finalize_preflight`` reads ``passed``/``detail``), so the shape below is a
# contract, not an implementation detail. These pin what both copies produced.


@pytest.mark.parametrize(
    ("args", "extra", "expected"),
    [
        (
            ("artifact_paths", True, "passed", "3 artifacts resolved"),
            {},
            {
                "name": "artifact_paths",
                "passed": True,
                "status": "passed",
                "detail": "3 artifacts resolved",
            },
        ),
        (
            ("manifest_signature", False, "failed", "signature missing"),
            {},
            {
                "name": "manifest_signature",
                "passed": False,
                "status": "failed",
                "detail": "signature missing",
            },
        ),
        (
            ("write_probe", 1, "passed", "ok"),
            {"object_name": "preflight/x.json", "bytes": 12},
            {
                "name": "write_probe",
                "passed": True,
                "status": "passed",
                "detail": "ok",
                "object_name": "preflight/x.json",
                "bytes": 12,
            },
        ),
        # Falsy non-bools narrow to False; the consumer compares with ``is True``.
        (
            ("write_probe", "", "skipped", ""),
            {},
            {"name": "write_probe", "passed": False, "status": "skipped", "detail": ""},
        ),
        (
            ("write_probe", None, "skipped", "no store"),
            {},
            {
                "name": "write_probe",
                "passed": False,
                "status": "skipped",
                "detail": "no store",
            },
        ),
    ],
)
def test_build_preflight_check_payload(args, extra, expected):
    assert build_preflight_check(*args, **extra) == expected


def test_build_preflight_check_keeps_fixed_keys_first():
    payload = build_preflight_check("n", True, "passed", "d", bytes=1, object_name="o")

    assert list(payload) == ["name", "passed", "status", "detail", "bytes", "object_name"]


def test_build_preflight_check_extras_cannot_shadow_fixed_keys():
    """``status`` and friends are named parameters, so an extra cannot overwrite them."""
    with pytest.raises(TypeError):
        build_preflight_check("n", True, "passed", "d", **{"status": "spoofed"})


def test_manifest_recommended_env_never_promotes_excluded_predictor(tmp_path):
    """자동 승격 제외 키(distribution)는 수제·스테일 리포트로도 선호 env 에 실리지 않는다.

    fresh 리포트는 build_predictor_backtest_report 가 best 후보에서 이미 거르지만,
    manifest 는 **파일**로 리포트를 받으므로 그 필터를 우회한 best_predictor_key 가
    들어올 수 있다 — recommended_env 기록 직전의 두 번째 가드를 고정한다.
    """
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "release-x.json"
    )
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "release-x-backtest.json",
        best_predictor_key="distribution",
    )

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-11-distribution-guard",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(backtest_report_path),
        )
    )

    # 승격 차단: 선호 키가 recommended_env 에 아예 실리지 않는다.
    assert "PRICE_PREDICTION_PREFERRED_PREDICTOR" not in manifest["recommended_env"]
    gate = manifest["promotion_gate"]["predictor_backtest"]
    # 게이트 metrics 도 제외 arm 에서 유도되면 안 된다(리뷰 K4): 이 리포트의 유일한
    # 결과가 distribution 이므로 best 는 비고, 오차 결측으로 게이트는 실패한다 —
    # 서빙되지 않을 엔진의 성적으로 pass 도장이 찍히지 않는다.
    assert gate["best_predictor_key"] is None
    assert gate["passed"] is False
    assert gate["metrics"]["average_absolute_error_rate"] is None


def _write_two_arm_excluded_best_report(
    path: Path, *, ensemble_guardrail_rate: float, ensemble_fallback_rate: float
) -> Path:
    """best=distribution(제외 arm)이고 top-level 성적이 전부 그 arm 것인 수제 리포트."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "completed",
                "holdout_size": 6,
                "best_predictor_key": "distribution",
                "best_predictor_name": "reserve_draw_distribution",
                "best_average_absolute_error_rate": 0.005,
                # top-level rate 는 제외 arm 의 나쁜 성적 — 게이트가 이 값을 쓰거나
                # (누출) 비워 두거나(fail-open 스킵) 하면 안 되고 재선정 arm 값이어야
                # 한다(리뷰 L1).
                "guardrail_rate": 0.99,
                "fallback_rate": 0.99,
                "settings": {"base_amount_basis": "clean"},
                "results": [
                    {
                        "predictor_key": "distribution",
                        "predictor_name": "reserve_draw_distribution",
                        "predictor_family": "distribution",
                        "status": "completed",
                        "sample_count": 6,
                        "average_absolute_error_rate": 0.005,
                        "guardrail_rate": 0.99,
                        "fallback_rate": 0.99,
                    },
                    {
                        "predictor_key": "ensemble",
                        "predictor_name": "ensemble_blend",
                        "predictor_family": "ensemble",
                        "status": "completed",
                        "sample_count": 6,
                        "average_absolute_error_rate": 0.02,
                        "guardrail_rate": ensemble_guardrail_rate,
                        "fallback_rate": ensemble_fallback_rate,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_promotion_gate_metrics_fall_back_to_non_excluded_arm(tmp_path):
    """제외 arm 이 best 로 선언된 리포트에서 게이트는 비제외 completed arm 으로 재선정한다.

    top-level best_* 스칼라(제외 arm 의 성적)도 함께 불신해야 한다 — 오차만 재유도
    하고 guardrail/fallback rate 를 비워 두면 소비부의 `is not None` 게이트가 두
    검사를 **스킵**한다(리뷰 L1 fail-open). 두 축이 재선정 arm 의 값으로 채워짐을
    단언한다(None 아님 = 스킵 아님, 0.99 아님 = 제외 arm 누출 아님).
    """
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "release-y.json"
    )
    report_path = _write_two_arm_excluded_best_report(
        repo_root / "models" / "reports" / "release-y-backtest.json",
        ensemble_guardrail_rate=0.0,
        ensemble_fallback_rate=0.0,
    )

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-12-gate-fallback",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(report_path),
        )
    )

    gate = manifest["promotion_gate"]["predictor_backtest"]
    assert gate["best_predictor_key"] == "ensemble"
    # 오차도 제외 arm(0.005)이 아니라 재선정 arm(0.02)의 값이어야 한다.
    assert gate["metrics"]["average_absolute_error_rate"] == 0.02
    # L1 두 축: None(검사 스킵)도 0.99(제외 arm 누출)도 아닌 재선정 arm 의 값.
    assert gate["metrics"]["guardrail_rate"] == 0.0
    assert gate["metrics"]["fallback_rate"] == 0.0
    assert gate["passed"] is True
    assert (
        manifest["recommended_env"]["PRICE_PREDICTION_PREFERRED_PREDICTOR"]
        == "ensemble"
    )


def test_promotion_gate_judges_reselected_arm_rates(tmp_path):
    """재선정 arm 의 rate 가 임계를 넘으면 게이트가 실제로 **실패**해야 한다.

    이 방향 단언이 없으면 위 테스트는 'rate 가 우연히 0 이라 통과'와 구별되지
    않는다 — 리포트에 제외 키를 적는 것만으로 두 임계를 우회하던 fail-open 의
    반증(리뷰어 재현 시나리오의 역방향)이다.
    """
    repo_root = tmp_path / "repo"
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "release-z.json"
    )
    report_path = _write_two_arm_excluded_best_report(
        repo_root / "models" / "reports" / "release-z-backtest.json",
        ensemble_guardrail_rate=0.9,
        ensemble_fallback_rate=0.9,
    )

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-08-12-gate-judged",
            ensemble_artifact_path=str(ensemble_path),
            predictor_backtest_report_path=str(report_path),
        )
    )

    gate = manifest["promotion_gate"]["predictor_backtest"]
    assert gate["passed"] is False
    reasons = " ".join(gate["reasons"])
    assert "Guardrail rate" in reasons
    assert "Fallback rate" in reasons
