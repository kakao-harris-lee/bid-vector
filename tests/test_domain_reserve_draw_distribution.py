"""값표 테스트 — 복수예비가격 추첨 평균 분포 커널 (완전 열거)."""

import pytest

from app.domain.reserve_draw_distribution import (
    DEFAULT_DRAW_COUNT,
    EXPECTED_RESERVE_PRICE_COUNT,
    draw_mean_moments,
    exact_draw_mean_distribution,
)

# C(5,2)=10 조합의 손계산 값표: (1,2)(1,3)(1,4)(1,5)(2,3)(2,4)(2,5)(3,4)(3,5)(4,5)
_FIVE_CHOOSE_TWO_MEANS = [1.5, 2.0, 2.5, 2.5, 3.0, 3.0, 3.5, 3.5, 4.0, 4.5]


def test_exact_enumeration_matches_hand_computed_value_table():
    distribution = exact_draw_mean_distribution([1.0, 2.0, 3.0, 4.0, 5.0], 2)

    assert distribution.source_count == 5
    assert distribution.draw_count == 2
    assert list(distribution.support) == _FIVE_CHOOSE_TWO_MEANS


def test_exact_mean_equals_population_mean_by_linearity():
    distribution = exact_draw_mean_distribution([1.0, 2.0, 3.0, 4.0, 5.0], 2)
    assert distribution.mean == pytest.approx(3.0)


def test_exact_variance_matches_finite_population_correction_closed_form():
    # pvariance([1..5]) = 2.0 → Var(x̄₂) = (2/2)·((5−2)/(5−1)) = 0.75
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    distribution = exact_draw_mean_distribution(values, 2)
    moment_mean, moment_std = draw_mean_moments(values, 2)

    assert distribution.std**2 == pytest.approx(0.75)
    assert moment_mean == pytest.approx(distribution.mean)
    assert moment_std == pytest.approx(distribution.std)


def test_exact_fifteen_choose_four_support_size_and_moments():
    # 정상 공고 형태: 기초금액 ±2% 안의 15개 예비가격.
    values = [100.0 + (index * 0.25) for index in range(EXPECTED_RESERVE_PRICE_COUNT)]
    distribution = exact_draw_mean_distribution(values, DEFAULT_DRAW_COUNT)
    moment_mean, moment_std = draw_mean_moments(values, DEFAULT_DRAW_COUNT)

    assert len(distribution.support) == 1365
    assert distribution.mean == pytest.approx(sum(values) / len(values))
    assert distribution.mean == pytest.approx(moment_mean)
    assert distribution.std == pytest.approx(moment_std)


def test_draw_mean_moments_zero_variance_when_drawing_all_values():
    mean_value, std_value = draw_mean_moments([2.0, 4.0, 6.0], 3)
    assert mean_value == pytest.approx(4.0)
    assert std_value == 0.0


def test_quantile_endpoints_and_monotonicity():
    # 5값·draw_count=2 는 손계산 값표용 형태다 — 프로덕션은 항상 15/4 이고,
    # quantile/central_interval 은 현재 Phase 3 표면(프로덕션 소비 0)이다(M4-8).
    distribution = exact_draw_mean_distribution([1.0, 2.0, 3.0, 4.0, 5.0], 2)

    assert distribution.quantile(0.0) == pytest.approx(1.5)
    assert distribution.quantile(1.0) == pytest.approx(4.5)
    quantiles = [distribution.quantile(q / 10.0) for q in range(11)]
    assert quantiles == sorted(quantiles)
    # clamp: 범위 밖 q 는 경계 분위수로 떨어진다.
    assert distribution.quantile(-1.0) == distribution.quantile(0.0)
    assert distribution.quantile(2.0) == distribution.quantile(1.0)


def test_central_interval_is_symmetric_quantile_pair():
    distribution = exact_draw_mean_distribution(
        [100.0 + index for index in range(EXPECTED_RESERVE_PRICE_COUNT)],
        DEFAULT_DRAW_COUNT,
    )
    low, high = distribution.central_interval(0.8)
    assert low == pytest.approx(distribution.quantile(0.1))
    assert high == pytest.approx(distribution.quantile(0.9))
    assert low < distribution.mean < high


def test_cumulative_probability_uses_midrank_for_ties():
    distribution = exact_draw_mean_distribution([1.0, 2.0, 3.0, 4.0, 5.0], 2)
    # 값표에서 3.0 미만 4개, 3.0 동률 2개 → (4 + 2/2)/10 = 0.5
    assert distribution.cumulative_probability(3.0) == pytest.approx(0.5)
    assert distribution.cumulative_probability(0.0) == 0.0
    assert distribution.cumulative_probability(99.0) == 1.0


@pytest.mark.parametrize(
    "values,draw_count",
    [
        ([1.0, 2.0, 3.0], 4),  # 표본 부족
        ([1.0, -2.0, 3.0, 4.0], 2),  # 음수
        ([1.0, 0.0, 3.0, 4.0], 2),  # 0
        ([1.0, float("nan"), 3.0, 4.0], 2),  # 비유한
        ([1.0, float("inf"), 3.0, 4.0], 2),  # 비유한
        ([1.0, 2.0, 3.0, 4.0], 0),  # draw_count 비양수
    ],
)
def test_invalid_inputs_fail_loudly(values, draw_count):
    with pytest.raises(ValueError):
        exact_draw_mean_distribution(values, draw_count)


@pytest.mark.parametrize(
    "values,draw_count",
    [
        ([1.0, 2.0, 3.0], 4),
        ([1.0, -2.0, 3.0, 4.0], 2),
        ([1.0, 2.0, 3.0, 4.0], 0),
    ],
)
def test_moments_share_the_same_validation(values, draw_count):
    with pytest.raises(ValueError):
        draw_mean_moments(values, draw_count)
