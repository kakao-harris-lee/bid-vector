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


def test_predict_price_uses_recent_service_history_without_heuristic_drag():
    """Deep service history should drive the bid-rate target instead of long-description heuristics."""
    history = [
        {
            "bid_rate": bid_rate,
            "base_amount": 100000000.0,
        }
        for bid_rate in [
            0.889,
            0.891,
            0.890,
            0.892,
            0.888,
            0.893,
            0.890,
            0.891,
            0.889,
            0.892,
            0.910,
            0.914,
            0.918,
            0.922,
            0.926,
        ]
    ]

    prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="긴 설명 " * 180,
        historical_records=history,
    )

    assert prediction["pricing_mode"] == "historical_blend"
    assert prediction["competitive_target_bid_rate"] == prediction["predicted_bid_rate"]
    assert prediction["predicted_bid_rate"] <= 0.893
    assert prediction["predicted_price"] <= 89300000.0


def test_predict_price_applies_service_procurement_rate_bands():
    """Service notice keywords should separate negotiated and price-competitive target bands."""
    history = [
        {"bid_rate": bid_rate, "base_amount": 100000000.0}
        for bid_rate in [0.91, 0.914, 0.918, 0.922, 0.926, 0.93, 0.934, 0.938, 0.942, 0.946]
    ]

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
    marine_engineering_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="동해바다숲 사전영향조사 및 일반해양이용협의 용역",
        historical_records=history,
    )
    direct_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="수산종자 방류효과조사 및 사전·사후영향조사 수의시담",
        historical_records=history,
    )
    body_only_direct_text_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="일반 시설점검 용역\n계약방법 안내: 수의계약 가능 문구 포함",
        historical_records=history,
    )
    two_stage_travel_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="해외문화체험 수학여행 위탁 용역 2단계 입찰 규격 가격 분리",
        historical_records=history,
    )
    bus_operation_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="이동형 근로자 휴게 및 교육 버스 운영 용역",
        historical_records=history,
    )
    service_low_tail_prediction = predict_price(
        budget=100000000.0,
        category="service",
        description="[천안]국지도70호 매주육교 정밀안전진단용역",
        historical_records=history,
    )
    goods_competitive_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="태장초등학교 냉난방기 구매 및 설치 소액수의 견적 제출 안내",
        historical_records=[
            {"bid_rate": rate, "base_amount": 100000000.0}
            for rate in [0.982, 0.991, 0.998, 1.0, 0.974, 0.989, 0.996, 0.951, 0.986, 1.0]
        ],
        business_group="goods",
    )
    goods_deep_discount_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="전주교도소 급식용 농산물 구매 2단계 입찰 공고",
        historical_records=[
            {"bid_rate": rate, "base_amount": 100000000.0}
            for rate in [0.982, 0.991, 0.998, 1.0, 0.974, 0.989, 0.996, 0.951, 0.986, 1.0]
        ],
        business_group="goods",
    )
    goods_narrow_control_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="백암면(평창5블록) 인입지점 복선화 사업(계측제어)",
        historical_records=[
            {"bid_rate": rate, "base_amount": 100000000.0}
            for rate in [0.982, 0.991, 0.998, 1.0, 0.974, 0.989, 0.996, 0.951, 0.986, 1.0]
        ],
        business_group="goods",
    )
    goods_control_panel_prediction = predict_price(
        budget=100000000.0,
        category="goods",
        description="관급자재(프로세스제어반) 계측제어 장치 구입",
        historical_records=[
            {"bid_rate": rate, "base_amount": 100000000.0}
            for rate in [0.982, 0.991, 0.998, 1.0, 0.974, 0.989, 0.996, 0.951, 0.986, 1.0]
        ],
        business_group="goods",
    )

    assert negotiated_prediction["procurement_rate_band"] == "service_high_negotiated"
    assert negotiated_prediction["predicted_bid_rate"] == 1.0
    assert competitive_prediction["procurement_rate_band"] == "service_price_competitive"
    assert competitive_prediction["predicted_bid_rate"] == 0.9
    assert all(item["bid_rate"] <= 0.9 for item in competitive_prediction["bid_rate_candidates"])
    assert marine_engineering_prediction["procurement_rate_band"] == "service_price_competitive"
    assert marine_engineering_prediction["predicted_bid_rate"] == 0.9
    assert direct_prediction["procurement_rate_band"] == "service_direct_negotiated"
    assert direct_prediction["predicted_bid_rate"] == 0.95
    assert body_only_direct_text_prediction["procurement_rate_band"] is None
    assert two_stage_travel_prediction["procurement_rate_band"] == "service_price_competitive"
    assert two_stage_travel_prediction["predicted_bid_rate"] == 0.9
    assert bus_operation_prediction["procurement_rate_band"] == "service_price_competitive"
    assert bus_operation_prediction["predicted_bid_rate"] == 0.9
    assert service_low_tail_prediction["procurement_rate_band"] == "service_price_competitive"
    assert service_low_tail_prediction["predicted_bid_rate"] == 0.9
    assert goods_competitive_prediction["procurement_rate_band"] == "goods_price_competitive"
    assert goods_competitive_prediction["predicted_bid_rate"] == 0.9
    assert goods_competitive_prediction["high_rate_tail_adjustment"] is None
    assert all(item["bid_rate"] <= 0.91 for item in goods_competitive_prediction["bid_rate_candidates"])
    assert goods_deep_discount_prediction["procurement_rate_band"] == "goods_deep_discount"
    assert goods_deep_discount_prediction["guardrail_applied"] is True
    assert goods_deep_discount_prediction["predicted_bid_rate"] == 0.841
    assert goods_deep_discount_prediction["high_rate_tail_adjustment"] is None
    assert goods_narrow_control_prediction["procurement_rate_band"] == "goods_price_competitive"
    assert goods_narrow_control_prediction["predicted_bid_rate"] == 0.9
    assert goods_narrow_control_prediction["high_rate_tail_adjustment"] is None
    assert goods_control_panel_prediction["procurement_rate_band"] is None


