"""Tests for predictor selection and metadata."""

import json
from datetime import UTC, datetime, timedelta

from app.ai.price_prediction import predict_price
from app.core.config import settings
from app.models.models import HistoricalData


def _build_grouped_history(count_per_group: int, *, base_rate: float = 0.910) -> list[dict]:
    """Build historical records with alternating business_group values."""
    records = []
    groups = ["construction", "service"]
    for i in range(count_per_group * len(groups)):
        group = groups[i % len(groups)]
        rate = round(base_rate + i * 0.001, 6)
        records.append({
            "bid_rate": rate,
            "base_amount": 100_000_000.0,
            "predicted_price": round(100_000_000.0 * rate, 2),
            "business_group": group,
        })
    return records


def _build_bid_rate_history(count: int, *, base_rate: float = 0.914, step: float = 0.0012) -> list[dict[str, float]]:
    return [
        {
            "bid_rate": round(base_rate + (index * step), 6),
            "base_amount": 100000000.0,
            "predicted_price": round(100000000.0 * (base_rate + (index * step)), 2),
        }
        for index in range(count)
    ]


def _write_lstm_artifact(tmp_path) -> str:
    artifact_path = tmp_path / "lstm_artifact.json"
    artifact_path.write_text(
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
    return str(artifact_path)


def _write_ensemble_artifact(tmp_path) -> str:
    artifact_path = tmp_path / "ensemble_artifact.json"
    embedded_lstm_artifact = json.loads((tmp_path / "lstm_artifact.json").read_text(encoding="utf-8"))
    artifact_path.write_text(
        json.dumps(
            {
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
                "lstm_artifact": embedded_lstm_artifact,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(artifact_path)


def test_predict_price_reports_historical_predictor_metadata_by_default():
    """The baseline historical predictor should identify itself in the response payload."""
    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="predictor metadata test",
        historical_records=[
            {"bid_rate": 0.914},
            {"bid_rate": 0.921},
            {"bid_rate": 0.933},
            {"bid_rate": 0.941},
        ],
    )

    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["predictor_family"] == "statistical"
    assert prediction["fallback_reason"] is None
    assert prediction["training_window_size"] == 4


def test_predict_price_falls_back_to_historical_when_lstm_is_unavailable(monkeypatch):
    """Unavailable experimental predictors should fall back to the stable historical baseline."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "lstm")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", False)

    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="lstm fallback test",
        historical_records=[
            {"bid_rate": 0.914},
            {"bid_rate": 0.921},
            {"bid_rate": 0.933},
            {"bid_rate": 0.941},
        ],
    )

    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["predictor_family"] == "statistical"
    assert prediction["training_window_size"] == 4
    assert prediction["fallback_reason"] is not None
    assert "lstm_sequence" in prediction["fallback_reason"]
    assert "unavailable" in prediction["fallback_reason"].lower()


def test_predict_price_uses_lstm_predictor_when_artifact_is_configured(monkeypatch, tmp_path):
    """Configured LSTM artifacts should enable real sequence-model inference."""
    lstm_artifact_path = _write_lstm_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "lstm")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_LSTM_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_LSTM_MODEL_PATH", lstm_artifact_path)

    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="sequence predictor inference test",
        historical_records=_build_bid_rate_history(10),
    )

    assert prediction["predictor_name"] == "lstm_sequence"
    assert prediction["predictor_family"] == "sequence_model"
    assert prediction["fallback_reason"] is None
    assert prediction["model_version"] == "v2.0-lstm"
    assert prediction["training_window_size"] == 10
    assert prediction["pricing_mode"] == "historical_blend"
    assert prediction["historical_sample_size"] == 10
    assert 0.9 <= prediction["predicted_bid_rate"] <= 1.05
    assert "LSTM artifact v1" in prediction["explanation"]


def test_predict_price_uses_ensemble_predictor_when_artifact_is_configured(monkeypatch, tmp_path):
    """Configured ensemble artifacts should blend multiple components into one prediction."""
    _write_lstm_artifact(tmp_path)
    ensemble_artifact_path = _write_ensemble_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", ensemble_artifact_path)

    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="ensemble predictor inference test",
        historical_records=_build_bid_rate_history(12, base_rate=0.912, step=0.001),
    )

    assert prediction["predictor_name"] == "ensemble_blend"
    assert prediction["predictor_family"] == "ensemble"
    assert prediction["fallback_reason"] is None
    assert prediction["model_version"] == "v2.0-ensemble"
    assert prediction["training_window_size"] == 12
    assert prediction["pricing_mode"] == "historical_blend"
    assert prediction["historical_sample_size"] == 12
    assert 0.9 <= prediction["predicted_bid_rate"] <= 1.05
    assert "ensemble이" in prediction["explanation"]


def test_ensemble_prediction_applies_service_procurement_rate_bands(monkeypatch, tmp_path):
    """Ensemble output should share the same service subtype target bands as the baseline."""
    _write_lstm_artifact(tmp_path)
    ensemble_artifact_path = _write_ensemble_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", ensemble_artifact_path)
    history = _build_bid_rate_history(12, base_rate=0.912, step=0.001)

    negotiated_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="콘텐츠 플랫폼 운영 위탁 용역 협상에 의한 계약",
        historical_records=history,
    )
    competitive_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="건설폐기물 처리용역 가격입찰",
        historical_records=history,
    )
    service_low_tail_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="[당진]지방도615호 덕두교 등 2개교 정밀안전진단용역",
        historical_records=history,
    )

    assert negotiated_prediction["predictor_name"] == "ensemble_blend"
    assert negotiated_prediction["procurement_rate_band"] == "service_high_negotiated"
    assert negotiated_prediction["predicted_bid_rate"] == 1.0
    assert competitive_prediction["predictor_name"] == "ensemble_blend"
    assert competitive_prediction["procurement_rate_band"] == "service_price_competitive"
    assert competitive_prediction["predicted_bid_rate"] == 0.9
    assert service_low_tail_prediction["predictor_name"] == "ensemble_blend"
    assert service_low_tail_prediction["procurement_rate_band"] == "service_price_competitive"
    assert service_low_tail_prediction["predicted_bid_rate"] == 0.9


def test_ensemble_prediction_lifts_goods_recent_high_rate_tail(monkeypatch, tmp_path):
    """Goods outcomes clustered near 100% should lift the recommended/base scenario."""
    _write_lstm_artifact(tmp_path)
    ensemble_artifact_path = _write_ensemble_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "ensemble")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES", 8)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH", ensemble_artifact_path)

    recent_high_tail = [{"bid_rate": rate, "base_amount": 100000000.0} for rate in [
        0.982,
        0.991,
        0.998,
        1.0,
        0.974,
        0.989,
        1.0,
        0.996,
        0.951,
        0.986,
        0.997,
        1.0,
    ]]
    older_low_band = [{"bid_rate": rate, "base_amount": 100000000.0} for rate in [
        0.862,
        0.875,
        0.881,
        0.889,
        0.902,
        0.918,
        0.927,
        0.934,
    ]]

    prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="디지털서비스 클라우드 구독 라이선스 단독공급",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )

    assert prediction["predictor_name"] == "ensemble_blend"
    assert prediction["predicted_bid_rate"] >= 0.97
    assert prediction["high_rate_tail_adjustment"]["reason"] == "goods_recent_high_rate_tail"
    assert "최근 고율 낙찰 분포" in prediction["explanation"]

    competitive_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="관급자재 부스터펌프 구매 및 설치 소액수의 견적 제출",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )
    deep_discount_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="급식용 농산물 구매 2단계 입찰 공고",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )
    narrow_control_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="백암면(평창5블록) 인입지점 복선화 사업(계측제어)",
        historical_records=recent_high_tail + older_low_band,
        business_group="goods",
    )

    assert competitive_prediction["predictor_name"] == "ensemble_blend"
    assert competitive_prediction["procurement_rate_band"] == "goods_price_competitive"
    assert competitive_prediction["predicted_bid_rate"] == 0.9
    assert competitive_prediction["high_rate_tail_adjustment"] is None
    assert all(item["bid_rate"] <= 0.91 for item in competitive_prediction["bid_rate_candidates"])
    assert deep_discount_prediction["predictor_name"] == "ensemble_blend"
    assert deep_discount_prediction["procurement_rate_band"] == "goods_deep_discount"
    assert deep_discount_prediction["predicted_bid_rate"] == 0.841
    assert deep_discount_prediction["high_rate_tail_adjustment"] is None
    assert narrow_control_prediction["predictor_name"] == "ensemble_blend"
    assert narrow_control_prediction["procurement_rate_band"] == "goods_price_competitive"
    assert narrow_control_prediction["predicted_bid_rate"] == 0.9
    assert narrow_control_prediction["high_rate_tail_adjustment"] is None


def test_predict_price_auto_selector_uses_backtest_metadata(monkeypatch):
    """Auto predictor selection should run a rolling backtest and expose selector metadata."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "auto")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", False)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 3)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 3)

    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="auto selector backtest metadata",
        historical_records=_build_bid_rate_history(8, base_rate=0.91, step=0.001),
    )

    assert prediction["predictor_name"] == "historical_statistical"
    assert prediction["selector_name"] == "rolling_backtest"
    assert prediction["backtest_sample_count"] == 3
    assert prediction["backtest_average_absolute_error_rate"] is not None
    assert prediction["selection_reason"]
    assert prediction["backtest_report"]["best_predictor_key"] == "historical"


