"""Tests for business_group propagation through PricePredictionContext."""

from app.ai.price_prediction import predict_price
from app.ai.predictors.base import PricePredictionContext


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