def test_predict_price_exposes_price_regime_features_and_selector_reason():
    """Predictions should explain the price regime before model-specific rates."""
    history = [
        {"bid_rate": bid_rate, "base_amount": 100000000.0}
        for bid_rate in [0.91, 0.914, 0.918, 0.922, 0.926, 0.93, 0.934, 0.938, 0.942, 0.946]
    ]

    floor_bound = predict_price(
        budget=100000000.0,
        category="service",
        description="항만 해양환경영향조사 용역 PQ 후 가격입찰",
        historical_records=history,
        legal_floor_bid_rate=87.995,
    )
    near_100 = predict_price(
        budget=100000000.0,
        category="service",
        description="콘텐츠 플랫폼 운영 위탁 용역 협상에 의한 계약",
        historical_records=history,
    )
    deep_discount = predict_price(
        budget=100000000.0,
        category="goods",
        description="급식용 농산물 구매 2단계 규격 가격 분리 입찰",
        historical_records=history,
        business_group="goods",
    )
    ambiguous = predict_price(
        budget=100000000.0,
        category="service",
        description="연구용역 협상에 의한 계약 및 가격입찰 동시 평가",
        historical_records=history,
    )

    assert floor_bound["price_regime_label"] == "floor_bound"
    assert floor_bound["price_regime_confidence"] >= 0.75
    assert floor_bound["price_regime_features"]["contract_method"] == "price_competitive"
    assert floor_bound["price_regime_features"]["legal_floor_bid_rate"] == 0.87995
    assert floor_bound["recommended_candidate_label"] == "base"
    assert "floor_bound" in floor_bound["recommended_selector_reason"]

    assert near_100["price_regime_label"] == "near_100"
    assert near_100["price_regime_features"]["contract_method"] == "negotiated"
    assert near_100["review_required"] is False

    assert deep_discount["price_regime_label"] == "deep_discount"
    assert deep_discount["price_regime_features"]["price_submission_mode"] == "separated"
    assert deep_discount["price_regime_features"]["data_quality_flags"] == []

    assert ambiguous["price_regime_label"] == "ambiguous"
    assert ambiguous["price_regime_confidence"] < 0.7
    assert ambiguous["review_required"] is True
    assert "conflicting" in ambiguous["recommended_selector_reason"]


def test_predict_price_rounds_final_bid_prices_to_ten_won():
    """Final bid candidates should avoid sub-ten KRW units."""
    prediction = predict_price(
        budget=49_461_000.0,
        category="service",
        description="일반해양이용협의 용역",
        historical_records=[
            {"bid_rate": 0.8782, "base_amount": 49_461_000.0},
            {"bid_rate": 0.879, "base_amount": 49_461_000.0},
            {"bid_rate": 0.88035, "base_amount": 49_461_000.0},
            {"bid_rate": 0.881, "base_amount": 49_461_000.0},
            {"bid_rate": 0.882, "base_amount": 49_461_000.0},
        ],
        legal_floor_bid_rate=87.995,
    )

    assert prediction["bid_price_granularity"] == 10
    assert prediction["bid_price_rounding_mode"] == "floor"
    assert prediction["price_granularity_applied"] is True
    assert prediction["predicted_price"] % 10 == 0
    assert all(item["predicted_price"] % 10 == 0 for item in prediction["bid_rate_candidates"])
    assert all(item["predicted_price"] >= prediction["safe_floor_price"] for item in prediction["bid_rate_candidates"])


