"""Tests for AI prediction endpoints"""
import pytest

from app.ai.price_prediction import predict_price
from app.models.models import HistoricalData, PricePrediction, TenderResult, User


def test_price_prediction(client):
    """Test price prediction endpoint"""
    # Create a project first
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    # Get price prediction
    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 10000.0,
            "category": "software",
            "description": "Test project description",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "predicted_price" in data
    assert "confidence_score" in data
    assert data["pricing_mode"] in {"historical_blend", "heuristic"}
    assert len(data["bid_rate_candidates"]) == 3
    assert 0 <= data["confidence_score"] <= 1


def test_predict_price_builds_three_historical_scenarios():
    """Historical bid-rate samples should produce conservative/base/aggressive scenarios."""
    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="과거 유사 공고 기반 투찰가 예측 테스트",
        historical_records=[
            {"bid_rate": 0.914},
            {"bid_rate": 0.921},
            {"bid_rate": 0.933},
            {"bid_rate": 0.941},
            {"bid_rate": 0.928},
        ],
    )

    assert prediction["pricing_mode"] == "historical_blend"
    assert prediction["historical_sample_size"] == 5
    assert prediction["model_version"] == "v1.1-historical"
    assert prediction["agency_match_sample_size"] == 0
    assert len(prediction["bid_rate_candidates"]) == 3
    assert [item["label"] for item in prediction["bid_rate_candidates"]] == [
        "conservative",
        "base",
        "aggressive",
    ]
    assert prediction["price_range_min"] == prediction["bid_rate_candidates"][0]["predicted_price"]
    assert prediction["predicted_price"] == prediction["bid_rate_candidates"][1]["predicted_price"]
    assert prediction["price_range_max"] == prediction["bid_rate_candidates"][2]["predicted_price"]
    assert 0.9 <= prediction["predicted_bid_rate"] <= 0.96
    assert prediction["explanation"]


def test_predict_price_weights_same_agency_and_summarizes_reserve_patterns():
    """Same-agency history should receive extra weight and reserve-price patterns should be surfaced."""
    prediction = predict_price(
        budget=100000000.0,
        category="software",
        description="짧은 설명",
        agency_name="서울특별시교육청",
        historical_records=[
            {
                "agency_name": "서울특별시교육청",
                "bid_rate": 0.882,
                "base_amount": 100000000.0,
                "reserve_prices": [100000000.0, 101000000.0, 102000000.0],
                "selected_numbers": [1, 4, 7, 12],
            },
            {
                "agency_name": "서울특별시교육청",
                "bid_rate": 0.891,
                "base_amount": 100000000.0,
                "reserve_prices": [100500000.0, 101500000.0, 102500000.0],
                "selected_numbers": [1, 5, 7, 11],
            },
            {
                "agency_name": "조달청",
                "bid_rate": 0.972,
                "base_amount": 100000000.0,
                "reserve_prices": [99500000.0, 101500000.0, 103500000.0],
                "selected_numbers": [2, 4, 8, 12],
            },
            {
                "agency_name": "조달청",
                "bid_rate": 0.983,
                "base_amount": 100000000.0,
                "reserve_prices": [99400000.0, 101800000.0, 103800000.0],
                "selected_numbers": [3, 4, 7, 12],
            },
            {
                "agency_name": "국방부",
                "bid_rate": 0.978,
                "base_amount": 100000000.0,
                "reserve_prices": [99600000.0, 101600000.0, 103600000.0],
                "selected_numbers": [1, 4, 9, 12],
            },
        ],
    )

    assert prediction["pricing_mode"] == "historical_blend"
    assert prediction["historical_sample_size"] == 5
    assert prediction["agency_match_sample_size"] == 2
    assert prediction["predicted_bid_rate"] < 0.95
    assert prediction["reserve_price_context"] is not None
    assert prediction["reserve_price_context"]["sample_count"] == 5
    assert prediction["reserve_price_context"]["average_reserve_span_rate"] > 0.0
    assert 1 in prediction["reserve_price_context"]["frequent_selected_numbers"]
    assert 12 in prediction["reserve_price_context"]["frequent_selected_numbers"]
    assert "동일 기관 이력 2건" in prediction["explanation"]


