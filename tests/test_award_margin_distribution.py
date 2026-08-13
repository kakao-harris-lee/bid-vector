"""값표 테스트 — 낙찰자 하한 초과 마진 Δ 의 0 경계 안전 분포 추정.

손으로 계산 가능한 소형 케이스만 쓴다(§4.7-4). 핵심 회귀 고정은 **반사 KDE 의 경계**
두 가지다:

* ``Ĝ(0) = 0`` 정확히 — 경계 누출 0.
* ``Ĝ(δ>0)`` 의 **구체값** — 0 경계만 단언하면 ``margin <= 0`` 이른 반환이 그 단언을
  대신 통과시켜, 반사항을 통째로 지워도 테스트가 안 깨진다(실증됨). δ>0 값표는 닫힌
  형태와 수치적분을 독립 대조해 얻었다.
"""

from math import erf, exp, pi, sqrt
from statistics import pstdev, quantiles

import pytest

from app.domain.assessment_shrinkage import pseudo_count_weight
from app.domain.award_margin_distribution import (
    MIN_BANDWIDTH,
    NEGATIVE_MARGIN_EPSILON,
    LandingObservation,
    MarginDistribution,
    MarginEcdf,
    ReflectedMarginKde,
    ShrunkMarginDistribution,
    TabulatedMarginCdf,
    build_margin_ecdf,
    build_reflected_kde,
    extract_margins,
    shrink_margin_distribution,
    silverman_bandwidth,
    tabulate_margin_cdf,
)

FLOOR_RATE = 0.88


def _observation(
    winning_rate: float, *, floor_rate: float = FLOOR_RATE, assessment: float = 1.0
) -> LandingObservation:
    return LandingObservation(
        floor_rate=floor_rate,
        winning_rate=winning_rate,
        assessment_ratio=assessment,
    )


# --- 마진 추출 --------------------------------------------------------------------
def test_margin_extraction_value_table():
    extraction = extract_margins(
        [_observation(0.8855), _observation(0.8813), _observation(0.88)]
    )
    assert extraction.margins == pytest.approx((0.0, 0.0013, 0.0055))
    assert extraction.accepted_count == 3
    assert extraction.rejected_count == 0


def test_negative_margin_is_rejected_not_clamped():
    # 하한 아래 낙찰은 도메인상 불가능하다(실측 Δ<0 은 전부 award_floor_rate==1.0
    # 오적재). 0 으로 clamp 하면 그 오염 신호가 하한 밀착 스파이크로 위장돼 사라진다.
    extraction = extract_margins([_observation(0.87), _observation(0.8855)])
    assert extraction.negative_count == 1
    assert extraction.margins == pytest.approx((0.0055,))
    assert extraction.noise_snapped_count == 0


def test_float_noise_negative_is_snapped_and_counted_separately():
    # (0.1 + 0.2) - 0.3 = -5.55e-17: 표현 잡음이지 데이터 오류가 아니다.
    noisy_floor = 0.1 + 0.2
    extraction = extract_margins([_observation(0.3, floor_rate=noisy_floor)])
    assert extraction.margins == (0.0,)
    assert extraction.noise_snapped_count == 1
    assert extraction.negative_count == 0


def test_negative_tolerance_boundary_is_closed():
    # Δ 가 정확히 −ε 이면 거부가 아니라 스냅이다(경계 포함). 2^-20 을 쓰는 이유:
    # 0.88 과 같은 binade 안에서 정확히 표현돼 w − f 가 딱 −ε 이 된다(1e-6 으로는
    # 반올림 때문에 −1.0000000000287e-06 이 나와 knife-edge 를 못 짚는다).
    tolerance = 2.0**-20
    on_boundary = _observation(FLOOR_RATE - tolerance)
    assert on_boundary.margin == -tolerance

    exact = extract_margins([on_boundary], negative_tolerance=tolerance)
    assert exact.noise_snapped_count == 1
    assert exact.negative_count == 0
    assert exact.margins == (0.0,)

    beyond = extract_margins(
        [_observation(FLOOR_RATE - (tolerance * 2))], negative_tolerance=tolerance
    )
    assert beyond.negative_count == 1
    assert beyond.accepted_count == 0


