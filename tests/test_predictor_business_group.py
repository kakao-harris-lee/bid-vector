"""Tests for business_group propagation through PricePredictionContext."""

import pytest

from app.ai.price_prediction import predict_price
from app.ai.predictors.base import PricePredictionContext
from app.models.models import Project  # noqa: F401 — registers models to Base.metadata
from app.schemas.schemas import OpportunityAnalysisRequest  # noqa: F401


class CapturingPricePredictionPort:
    def __init__(self) -> None:
        self.kwargs = {}

    def predict_price(self, **kwargs):
        self.kwargs = kwargs
        return {
            "predicted_price": 90_000_000.0,
            "price_range_min": 89_000_000.0,
            "price_range_max": 91_000_000.0,
            "confidence_score": 0.7,
            "model_version": "fake",
            "predictor_name": "fake",
            "predictor_family": "fake",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "predicted_bid_rate": 0.9,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "fake",
        }


class StaticBidRecommendationPort:
    def recommend(self, *, project_data, user_data):
        return {
            "recommended_bid": 90_000_000.0,
            "confidence_score": 0.8,
            "reasoning": "fake",
            "market_analysis": {},
        }


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
    # The port factories are called by ``_OpportunityAnalysisBase.__init__`` and
    # resolved in its namespace, which moved to the ``base`` submodule when
    # ``opportunity_analysis`` was decomposed into a package; patch them where they
    # are now resolved (assertions unchanged).
    from app.services.opportunity_analysis import base as oa_base_module

    price_port = CapturingPricePredictionPort()

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

    monkeypatch.setattr(oa_base_module, "build_price_prediction_port", lambda: price_port)
    monkeypatch.setattr(oa_base_module, "build_bid_recommendation_port", lambda: StaticBidRecommendationPort())
    service = oa_module.OpportunityAnalysisService()
    # analyze_project is the entry method; build a minimal request
    request = OpportunityAnalysisRequest(project_id=project.id, legal_floor_bid_rate=87.995)
    service.analyze_project(test_db, project=project, request=request)

    assert price_port.kwargs.get("business_type_code") == "0411"
    assert price_port.kwargs.get("business_group") == "construction"
    assert price_port.kwargs.get("legal_floor_bid_rate") == 87.995