def test_bid_recommendation(client):
    """Test bid recommendation endpoint"""
    # Create a project
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    # Get bid recommendation
    response = client.post(
        "/api/v1/predictions/bid-recommendation",
        json={
            "project_id": project_id,
            "user_historical_data": {"average_bid": 8000.0, "win_rate": 0.5},
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "recommended_bid" in data
    assert "reasoning" in data


def test_document_analysis(client):
    """Test document analysis endpoint"""
    # Create a project
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Test Project",
            "description": "Test description",
            "requirements": "Test requirements",
            "budget_estimate": 10000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    # Analyze document
    response = client.post(
        "/api/v1/predictions/analyze-document",
        json={
            "project_id": project_id,
            "document_content": "1. Must have user authentication\n2. Should support 10000 users\n3. Need API documentation",
            "document_type": "specification",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "key_requirements" in data
    assert "complexity_score" in data
    assert "estimated_effort" in data
    assert isinstance(data["key_requirements"], list)


def test_price_prediction_bootstraps_single_operator_account(client, test_db):
    """Prediction persistence should auto-bind to the singleton operator account."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Prediction Bootstrap Project",
            "description": "Operator persistence check",
            "requirements": "Need prediction persistence",
            "budget_estimate": 20000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 20000.0,
            "category": "software",
            "description": "Prediction bootstrap project",
        }
    )

    assert response.status_code == 200

    users = test_db.query(User).all()
    predictions = test_db.query(PricePrediction).all()

    assert len(users) == 1
    assert len(predictions) == 1
    assert predictions[0].user_id == users[0].id


def test_price_prediction_endpoint_uses_historical_data_when_available(client, test_db):
    """Price prediction endpoint should switch to historical blend mode when matching records exist."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Historical Prediction Project",
            "description": "과거 공고 사정률을 활용한 예측 확인",
            "requirements": "히스토리컬 데이터 필요",
            "budget_estimate": 130000000.0,
            "category": "software",
        }
    )

    project_id = project_response.json()["id"]

    history_rows = [
        HistoricalData(
            notice_number=f"HIST-{index}",
            category="software",
            base_amount=130000000.0,
            predicted_price=130000000.0 * bid_rate,
            bid_rate=bid_rate,
        )
        for index, bid_rate in enumerate([0.916, 0.924, 0.931, 0.939], start=1)
    ]
    test_db.add_all(history_rows)
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 130000000.0,
            "category": "software",
            "description": "소프트웨어 구축 공고 예측",
            "agency_name": "서울특별시교육청",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["pricing_mode"] == "historical_blend"
    assert data["historical_sample_size"] == 4
    assert data["agency_match_sample_size"] == 0
    assert len(data["bid_rate_candidates"]) == 3
    assert data["model_version"] == "v1.1-historical"

    persisted_prediction = test_db.query(PricePrediction).order_by(PricePrediction.id.desc()).first()
    assert persisted_prediction is not None
    assert persisted_prediction.model_version == "v1.1-historical"


def test_price_prediction_endpoint_surfaces_reserve_context_for_same_agency(client, test_db):
    """Prediction endpoint should return reserve-pattern context and agency match counts."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Reserve Context Project",
            "description": "예비가격 패턴 확인",
            "requirements": "기관 가중치 확인",
            "budget_estimate": 125000000.0,
            "category": "software",
        }
    )
    project_id = project_response.json()["id"]

    test_db.add_all([
        HistoricalData(
            notice_number="AGENCY-1",
            agency_name="서울특별시교육청",
            category="software",
            base_amount=125000000.0,
            predicted_price=116250000.0,
            bid_rate=0.93,
            reserve_prices="[120000000.0, 121000000.0, 122000000.0]",
            selected_numbers="[1, 4, 7, 12]",
        ),
        HistoricalData(
            notice_number="AGENCY-2",
            agency_name="서울특별시교육청",
            category="software",
            base_amount=125000000.0,
            predicted_price=115625000.0,
            bid_rate=0.925,
            reserve_prices="[119500000.0, 120500000.0, 121500000.0]",
            selected_numbers="[1, 5, 7, 11]",
        ),
        HistoricalData(
            notice_number="AGENCY-3",
            agency_name="조달청",
            category="software",
            base_amount=125000000.0,
            predicted_price=121250000.0,
            bid_rate=0.97,
            reserve_prices="[118000000.0, 121000000.0, 123000000.0]",
            selected_numbers="[2, 4, 8, 12]",
        ),
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 125000000.0,
            "category": "software",
            "description": "기관별 가중치를 반영한 투찰가 예측",
            "agency_name": "서울특별시교육청",
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["agency_match_sample_size"] == 2
    assert data["reserve_price_context"]["sample_count"] == 3
    assert data["reserve_price_context"]["average_reserve_span_rate"] > 0.0
    assert 1 in data["reserve_price_context"]["frequent_selected_numbers"]

def test_predict_price_applies_feedback_calibration_bias():
    """Feedback calibration should shift predicted amounts toward historically observed outcomes."""
    baseline = predict_price(
        budget=100000000.0,
        category="software",
        description="휴리스틱 보정 테스트",
    )
    calibrated = predict_price(
        budget=100000000.0,
        category="software",
        description="휴리스틱 보정 테스트",
        feedback_calibration={
            "sample_count": 4,
            "agency_match_sample_count": 1,
            "average_signed_error_rate": 0.03,
            "average_absolute_error_rate": 0.03,
            "applied_adjustment_rate": -0.02,
        },
    )

    assert calibrated["feedback_calibration"]["sample_count"] == 4
    assert calibrated["feedback_calibration"]["agency_match_sample_count"] == 1
    assert calibrated["predicted_price"] < baseline["predicted_price"]
    assert calibrated["model_version"].endswith("+feedback")
    assert "피드백 보정률 -2.00%" in calibrated["explanation"]

def test_price_prediction_endpoint_applies_feedback_calibration_from_recent_results(client, test_db):
    """Prediction endpoint should derive a calibration bias from recent linked tender results."""
    client.get("/api/v1/operator/profile")
    operator = test_db.query(User).one()

    historical_project_id = client.post(
        "/api/v1/projects/",
        json={
            "title": "Historical Calibration Source",
            "description": "Used to learn prediction bias",
            "requirements": "Feedback calibration source",
            "budget_estimate": 100000000.0,
            "category": "software",
        },
    ).json()["id"]
    target_project_id = client.post(
        "/api/v1/projects/",
        json={
            "title": "Calibrated Prediction Target",
            "description": "Prediction should be corrected by recent feedback",
            "requirements": "Feedback-aware prediction target",
            "budget_estimate": 100000000.0,
            "category": "software",
        },
    ).json()["id"]

    test_db.add_all([
        PricePrediction(
            user_id=operator.id,
            project_id=historical_project_id,
            predicted_price=105000000.0,
            price_range_min=100000000.0,
            price_range_max=110000000.0,
            confidence_score=0.78,
            model_version="v1.1-historical",
        ),
        HistoricalData(
            project_id=historical_project_id,
            notice_number="CAL-FEEDBACK-1",
            agency_name="서울특별시교육청",
            category="software",
            base_amount=100000000.0,
            predicted_price=100000000.0,
            bid_rate=1.0,
        ),
        TenderResult(
            project_id=historical_project_id,
            winning_company="테스트 낙찰사",
            winning_amount=100000000.0,
            winning_rate=95.1,
            result_status="awarded",
        ),
    ])
    test_db.commit()

    category_history = test_db.query(HistoricalData).filter(HistoricalData.category == "software").all()
    baseline = predict_price(
        budget=100000000.0,
        category="software",
        description="Prediction should be corrected by recent feedback",
        historical_records=category_history,
        agency_name="서울특별시교육청",
    )

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": target_project_id,
            "budget_estimate": 100000000.0,
            "category": "software",
            "description": "Prediction should be corrected by recent feedback",
            "agency_name": "서울특별시교육청",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["feedback_calibration"]["sample_count"] == 1
    assert data["feedback_calibration"]["agency_match_sample_count"] == 1
    assert data["feedback_calibration"]["applied_adjustment_rate"] < 0
    assert data["predicted_price"] < baseline["predicted_price"]
    assert data["model_version"].endswith("+feedback")
