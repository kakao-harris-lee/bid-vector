"""값표 테스트 — win-proxy 곡선의 격자·argmax·실격 예산 제약·불안정성 진단.

판정축의 회귀 고정점 셋:

* **동률 기본값은 상단(UPPER)** — 같은 ``WW`` 구간에서 높은 ``b`` 가 약우위다
  (실격확률 비증가 · 수취액 증가).
* **α-제약이 소박한 argmax 를 대체** — 제약이 실제로 ``b*`` 를 **위로 미는**(하한을
  하나 더 얹는) 케이스와, 두 실패(예산 불능 / WW 전부 0)를 조용한 fallback 없이
  구분해 알리는 케이스.
* **평탄/다봉 진단** — 판정 옆에 임의성 증거가 함께 나오고, 제약 판정에는 **실행가능
  집합 스코프**의 진단이 붙는다.
"""

import pytest

from app.domain.award_landing_curve import (
    DISQUALIFICATION_MONOTONICITY_TOLERANCE,
    MAX_GRID_POINTS,
    ConstrainedOptimumStatus,
    PlateauChoice,
    WinProxyCurve,
    build_bid_rate_grid,
)
from app.domain.award_landing_curve_builders import (
    composed_win_proxy_curve,
    empirical_win_proxy_curve,
    rebased_win_proxy_curve,
)
from app.domain.award_landing_distribution import (
    assessment_support_from_pairs,
    equiprobable_assessment_support,
)
from app.domain.award_margin_distribution import LandingObservation, build_margin_ecdf

FLOOR_RATE = 0.88
GRID = (0.88, 0.89, 0.90, 0.91, 0.92)


def _observation(
    winning_rate: float, *, floor_rate: float = FLOOR_RATE, assessment: float = 1.0
) -> LandingObservation:
    return LandingObservation(
        floor_rate=floor_rate,
        winning_rate=winning_rate,
        assessment_ratio=assessment,
    )


def _curve(
    values: tuple[float, ...],
    *,
    disqualification: tuple[float, ...] | None = None,
    plateau_choice: PlateauChoice = PlateauChoice.UPPER,
) -> WinProxyCurve:
    return WinProxyCurve(
        bid_rates=GRID,
        values=values,
        disqualification_probabilities=disqualification,
        plateau_choice=plateau_choice,
    )


# --- 동률 처리(P1) ----------------------------------------------------------------
def test_plateau_default_is_the_upper_edge():
    # 같은 WW 구간에서 높은 b 가 약우위 — 실격확률은 b 에 비증가, 수취액은 증가.
    diagnostics = _curve((0.1, 0.5, 0.5, 0.5, 0.2)).diagnostics()
    assert diagnostics.optimal_bid_rate == 0.91
    assert diagnostics.plateau_lower_rate == 0.89
    assert diagnostics.plateau_upper_rate == 0.91
    assert diagnostics.plateau_width == pytest.approx(0.02)
    assert diagnostics.peak_value == 0.5
    assert diagnostics.mode_count == 1
    assert diagnostics.value_range == pytest.approx(0.4)
    assert not diagnostics.is_flat


def test_plateau_choice_is_injectable():
    values = (0.1, 0.5, 0.5, 0.5, 0.2)
    lower = _curve(values, plateau_choice=PlateauChoice.LOWER).diagnostics()
    assert lower.optimal_bid_rate == 0.89
    # 어느 쪽을 고르든 구간 양 끝은 그대로 공시된다.
    assert (lower.plateau_lower_rate, lower.plateau_upper_rate) == (0.89, 0.91)