def test_negative_tolerance_is_injectable_and_defaults_to_the_declared_epsilon():
    observations = [_observation(FLOOR_RATE - 1e-9)]
    assert extract_margins(observations).negative_count == 1  # 기본 ε=1e-12 → 거부
    assert extract_margins(observations, negative_tolerance=1e-8).noise_snapped_count == 1
    assert NEGATIVE_MARGIN_EPSILON == 1e-12
    with pytest.raises(ValueError):
        extract_margins(observations, negative_tolerance=-1.0)


def test_non_finite_and_non_positive_rates_are_counted_invalid():
    extraction = extract_margins(
        [
            _observation(float("nan")),
            _observation(0.0),
            _observation(0.9, assessment=0.0),
            _observation(0.8855),
        ]
    )
    assert extraction.invalid_count == 3
    assert extraction.accepted_count == 1


def test_landing_observation_exposes_both_basis_axes():
    observation = _observation(0.8855, assessment=0.99)
    assert observation.margin == pytest.approx(0.0055)
    assert observation.realized_floor_rate == pytest.approx(0.8712)
    assert observation.realized_winning_rate == pytest.approx(0.876645)


def test_empty_observation_list_yields_empty_extraction():
    extraction = extract_margins([])
    assert extraction.accepted_count == 0
    assert extraction.rejected_count == 0


# --- ECDF -------------------------------------------------------------------------
def _sample_ecdf() -> MarginEcdf:
    return build_margin_ecdf([0.01, 0.002, 0.0, 0.002, 0.001])


@pytest.mark.parametrize(
    ("margin", "expected"),
    [
        (-0.001, 0.0),
        (0.0, 1 / 5),
        (0.0015, 2 / 5),
        (0.002, 4 / 5),
        (0.01, 1.0),
        (1.0, 1.0),
    ],
)
def test_ecdf_cumulative_value_table(margin, expected):
    assert _sample_ecdf().cumulative_probability(margin) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("margin", "expected"),
    [(-1.0, 1.0), (0.0, 1.0), (0.0015, 3 / 5), (0.002, 3 / 5), (0.011, 0.0)],
)
def test_ecdf_survival_includes_ties(margin, expected):
    assert _sample_ecdf().survival_at_least(margin) == pytest.approx(expected)


def test_ecdf_rejects_empty_and_unsorted_and_negative_samples():
    with pytest.raises(ValueError):
        build_margin_ecdf([])
    with pytest.raises(ValueError):
        build_margin_ecdf([0.001, -0.002])
    with pytest.raises(ValueError):
        MarginEcdf(sorted_margins=(0.002, 0.001))


def test_single_sample_ecdf_is_a_step_at_that_value():
    ecdf = build_margin_ecdf([0.0055])
    assert ecdf.cumulative_probability(0.005) == 0.0
    assert ecdf.cumulative_probability(0.0055) == 1.0
    assert ecdf.survival_at_least(0.0055) == 1.0


# --- 반사 KDE ---------------------------------------------------------------------
KDE_SAMPLES = (0.0, 0.0005, 0.001, 0.002, 0.005)
KDE_BANDWIDTH = 0.001


def _standard_normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _reflected_density(margin: float) -> float:
    """반사 밀도 — 닫힌형 CDF 를 수치적분으로 독립 대조하기 위한 참조 구현."""
    if margin < 0.0:
        return 0.0
    return sum(
        exp(-0.5 * (((margin - sample) / KDE_BANDWIDTH) ** 2)) / sqrt(2.0 * pi)
        + exp(-0.5 * (((margin + sample) / KDE_BANDWIDTH) ** 2)) / sqrt(2.0 * pi)
        for sample in KDE_SAMPLES
    ) / (len(KDE_SAMPLES) * KDE_BANDWIDTH)