def test_opportunity_analysis_uses_injected_prediction_ports(test_db):
    from app.services.opportunity_analysis import OpportunityAnalysisService

    price_port = CapturingPricePredictionPort()
    service = OpportunityAnalysisService(
        price_prediction_port=price_port,
        bid_recommendation_port=StaticBidRecommendationPort(),
    )
    project = Project(
        title="건축공사 포트 검증",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    test_db.add(project)
    test_db.flush()

    request = OpportunityAnalysisRequest(project_id=project.id, legal_floor_bid_rate=87.995)
    service.analyze_project(test_db, project=project, request=request)

    assert price_port.kwargs["business_type_code"] == "0411"
    assert price_port.kwargs["business_group"] == "construction"
    assert price_port.kwargs["legal_floor_bid_rate"] == 87.995


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
    bid_rate = float(result.predicted_bid_rate or 0)
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


def test_small_construction_high_rate_tail_targets_group_ceiling():
    """Small construction bids with high historical anchor should target the safe upper band."""
    from app.ai.predictors.historical import apply_high_rate_distribution_adjustment

    rate, adjustment = apply_high_rate_distribution_adjustment(
        0.9272,
        category="construction",
        description="원상복구 및 사무실 조성공사",
        business_group="construction",
        budget=40_250_000.0,
        historical_summary={
            "sample_size": 1000,
            "recent_sample_size": 20,
            "recent_median_bid_rate": 0.9028,
            "recent_upper_quantile_bid_rate": 0.9051,
            "upper_quantile_bid_rate": 0.9050,
            "recent_rate_ge_0_93_share": 0.0,
            "recent_rate_ge_0_95_share": 0.0,
            "recent_rate_ge_0_98_share": 0.0,
        },
    )

    assert rate == 0.93
    assert adjustment["reason"] == "construction_small_budget_high_rate_target"


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


# ---------------------------------------------------------------------------
# Agency-keyed bid-rate band (Lever 1): 한국수산자원공단 recenter to the UPPER edge
# of the realized 88.001–88.053% winning-floor band. The raw 예정가-basis band
# [0.8806, 0.882] is converted to a 기초금액 basis via E[사정률] (base 정합화 P0), so
# the resolver returns raw × E[사정률]. Keys match by NORMALIZED substring so regional
# bureaus inherit the HQ band.
# ---------------------------------------------------------------------------


def _marine_edges():
    """Converted (기초금액-basis) marine agency band = raw 예정가-basis band × E[사정률]."""
    from app.core.config import settings

    assessment = settings.PREDICTION_AGENCY_BAND_ASSESSMENT_RATES["한국수산자원공단"]
    return (
        settings.PREDICTION_AGENCY_MINIMUM_BID_RATES["한국수산자원공단"] * assessment,
        settings.PREDICTION_AGENCY_MAXIMUM_BID_RATES["한국수산자원공단"] * assessment,
    )


def test_agency_floor_raises_above_category_for_fisheries():
    """한국수산자원공단 converted agency floor RAISES the service category floor(0.87)."""
    from app.ai.price_prediction import _resolve_floor_bid_rate

    floor = _resolve_floor_bid_rate(
        category="service", business_group="service", agency_name="한국수산자원공단"
    )
    marine_floor, _ = _marine_edges()
    assert floor == pytest.approx(marine_floor), "converted agency floor must tighten above category/group floor(0.87)"
    assert floor > 0.87


def test_agency_ceiling_lowers_below_category_for_fisheries():
    """한국수산자원공단 converted agency ceiling LOWERS the service ceiling(1.0)."""
    from app.ai.price_prediction import _resolve_ceiling_bid_rate

    ceiling = _resolve_ceiling_bid_rate(
        category="service", business_group="service", agency_name="한국수산자원공단"
    )
    _, marine_ceiling = _marine_edges()
    assert ceiling == pytest.approx(marine_ceiling), "converted agency ceiling must tighten below category/group ceiling(1.0)"
    assert ceiling < 1.0


def test_agency_band_matches_regional_bureau_substring():
    """발주처 '한국수산자원공단동해본부' (regional bureau) inherits the HQ band via substring."""
    from app.ai.price_prediction import _resolve_ceiling_bid_rate, _resolve_floor_bid_rate

    marine_floor, marine_ceiling = _marine_edges()
    for agency in (
        "한국수산자원공단동해본부",
        "한국수산자원공단남해본부",
        "한국수산자원공단 제주본부",  # whitespace must be normalized away
    ):
        floor = _resolve_floor_bid_rate(
            category="service", business_group="service", agency_name=agency
        )
        ceiling = _resolve_ceiling_bid_rate(
            category="service", business_group="service", agency_name=agency
        )
        assert floor == pytest.approx(marine_floor), f"{agency} should inherit converted agency floor, got {floor}"
        assert ceiling == pytest.approx(marine_ceiling), f"{agency} should inherit converted agency ceiling, got {ceiling}"


def test_agency_band_longest_key_wins():
    """When several agency keys match, the most specific (longest) key wins.

    _resolve_agency_bid_rate takes the rate map directly, so we exercise the
    substring-precedence logic without mutating global settings.
    """
    from app.ai.price_prediction import _resolve_agency_bid_rate

    rate_map = {
        "한국수산자원공단": 0.8806,
        "한국수산자원공단동해본부": 0.885,  # more specific → must win for 동해본부
    }
    # 동해본부: both keys are substrings → longest (동해본부) wins.
    assert _resolve_agency_bid_rate("한국수산자원공단동해본부", rate_map) == 0.885
    # 남해본부: only the HQ key matches → HQ rate.
    assert _resolve_agency_bid_rate("한국수산자원공단남해본부", rate_map) == 0.8806
    # Whitespace in the issuing-agency name is normalized away before matching.
    assert _resolve_agency_bid_rate("한국수산자원공단 동해본부", rate_map) == 0.885


def test_agency_ceiling_does_not_undercut_protected_category_floor(monkeypatch):
    """§4.7 protected floor precedence: a category floor ABOVE the agency ceiling
    must NOT be undercut. The agency ceiling (0.882) tries to lower the band below
    a protected 0.90 category floor; the ceiling<floor→ceiling=floor guard in
    _apply_prediction_guardrails must hold, so no candidate prices below 0.90.
    """
    from app.ai import price_prediction as pp
    from app.ai.price_prediction import (
        _resolve_ceiling_bid_rate,
        _resolve_floor_bid_rate,
        predict_price,
    )

    # Inject a synthetic category whose floor (0.90) exceeds the agency ceiling (0.882).
    monkeypatch.setitem(pp.settings.PREDICTION_CATEGORY_MINIMUM_BID_RATES, "protectedfloor", 0.90)

    floor = _resolve_floor_bid_rate(
        category="protectedfloor", business_group=None, agency_name="한국수산자원공단"
    )
    ceiling = _resolve_ceiling_bid_rate(
        category="protectedfloor", business_group=None, agency_name="한국수산자원공단"
    )
    # Resolver: the converted agency floor cannot lower the protected category floor(0.90).
    assert floor == 0.90, "protected category floor must not be undercut by the agency band"
    # Resolver returns the converted agency ceiling; the guardrail then raises it to the floor.
    _, marine_ceiling = _marine_edges()
    assert ceiling == pytest.approx(marine_ceiling)

    pred = predict_price(
        budget=100_000_000.0,
        category="protectedfloor",
        description="보호 floor 검증",
        historical_records=[{"bid_rate": 0.80} for _ in range(20)],
        agency_name="한국수산자원공단",
        business_group=None,
    )
    # ceiling<floor guard equalizes the band at 0.90 → nothing prices below the floor.
    assert pred["floor_bid_rate"] == 0.90
    assert pred["predicted_bid_rate"] >= 0.90 - 1e-9
    for candidate in pred["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= 0.90 - 1e-9, candidate


def test_agency_band_unchanged_for_non_matching_agency():
    """Non-matching agency (서울특별시) leaves category/group resolution untouched."""
    from app.ai.price_prediction import _resolve_ceiling_bid_rate, _resolve_floor_bid_rate

    floor = _resolve_floor_bid_rate(
        category="service", business_group="service", agency_name="서울특별시"
    )
    ceiling = _resolve_ceiling_bid_rate(
        category="service", business_group="service", agency_name="서울특별시"
    )
    # Identical to the no-agency resolution (category 0.87 / 1.0).
    assert floor == _resolve_floor_bid_rate(category="service", business_group="service")
    assert ceiling == _resolve_ceiling_bid_rate(category="service", business_group="service")
    assert floor == 0.87
    assert ceiling == 1.0


def test_agency_band_stays_within_hard_bounds():
    """Agency band resolves within the hard clamp_bid_rate [0.7, 1.4] bounds."""
    from app.ai.price_prediction import _resolve_ceiling_bid_rate, _resolve_floor_bid_rate

    floor = _resolve_floor_bid_rate(
        category="service", business_group="service", agency_name="한국수산자원공단동해본부"
    )
    ceiling = _resolve_ceiling_bid_rate(
        category="service", business_group="service", agency_name="한국수산자원공단동해본부"
    )
    assert 0.7 <= floor <= 1.4
    assert 0.7 <= ceiling <= 1.4
    # The band stays a valid (non-inverted) interval.
    assert floor <= ceiling


def test_agency_floor_vs_legal_floor_binding_and_safe_margin():
    """agency floor vs legal floor: whichever is higher BINDS, and the generic safe
    margin only re-applies when the AGENCY floor is NOT the binding edge (Finding 1).

    Exercises _resolve_guardrail_context directly so the attribution + safe-margin
    decision is asserted without granularity/candidate noise.
    """
    from app.ai.price_prediction import _resolve_guardrail_context
    from app.core.config import settings

    # The safe-margin bypass is only meaningful if a non-zero margin is configured.
    assert settings.PREDICTION_FLOOR_SAFETY_MARGIN_RATE > 0

    # Legal floor BELOW the agency floor → agency floor binds, safe margin bypassed.
    below = _resolve_guardrail_context(
        budget=100_000_000.0,
        category="service",
        business_group="service",
        legal_floor_bid_rate=0.85,
        agency_name="한국수산자원공단",
    )
    marine_floor, _ = _marine_edges()
    assert below.floor_from_agency is True
    assert below.floor_bid_rate == pytest.approx(marine_floor)
    assert below.safe_floor_bid_rate == pytest.approx(marine_floor)  # no margin (agency target sits at floor)

    # Legal floor just ABOVE the converted agency floor but still WITHIN the converted
    # band → legal binds and the generic safe margin re-applies (there is headroom below
    # the converted ceiling). A legal floor above the whole band would collapse it (the
    # ceiling<floor guard), leaving no margin room — so it is derived from the band edge.
    legal_above = marine_floor + 0.0001
    above = _resolve_guardrail_context(
        budget=100_000_000.0,
        category="service",
        business_group="service",
        legal_floor_bid_rate=legal_above,
        agency_name="한국수산자원공단",
    )
    assert above.floor_from_agency is False
    assert above.floor_bid_rate == pytest.approx(legal_above)
    assert above.safe_floor_bid_rate > above.floor_bid_rate  # generic margin re-applied

    # Monotonic tightening either way: the binding floor never loosens.
    assert above.floor_bid_rate >= below.floor_bid_rate
    assert above.safe_floor_bid_rate >= below.safe_floor_bid_rate