def test_tied_peaks_across_a_valley_pick_the_dominant_one_but_report_one_run():
    # 0.88 과 [0.90, 0.91] 이 동률 최고점이다. 약우위 논증은 "같은 WW 면 높은 b"이지
    # "같은 봉우리 안에서"가 아니므로 b* 는 0.91 이다. 다만 **구간 폭**은 골짜기를
    # 건너뛰지 않고 b* 를 품은 연속 구간만 공시한다(폭이 부풀면 임의성이 과대평가된다).
    diagnostics = _curve((0.5, 0.2, 0.5, 0.5, 0.1)).diagnostics()
    assert diagnostics.optimal_bid_rate == 0.91
    assert diagnostics.plateau_lower_rate == 0.90
    assert diagnostics.plateau_upper_rate == 0.91
    assert diagnostics.mode_count == 2  # 다봉이라는 사실은 별도로 드러난다
    # LOWER 규칙은 반대편 동률점을 고르고, 그쪽 연속 구간을 공시한다.
    lower = _curve(
        (0.5, 0.2, 0.5, 0.5, 0.1), plateau_choice=PlateauChoice.LOWER
    ).diagnostics()
    assert lower.optimal_bid_rate == 0.88
    assert (lower.plateau_lower_rate, lower.plateau_upper_rate) == (0.88, 0.88)


def test_multimodal_curve_is_flagged():
    diagnostics = _curve((0.1, 0.5, 0.2, 0.5, 0.1)).diagnostics()
    assert diagnostics.mode_count == 2
    assert diagnostics.is_multimodal
    assert diagnostics.plateau_width == 0.0


def test_flat_curve_is_flagged_as_undecidable():
    diagnostics = _curve((0.3, 0.3, 0.3, 0.3, 0.3)).diagnostics()
    assert diagnostics.is_flat
    assert diagnostics.mode_count == 1
    assert diagnostics.plateau_width == pytest.approx(0.04)


def test_monotone_curves_have_a_single_mode():
    assert _curve((0.5, 0.4, 0.3, 0.2, 0.1)).diagnostics().mode_count == 1
    assert _curve((0.1, 0.2, 0.3, 0.4, 0.5)).diagnostics().mode_count == 1
    assert _curve((0.1, 0.2, 0.3, 0.4, 0.5)).optimal_bid_rate == 0.92


def test_single_point_curve_is_degenerate_but_valid():
    diagnostics = WinProxyCurve(bid_rates=(0.88,), values=(0.25,)).diagnostics()
    assert diagnostics.optimal_bid_rate == 0.88
    assert diagnostics.mode_count == 1
    assert diagnostics.is_flat


def test_curve_rejects_misaligned_or_empty_input():
    with pytest.raises(ValueError):
        WinProxyCurve(bid_rates=(), values=())
    with pytest.raises(ValueError):
        WinProxyCurve(bid_rates=GRID, values=(0.1, 0.2))
    with pytest.raises(ValueError):
        WinProxyCurve(
            bid_rates=GRID,
            values=(0.1, 0.2, 0.3, 0.4, 0.5),
            disqualification_probabilities=(0.1, 0.2),
        )


# --- α-제약 argmax (D1) -----------------------------------------------------------
# 실격확률 P(φ>b) 는 b 에 **비증가**다(높이 쓸수록 하한 미달 위험이 준다). 그래서
# 제약은 착지점을 **위로** 민다 — 소박한 argmax 는 하한 밀착(실격 90%)을 고르지만
# α=0.2 는 그 지점을 배제한다. 설계 §7 M2 의 실측(공사 32.5%·용역 52.6%)이 이 구조다.
CONSTRAINED_VALUES = (0.60, 0.45, 0.30, 0.20, 0.10)
CONSTRAINED_DISQUALIFICATION = (0.90, 0.60, 0.20, 0.05, 0.00)


def test_non_monotone_disqualification_curve_is_rejected_at_construction():
    # 비증가는 이제 **타입이 입구에서 지키는 계약**이다. 그래서 예전
    # `..._fixture_is_physically_monotone`(fixture 를 테스트가 눈으로 확인)을 이 테스트가
    # 대신한다 — 나머지 모든 테스트는 곡선을 만들 수 있다는 사실만으로 자기 fixture 의
    # 물리성을 이미 통과시킨다. 검증이 필요한 진짜 이유는 제약 판정이 **잘라낸 곡선**
    # 위에서 돌기 때문이다: 비단조 입력이면 실행가능 점들이 이웃이 되어 동률 구간이 원
    # 격자의 골짜기를 가로지를 수 있다.
    with pytest.raises(ValueError):
        _curve(CONSTRAINED_VALUES, disqualification=(0.0, 0.2, 0.6, 0.75, 0.9))
    with pytest.raises(ValueError):  # 마지막 한 점만 뒤집혀도 거부
        _curve(CONSTRAINED_VALUES, disqualification=(0.9, 0.6, 0.2, 0.05, 0.3))