def _simpson_cdf(margin: float, steps: int = 20_000) -> float:
    step = margin / steps
    total = _reflected_density(0.0) + _reflected_density(margin)
    for index in range(1, steps):
        total += (4 if index % 2 else 2) * _reflected_density(index * step)
    return total * step / 3.0


def test_reflected_kde_puts_exactly_zero_mass_below_the_boundary():
    kde = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    assert kde.cumulative_probability(0.0) == 0.0
    assert kde.cumulative_probability(-0.5) == 0.0
    assert kde.survival_at_least(0.0) == 1.0


@pytest.mark.parametrize(
    ("margin", "expected"),
    [
        (0.0005, 0.205320184140068),
        (0.001, 0.388386329269827),
        (0.002, 0.644559188866888),
    ],
)
def test_reflected_kde_value_table_above_the_boundary(margin, expected):
    # 이 값들이 M2 의 핵심이다: δ>0 에서 반사항이 실제로 살아 있는지 고정한다.
    # (δ≤0 단언은 이른 반환이 대신 통과시키므로 반사항 삭제를 못 잡는다.)
    kde = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    assert kde.cumulative_probability(margin) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("margin", [0.0005, 0.001, 0.002])
def test_reflected_kde_closed_form_matches_numerical_integration(margin):
    # 값표의 출처를 테스트 안에서 재현한다 — 닫힌형이 반사 밀도의 적분과 같은가.
    kde = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    assert kde.cumulative_probability(margin) == pytest.approx(
        _simpson_cdf(margin), rel=1e-9
    )


def test_naive_gaussian_kde_diverges_in_both_readings():
    # 금지된 대안을 실제로 계산해 두 독법의 편향 방향을 고정한다(리뷰 정정 반영):
    # 방치하면 CDF 과대(생존 과소 → 비관), 절단·재정규화하면 CDF 과소(마진 과대 → 낙관).
    naive = [
        sum(
            _standard_normal_cdf((margin - sample) / KDE_BANDWIDTH)
            for sample in KDE_SAMPLES
        )
        / len(KDE_SAMPLES)
        for margin in (0.0, 0.0005)
    ]
    leaked_below_zero, naive_at_target = naive
    assert leaked_below_zero == pytest.approx(0.197988642251439, abs=1e-9)
    renormalised = (naive_at_target - leaked_below_zero) / (1.0 - leaked_below_zero)

    reflected = build_reflected_kde(
        KDE_SAMPLES, bandwidth=KDE_BANDWIDTH
    ).cumulative_probability(0.0005)
    assert naive_at_target > reflected > renormalised
    assert naive_at_target == pytest.approx(0.313362119788397, abs=1e-9)
    assert renormalised == pytest.approx(0.143855167, abs=1e-9)


def test_reflected_kde_conserves_total_mass():
    kde = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    assert kde.cumulative_probability(0.05) == pytest.approx(1.0, abs=1e-9)
    assert kde.survival_at_least(0.05) == pytest.approx(0.0, abs=1e-9)


def test_reflected_kde_cdf_is_monotone_and_bounded():
    kde = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    grid = [index * 0.0002 for index in range(60)]
    values = [kde.cumulative_probability(margin) for margin in grid]
    assert all(0.0 <= value <= 1.0 for value in values)
    assert all(
        later >= earlier - 1e-12
        for earlier, later in zip(values, values[1:], strict=False)
    )


def test_bandwidth_falls_back_to_the_floor_when_the_sample_has_no_spread():
    assert silverman_bandwidth([0.001] * 40) == MIN_BANDWIDTH
    assert silverman_bandwidth([0.001]) == MIN_BANDWIDTH
    assert build_reflected_kde([0.001] * 40).bandwidth == MIN_BANDWIDTH