def test_predict_price_summarizes_selected_reserve_estimated_price_rates():
    """Selected reserve numbers should expose estimated-price and bid-to-estimate context."""
    reserve_prices = [99000000.0 + (index * 100000.0) for index in range(15)]
    prediction = predict_price(
        budget=100000000.0,
        category="construction",
        description="복수예가 컨텍스트 검증",
        historical_records=[
            {
                "bid_rate": 0.904,
                "base_amount": 100000000.0,
                "reserve_prices": reserve_prices,
                "selected_numbers": [1, 4, 7, 12],
            },
            {
                "bid_rate": 0.902,
                "base_amount": 100000000.0,
                "reserve_prices": reserve_prices,
                "selected_numbers": [2, 5, 8, 11],
            },
            {
                "bid_rate": 0.906,
                "base_amount": 100000000.0,
                "reserve_prices": reserve_prices,
                "selected_numbers": [3, 6, 9, 15],
            },
        ],
    )

    context = prediction["reserve_price_context"]
    assert context["estimated_price_sample_count"] == 3
    assert context["median_estimated_price_rate"] > 0.99
    assert context["median_bid_to_estimated_price_rate"] > 0.0


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


def test_predict_price_applies_minimum_bid_rate_guardrail():
    """Configured bid-rate floors should clamp unrealistically low price scenarios."""
    prediction = predict_price(
        budget=100000000.0,
        category="construction",
        description="낙찰하한 가드레일 검증 테스트",
        historical_records=[
            {"bid_rate": 0.78},
            {"bid_rate": 0.81},
            {"bid_rate": 0.82},
        ],
    )

    assert prediction["guardrail_applied"] is True
    assert prediction["guardrail_reason"]
    assert prediction["floor_bid_rate"] is not None
    assert prediction["floor_price"] is not None
    assert prediction["predicted_bid_rate"] >= prediction["floor_bid_rate"]
    assert prediction["price_range_min"] >= prediction["floor_price"]
    assert all(item["bid_rate"] >= prediction["floor_bid_rate"] for item in prediction["bid_rate_candidates"])
    assert prediction["model_version"].endswith("+guardrail")
    assert "가드레일" in prediction["explanation"]


def test_predict_price_applies_notice_legal_floor_to_conservative_scenario():
    """Notice-specific legal floors should keep conservative bids above the final floor."""
    budget = 49_461_000.0
    prediction = predict_price(
        budget=budget,
        category="construction",
        description="2026년 울주군(나사리) 자연석 투석 사업지 및 천연해조장 서식환경개선",
        legal_floor_bid_rate=88.0,
        historical_records=[
            {"bid_rate": 0.8782},
            {"bid_rate": 0.8790},
            {"bid_rate": 0.8800},
            {"bid_rate": 0.8810},
            {"bid_rate": 0.8820},
        ],
    )

    conservative = next(
        item for item in prediction["bid_rate_candidates"]
        if item["label"] == "conservative"
    )
    assert prediction["legal_floor_bid_rate"] == pytest.approx(0.88, abs=0.0001)
    assert prediction["floor_bid_rate"] == pytest.approx(0.88, abs=0.0001)
    assert prediction["floor_guardrail_source"] == "legal"
    assert prediction["floor_safety_margin_rate"] == pytest.approx(0.001, abs=0.0001)
    assert prediction["safe_floor_bid_rate"] == pytest.approx(0.881, abs=0.0001)
    assert conservative["guardrail_applied"] is True
    assert conservative["pre_guardrail_bid_rate"] < prediction["floor_bid_rate"]
    assert conservative["bid_rate"] >= prediction["safe_floor_bid_rate"]
    assert conservative["predicted_price"] >= round(budget * prediction["safe_floor_bid_rate"], 2)
    assert conservative["predicted_price"] % 10 == 0
    assert "공고별 법정 최소 투찰률" in prediction["guardrail_reason"]