def test_monotonicity_check_tolerates_float_noise_but_not_real_inversions():
    # 엄격 비교를 쓰면 안 된다: 합성 경로 실격확률은 지지집합의 **부분합**이라 덧셈
    # 순서 차이로 1 ulp 역전이 이론상 가능하다. 그 규모는 통과시키고, 원자 1개 규모의
    # 진짜 역전은 거부해야 한다.
    noise = DISQUALIFICATION_MONOTONICITY_TOLERANCE / 2.0
    tolerated = _curve(
        CONSTRAINED_VALUES, disqualification=(0.5, 0.5 + noise, 0.2, 0.1, 0.0)
    )
    assert tolerated.disqualification_probabilities is not None
    with pytest.raises(ValueError):
        _curve(CONSTRAINED_VALUES, disqualification=(0.5, 0.5001, 0.2, 0.1, 0.0))


def test_unconstrained_argmax_ignores_the_disqualification_budget():
    curve = _curve(CONSTRAINED_VALUES, disqualification=CONSTRAINED_DISQUALIFICATION)
    assert curve.optimal_bid_rate == 0.88  # 실격확률 90% 인 하한 밀착점
    assert curve.peak_value == 0.60


def test_alpha_constraint_pushes_the_landing_point_up_off_the_floor():
    curve = _curve(CONSTRAINED_VALUES, disqualification=CONSTRAINED_DISQUALIFICATION)
    optimum = curve.constrained_optimum(alpha=0.2)
    assert optimum.is_feasible
    assert optimum.feasible_point_count == 3  # 0.90, 0.91, 0.92
    assert optimum.optimal_bid_rate == 0.90
    assert optimum.peak_value == pytest.approx(0.30)
    assert optimum.disqualification_probability == pytest.approx(0.20)
    assert optimum.alpha == 0.2


def test_alpha_constraint_boundary_is_closed():
    # 실격확률이 정확히 α 인 점은 **허용**된다(≤).
    curve = _curve(CONSTRAINED_VALUES, disqualification=CONSTRAINED_DISQUALIFICATION)
    assert curve.constrained_optimum(alpha=0.2).optimal_bid_rate == 0.90
    assert curve.constrained_optimum(alpha=0.19).optimal_bid_rate == 0.91


def test_infeasible_budget_is_an_explicit_signal_not_a_silent_fallback():
    curve = _curve(
        CONSTRAINED_VALUES, disqualification=(0.9, 0.8, 0.7, 0.6, 0.5)
    )
    optimum = curve.constrained_optimum(alpha=0.1)
    assert optimum.status is ConstrainedOptimumStatus.BUDGET_INFEASIBLE
    assert not optimum.is_feasible
    assert optimum.feasible_point_count == 0
    assert optimum.optimal_bid_rate is None
    assert optimum.peak_value is None
    assert optimum.disqualification_probability is None
    assert optimum.plateau_lower_rate is None
    assert optimum.feasible_diagnostics is None
    # 소박한 argmax 로 되돌아가지 않았음을 명시적으로 고정한다.
    assert curve.optimal_bid_rate == 0.88


def test_degenerate_all_zero_win_proxy_is_a_failure_not_a_grid_artifact():
    # α 는 만족하지만 실행가능 구간의 WW 가 전부 0 — 그때 "최고점"은 판정이 아니라
    # 격자 끝일 뿐이다(UPPER 면 밴드 상단. 격자 밴드 오설정에서 실제로 도달한다).
    curve = _curve(
        (0.5, 0.0, 0.0, 0.0, 0.0), disqualification=(0.9, 0.0, 0.0, 0.0, 0.0)
    )
    optimum = curve.constrained_optimum(alpha=0.1)
    assert optimum.status is ConstrainedOptimumStatus.DEGENERATE_WIN_PROXY
    assert not optimum.is_feasible
    assert optimum.feasible_point_count == 4  # 예산은 만족했다 — 실패 사유가 다르다
    assert optimum.optimal_bid_rate is None
    assert optimum.peak_value is None
    assert optimum.feasible_diagnostics is None