def test_silverman_bandwidth_value_table_and_formula_mirror():
    samples = [0.0, 0.001, 0.002, 0.004, 0.008, 0.016]
    # 손계산 값(고정): 0.9 · min(σ, IQR/1.349) · n^(−1/5)
    assert silverman_bandwidth(samples) == pytest.approx(2.680823083501e-03, rel=1e-9)
    quartiles = quantiles(samples, n=4, method="inclusive")
    expected_scale = min(pstdev(samples), (quartiles[2] - quartiles[0]) / 1.349)
    assert silverman_bandwidth(samples) == pytest.approx(
        0.9 * expected_scale * len(samples) ** -0.2
    )


def test_reflected_kde_rejects_empty_samples_and_bad_bandwidth():
    with pytest.raises(ValueError):
        build_reflected_kde([])
    with pytest.raises(ValueError):
        build_reflected_kde(KDE_SAMPLES, bandwidth=0.0)


# --- 표 기반 O(1) 평가판 ----------------------------------------------------------
def test_tabulated_cdf_tracks_its_source_within_a_declared_error():
    kde = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    grid = [index * 0.0001 for index in range(121)]  # 0 ~ 120bp, 1bp 간격
    table = tabulate_margin_cdf(kde, grid)

    probe = [index * 0.00001 for index in range(1, 1200)]
    worst = max(
        abs(table.cumulative_probability(margin) - kde.cumulative_probability(margin))
        for margin in probe
    )
    # 1bp 격자 · h=10bp 에서 선형 보간 오차 상한을 고정한다(회귀 시 즉시 드러난다).
    assert worst < 5e-4


def test_tabulated_cdf_preserves_boundary_safety_and_saturation():
    kde = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    table = tabulate_margin_cdf(kde, [index * 0.0005 for index in range(101)])
    assert table.cumulative_probability(-1.0) == 0.0
    assert table.cumulative_probability(0.0) == 0.0
    assert table.survival_at_least(0.0) == 1.0
    assert table.cumulative_probability(10.0) == pytest.approx(1.0, abs=1e-9)


def test_tabulated_cdf_interpolates_linearly_between_grid_points():
    table = TabulatedMarginCdf(grid=(0.0, 0.001, 0.002), values=(0.0, 0.5, 1.0))
    assert table.cumulative_probability(0.0005) == pytest.approx(0.25)
    assert table.survival_at_least(0.0015) == pytest.approx(0.25)


def test_tabulated_cdf_rejects_degenerate_grids():
    with pytest.raises(ValueError):
        TabulatedMarginCdf(grid=(0.0,), values=(0.0,))
    with pytest.raises(ValueError):
        TabulatedMarginCdf(grid=(0.0, 0.001), values=(0.0,))
    with pytest.raises(ValueError):
        TabulatedMarginCdf(grid=(0.001, 0.0), values=(0.0, 1.0))
    with pytest.raises(ValueError):
        TabulatedMarginCdf(grid=(0.0, 0.0), values=(0.0, 1.0))


# --- 계층 수축 --------------------------------------------------------------------
def test_shrinkage_weight_is_n_over_n_plus_kappa():
    blended = shrink_margin_distribution(
        cell=build_margin_ecdf([0.0]),
        parent=build_margin_ecdf([0.01]),
        cell_sample_count=50,
        prior_strength=50.0,
    )
    assert blended.cell_weight == pytest.approx(0.5)
    # 셀은 전부 0 마진(하한 밀착), 부모는 전부 0.01 → δ=0.005 에서 정확히 절반씩.
    assert blended.cumulative_probability(0.005) == pytest.approx(0.5)
    assert blended.survival_at_least(0.005) == pytest.approx(0.5)


def test_shrinkage_weight_shares_the_single_pseudo_count_source():
    blended = shrink_margin_distribution(
        cell=build_margin_ecdf([0.0]),
        parent=build_margin_ecdf([0.01]),
        cell_sample_count=37,
        prior_strength=25.0,
    )
    assert blended.cell_weight == pseudo_count_weight(37, 25.0)


