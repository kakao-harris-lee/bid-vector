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
