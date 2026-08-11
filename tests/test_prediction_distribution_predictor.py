"""분포 predictor 테스트 — 계약·계층 수축 방향·guardrail 뮤테이션 증명.

guardrail red line 은 "결과 ≥ floor" 단언만으로는 **구조적으로 실패할 수 없다**
(#360 교훈: guardrail 이 항상 끌어올리므로). 그래서 여기서는 (a) 원시 predictor
산출이 하한 **미만**임을 먼저 단언해 시나리오가 실제로 guardrail 을 발동시킴을
증명하고, (b) guardrail 우회 뮤턴트(단계 무력화)에서는 red line 이 **실패함**을
같은 테스트가 보인다.
"""

import pytest

from app.ai.predictors.base import PredictionResult, PricePredictionContext
from app.ai.predictors.distribution import ReserveDrawDistributionPredictor
from app.ai.predictors.registry import build_default_predictor_registry
from app.ai.price_prediction import predict_price
from app.ai.price_prediction import orchestration as prediction_orchestration
from app.core.config import settings

_BASE_AMOUNT = 100_000_000.0
_LEGAL_FLOOR = 0.88


def _reserve_record(
    *,
    center: float = 0.99,
    spread: float = 0.02,
    bid_to_assessment_ratio: float | None = 0.87,
    agency_name: str | None = None,
    category: str = "construction",
    base_amount: float = _BASE_AMOUNT,
    base_amount_basis: str | None = None,
) -> dict:
    """15개 예비가격이 [center−spread, center+spread] 균등 격자인 이력 행."""
    ratios = [center - spread + ((2 * spread) * index / 14) for index in range(15)]
    reserve_prices = [base_amount * ratio for ratio in ratios]
    record = {
        "base_amount": base_amount,
        "reserve_prices": reserve_prices,
        "selected_numbers": [1, 5, 10, 15],
        "category": category,
    }
    if agency_name is not None:
        record["agency_name"] = agency_name
    if base_amount_basis is not None:
        record["base_amount_basis"] = base_amount_basis
    if bid_to_assessment_ratio is not None:
        realized = (
            reserve_prices[0] + reserve_prices[4] + reserve_prices[9] + reserve_prices[14]
        ) / (4 * base_amount)
        record["bid_rate"] = bid_to_assessment_ratio * realized
    return record


def _context(
    records: list[dict],
    *,
    agency_name: str | None = None,
    category: str = "construction",
) -> PricePredictionContext:
    return PricePredictionContext(
        budget=_BASE_AMOUNT,
        category=category,
        description="항만 준설 공사",
        historical_records=tuple(records),
        agency_name=agency_name,
    )


@pytest.fixture
def experimental_on(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)


