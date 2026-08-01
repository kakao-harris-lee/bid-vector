"""app.domain.aggregates 단일 출처 + 오차율·평균 콜사이트 특성화(characterization).

목적:
- 신설 순수 함수 :func:`error_rate`/:func:`average` 의 경계(0/음수 분모, None,
  빈 입력, 자릿수)를 검증한다.
- 각 콜사이트를 도메인 헬퍼 위임으로 교체한 뒤에도 **동작이 불변**임을 golden 으로
  고정한다. 아래 grid 가 리팩터 전/후 diff 0 의 근거다.
- 자릿수(4·6)와 0-분모 폴백(None vs 0.0)이 콜사이트마다 다른 "중복처럼 보이는 상이"를
  파라미터·`or 0.0` 으로 흡수했음을 명시한다.
"""

from __future__ import annotations

import pytest

from app.domain.aggregates import average, error_rate, rate


# --- error_rate: 순수 규칙 경계 -------------------------------------------


@pytest.mark.parametrize(
    "value, reference, expected",
    [
        (90.0, 100.0, 0.1),
        (110.0, 100.0, 0.1),
        (100.0, 100.0, 0.0),
        (0.0, 100.0, 1.0),
        (None, 100.0, None),  # value None → None (dashboard/feedback 가드)
        (90.0, 0.0, None),  # 0 분모 → None
        (90.0, -5.0, None),  # 음수 분모 → None
        (None, 0.0, None),  # 둘 다 무효 → None
    ],
)
def test_error_rate_boundaries(
    value: float | None, reference: float, expected: float | None
) -> None:
    assert error_rate(value, reference) == expected


def test_error_rate_matches_inline_formula() -> None:
    # dashboard/feedback/reporting/paper/holdouts 의 기존 인라인식 abs(a-b)/b 와 동일.
    for value, reference in [(88.0, 100.0), (123.4, 99.9), (0.0, 1.0), (250.0, 200.0)]:
        assert error_rate(value, reference) == abs(value - reference) / reference


# --- average: 순수 규칙 경계 ----------------------------------------------


def test_average_empty_returns_none() -> None:
    assert average([], digits=4) is None
    assert average([], digits=6) is None


@pytest.mark.parametrize("digits", [4, 6])
def test_average_matches_inline_formula(digits: int) -> None:
    for values in [[1.0], [1.0, 2.0, 3.0], [0.1, 0.2, 0.3], [87.5, 88.1], [0.0]]:
        assert average(values, digits=digits) == round(
            sum(values) / len(values), digits
        )


def test_average_float_cast_removal_is_noop() -> None:
    # prediction_reporting/analytics_reporting 의 sum(float(v) for v ...) 캐스트 제거가
    # int/float 입력(Decimal 미유입, 값 규모 << 2**53)에서 no-op 임을 고정.
    for values in [[1, 2, 3, 4], [1, 2.5, 3], [10, 20, 30]]:
        cast = round(sum(float(v) for v in values) / len(values), 4)
        assert average(values, digits=4) == cast


def test_average_digits_is_required_keyword() -> None:
    with pytest.raises(TypeError):
        average([1.0, 2.0])  # type: ignore[call-arg]


# --- rate: 순수 규칙 경계 -------------------------------------------------


@pytest.mark.parametrize(
    "numerator, denominator, expected",
    [
        (1, 2, 0.5),
        (5, 5, 1.0),
        (0, 5, 0.0),
        (1, 3, 0.3333),
        (2, 3, 0.6667),
        (1, 0, 0.0),  # 0 분모 → 0.0 폴백 (error_rate 의 None 과 갈리는 지점)
        (1, -4, 0.0),  # 음수 분모 → 0.0 폴백
        (0, 0, 0.0),
    ],
)
def test_rate_boundaries(numerator: int, denominator: int, expected: float) -> None:
    assert rate(numerator, denominator, digits=4) == expected


def test_rate_matches_inline_formula() -> None:
    # 리포팅 두 곳의 기존 인라인식 round(n/d, 4) 와 동일.
    for numerator, denominator in [(3, 7), (11, 13), (0, 9), (99, 100), (12, 5)]:
        assert rate(numerator, denominator, digits=4) == round(
            numerator / denominator, 4
        )


def test_rate_digits_is_required_keyword() -> None:
    with pytest.raises(TypeError):
        rate(1, 2)  # type: ignore[call-arg]


# --- 콜사이트 위임 등가(golden) -------------------------------------------


@pytest.mark.parametrize(
    "numerator, denominator",
    [(0, 0), (3, 0), (3, -1), (0, 10), (7, 10), (1, 3), (10, 10)],
)
def test_reporting_rate_delegates_unchanged(numerator: int, denominator: int) -> None:
    # analytics_reporting/prediction_reporting 의 `_rate` 는 바이트 동일 사본이었다.
    # 통합 후에도 두 메서드가 기존 인라인식과 값이 같아야 한다(0-분모 → 0.0).
    from app.services.analytics_reporting.base import _AnalyticsReportingBase
    from app.services.prediction_reporting import PredictionReportingService

    old = 0.0 if denominator <= 0 else round(numerator / denominator, 4)
    assert _AnalyticsReportingBase()._rate(numerator, denominator) == old
    assert PredictionReportingService()._rate(numerator, denominator) == old


def test_dashboard_compute_error_rate_delegates_unchanged() -> None:
    from app.services.dashboard_summary import _compute_error_rate

    assert _compute_error_rate(None, 100.0) is None
    assert _compute_error_rate(90.0, 0.0) is None
    assert _compute_error_rate(90.0, 100.0) == abs(90.0 - 100.0) / 100.0


def test_predictor_backtest_average_delegates_unchanged() -> None:
    from app.ai.predictor_backtest import _average

    assert _average([]) is None
    assert _average([0.1, 0.2, 0.3]) == round(sum([0.1, 0.2, 0.3]) / 3, 6)


@pytest.mark.parametrize(
    "paper, winning",
    [(88.0, 100.0), (100.0, 100.0), (120.0, 100.0), (50.0, 0.0), (50.0, -1.0)],
)
def test_paper_bidding_error_fallback_equivalent(paper: float, winning: float) -> None:
    # paper_bidding_backtest: 기존 `abs(delta)/winning if winning>0 else 0.0` 를
    # `error_rate(...) or 0.0` 로 교체 — 모든 분기에서 값 동일.
    amount_delta = paper - winning
    old = abs(amount_delta) / winning if winning > 0 else 0.0
    new = error_rate(paper, winning) or 0.0
    assert old == new


@pytest.mark.parametrize("price, actual", [(88.0, 100.0), (100.0, 100.0), (120.0, 100.0)])
def test_holdouts_scenario_error_pct_equivalent(price: float, actual: float) -> None:
    # backtest_latest_award_holdouts.scenario_metrics: actual_amount>0 보장 도메인에서
    # `round(abs(price-actual)/actual, 6)` 와 `round(error_rate(...) or 0.0, 6)` 동일.
    amount_error = price - actual
    old = round(abs(amount_error) / actual, 6)
    new = round(error_rate(price, actual) or 0.0, 6)
    assert old == new
