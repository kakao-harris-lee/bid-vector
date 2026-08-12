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
    # basis 필터는 fail-closed(L4-3) — 픽스처 기본값을 clean 으로 태깅하고,
    # 미태깅 거절은 base_amount_basis=None 케이스가 명시적으로 검증한다.
    base_amount_basis: str | None = "clean",
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


def test_untagged_basis_rows_are_rejected_fail_closed(experimental_on):
    """basis 미태깅 행은 거절(fail-closed) — models.py 의 clean-only 강제(L4-3).

    태그 없는 행을 통과시키면 classify_base_basis 가 suspect-fractional 로 거절했을
    비정수 기초금액 행(실측 노출 3행 전부)이 바로 그 구멍으로 들어온다.
    """
    availability = ReserveDrawDistributionPredictor().check_availability(
        _context([_reserve_record(base_amount_basis=None) for _ in range(10)])
    )
    assert not availability.available
    assert "(got 0)" in availability.reason


def test_predict_maps_distribution_onto_contract_value_table(experimental_on):
    # 사정률 중심 0.99 × 환산 비 0.87 → 기준 투찰율 ≈ 0.8613. 대칭 격자라 center == 평균.
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


class _FixedRatePredictor(prediction_orchestration.BasePricePredictor):
    """고정 투찰율 fake — 백테스트 승격 경로 검증용 (컨텍스트 캡처 포함)."""

    family = "test"

    def __init__(self, name: str, bid_rate: float) -> None:
        self.name = name
        self._bid_rate = bid_rate
        self.contexts: list[PricePredictionContext] = []

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        self.contexts.append(context)
        rate = self._bid_rate
        return PredictionResult(
            predicted_price=context.budget * rate,
            price_range_min=context.budget * (rate - 0.01),
            price_range_max=context.budget * (rate + 0.01),
            confidence_score=0.7,
            model_version="fixed-v1",
            pricing_mode="historical_blend",
            historical_sample_size=context.historical_sample_size,
            agency_match_sample_size=0,
            predicted_bid_rate=rate,
            bid_rate_candidates=[
                {"label": "base", "bid_rate": rate, "predicted_price": context.budget * rate},
            ],
            reserve_price_context=None,
            feedback_calibration=None,
            guardrail_applied=False,
            guardrail_reason=None,
            floor_bid_rate=None,
            floor_price=None,
            explanation="fixed-rate probe",
        )


def _bid_rate_history(count: int) -> list[dict]:
    return [
        {
            "bid_rate": 0.91,
            "base_amount": _BASE_AMOUNT,
            "predicted_price": _BASE_AMOUNT * 0.91,
            "agency_name": f"기관{index}",
            "category": "construction" if index % 2 == 0 else "service",
        }
        for index in range(count)
    ]


def test_distribution_is_excluded_from_backtest_best_candidate(monkeypatch):
    """정확도가 더 좋아도 distribution 은 best 후보가 될 수 없다(자동 승격 차단).

    best_predictor_key 는 auto 선택과 manifest recommended_env 로 흘러가는 승격
    경로다. 결과(results)에는 그대로 평가·보고되어야 비교 수치는 계속 쌓인다.
    """
    from app.ai.predictor_backtest import build_predictor_backtest_report

    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 5)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 5)
    context = PricePredictionContext(
        budget=_BASE_AMOUNT,
        category="construction",
        description="승격 제외 검증",
        historical_records=tuple(_bid_rate_history(12)),
    )

    report = build_predictor_backtest_report(
        context,
        {
            "distribution": _FixedRatePredictor("dist_probe", 0.91),
            "historical": _FixedRatePredictor("hist_probe", 0.95),
        },
    )

    by_key = {result["predictor_key"]: result for result in report["results"]}
    assert by_key["distribution"]["status"] == "completed"
    # 제외가 '우연히 진 것'이 아님을 증명: distribution 오차가 명백히 더 작다.
    assert (
        by_key["distribution"]["average_absolute_error_rate"]
        < by_key["historical"]["average_absolute_error_rate"]
    )
    assert report["best_predictor_key"] == "historical"


def test_auto_selector_never_promotes_distribution(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "auto")
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 5)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 5)

    prediction = predict_price(
        budget=_BASE_AMOUNT,
        category="construction",
        description="auto 승격 차단 검증",
        historical_records=_bid_rate_history(12),
        predictor_registry={
            "distribution": _FixedRatePredictor("dist_probe", 0.91),
            "historical": _FixedRatePredictor("hist_probe", 0.95),
        },
    )

    assert prediction["predictor_name"] == "hist_probe"
    assert prediction["backtest_report"]["best_predictor_key"] == "historical"


