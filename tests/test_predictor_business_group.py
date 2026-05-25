"""Tests for business_group propagation through PricePredictionContext."""

from app.ai.price_prediction import predict_price
from app.ai.predictors.base import PricePredictionContext
from app.models.models import Project  # noqa: F401 — registers models to Base.metadata
from app.schemas.schemas import OpportunityAnalysisRequest  # noqa: F401


def test_context_accepts_business_type_fields():
    context = PricePredictionContext(
        budget=100_000_000.0,
        description="OO 건축공사",
        historical_records=(),
        category="construction",
        business_type_code="0411",
        business_group="construction",
    )
    assert context.business_type_code == "0411"
    assert context.business_group == "construction"


def test_predict_price_accepts_business_type_kwargs():
    """Signature must accept new kwargs without raising."""
    result = predict_price(
        budget=100_000_000.0,
        description="OO 건축공사",
        historical_records=(),
        category="construction",
        business_type_code="0411",
        business_group="construction",
    )
    assert isinstance(result, dict)


def test_opportunity_analysis_passes_business_type(test_db, monkeypatch):
    """OpportunityAnalysisService가 Project.business_type_code를 predict_price로 전달."""
    from app.services import opportunity_analysis as oa_module

    captured = {}

    def fake_predict_price(**kwargs):
        captured.update(kwargs)
        return {
            "predicted_price": 90_000_000.0,
            "recommended_amount": 90_000_000.0,
            "probability_score": 0.6,
            "matched_score": 0.6,
        }

    project = Project(
        title="건축공사 시그널 검증",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    test_db.add(project)
    test_db.flush()

    monkeypatch.setattr(oa_module, "predict_price", fake_predict_price)
    service = oa_module.OpportunityAnalysisService()
    # analyze_project is the entry method; build a minimal request
    request = OpportunityAnalysisRequest(project_id=project.id)
    service.analyze_project(test_db, project=project, request=request)

    assert captured.get("business_type_code") == "0411"
    assert captured.get("business_group") == "construction"


def test_select_base_rate_construction_uses_recent_target_weight():
    """construction 그룹: 단봉 분포 → recent_target 비중 0.6."""
    from app.ai.predictors.historical import select_competitive_base_rate

    rate = select_competitive_base_rate(
        category="construction",
        description="OO 건축공사",
        sample_size=20,
        mean_rate=0.90,
        median_rate=0.903,
        recent_median_rate=0.905,
        competitive_quantile_rate=0.900,
        heuristic_rate=0.88,
        business_group="construction",
    )
    # 0.905*0.6 + 0.903*0.3 + 0.88*0.1 = 0.9019
    assert 0.900 <= rate <= 0.910


def test_select_base_rate_service_emphasizes_competitive_quantile():
    """service 그룹: 양봉 분포 → competitive_quantile_rate 비중 0.5."""
    from app.ai.predictors.historical import select_competitive_base_rate

    rate = select_competitive_base_rate(
        category="service",
        description="OO 연구개발용역",
        sample_size=20,
        mean_rate=0.90,
        median_rate=0.883,
        recent_median_rate=0.88,
        competitive_quantile_rate=0.83,
        heuristic_rate=0.85,
        business_group="service",
    )
    # 0.83*0.5 + 0.883*0.35 + 0.85*0.15 = 0.8516
    assert 0.83 <= rate <= 0.88


def test_select_base_rate_falls_back_when_group_missing():
    """business_group=None이면 기존 category-keyed 로직 사용."""
    from app.ai.predictors.historical import select_competitive_base_rate

    rate = select_competitive_base_rate(
        category="construction",
        description="OO 공사",
        sample_size=20,
        mean_rate=0.90,
        median_rate=0.903,
        recent_median_rate=0.905,
        competitive_quantile_rate=0.900,
        heuristic_rate=0.88,
        business_group=None,
    )
    assert 0.85 <= rate <= 0.95


def test_guardrail_uses_group_key_when_available():
    from app.ai.price_prediction import _resolve_floor_bid_rate, _resolve_ceiling_bid_rate

    floor = _resolve_floor_bid_rate(category="service", business_group="service")
    ceiling = _resolve_ceiling_bid_rate(category="construction", business_group="construction")
    assert floor == 0.70
    assert ceiling == 0.93


def test_guardrail_falls_back_to_category_when_group_missing():
    from app.ai.price_prediction import _resolve_floor_bid_rate

    floor = _resolve_floor_bid_rate(category="service", business_group=None)
    assert floor == 0.87


def test_predictor_uses_group_calibration_prior(monkeypatch):
    """manifest group_calibration이 주어지면 base_rate가 prior median에 끌린다."""
    from app.ai.predictors.historical import HistoricalStatisticalPredictor
    from app.ai.predictors.base import PricePredictionContext

    monkeypatch.setattr(
        "app.ai.predictors.historical.load_group_calibration",
        lambda: {"service": {"median_rate": 0.883, "std": 0.059, "sample_count": 8000}},
        raising=False,
    )
    predictor = HistoricalStatisticalPredictor()
    context = PricePredictionContext(
        budget=100_000_000.0,
        description="OO 용역",
        historical_records=[],  # 데이터 부족 → prior에 더 의존해야 함
        category="service",
        business_type_code="0621",
        business_group="service",
    )
    result = predictor.predict(context)
    bid_rate = float(result.get("predicted_bid_rate") or 0)
    assert 0.83 <= bid_rate <= 0.93