def test_availability_requires_experimental_flag(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", False)
    availability = ReserveDrawDistributionPredictor().check_availability(
        _context([_reserve_record() for _ in range(10)])
    )
    assert not availability.available
    assert "Experimental" in availability.reason


def test_availability_requires_enough_reserve_records(experimental_on):
    availability = ReserveDrawDistributionPredictor().check_availability(
        _context([_reserve_record() for _ in range(3)])
    )
    assert not availability.available
    assert "reserve prices" in availability.reason


def test_availability_excludes_non_clean_base_rows(experimental_on):
    # 전부 오염 태그(derived-yega)면 관측 0 — 사용 불가로 떨어져야 한다(#199).
    availability = ReserveDrawDistributionPredictor().check_availability(
        _context([
            _reserve_record(base_amount_basis="derived-yega") for _ in range(10)
        ])
    )
    assert not availability.available


def test_availability_passes_with_clean_tagged_and_untagged_rows(experimental_on):
    records = [_reserve_record(base_amount_basis="clean") for _ in range(5)]
    records += [_reserve_record() for _ in range(5)]
    availability = ReserveDrawDistributionPredictor().check_availability(_context(records))
    assert availability.available


def test_predict_maps_distribution_onto_contract_value_table(experimental_on):
    # 중심 0.99 × 비 0.87 → 기준 사정률 ≈ 0.8613. 대칭 격자라 center == 평균.
    records = [_reserve_record(center=0.99, bid_to_assessment_ratio=0.87) for _ in range(12)]
    result = ReserveDrawDistributionPredictor().predict(_context(records))

    assert isinstance(result, PredictionResult)
    assert result.predicted_bid_rate == pytest.approx(0.87 * 0.99, abs=0.002)
    assert result.pricing_mode == "reserve_distribution"
    assert result.model_version == "v1.0-distribution"
    labels = [candidate["label"] for candidate in result.bid_rate_candidates]
    assert labels == ["conservative", "base", "aggressive"]
    rates = [candidate["bid_rate"] for candidate in result.bid_rate_candidates]
    assert rates[0] < rates[1] < rates[2]
    assert result.price_range_min == min(
        candidate["predicted_price"] for candidate in result.bid_rate_candidates
    )
    assert result.price_range_max == max(
        candidate["predicted_price"] for candidate in result.bid_rate_candidates
    )
    assert result.guardrail_applied is False
    assert 0.0 < result.confidence_score <= 0.95


def test_predict_is_deterministic(experimental_on):
    records = [_reserve_record() for _ in range(10)]
    first = ReserveDrawDistributionPredictor().predict(_context(records))
    second = ReserveDrawDistributionPredictor().predict(_context(records))
    assert first == second


def test_agency_history_pulls_posterior_toward_agency_center(experimental_on):
    # 타 기관 40건은 중심 0.98, 대상 기관 30건은 중심 1.01 — 기관 이력이 깊으면
    # 사후 중심이 기관 쪽으로 이동해야 한다(κ=12 대비 n=30 → 자기 가중 ≥ 71%).
    other = [_reserve_record(center=0.98, agency_name="타기관") for _ in range(40)]
    target = [_reserve_record(center=1.01, agency_name="조달청") for _ in range(30)]
    predictor = ReserveDrawDistributionPredictor()

    with_agency = predictor.predict(_context(other + target, agency_name="조달청"))
    without_agency = predictor.predict(_context(other + target, agency_name=None))

    assert with_agency.predicted_bid_rate > without_agency.predicted_bid_rate


def test_default_registry_exposes_distribution_predictor():
    registry = build_default_predictor_registry()
    assert isinstance(registry["distribution"], ReserveDrawDistributionPredictor)


def test_predict_price_selects_distribution_predictor(experimental_on, monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "distribution")
    records = [_reserve_record() for _ in range(10)]

    prediction = predict_price(
        budget=_BASE_AMOUNT,
        category="construction",
        description="항만 준설 공사",
        historical_records=records,
    )

    assert prediction["predictor_name"] == "reserve_draw_distribution"
    assert prediction["predictor_family"] == "distribution"
    assert prediction["pricing_mode"] == "reserve_distribution"


def test_guardrail_red_line_with_bypass_mutant_proof(experimental_on, monkeypatch):
    """red line: 하한 미만 원시 산출이 guardrail 로 상향되고, 우회 뮤턴트면 실패한다."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "distribution")
    # 비 0.808 × 중심 0.99 ≈ 0.80 — 법정하한 0.88 보다 8%p 낮은 원시 산출을 유도.
    records = [_reserve_record(bid_to_assessment_ratio=0.808) for _ in range(10)]
    predictor = ReserveDrawDistributionPredictor()

    # (a) 원시 predictor 산출이 실제로 하한 미만이다 — 이 단언이 없으면 아래
    #     red line 은 guardrail 이 없어도 통과하는 무의미한 검사가 된다(#360).
    raw = predictor.predict(_context(records))
    assert raw.predicted_bid_rate < _LEGAL_FLOOR - 0.05

    kwargs = dict(
        budget=_BASE_AMOUNT,
        category="construction",
        description="항만 준설 공사",
        historical_records=records,
        legal_floor_bid_rate=_LEGAL_FLOOR,
    )
    guarded = predict_price(**kwargs)
    assert guarded["predictor_name"] == "reserve_draw_distribution"
    assert guarded["predicted_bid_rate"] >= _LEGAL_FLOOR - 1e-9
    assert guarded["guardrail_applied"] is True
    assert all(
        candidate["bid_rate"] >= _LEGAL_FLOOR - 1e-9
        for candidate in guarded["bid_rate_candidates"]
    )

    # (b) guardrail 우회 뮤턴트: 단계를 무력화하면 red line 이 실패해야 한다 —
    #     즉 이 테스트는 guardrail 이 살아 있어야만 통과한다.
    monkeypatch.setattr(
        prediction_orchestration,
        "_apply_prediction_guardrails",
        lambda payload, **_kwargs: payload,
    )
    mutant = predict_price(**kwargs)
    assert mutant["predicted_bid_rate"] < _LEGAL_FLOOR
    assert mutant["predicted_bid_rate"] == pytest.approx(raw.predicted_bid_rate, abs=0.005)