def test_price_prediction_endpoint_exposes_predictor_metadata(client, test_db):
    """The API response should surface the selected predictor metadata."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Predictor Metadata Project",
            "description": "predictor metadata endpoint test",
            "requirements": "Need predictor metadata",
            "budget_estimate": 130000000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    test_db.add_all([
        HistoricalData(
            notice_number="PREDICTOR-META-1",
            category="software",
            base_amount=130000000.0,
            predicted_price=130000000.0 * 0.916,
            bid_rate=0.916,
        ),
        HistoricalData(
            notice_number="PREDICTOR-META-2",
            category="software",
            base_amount=130000000.0,
            predicted_price=130000000.0 * 0.924,
            bid_rate=0.924,
        ),
        HistoricalData(
            notice_number="PREDICTOR-META-3",
            category="software",
            base_amount=130000000.0,
            predicted_price=130000000.0 * 0.931,
            bid_rate=0.931,
        ),
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 130000000.0,
            "category": "software",
            "description": "predictor metadata endpoint test",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["predictor_name"] == "historical_statistical"
    assert data["predictor_family"] == "statistical"
    assert data["fallback_reason"] is None
    assert data["training_window_size"] == 3


def test_price_prediction_endpoint_can_use_lstm_predictor(client, test_db, monkeypatch, tmp_path):
    """The API should surface experimental predictor metadata when a real artifact is configured."""
    lstm_artifact_path = _write_lstm_artifact(tmp_path)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "lstm")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_LSTM_MIN_SAMPLES", 6)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_LSTM_MODEL_PATH", lstm_artifact_path)

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "LSTM Predictor Project",
            "description": "endpoint should expose lstm predictor metadata",
            "requirements": "Need sequence model metadata",
            "budget_estimate": 125000000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    now = datetime.now(UTC)
    test_db.add_all([
        HistoricalData(
            notice_number=f"LSTM-PREDICTOR-{index}",
            category="software",
            base_amount=125000000.0,
            predicted_price=125000000.0 * bid_rate,
            bid_rate=bid_rate,
            opened_at=now - timedelta(days=(8 - index)),
        )
        for index, bid_rate in enumerate([0.913, 0.916, 0.919, 0.923, 0.927, 0.931, 0.934], start=1)
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 125000000.0,
            "category": "software",
            "description": "endpoint should expose lstm predictor metadata",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["predictor_name"] == "lstm_sequence"
    assert data["predictor_family"] == "sequence_model"
    assert data["fallback_reason"] is None
    assert data["model_version"] == "v2.0-lstm"
    assert data["training_window_size"] == 7


def test_backtest_report_includes_by_group_dimension(monkeypatch):
    """Report includes per-group aggregated metrics (construction/service)."""
    from app.ai.predictor_backtest import build_predictor_backtest_report
    from app.ai.predictors.base import PricePredictionContext
    from app.ai.predictors.historical import HistoricalStatisticalPredictor

    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 4)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 4)

    # 12 records total: 6 construction + 6 service, interleaved — last 4 become holdout
    historical_records = tuple(_build_grouped_history(6, base_rate=0.910))

    context = PricePredictionContext(
        budget=100_000_000.0,
        category="construction",
        description="by_group dimension test",
        historical_records=historical_records,
    )
    registry = {"historical": HistoricalStatisticalPredictor()}

    report = build_predictor_backtest_report(context, registry)

    assert "by_group" in report, "report must contain 'by_group' key"
    by_group = report["by_group"]
    assert isinstance(by_group, dict), "by_group must be a dict"
    # both groups must be present (interleaved records ensure at least one holdout each)
    assert set(by_group.keys()) >= {"construction", "service"}, (
        f"expected both groups, got {set(by_group.keys())}"
    )
    construction = by_group["construction"]
    assert "sample_count" in construction, "group entry must have sample_count"
    assert construction["sample_count"] >= 1
    assert "average_absolute_error_rate" in construction or "mae" in construction, (
        "group entry must have error metric"
    )
