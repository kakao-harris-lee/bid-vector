"""Tests for queued ML training service outputs."""

from __future__ import annotations

import json
from datetime import timedelta

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import HistoricalData, Project, TenderResult
from app.services.ml_training import PricePredictionTrainingService


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
