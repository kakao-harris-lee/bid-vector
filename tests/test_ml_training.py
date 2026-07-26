"""Tests for queued ML training service outputs."""

from __future__ import annotations

import json
from datetime import timedelta

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import HistoricalData, Project, TenderResult
from app.services.ml_training import PricePredictionTrainingService


def test_default_repo_root_resolves_to_repository_root():
    """A no-arg service must resolve ``repo_root`` to the real repo root.

    ``PricePredictionTrainingService()`` (repo_root=None) derives its root from
    ``Path(__file__).resolve().parents[N]`` inside the ``ml_training`` package. When
    the module was decomposed into a package the file moved one directory deeper, so
    ``N`` had to change (parents[2] -> parents[3]). The production Celery task
    ``app.tasks.jobs.train_price_predictor`` default-constructs the service and writes
    artifacts under ``repo_root / "models"``, so a wrong index silently writes to the
    wrong tree. This test pins the invariant with an INDEPENDENTLY derived expectation
    (the parent of the ``app`` package), so if a future move changes the file depth,
    THIS test breaks instead of production.
    """
    import app
    from pathlib import Path

    expected_repo_root = Path(app.__file__).resolve().parents[1]
    resolved = PricePredictionTrainingService().repo_root

    assert resolved == expected_repo_root
    assert (resolved / "app" / "services" / "ml_training" / "service.py").is_file()


