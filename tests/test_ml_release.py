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


def _write_embedding_snapshot(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(json.dumps({"model_type": "bert"}), encoding="utf-8")
    (path / "modules.json").write_text(json.dumps([{"idx": 0, "name": "0"}]), encoding="utf-8")
    return path


def _write_lstm_artifact(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "artifact_version": "1",
                "model_version": "v2.0-lstm",
                "sequence_length": 6,
                "input_center": 0.9,
                "input_scale": 0.05,
                "output_scale": 0.03,
                "output_bias": 0.9,
                "scenario_spread_multiplier": 1.1,
                "confidence_bias": 0.03,
                "blend_weights": {
                    "lstm": 0.72,
                    "historical": 0.18,
                    "trend": 0.10,
                },
                "weights": {
                    "W_i": [[0.9]],
                    "U_i": [[0.15]],
                    "b_i": [3.0],
                    "W_f": [[0.2]],
                    "U_f": [[0.05]],
                    "b_f": [2.8],
                    "W_o": [[0.4]],
                    "U_o": [[0.1]],
                    "b_o": [2.5],
                    "W_c": [[1.1]],
                    "U_c": [[0.2]],
                    "b_c": [0.0],
                    "dense_W": [0.85],
                    "dense_b": [0.0],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_ensemble_artifact(
    path: Path,
    *,
    embedded_lstm_artifact: dict | None = None,
    linked_lstm_artifact_path: str | None = None,
) -> Path:
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
            "lstm": 0.15,
        },
    }
    if embedded_lstm_artifact is not None:
        payload["lstm_artifact"] = embedded_lstm_artifact
    if linked_lstm_artifact_path is not None:
        payload["lstm_artifact_path"] = linked_lstm_artifact_path
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
                **({"dataset_quality_status": dataset_quality_status} if dataset_quality_status is not None else {}),
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
    embedding_dir = _write_embedding_snapshot(repo_root / "models" / "embeddings" / "ko-sbert-v3")
    lstm_path = _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "release-1.json")
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "release-1.json",
        linked_lstm_artifact_path="../lstm/release-1.json",
    )
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "release-1-backtest.json"
    )

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-release-1",
            embedding_model_path=str(embedding_dir),
            lstm_artifact_path=str(lstm_path),
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
    assert persisted["recommended_env"]["CLASSIFIER_EMBEDDING_MODEL"] == "models/embeddings/ko-sbert-v3"
    assert persisted["recommended_env"]["PRICE_PREDICTION_LSTM_MODEL_PATH"] == "models/predictors/lstm/release-1.json"
    assert persisted["recommended_env"]["PRICE_PREDICTION_ENSEMBLE_MODEL_PATH"] == "models/predictors/ensemble/release-1.json"
    assert persisted["recommended_env"]["PRICE_PREDICTION_PREFERRED_PREDICTOR"] == "ensemble"
    assert persisted["promotion_gate"]["predictor_backtest"]["passed"] is True
    assert persisted["promotion_gate"]["predictor_backtest"]["thresholds"]["policy"] == "standard"
    assert persisted["promotion_gate"]["predictor_backtest"]["metrics"]["sample_count"] == 6
    assert persisted["promotion_gate"]["predictor_backtest"]["metrics"]["average_absolute_error_rate"] == pytest.approx(0.012)
    assert persisted["artifacts"]["predictors"]["ensemble"]["resolved_lstm_artifact_path"] == "models/predictors/lstm/release-1.json"
    assert persisted["rebuild"]["default_limit"] == 250
    assert persisted["rebuild"]["default_category"] == "software"
    assert persisted["signature"]["algorithm"] == "HMAC-SHA256"
    assert persisted["artifacts"]["embedding_model"]["integrity"]["checksum_algorithm"] == "sha256-tree"


def test_release_manifest_signature_detects_tampering(tmp_path):
    """Manifest loading should reject signed payloads that were modified after creation."""
    repo_root = tmp_path / "repo"
    lstm_path = _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "signed.json")
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-signed",
            lstm_artifact_path=str(lstm_path),
        )
    )

    manifest_path = Path(manifest["manifest_path"])
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted["recommended_env"]["PRICE_PREDICTION_LSTM_MODEL_PATH"] = "models/predictors/lstm/tampered.json"
    manifest_path.write_text(json.dumps(persisted, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="signature verification failed"):
        service.load_release_manifest("2026-05-11-signed")


def test_apply_release_manifest_blocks_failed_predictor_promotion_gate(tmp_path, monkeypatch):
    """Applying predictor artifacts should fail when the embedded backtest gate does not pass."""
    repo_root = tmp_path / "repo"
    lstm_path = _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "weak.json")
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "weak-backtest.json",
        sample_count=3,
        average_error_rate=0.06,
        best_predictor_key="lstm",
    )
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT", 5)
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE", 0.03)

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-weak-predictor",
            lstm_artifact_path=str(lstm_path),
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
    lstm_path = _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "dataset-quality.json")
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "failed-dataset-quality.json",
        sample_count=6,
        average_error_rate=0.012,
        best_predictor_key="lstm",
        dataset_quality_status="failed",
    )

    manifest = MLReleasePromotionService(repo_root=repo_root).create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-failed-dataset-quality",
            lstm_artifact_path=str(lstm_path),
            predictor_backtest_report_path=str(backtest_report_path),
        )
    )

    gate = manifest["promotion_gate"]["predictor_backtest"]
    assert gate["passed"] is False
    assert gate["thresholds"]["min_dataset_quality_status"] == "warning"
    assert gate["metrics"]["dataset_quality_status"] == "failed"
    assert any("Dataset quality status 'failed'" in reason for reason in gate["reasons"])


