"""Tests for prediction observability reporting."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.models import PricePrediction, TenderResult


def _bootstrap_operator(client):
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "prediction-report-operator",
            "email": "prediction-report@example.com",
            "full_name": "Prediction Report Operator",
            "company": "Prediction Report Corp",
            "password": "password123",
        },
    )
    assert response.status_code == 200
    return response.json()


def _create_project(client, title: str) -> int:
    response = client.post(
        "/api/v1/projects/",
        json={
            "title": title,
            "description": f"{title} reporting fixture",
            "requirements": "Need prediction observability metrics",
            "budget_estimate": 100000000.0,
            "category": "software",
        },
    )
    assert response.status_code == 200
    return int(response.json()["id"])


def test_prediction_observability_endpoint_aggregates_predictor_accuracy_and_guardrails(client, test_db):
    """Prediction observability should summarize model selection, fallback, guardrails, and result accuracy."""
    operator = _bootstrap_operator(client)
    operator_id = operator["id"]
    first_project = _create_project(client, "Prediction Reporting One")
    second_project = _create_project(client, "Prediction Reporting Two")
    third_project = _create_project(client, "Prediction Reporting Three")
    now = datetime.now(UTC)

    test_db.add_all([
        PricePrediction(
            user_id=operator_id,
            project_id=first_project,
            predicted_price=101000000.0,
            price_range_min=99000000.0,
            price_range_max=102000000.0,
            confidence_score=0.8,
            model_version="v1-historical",
            predictor_name="historical_statistical",
            predictor_family="statistical",
            selector_name="backtest",
            selection_reason="rolling backtest selected historical predictor",
            backtest_sample_count=5,
            backtest_average_absolute_error_rate=0.012,
            training_window_size=4,
            pricing_mode="historical_blend",
            historical_sample_size=4,
            predicted_bid_rate=0.91,
            guardrail_applied=False,
            created_at=now - timedelta(days=2),
        ),
        PricePrediction(
            user_id=operator_id,
            project_id=second_project,
            predicted_price=95000000.0,
            price_range_min=95000000.0,
            price_range_max=99000000.0,
            confidence_score=0.7,
            model_version="v1-historical+guardrail",
            predictor_name="historical_statistical",
            predictor_family="statistical",
            fallback_reason="Requested lstm_sequence predictor is unavailable.",
            selector_name="backtest",
            selection_reason="rolling backtest fell back to historical predictor",
            backtest_sample_count=5,
            backtest_average_absolute_error_rate=0.018,
            training_window_size=2,
            pricing_mode="heuristic",
            historical_sample_size=2,
            predicted_bid_rate=0.95,
            guardrail_applied=True,
            guardrail_reason="업종별 최소 투찰률 가드레일을 적용했습니다.",
            floor_bid_rate=0.95,
            floor_price=95000000.0,
            created_at=now - timedelta(days=1),
        ),
        PricePrediction(
            user_id=operator_id,
            project_id=third_project,
            predicted_price=102000000.0,
            price_range_min=100000000.0,
            price_range_max=103000000.0,
            confidence_score=0.9,
            model_version="v2-ensemble",
            predictor_name="ensemble_blend",
            predictor_family="ensemble",
            selector_name="backtest",
            selection_reason="rolling backtest selected ensemble predictor",
            backtest_sample_count=6,
            backtest_average_absolute_error_rate=0.009,
            training_window_size=12,
            pricing_mode="historical_blend",
            historical_sample_size=12,
            predicted_bid_rate=1.02,
            guardrail_applied=False,
            created_at=now,
        ),
        TenderResult(
            project_id=first_project,
            winning_company="Winner One",
            winning_amount=100000000.0,
            winning_rate=0.91,
            result_status="awarded",
            announced_at=now - timedelta(days=1),
        ),
        TenderResult(
            project_id=second_project,
            winning_company="Winner Two",
            winning_amount=100000000.0,
            winning_rate=0.95,
            result_status="awarded",
            announced_at=now,
        ),
        TenderResult(
            project_id=third_project,
            winning_company="Winner Three",
            winning_amount=100000000.0,
            winning_rate=1.02,
            result_status="awarded",
            announced_at=now,
        ),
    ])
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/prediction-observability",
        params={"days": 30, "trend_bucket_days": 30},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["prediction_count"] == 3
    assert payload["fallback_count"] == 1
    assert payload["fallback_rate"] == pytest.approx(0.3333, abs=0.0001)
    assert payload["guardrail_count"] == 1
    assert payload["guardrail_rate"] == pytest.approx(0.3333, abs=0.0001)
    assert payload["accuracy_sample_count"] == 3
    assert payload["average_absolute_error_rate"] == pytest.approx(0.0267, abs=0.0001)
    assert payload["within_1_percent_count"] == 1
    assert payload["within_3_percent_count"] == 2
    assert payload["fallback_reason_breakdown"]["Requested lstm_sequence predictor is unavailable."] == 1
    assert payload["guardrail_reason_breakdown"]["업종별 최소 투찰률 가드레일을 적용했습니다."] == 1

    historical = payload["predictor_breakdown"][0]
    assert historical["predictor_name"] == "historical_statistical"
    assert historical["prediction_count"] == 2
    assert historical["fallback_count"] == 1
    assert historical["guardrail_count"] == 1
    assert historical["accuracy_sample_count"] == 2
    assert historical["average_absolute_error_rate"] == pytest.approx(0.03, abs=0.0001)
    assert historical["average_training_window_size"] == pytest.approx(3.0, abs=0.0001)

    ensemble = payload["predictor_breakdown"][1]
    assert ensemble["predictor_name"] == "ensemble_blend"
    assert ensemble["prediction_count"] == 1
    assert ensemble["average_absolute_error_rate"] == pytest.approx(0.02, abs=0.0001)

    pricing_modes = {item["pricing_mode"]: item for item in payload["pricing_mode_breakdown"]}
    assert pricing_modes["historical_blend"]["prediction_count"] == 2
    assert pricing_modes["heuristic"]["prediction_count"] == 1

    trend = payload["performance_trend"]
    assert sum(item["prediction_count"] for item in trend) == 3
    assert sum(item["backtest_sample_count"] for item in trend) == 16
    assert any(item["average_backtest_error_rate"] == pytest.approx(0.013, abs=0.0001) for item in trend)