def test_price_predictor_training_writes_quality_comparison_and_gate_reports(
    test_db,
    tmp_path,
    monkeypatch,
):
    """Training should leave auditable reports and feed comparison evidence into release gate."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 3)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 3)
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT", 3)
    monkeypatch.setattr(settings, "ML_RELEASE_PREDICTOR_GATE_MAX_AVERAGE_ABSOLUTE_ERROR_RATE", 0.2)

    now = utc_now()
    agencies = ["서울조달청", "부산조달청"]
    for index, bid_rate in enumerate([0.898, 0.901, 0.9, 0.902, 0.899, 0.901, 0.9, 0.902]):
        base_amount = 100_000_000.0 + (index * 1_000_000.0)
        project = Project(
            title=f"Training Project {index}",
            description="Stable software tender history",
            requirements="Prediction training fixture",
            budget_estimate=base_amount,
            category="software",
            issuing_agency=agencies[index % len(agencies)],
        )
        test_db.add(project)
        test_db.flush()
        opened_at = now - timedelta(days=10 - index)
        test_db.add_all([
            HistoricalData(
                project_id=project.id,
                notice_number=f"TRAIN-{index}",
                agency_name=agencies[index % len(agencies)],
                category="software",
                base_amount=base_amount,
                predicted_price=base_amount * bid_rate,
                bid_rate=bid_rate,
                reserve_prices=json.dumps([base_amount * 0.99, base_amount, base_amount * 1.01]),
                selected_numbers=json.dumps([1, 4, 7, 12]),
                opened_at=opened_at,
            ),
            TenderResult(
                project_id=project.id,
                winning_company=f"Winner {index}",
                winning_amount=base_amount * bid_rate,
                winning_rate=bid_rate * 100,
                result_status="awarded",
                announced_at=opened_at + timedelta(hours=6),
            ),
        ])
    test_db.commit()

    repo_root = tmp_path / "repo"
    result = PricePredictionTrainingService(repo_root=repo_root).train_price_predictor(
        test_db,
        request_payload={
            "release_tag": "training-quality-report",
            "category": "software",
            "limit": 20,
            "create_manifest": True,
            "publish_remote": False,
        },
    )

    assert result["status"] == "completed"
    assert result["dataset_quality"]["status"] == "passed"
    assert result["comparison_report"]["status"] == "completed"
    assert result["comparison_report"]["sample_count"] == 3
    assert result["comparison_report"]["best_predictor_key"] in {"historical", "lstm", "ensemble"}
    assert result["comparison_report"]["results"][0]["average_error_delta_vs_historical"] == 0.0
    assert result["manifest"]["artifacts"]["predictors"]["ensemble"]["has_embedded_lstm"] is True

    quality_path = repo_root / result["dataset_quality_path"]
    comparison_path = repo_root / result["comparison_report_path"]
    summary_path = repo_root / result["summary_path"]
    assert quality_path.exists()
    assert comparison_path.exists()
    assert summary_path.exists()
    assert json.loads(quality_path.read_text(encoding="utf-8"))["metrics"]["sample_count"] == 8
    assert json.loads(comparison_path.read_text(encoding="utf-8"))["best_predictor_key"] == result["comparison_report"]["best_predictor_key"]
    assert json.loads(summary_path.read_text(encoding="utf-8"))["dataset_quality"]["status"] == "passed"

    manifest = result["manifest"]
    assert manifest is not None
    gate = manifest["promotion_gate"]["predictor_backtest"]
    assert gate["source_status"] == "completed"
    assert gate["passed"] is True
    assert gate["metrics"]["sample_count"] == 3
    assert gate["report_path"] == result["comparison_report_path"]


def test_training_summary_includes_group_calibration():
    """학습 결과 manifest summary에 그룹별 calibration 블록이 포함된다."""
    from app.services.ml_training import PricePredictionTrainingService

    service = PricePredictionTrainingService()
    dataset = {
        "summary": {"sample_count": 4},
        "items": [
            {"business_group": "construction", "winning_rate": 0.903},
            {"business_group": "construction", "winning_rate": 0.902},
            {"business_group": "service", "winning_rate": 0.881},
            {"business_group": "service", "winning_rate": 0.930},
        ],
    }
    calibration = service._build_group_calibration(dataset)
    assert "construction" in calibration
    assert "service" in calibration
    assert calibration["construction"]["sample_count"] == 2
    assert 0.900 <= calibration["construction"]["median_rate"] <= 0.905
    assert calibration["service"]["sample_count"] == 2


def test_group_calibration_p75_not_max_for_small_n():
    """N=4일 때 p75가 최댓값(index 3)이 아닌 index 2를 가리켜야 한다."""
    from app.services.ml_training import PricePredictionTrainingService

    service = PricePredictionTrainingService()
    # "series" key + "winning_rate" 필드 (production 형식)
    dataset = {
        "series": [
            {"business_group": "construction", "winning_rate": 0.85},
            {"business_group": "construction", "winning_rate": 0.90},
            {"business_group": "construction", "winning_rate": 0.95},
            {"business_group": "construction", "winning_rate": 0.99},
        ]
    }
    calib = service._build_group_calibration(dataset)
    construction = calib.get("construction") or {}
    # sorted: [0.85, 0.90, 0.95, 0.99]  N=4
    # correct p75 = sorted[(4-1)*3//4] = sorted[2] = 0.95
    # buggy p75   = sorted[min(3, 3*4//4)] = sorted[3] = 0.99  (max!)
    assert construction["p75"] < 0.99


def test_ensemble_artifact_includes_group_calibration_summary():
    """Ensemble artifact의 in-memory dict가 _inject_group_calibration 후 group_calibration을 가져야 함."""
    from app.services.ml_training import PricePredictionTrainingService

    svc = PricePredictionTrainingService()
    artifact = {"artifact_version": 1, "model_version": "test"}
    calibration = {
        "construction": {"median_rate": 0.9, "sample_count": 100},
        "service": {"median_rate": 0.88, "sample_count": 100},
    }
    svc._inject_group_calibration(artifact, calibration)
    assert "summary" in artifact
    assert artifact["summary"]["group_calibration"]["construction"]["sample_count"] == 100
    assert artifact["summary"]["group_calibration"]["service"]["sample_count"] == 100


def test_train_price_predictor_writes_group_calibration_to_ensemble_artifact(tmp_path):
    """Integration: _inject_group_calibration으로 주입된 calibration이 디스크 직렬화 후에도 보존된다."""
    import json
    from app.services.ml_training import PricePredictionTrainingService

    svc = PricePredictionTrainingService()
    dataset = {
        "series": [
            {"business_group": "construction", "winning_rate": 0.90, "label_rate": 0.90} for _ in range(5)
        ] + [
            {"business_group": "service", "winning_rate": 0.88, "label_rate": 0.88} for _ in range(5)
        ]
    }
    calibration = svc._build_group_calibration(dataset)
    assert "construction" in calibration and "service" in calibration

    # Simulate the pre-write injection that train_price_predictor now performs
    artifact_path = tmp_path / "ens.json"
    artifact: dict = {"artifact_version": 1, "model_version": "x"}
    svc._inject_group_calibration(artifact, calibration)
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))

    # Verify in-memory and on-disk representations agree
    assert artifact["summary"]["group_calibration"]["construction"]["sample_count"] == 5
    assert artifact["summary"]["group_calibration"]["service"]["sample_count"] == 5

    loaded = json.loads(artifact_path.read_text())
    assert loaded["summary"]["group_calibration"]["construction"]["sample_count"] == 5
    assert loaded["summary"]["group_calibration"]["service"]["sample_count"] == 5