def test_predictor_promotion_gate_canary_policy_uses_release_tier_thresholds(tmp_path, monkeypatch):
    """Canary rollout policy should expose and apply its own gate preset."""
    repo_root = tmp_path / "repo"
    lstm_path = _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "canary.json")
    backtest_report_path = _write_predictor_backtest_report(
        repo_root / "models" / "reports" / "canary-backtest.json",
        sample_count=3,
        average_error_rate=0.035,
        best_predictor_key="lstm",
        dataset_quality_status="warning",
    )
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_POLICY", "canary")

    manifest = MLReleasePromotionService(repo_root=repo_root).create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-canary-predictor",
            lstm_artifact_path=str(lstm_path),
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
    lstm_path = _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "remote.json")
    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-remote-storage",
            lstm_artifact_path=str(lstm_path),
        )
    )

    monkeypatch.setattr(settings, "ML_RELEASE_OBJECT_STORAGE_URL", object_store.as_uri())
    result = service.publish_release_manifest(manifest["manifest_path"])

    assert result["enabled"] is True
    assert result["object_count"] == 2
    assert (object_store / "manifests" / "2026-05-11-remote-storage.json").exists()
    assert (object_store / "artifacts" / "2026-05-11-remote-storage" / "lstm" / "remote.json").exists()


class _FakeEmbeddingModel:
    def encode(self, texts, normalize_embeddings=True):
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_apply_release_manifest_can_rebuild_embeddings_with_temporary_overrides(test_db, tmp_path, monkeypatch):
    """Applying a manifest should temporarily switch embedding settings and rebuild vectors."""
    repo_root = tmp_path / "repo"
    embedding_dir = _write_embedding_snapshot(repo_root / "models" / "embeddings" / "ko-sbert-v4")
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
    monkeypatch.setattr(NoticeClassifierService, "_get_embedding_model", lambda self: _FakeEmbeddingModel())

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


def test_create_release_manifest_auto_infers_lstm_env_from_ensemble_link(tmp_path):
    """Ensemble manifests should surface linked LSTM artifacts even when only the ensemble path is provided."""
    repo_root = tmp_path / "repo"
    _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "linked-lstm.json")
    ensemble_path = _write_ensemble_artifact(
        repo_root / "models" / "predictors" / "ensemble" / "linked-ensemble.json",
        linked_lstm_artifact_path="../lstm/linked-lstm.json",
    )

    service = MLReleasePromotionService(repo_root=repo_root)
    manifest = service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-linked-ensemble",
            ensemble_artifact_path=str(ensemble_path),
        )
    )

    assert manifest["recommended_docker_target"] == "api-runtime"
    assert manifest["recommended_env"]["PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS"] is True
    assert manifest["recommended_env"]["PRICE_PREDICTION_ENSEMBLE_MODEL_PATH"] == "models/predictors/ensemble/linked-ensemble.json"
    assert manifest["recommended_env"]["PRICE_PREDICTION_LSTM_MODEL_PATH"] == "models/predictors/lstm/linked-lstm.json"


def test_write_manifest_env_file_updates_existing_keys_and_appends_missing_ones(tmp_path):
    """Applying a manifest to a dotenv file should replace known keys and append missing ones."""
    repo_root = tmp_path / "repo"
    embedding_dir = _write_embedding_snapshot(repo_root / "models" / "embeddings" / "ko-sbert-v5")
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
    lstm_path = _write_lstm_artifact(repo_root / "models" / "predictors" / "lstm" / "release-2.json")
    service = MLReleasePromotionService(repo_root=repo_root)
    service.create_release_manifest(
        MLReleasePromotionRequest(
            release_tag="2026-05-11-new-env",
            lstm_artifact_path=str(lstm_path),
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
    assert "PRICE_PREDICTION_LSTM_MODEL_PATH=models/predictors/lstm/release-2.json" in content


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
    embedding_dir = _write_embedding_snapshot(repo_root / "models" / "embeddings" / "ko-sbert-v6")
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