def test_constrained_diagnostics_are_scoped_to_the_feasible_set():
    # 전 격자 진단과 값이 **다를 수 있다**: 제약이 봉우리 하나를 잘라내면 다봉이
    # 단봉이 되고 진폭도 좁아진다. 제약 판정 옆에 무제약 진단을 갖다 붙이는 잘못된
    # 짝 맞추기를, 결과가 자기 스코프 진단을 실어 오는 구조로 막았는지 고정한다.
    curve = _curve(
        (0.9, 0.1, 0.5, 0.5, 0.1), disqualification=(0.9, 0.0, 0.0, 0.0, 0.0)
    )
    whole_grid = curve.diagnostics()
    optimum = curve.constrained_optimum(alpha=0.1)
    feasible = optimum.feasible_diagnostics
    assert feasible is not None

    assert whole_grid.mode_count == 2
    assert whole_grid.peak_value == 0.9
    assert whole_grid.value_range == pytest.approx(0.8)
    assert feasible.mode_count == 1
    assert feasible.peak_value == 0.5
    assert feasible.value_range == pytest.approx(0.4)
    assert not feasible.is_multimodal
    assert optimum.optimal_bid_rate == 0.91
    assert (optimum.plateau_lower_rate, optimum.plateau_upper_rate) == (0.90, 0.91)


def test_constrained_plateau_follows_the_injected_tie_rule():
    values = (0.9, 0.5, 0.5, 0.5, 0.1)
    disqualification = (0.9, 0.1, 0.1, 0.1, 0.0)
    upper = _curve(values, disqualification=disqualification).constrained_optimum(
        alpha=0.1
    )
    lower = _curve(
        values, disqualification=disqualification, plateau_choice=PlateauChoice.LOWER
    ).constrained_optimum(alpha=0.1)
    assert upper.optimal_bid_rate == 0.91
    assert lower.optimal_bid_rate == 0.89
    assert (upper.plateau_lower_rate, upper.plateau_upper_rate) == (0.89, 0.91)


def test_constrained_plateau_never_reports_infeasible_coordinates():
    # 동률 최고점이 제약 밖까지 이어져도 구간은 실행가능 집합 안에서 끊긴다.
    values = (0.5, 0.5, 0.5, 0.5, 0.1)
    disqualification = (0.9, 0.9, 0.1, 0.1, 0.0)
    optimum = _curve(
        values, disqualification=disqualification
    ).constrained_optimum(alpha=0.1)
    assert optimum.optimal_bid_rate == 0.91
    assert optimum.plateau_lower_rate == 0.90  # 0.88·0.89 는 동률이지만 실격 90%
    assert optimum.plateau_upper_rate == 0.91


def test_constrained_optimum_exposes_the_feasible_lower_coordinate():
    """``b_min(α)`` 를 결과에 실어 소비자가 격자 길이로 **역산하지 않게** 한다(§4.5-8).

    역산(``bid_rates[len − feasible_point_count]``)은 "실행가능 집합은 상향폐집합"이라는
    성질을 호출부가 다시 가정하는 일이다 — 이 클래스가 이미 아는 사실이므로 좌표를 그대로
    준다. 소비자는 이 값과 ``b*`` 를 비교해 binding 을 판정한다.
    """
    values = (0.5, 0.5, 0.5, 0.5, 0.1)
    disqualification = (0.9, 0.9, 0.1, 0.1, 0.0)
    curve = _curve(values, disqualification=disqualification)

    optimum = curve.constrained_optimum(alpha=0.1)

    assert optimum.feasible_lower_rate == 0.90
    assert optimum.feasible_point_count == 3
    assert optimum.optimal_bid_rate == 0.91  # 하단이 아니라 동률 상단을 골랐다

    # 실행가능 점이 하나도 없으면 좌표도 없다.
    infeasible = _curve(
        CONSTRAINED_VALUES, disqualification=(0.9, 0.9, 0.9, 0.9, 0.9)
    ).constrained_optimum(alpha=0.1)
    assert infeasible.status is ConstrainedOptimumStatus.BUDGET_INFEASIBLE
    assert infeasible.feasible_lower_rate is None

    # 퇴화(WW 전부 0)에서는 **경계는 남긴다** — 격자를 넓힐지 α 를 고칠지 가르려면
    # "α 는 만족했는데 그 구간이 어디였나"가 필요하다.
    degenerate = _curve(
        (0.5, 0.0, 0.0, 0.0, 0.0), disqualification=(0.9, 0.0, 0.0, 0.0, 0.0)
    ).constrained_optimum(alpha=0.1)
    assert degenerate.status is ConstrainedOptimumStatus.DEGENERATE_WIN_PROXY
    assert degenerate.feasible_lower_rate == 0.89