def test_predict_price_applies_maximum_bid_rate_guardrail():
    """Configured bid-rate ceilings should clamp unrealistically high price scenarios."""
    prediction = predict_price(
        budget=100000000.0,
        category="construction",
        description="상한 가드레일 검증 테스트",
        historical_records=[
            {"bid_rate": 1.18},
            {"bid_rate": 1.21},
            {"bid_rate": 1.24},
            {"bid_rate": 1.27},
            {"bid_rate": 1.30},
        ],
    )

    assert prediction["guardrail_applied"] is True
    assert prediction["guardrail_reason"]
    assert prediction["ceiling_bid_rate"] == 0.93
    assert prediction["ceiling_price"] == 93000000.0
    assert prediction["predicted_bid_rate"] <= prediction["ceiling_bid_rate"]
    assert prediction["price_range_max"] <= prediction["ceiling_price"]
    assert all(item["bid_rate"] <= prediction["ceiling_bid_rate"] for item in prediction["bid_rate_candidates"])
    assert prediction["model_version"].endswith("+guardrail")
    assert "최대 투찰률" in prediction["guardrail_reason"]


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
            predicted_price=96000000.0,
            bid_rate=0.96,
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


def test_price_prediction_endpoint_surfaces_guardrail_metadata(client, test_db):
    """Prediction endpoint should expose bid-rate floor metadata when guardrails are applied."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Construction Guardrail Project",
            "description": "낙찰하한 검증용 공고",
            "requirements": "건설 카테고리 예측",
            "budget_estimate": 100000000.0,
            "category": "construction",
        },
    )
    project_id = project_response.json()["id"]

    test_db.add_all([
        HistoricalData(
            notice_number="CONST-GUARD-1",
            category="construction",
            base_amount=100000000.0,
            predicted_price=79000000.0,
            bid_rate=0.79,
        ),
        HistoricalData(
            notice_number="CONST-GUARD-2",
            category="construction",
            base_amount=100000000.0,
            predicted_price=81000000.0,
            bid_rate=0.81,
        ),
        HistoricalData(
            notice_number="CONST-GUARD-3",
            category="construction",
            base_amount=100000000.0,
            predicted_price=82000000.0,
            bid_rate=0.82,
        ),
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 100000000.0,
            "category": "construction",
            "description": "낙찰하한 검증용 공고",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["guardrail_applied"] is True
    assert data["floor_bid_rate"] is not None
    assert data["floor_price"] is not None
    assert data["predicted_bid_rate"] >= data["floor_bid_rate"]
    assert data["price_range_min"] >= data["floor_price"]
    assert data["model_version"].endswith("+guardrail")


def test_price_prediction_endpoint_accepts_notice_legal_floor_rate(client, test_db):
    """The price endpoint should accept percent-style notice floor rates."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "R26BK01603215-000 하한율 검증",
            "description": "자연석 투석 사업지 및 천연해조장 서식환경개선",
            "requirements": "공고별 법정 하한율 반영",
            "budget_estimate": 49_461_000.0,
            "category": "construction",
        },
    )
    project_id = project_response.json()["id"]

    test_db.add_all([
        HistoricalData(
            notice_number=f"LEGAL-FLOOR-{index}",
            category="construction",
            base_amount=49_461_000.0,
            predicted_price=49_461_000.0 * bid_rate,
            bid_rate=bid_rate,
        )
        for index, bid_rate in enumerate([0.8782, 0.879, 0.88, 0.881], start=1)
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 49_461_000.0,
            "category": "construction",
            "description": "R26BK01603215-000 하한율 검증",
            "legal_floor_bid_rate": 87.995,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["floor_guardrail_source"] == "legal"
    assert data["legal_floor_bid_rate"] == pytest.approx(0.87995, abs=0.000001)
    assert data["floor_bid_rate"] == pytest.approx(0.87995, abs=0.000001)
    assert data["safe_floor_bid_rate"] == pytest.approx(0.88095, abs=0.000001)
    assert all(
        item["bid_rate"] >= data["safe_floor_bid_rate"]
        for item in data["bid_rate_candidates"]
        if item["guardrail_applied"]
    )


def test_price_prediction_endpoint_uses_related_category_history_for_technical_service(client, test_db):
    """Technical-service predictions should fall back to service bid-rate history."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Technical Service Price Fallback",
            "description": "기술용역 가격 fallback 확인",
            "requirements": "기술용역",
            "budget_estimate": 100000000.0,
            "category": "technical-service",
        },
    )
    project_id = project_response.json()["id"]

    test_db.add_all([
        HistoricalData(
            notice_number=f"SERVICE-FALLBACK-{index}",
            category="service",
            base_amount=100000000.0,
            predicted_price=100000000.0 * bid_rate,
            bid_rate=bid_rate,
        )
        for index, bid_rate in enumerate([0.884, 0.891, 0.897, 0.902], start=1)
    ])
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": 100000000.0,
            "category": "technical-service",
            "description": "기술용역 가격 fallback 확인",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["pricing_mode"] == "historical_blend"
    assert data["historical_sample_size"] == 4
    assert data["predicted_bid_rate"] <= data["ceiling_bid_rate"]