def test_backtest_record_context_optin_carries_per_row_agency_category(monkeypatch):
    """use_record_context=True 는 홀드아웃 행 자신의 기관·공종으로 평가한다.

    기본(False)은 종전과 같이 호출 공고의 값을 전파한다 — 라이브 auto 경로 불변.
    """
    from app.ai.predictor_backtest import build_predictor_backtest_report

    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 3)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 5)
    records = _bid_rate_history(9)
    context = PricePredictionContext(
        budget=_BASE_AMOUNT,
        category="all",
        description="행별 컨텍스트 검증",
        historical_records=tuple(records),
        agency_name=None,
    )

    per_row = _FixedRatePredictor("per_row", 0.91)
    build_predictor_backtest_report(context, {"probe": per_row}, use_record_context=True)
    assert [captured.agency_name for captured in per_row.contexts] == [
        record["agency_name"] for record in records[-3:]
    ]
    assert [captured.category for captured in per_row.contexts] == [
        record["category"] for record in records[-3:]
    ]

    propagated = _FixedRatePredictor("propagated", 0.91)
    build_predictor_backtest_report(context, {"probe": propagated})
    assert {captured.agency_name for captured in propagated.contexts} == {None}
    assert {captured.category for captured in propagated.contexts} == {"all"}


def test_extractor_requires_exactly_fifteen_reserve_prices(experimental_on):
    """다회차 누적(30개)·부분 결측(14개) 행은 관측에서 제외 — 캘리브레이션 코어와 정합."""
    incomplete = _reserve_record()
    incomplete["reserve_prices"] = incomplete["reserve_prices"][:14]
    accumulated = _reserve_record()
    accumulated["reserve_prices"] = accumulated["reserve_prices"] * 2  # 30개

    availability = ReserveDrawDistributionPredictor().check_availability(
        _context([incomplete, accumulated] * 5)
    )
    assert not availability.available
    assert "(got 0)" in availability.reason


def _orm_reserve_row(*, basis: str | None, base_amount: float = 100_000_000.0):
    """직렬화기 입력용 최소 ORM 행 — non-clean 이면 estimate(예비가 중점) 치환 대상."""
    import json as _json

    from app.models.models import HistoricalData

    ratios = [0.97 + (0.04 * index / 14) for index in range(15)]
    reserve_prices = [base_amount * ratio for ratio in ratios]
    return HistoricalData(
        id=1,
        notice_number="SERIALIZER-BASIS-1",
        category="construction",
        agency_name="조달청",
        base_amount=base_amount,
        base_amount_basis=basis,
        base_amount_estimated=(min(reserve_prices) + max(reserve_prices)) / 2,
        predicted_price=base_amount * 0.9,
        bid_rate=0.9,
        reserve_prices=_json.dumps(reserve_prices),
        selected_numbers=_json.dumps([1, 5, 10, 15]),
    )


def test_serializers_carry_base_amount_basis_and_filter_fires(experimental_on):
    """K2 회귀: 두 프로덕션 직렬화기가 basis 태그를 실어야 clean 필터가 실동작한다.

    태그가 빠지면 non-clean 행의 base 는 이미 base_amount_estimated(같은 예비가의
    중점)로 치환돼 있어, 엔진이 예비가/중점(구성상 ≈1.0) 유사관측을 사정률로 소비
    한다 — 필터가 프로덕션에서 한 번도 켜지지 않는 상태였다.
    """
    from app.services.backtest_cutoff import BacktestCutoffService
    from app.services.prediction_dataset import PredictionDatasetService

    non_clean = BacktestCutoffService().serialize_historical_record(
        _orm_reserve_row(basis="derived-yega")
    )
    assert non_clean["base_amount_basis"] == "derived-yega"
    # 자기참조 확인: 직렬화된 base 는 raw 가 아니라 예비가 중점 추정치다.
    assert non_clean["base_amount"] != 100_000_000.0

    dataset_non_clean = PredictionDatasetService()._serialize_series_point(
        _orm_reserve_row(basis="derived-yega"), tender_result=None
    )
    assert dataset_non_clean is not None
    assert dataset_non_clean["base_amount_basis"] == "derived-yega"

    # 필터 발화 증명은 **두 직렬화기 출력 각각**에서 한다 — 한쪽만 증명하면 다른
    # 경로의 키 누락 회귀를 다시 놓친다(이 버그의 원형).
    predictor = ReserveDrawDistributionPredictor()
    for non_clean_row in (non_clean, dataset_non_clean):
        unavailable = predictor.check_availability(
            _context([dict(non_clean_row) for _ in range(10)])
        )
        assert not unavailable.available
        assert "(got 0)" in unavailable.reason  # 관측 0 = 필터가 전 행을 걸렀다

    # 대조군: clean 행 직렬화는 두 경로 모두 관측으로 수용된다.
    clean = BacktestCutoffService().serialize_historical_record(
        _orm_reserve_row(basis="clean")
    )
    dataset_clean = PredictionDatasetService()._serialize_series_point(
        _orm_reserve_row(basis="clean"), tender_result=None
    )
    assert clean["base_amount_basis"] == "clean"
    assert dataset_clean is not None and dataset_clean["base_amount_basis"] == "clean"
    for clean_row in (clean, dataset_clean):
        available = predictor.check_availability(
            _context([dict(clean_row) for _ in range(10)])
        )
        assert available.available