def test_constrained_optimum_requires_a_budget_curve_and_a_valid_alpha():
    with pytest.raises(ValueError):
        _curve(CONSTRAINED_VALUES).constrained_optimum(alpha=0.2)
    curve = _curve(CONSTRAINED_VALUES, disqualification=CONSTRAINED_DISQUALIFICATION)
    for alpha in (-0.1, 1.5, float("nan")):
        with pytest.raises(ValueError):
            curve.constrained_optimum(alpha=alpha)


# --- 격자 -------------------------------------------------------------------------
def test_bid_rate_grid_value_table():
    assert build_bid_rate_grid(0.0, 1.0, step=0.25) == pytest.approx(
        (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    assert build_bid_rate_grid(0.88, 0.88, step=0.001) == (0.88,)


def test_bid_rate_grid_rejects_bad_bounds_and_runaway_size():
    with pytest.raises(ValueError):
        build_bid_rate_grid(0.9, 0.88)
    with pytest.raises(ValueError):
        build_bid_rate_grid(0.88, 0.90, step=0.0)
    with pytest.raises(ValueError):
        build_bid_rate_grid(0.0, 1.0, step=1e-5)
    assert len(build_bid_rate_grid(0.0, 0.2, step=1e-5)) <= MAX_GRID_POINTS


# --- 곡선 빌더(층 A · 층 C · f-재기준) --------------------------------------------
LAYER_A_OBSERVATIONS = (
    _observation(0.8855),
    _observation(0.89),
    _observation(0.8855, assessment=0.99),
)


def test_empirical_curve_carries_both_win_and_disqualification_values():
    curve = empirical_win_proxy_curve((0.8712, 0.8850, 1.00), LAYER_A_OBSERVATIONS)
    assert curve.values == pytest.approx((1 / 3, 2 / 3, 0.0))
    assert curve.disqualification_probabilities == pytest.approx((2 / 3, 0.0, 0.0))
    assert curve.optimal_bid_rate == 0.8850


def test_rebased_curve_moves_with_the_target_floor():
    observations = (_observation(0.885),)  # f=0.88, Δ=0.005, a=1.0
    curve = rebased_win_proxy_curve(
        (0.8950, 0.9000, 0.9050, 0.9100),
        observations,
        target_floor_rate=0.90,
    )
    assert curve.values == pytest.approx((0.0, 1.0, 1.0, 0.0))
    assert curve.disqualification_probabilities == pytest.approx((1.0, 0.0, 0.0, 0.0))
    assert curve.optimal_bid_rate == 0.9050  # 동률 구간 상단


def test_composed_curve_lands_just_above_the_lowest_realised_floor():
    # 비용 없는 곡선의 argmax 는 "생존 유지 최저" 근방에 착지한다. 하한 밀착 마진
    # 표본에서 그 성질과, 그때 실격확률이 이미 크다는 사실을 함께 고정한다.
    support = equiprobable_assessment_support([0.99, 1.00, 1.01])
    margins = build_margin_ecdf([0.0005, 0.001, 0.002, 0.005])
    grid = build_bid_rate_grid(0.86, 0.90, step=0.0005)
    curve = composed_win_proxy_curve(
        grid, floor_rate=FLOOR_RATE, assessment=support, margins=margins
    )
    diagnostics = curve.diagnostics()

    lowest_realised_floor = FLOOR_RATE * 0.99
    assert all(
        value == 0.0
        for bid_rate, value in zip(curve.bid_rates, curve.values, strict=True)
        if bid_rate < lowest_realised_floor
    )
    assert diagnostics.peak_value == pytest.approx(1 / 3)
    # 사정률 지지집합이 이산이라 봉우리도 이산으로 갈라진다 — 단일 b* 보고가 곡선을
    # 대표하지 못한다는 사실을 진단이 드러내야 한다(설계 §11-7).
    assert diagnostics.is_multimodal


def test_generated_disqualification_curves_are_non_increasing_in_the_bid():
    # 실격확률의 물리적 성질: 높이 쓸수록 하한 미달 위험은 줄기만 한다. 세 층 모두.
    # 이제 생성자가 같은 계약을 강제하므로 위반 시 단언이 아니라 **생성에서** 터진다.
    # 그래도 남겨 두는 이유: 이 테스트는 "가드가 있다"가 아니라 "세 추정량이 자기
    # 계약을 실제로 만족한다"를 재는 유일한 지점이다(빌더가 깨지면 여기서 드러난다).
    support = equiprobable_assessment_support([0.99, 1.00, 1.01])
    margins = build_margin_ecdf([0.0005, 0.001, 0.002, 0.005])
    grid = build_bid_rate_grid(0.86, 0.92, step=0.0005)
    curves = (
        composed_win_proxy_curve(
            grid, floor_rate=FLOOR_RATE, assessment=support, margins=margins
        ),
        empirical_win_proxy_curve(grid, LAYER_A_OBSERVATIONS),
        rebased_win_proxy_curve(
            grid, LAYER_A_OBSERVATIONS, target_floor_rate=0.90
        ),
    )
    for curve in curves:
        probabilities = curve.disqualification_probabilities
        assert probabilities is not None
        assert all(
            later <= earlier
            for earlier, later in zip(
                probabilities, probabilities[1:], strict=False
            )
        )


def test_alpha_constraint_on_a_composed_curve_rejects_the_deep_floor_hugging_point():
    # 제약이 **실제로 행사되는** fixture 여야 한다: 무제약 argmax 가 이미 실격확률
    # 0 인 지점이면 α 를 통째로 무시해도 테스트가 통과한다. 그래서 사정률 질량을
    # 최저 a 에 몰아, 무제약 최고점(WW 0.6)이 실격확률 0.40 인 하한 밀착점이 되게
    # 만든다 — 설계 §7 M2 의 실측 구조(공사 32.5%·용역 52.6%)와 같은 모양이다.
    support = assessment_support_from_pairs([(0.99, 0.6), (1.00, 0.25), (1.01, 0.15)])
    margins = build_margin_ecdf([0.0005, 0.001, 0.002, 0.005])
    grid = build_bid_rate_grid(0.86, 0.90, step=0.0005)
    curve = composed_win_proxy_curve(
        grid, floor_rate=FLOOR_RATE, assessment=support, margins=margins
    )

    unconstrained = curve.diagnostics()
    budget = curve.disqualification_probabilities
    assert budget is not None
    unconstrained_budget = budget[
        curve.bid_rates.index(unconstrained.optimal_bid_rate)
    ]
    assert unconstrained.peak_value == pytest.approx(0.6)
    assert unconstrained_budget == pytest.approx(0.40)  # 제약이 실제로 걸린다

    optimum = curve.constrained_optimum(alpha=0.2)
    assert optimum.is_feasible
    assert optimum.disqualification_probability == pytest.approx(0.15)
    # 핵심 단언: 제약 전후의 b* 가 다르고, 방향은 **위**다(하한을 더 얹은 셈).
    assert optimum.optimal_bid_rate != unconstrained.optimal_bid_rate
    assert optimum.optimal_bid_rate is not None
    assert optimum.optimal_bid_rate > unconstrained.optimal_bid_rate
    assert optimum.optimal_bid_rate == pytest.approx(0.8805)
    assert optimum.peak_value == pytest.approx(0.25)  # 승률은 그 대가로 내려간다