def test_empty_cell_falls_through_to_the_parent_distribution():
    parent = build_margin_ecdf([0.01])
    blended = shrink_margin_distribution(
        cell=build_margin_ecdf([0.0]),
        parent=parent,
        cell_sample_count=0,
        prior_strength=25.0,
    )
    assert blended.cell_weight == 0.0
    assert blended.cumulative_probability(0.005) == parent.cumulative_probability(0.005)


def test_deep_cell_keeps_its_own_distribution():
    blended = shrink_margin_distribution(
        cell=build_margin_ecdf([0.0]),
        parent=build_margin_ecdf([0.01]),
        cell_sample_count=10_000,
        prior_strength=25.0,
    )
    assert blended.cumulative_probability(0.005) == pytest.approx(1.0, abs=3e-3)


def test_two_level_nesting_composes_weights_multiplicatively():
    # 셀(n=30, κ=30) → 공종(n=90, κ=30) → 전역. 각 분포는 서로 다른 한 점에 질량을
    # 몰아 두어 δ=0.0075 에서의 CDF 가 곧 가중치가 되게 만든다.
    cell_weight = pseudo_count_weight(30, 30.0)  # 0.5
    category_weight = pseudo_count_weight(90, 30.0)  # 0.75
    category_level = shrink_margin_distribution(
        cell=build_margin_ecdf([0.005]),  # 공종: CDF(0.0075) = 1
        parent=build_margin_ecdf([0.01]),  # 전역: CDF(0.0075) = 0
        cell_sample_count=90,
        prior_strength=30.0,
    )
    nested = shrink_margin_distribution(
        cell=build_margin_ecdf([0.001]),  # 셀: CDF(0.0075) = 1
        parent=category_level,
        cell_sample_count=30,
        prior_strength=30.0,
    )
    expected = cell_weight + ((1.0 - cell_weight) * category_weight)
    assert nested.cumulative_probability(0.0075) == pytest.approx(expected)
    assert nested.survival_at_least(0.0075) == pytest.approx(1.0 - expected)


def test_shrinkage_preserves_boundary_safety():
    blended = shrink_margin_distribution(
        cell=build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH),
        parent=build_margin_ecdf([0.0, 0.01]),
        cell_sample_count=5,
        prior_strength=25.0,
    )
    assert blended.cumulative_probability(-0.001) == 0.0


def test_shrinkage_rejects_invalid_parameters():
    cell = build_margin_ecdf([0.0])
    parent = build_margin_ecdf([0.01])
    with pytest.raises(ValueError):
        shrink_margin_distribution(
            cell=cell, parent=parent, cell_sample_count=-1, prior_strength=10.0
        )
    with pytest.raises(ValueError):
        shrink_margin_distribution(
            cell=cell, parent=parent, cell_sample_count=10, prior_strength=0.0
        )


# --- 포트 준수(정적 + 런타임) -----------------------------------------------------
def test_every_estimator_satisfies_the_margin_distribution_port():
    # 정적 단언: mypy 가 각 구현을 MarginDistribution 로 좁힐 수 있어야 한다(§4.7-1).
    ecdf: MarginDistribution = build_margin_ecdf([0.0, 0.005])
    kde: MarginDistribution = build_reflected_kde(KDE_SAMPLES, bandwidth=KDE_BANDWIDTH)
    table: MarginDistribution = tabulate_margin_cdf(
        kde, [index * 0.001 for index in range(20)]
    )
    shrunk: MarginDistribution = shrink_margin_distribution(
        cell=build_margin_ecdf([0.0]),
        parent=build_margin_ecdf([0.01]),
        cell_sample_count=10,
        prior_strength=10.0,
    )
    for implementation in (ecdf, kde, table, shrunk):
        assert 0.0 <= implementation.cumulative_probability(0.003) <= 1.0
        assert 0.0 <= implementation.survival_at_least(0.003) <= 1.0
    assert isinstance(ecdf, MarginEcdf)
    assert isinstance(kde, ReflectedMarginKde)
    assert isinstance(table, TabulatedMarginCdf)
    assert isinstance(shrunk, ShrunkMarginDistribution)
