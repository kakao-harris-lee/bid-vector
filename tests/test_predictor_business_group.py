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
    """service 그룹: 양봉 분포 → competitive_quantile_rate 비중 0.5.

    Note: 'OO 청소용역' matches no rate band keyword (returns None), so the
    pure weighting logic is exercised without service_high_negotiated interference.
    """
    from app.ai.predictors.historical import select_competitive_base_rate

    rate = select_competitive_base_rate(
        category="service",
        description="OO 청소용역",  # no rate-band keyword → band=None
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

    # group floor(0.70) must not undercut category floor(0.87) — max() wins
    floor = _resolve_floor_bid_rate(category="service", business_group="service")
    # group ceiling(1.00) vs category ceiling(1.00) — same for service, min() still correct
    ceiling = _resolve_ceiling_bid_rate(category="construction", business_group="construction")
    assert floor == 0.87, "category floor(0.87) must take priority over group floor(0.70)"
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


def test_service_branch_respects_group_ceiling():
    """service 분기가 _resolve_ceiling_bid_rate 상한을 초과하지 않음을 검증.

    service_price_competitive 키워드가 포함된 description이면
    apply_procurement_rate_band()가 cap을 0.90으로 내려야 하지만,
    현재 service/construction 그룹 분기는 그 함수를 호출하지 않아 bypass된다.
    수정 후에는 ceiling clamp가 적용돼 0.90을 넘지 않아야 한다.
    """
    from app.ai.predictors.historical import select_competitive_base_rate

    # 높은 history rate + service_price_competitive 키워드(폐기물)
    rate = select_competitive_base_rate(
        category="service",
        description="폐기물 처리용역",  # → service_price_competitive band
        sample_size=20,
        mean_rate=0.95,
        median_rate=0.95,
        recent_median_rate=0.95,
        competitive_quantile_rate=0.95,
        heuristic_rate=0.95,
        business_group="service",
    )
    # service_price_competitive의 0.90 상한이 적용되어야 함
    assert rate <= 0.90


def test_floor_resolver_clamps_to_category_when_group_lower():
    """service 그룹 floor(0.70)가 카테고리 floor(0.87)보다 낮으면 카테고리 floor 우선."""
    from app.ai.price_prediction import _resolve_floor_bid_rate
    floor = _resolve_floor_bid_rate(category="service", business_group="service")
    assert floor >= 0.87, "category floor가 group floor보다 우선이어야 함 (§4.7)"


def test_service_high_negotiated_floor_preserved_in_group_branch():
    """service 그룹 분기에서도 service_high_negotiated 카테고리의 1.0 floor를 보존해야 함."""
    from app.ai.predictors.historical import select_competitive_base_rate

    # 협상에 의한 계약 → service_high_negotiated band → max(base_rate, 1.0) 강제
    history = [{"bid_rate": 0.85} for _ in range(20)]
    rate = select_competitive_base_rate(
        category="service",
        description="OO 협상에 의한 계약",
        sample_size=20,
        mean_rate=0.85,
        median_rate=0.85,
        recent_median_rate=0.85,
        competitive_quantile_rate=0.85,
        heuristic_rate=0.85,
        business_group="service",
    )
    assert rate >= 1.0, "service_high_negotiated band는 1.0 floor를 적용해야 함"


def test_construction_group_ceiling_never_bypassed_for_high_history():
    """REGRESSION: paper_bid id=222 (2026-05-25) returned predicted_bid_rate=1.0043
    on a construction-category bid before Phase B activation. The group ceiling
    of 0.93 must clamp ANY predicted_bid_rate and any per-scenario bid_rate.

    Reproduces the production scenario: construction-category project with
    business_group='construction', high-rate history (would push the model
    above the ceiling), and verifies the ceiling guardrail kicks in.

    This test freezes Phase B guardrail behavior — no implementation change required.
    It is a regression lock: a future refactor that silently removes the ceiling clamp
    will cause this test to fail.
    """
    from app.ai.price_prediction import predict_price
    from app.core.config import settings

    ceiling = float(settings.PREDICTION_GROUP_MAXIMUM_BID_RATES["construction"])  # 0.93
    epsilon = 1e-4

    # Simple dicts work because read_record_value() handles both dicts and ORM objects.
    # Only bid_rate is needed to drive summarize_historical_records toward a high value.
    history = [{"bid_rate": 0.985} for _ in range(60)]

    pred = predict_price(
        budget=810_072_727.0,
        category="construction",
        description="제주대학교 안전환경개선 건축공사",
        historical_records=history,
        agency_name="제주대학교",
        feedback_calibration=None,
        business_type_code="0411",
        business_group="construction",
    )

    rate = float(pred.get("predicted_bid_rate") or 0)
    assert rate <= ceiling + epsilon, (
        f"construction ceiling {ceiling} bypassed: predicted_bid_rate={rate}, "
        f"guardrail_applied={pred.get('guardrail_applied')}, "
        f"guardrail_reason={pred.get('guardrail_reason')}"
    )
    assert pred.get("guardrail_applied") is True, (
        f"guardrail_applied must be True when history pushes rate above ceiling; "
        f"predicted_bid_rate={rate}, guardrail_reason={pred.get('guardrail_reason')}"
    )

    # All per-scenario bid_rates in bid_rate_candidates must also respect the ceiling.
    for candidate in pred.get("bid_rate_candidates", []):
        scenario_rate = float(candidate.get("bid_rate") or 0)
        label = candidate.get("label", "?")
        assert scenario_rate <= ceiling + epsilon, (
            f"scenario '{label}' bid_rate {scenario_rate} > ceiling {ceiling}"
        )
