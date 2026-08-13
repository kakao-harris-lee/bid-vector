"""값표 테스트 — 반사실 win-proxy ``WW(b)`` 추정량(층 A · 층 C · f-재기준).

손으로 계산 가능한 소형 케이스만 쓴다(§4.7-4). 정합 축이 둘이다:

* **층 A ≡ 층 C**: ``a ⊥ Δ`` 가 구조적으로 성립하는 합성 데이터에서 두 추정이 일치
  한다. 격자 좌표뿐 아니라 **경계 좌표(b = φ_i · b = ω_i)** 를 반드시 포함한다 —
  경계는 부동소수 형태 차이가 드러나는 유일한 지점이고, 하한 밀착이 이 도메인의
  관심 구간이라 경계가 곧 실전 좌표다.
* **층 A ≡ f-재기준**: ``f_tgt`` 가 관측의 ``f_i`` 와 같으면 식 3 은 층 A 로 환원된다.
"""

import pytest

from app.domain.award_landing_distribution import (
    AssessmentSupport,
    assessment_support_from_pairs,
    composed_disqualification_probability,
    composed_win_proxy,
    empirical_disqualification_probability,
    empirical_win_proxy,
    equiprobable_assessment_support,
    rebased_disqualification_probability,
    rebased_win_proxy,
)
from app.domain.award_margin_distribution import (
    LandingObservation,
    build_margin_ecdf,
    build_reflected_kde,
    shrink_margin_distribution,
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


# --- 사정률 지지집합 --------------------------------------------------------------
def test_equiprobable_support_matches_phase1_style_enumeration():
    support = equiprobable_assessment_support([0.99, 1.00, 1.01])
    assert support.probabilities == pytest.approx((1 / 3, 1 / 3, 1 / 3))
    assert sum(support.probabilities) == pytest.approx(1.0)


def test_support_rejects_malformed_inputs():
    with pytest.raises(ValueError):
        equiprobable_assessment_support([])
    with pytest.raises(ValueError):
        assessment_support_from_pairs([(0.99, 0.4), (1.01, 0.4)])
    with pytest.raises(ValueError):
        AssessmentSupport(values=(0.99, 1.01), probabilities=(1.0,))
    with pytest.raises(ValueError):
        AssessmentSupport(values=(-0.99, 1.01), probabilities=(0.5, 0.5))
    with pytest.raises(ValueError):
        AssessmentSupport(values=(0.99, 1.01), probabilities=(1.5, -0.5))


# --- 층 A: 경험 win-proxy ---------------------------------------------------------
LAYER_A_OBSERVATIONS = (
    _observation(0.8855),  # φ=0.88,   ω=0.8855
    _observation(0.89),  # φ=0.88,   ω=0.89
    _observation(0.8855, assessment=0.99),  # φ=0.8712, ω=0.876645
)


@pytest.mark.parametrize(
    ("bid_rate", "expected"),
    [
        (0.80, 0.0),  # 전부 하한 미만 → 실격
        (0.8712, 1 / 3),  # 경계 포함: φ 에 정확히 착지해도 생존
        (0.8750, 1 / 3),
        (0.8850, 2 / 3),
        (0.8900, 1 / 3),  # 경계 포함: ω 에 정확히 착지하면 승
        (1.00, 0.0),  # 전부 낙찰자보다 비쌈 → 패
    ],
)
def test_empirical_win_proxy_value_table(bid_rate, expected):
    assert empirical_win_proxy(bid_rate, LAYER_A_OBSERVATIONS) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("bid_rate", "expected"),
    [(0.80, 1.0), (0.8712, 2 / 3), (0.8799, 2 / 3), (0.88, 0.0), (1.0, 0.0)],
)
def test_empirical_disqualification_value_table(bid_rate, expected):
    assert empirical_disqualification_probability(
        bid_rate, LAYER_A_OBSERVATIONS
    ) == pytest.approx(expected)


def test_win_proxy_and_disqualification_need_observations():
    with pytest.raises(ValueError):
        empirical_win_proxy(0.88, [])
    with pytest.raises(ValueError):
        empirical_disqualification_probability(0.88, [])


# --- 층 C: 합성 win-proxy ---------------------------------------------------------
COMPOSED_SUPPORT = equiprobable_assessment_support([0.99, 1.00])
COMPOSED_MARGINS = build_margin_ecdf([0.0, 0.01])


def _composed(bid_rate: float) -> float:
    return composed_win_proxy(
        bid_rate,
        floor_rate=FLOOR_RATE,
        assessment=COMPOSED_SUPPORT,
        margins=COMPOSED_MARGINS,
    )


def test_composed_win_proxy_hand_computed_value_table():
    # b=0.88: a=0.99 → δ=0.008889 → S=1/2 → 0.25 ; a=1.00 → δ=0 → S=1 → 0.5
    assert _composed(0.88) == pytest.approx(0.75)
    # b=0.8802: a=0.99 → δ=0.009091 → S=1/2 ; a=1.00 → δ=0.0002 → S=1/2
    assert _composed(0.8802) == pytest.approx(0.5)


def test_composed_win_proxy_is_zero_below_the_lowest_realised_floor():
    # b < f·min(a) = 0.8712 → 생존항이 통째로 잘린다. 곡선이 법정 하한을 스스로
    # 배제하므로 guardrail 을 선점·우회하지 않는다(red line #221).
    for bid_rate in (0.0, 0.5, 0.86, 0.8700, 0.87119):
        assert _composed(bid_rate) == 0.0


def test_composed_win_proxy_is_zero_when_the_bid_beats_no_winner():
    assert _composed(1.0) == 0.0


@pytest.mark.parametrize(
    ("bid_rate", "expected"),
    [(0.86, 1.0), (0.8712, 0.5), (0.8799, 0.5), (0.88, 0.0), (1.0, 0.0)],
)
def test_composed_disqualification_value_table(bid_rate, expected):
    assert composed_disqualification_probability(
        bid_rate, floor_rate=FLOOR_RATE, assessment=COMPOSED_SUPPORT
    ) == pytest.approx(expected)


def test_composed_win_proxy_rejects_invalid_arguments():
    with pytest.raises(ValueError):
        composed_win_proxy(
            0.88,
            floor_rate=0.0,
            assessment=COMPOSED_SUPPORT,
            margins=COMPOSED_MARGINS,
        )
    with pytest.raises(ValueError):
        composed_win_proxy(
            float("nan"),
            floor_rate=FLOOR_RATE,
            assessment=COMPOSED_SUPPORT,
            margins=COMPOSED_MARGINS,
        )
    with pytest.raises(ValueError):
        composed_disqualification_probability(
            0.88, floor_rate=-1.0, assessment=COMPOSED_SUPPORT
        )


def test_composed_win_proxy_accepts_any_margin_distribution_implementation():
    # 포트 계약(§4.7-1): ECDF·KDE·수축 혼합이 같은 코드 경로에 꽂힌다.
    kde = build_reflected_kde([0.0, 0.0005, 0.001], bandwidth=0.001)
    shrunk = shrink_margin_distribution(
        cell=kde,
        parent=COMPOSED_MARGINS,
        cell_sample_count=5,
        prior_strength=25.0,
    )
    for margins in (kde, shrunk):
        value = composed_win_proxy(
            0.8802,
            floor_rate=FLOOR_RATE,
            assessment=COMPOSED_SUPPORT,
            margins=margins,
        )
        assert 0.0 <= value <= 1.0


# --- M1: 생존 컷은 곱셈형이어야 한다 ----------------------------------------------
# 실측으로 고른 사정률: f=0.88 에서 (f·a)/f 가 a 보다 1 ulp 아래로 떨어져 **나눗셈형
# 컷이 자기 경계에서 탈락**하는 값들이다(0.98~1.02 구간에 77개 존재). 이 좌표를 쓰지
# 않으면 나눗셈형으로도 테스트가 통과해 결함을 못 잡는다.
BOUNDARY_ASSESSMENTS = (0.99017, 0.99326, 1.00)
BOUNDARY_MARGINS = (0.0, 0.005, 0.02)
BOUNDARY_OBSERVATIONS = tuple(
    _observation(FLOOR_RATE + margin, assessment=assessment)
    for assessment in BOUNDARY_ASSESSMENTS
    for margin in BOUNDARY_MARGINS
)


@pytest.mark.parametrize("assessment", BOUNDARY_ASSESSMENTS)
def test_survival_cut_keeps_the_atom_at_its_own_floor_boundary(assessment):
    # b = φ_i = f·a_i 에서 그 사정률 원자는 **반드시** 살아 있어야 한다.
    bid_rate = FLOOR_RATE * assessment
    empirical = empirical_win_proxy(bid_rate, BOUNDARY_OBSERVATIONS)
    composed = composed_win_proxy(
        bid_rate,
        floor_rate=FLOOR_RATE,
        assessment=equiprobable_assessment_support(BOUNDARY_ASSESSMENTS),
        margins=build_margin_ecdf(BOUNDARY_MARGINS),
    )
    assert composed == pytest.approx(empirical)
    assert composed > 0.0


def test_division_form_survival_cut_would_drop_the_boundary_atom():
    # 결함의 존재 증명 — 이 좌표에서 동치식 a ≤ b/f 는 실제로 거짓이 된다.
    tripping = [
        assessment
        for assessment in BOUNDARY_ASSESSMENTS
        if not (assessment <= (FLOOR_RATE * assessment) / FLOOR_RATE)
    ]
    assert tripping, "fixture must contain assessments that trip the division form"
    for assessment in tripping:
        assert assessment * FLOOR_RATE <= FLOOR_RATE * assessment


# --- 층 A ≡ 층 C 정합 -------------------------------------------------------------
INDEPENDENT_ASSESSMENTS = (0.98, 1.00, 1.02)
INDEPENDENT_MARGINS = (0.0, 0.005, 0.02, 0.05)
INDEPENDENT_OBSERVATIONS = tuple(
    _observation(FLOOR_RATE + margin, assessment=assessment)
    for assessment in INDEPENDENT_ASSESSMENTS
    for margin in INDEPENDENT_MARGINS
)
FLOOR_BOUNDARIES = tuple(
    sorted({FLOOR_RATE * assessment for assessment in INDEPENDENT_ASSESSMENTS})
)
WINNER_BOUNDARIES = tuple(
    sorted(
        {
            assessment * (FLOOR_RATE + margin)
            for assessment in INDEPENDENT_ASSESSMENTS
            for margin in INDEPENDENT_MARGINS
        }
    )
)


def _independent_composed(bid_rate: float) -> float:
    return composed_win_proxy(
        bid_rate,
        floor_rate=FLOOR_RATE,
        assessment=equiprobable_assessment_support(INDEPENDENT_ASSESSMENTS),
        margins=build_margin_ecdf(INDEPENDENT_MARGINS),
    )


@pytest.mark.parametrize(
    "bid_rate",
    [0.8655, 0.8755, 0.8855, 0.8955, 0.9055, 0.9155, 0.9255, 0.9355, 0.9455],
)
def test_layer_a_and_layer_c_agree_under_structural_independence(bid_rate):
    # 관측을 a × Δ 전 조합으로 만들면 a ⊥ Δ 가 구조적으로 성립한다 — 그때 식 2 의
    # 곱 분해는 층 A 의 결합 계수와 정확히 같은 값을 낸다.
    assert _independent_composed(bid_rate) == pytest.approx(
        empirical_win_proxy(bid_rate, INDEPENDENT_OBSERVATIONS)
    )


@pytest.mark.parametrize("bid_rate", FLOOR_BOUNDARIES)
def test_layer_a_and_layer_c_agree_exactly_on_floor_boundaries(bid_rate):
    assert _independent_composed(bid_rate) == pytest.approx(
        empirical_win_proxy(bid_rate, INDEPENDENT_OBSERVATIONS)
    )


def test_winner_boundary_residual_is_bounded_by_one_observation():
    """ω 경계의 인수분해 잔여 — **0 이라 주장하지 않고 크기를 고정한다**.

    층 C 의 이김 임계는 ``δ = b/a − f`` 라 나눗셈을 피할 수 없고(포트가 δ 하나만
    받는다), ``b`` 가 정확히 ``ω_i = a(f+Δ)`` 이면 ``(a·w)/a ≠ w`` 로 원자 1개를
    잃을 수 있다. 잃는 양은 **관측 1건 몫(1/N)** 을 넘지 않아야 한다 — 그보다 크면
    1 ulp 잔여가 아니라 다른 결함이다.
    """
    atom_weight = 1.0 / len(INDEPENDENT_OBSERVATIONS)
    gaps = [
        empirical_win_proxy(bid_rate, INDEPENDENT_OBSERVATIONS)
        - _independent_composed(bid_rate)
        for bid_rate in WINNER_BOUNDARIES
    ]
    assert all(0.0 <= gap <= atom_weight + 1e-12 for gap in gaps)
    # 방향도 고정: 잔여는 층 C 가 **덜 세는** 쪽이라 낙관이 아니라 보수적이다.
    assert max(gaps) == pytest.approx(atom_weight)
    assert sum(1 for gap in gaps if gap > 1e-12) == 9  # 12 경계 중 9개(측정값)


def test_the_independence_fixture_is_not_degenerate():
    values = [
        empirical_win_proxy(bid_rate, INDEPENDENT_OBSERVATIONS)
        for bid_rate in (0.8655, 0.8855, 0.9055)
    ]
    assert max(values) > 0.0


# --- f-재기준 fallback (식 3) -----------------------------------------------------
def test_rebasing_to_the_observed_floor_reduces_to_layer_a():
    for bid_rate in (0.8655, 0.8755, 0.8855, 0.8955, 0.9155, *FLOOR_BOUNDARIES):
        assert rebased_win_proxy(
            bid_rate, INDEPENDENT_OBSERVATIONS, target_floor_rate=FLOOR_RATE
        ) == pytest.approx(empirical_win_proxy(bid_rate, INDEPENDENT_OBSERVATIONS))


def test_rebased_win_proxy_hand_computed_value_table():
    # 관측 f=0.88, 대상 f_tgt=0.90 으로 전이. a=1.00·Δ=0.005 인 관측 하나면
    # φ=0.90, ω=0.905 로 옮겨간다.
    observations = (_observation(0.885),)
    assert rebased_win_proxy(0.8950, observations, target_floor_rate=0.90) == 0.0
    assert rebased_win_proxy(0.9000, observations, target_floor_rate=0.90) == 1.0
    assert rebased_win_proxy(0.9025, observations, target_floor_rate=0.90) == 1.0
    assert rebased_win_proxy(0.9050, observations, target_floor_rate=0.90) == 1.0
    assert rebased_win_proxy(0.9051, observations, target_floor_rate=0.90) == 0.0


def test_rebasing_preserves_the_joint_pairing_not_just_the_marginals():
    # a 와 Δ 가 완전 종속인 표본(큰 a 에 큰 Δ). 결합을 보존하면 두 관측만 존재하지만
    # 주변분포만 보는 합성은 존재하지 않는 (작은 a, 큰 Δ) 조합까지 세게 된다.
    observations = (
        _observation(FLOOR_RATE + 0.001, assessment=0.98),
        _observation(FLOOR_RATE + 0.030, assessment=1.02),
    )
    bid_rate = 0.9000  # a=0.98 이면 ω=0.86338 < b, a=1.02 이면 φ=0.8976 ≤ b ≤ 0.92718
    joint = rebased_win_proxy(
        bid_rate, observations, target_floor_rate=FLOOR_RATE
    )
    marginal = composed_win_proxy(
        bid_rate,
        floor_rate=FLOOR_RATE,
        assessment=equiprobable_assessment_support((0.98, 1.02)),
        margins=build_margin_ecdf((0.001, 0.030)),
    )
    assert joint == pytest.approx(0.5)
    assert marginal == pytest.approx(0.25)
    assert joint != pytest.approx(marginal)


@pytest.mark.parametrize(
    ("bid_rate", "expected"),
    [(0.85, 1.0), (0.8820, 0.5), (0.90, 0.0)],
)
def test_rebased_disqualification_value_table(bid_rate, expected):
    # f_tgt=0.90 · a ∈ {0.98, 1.00} → φ ∈ {0.882, 0.90}
    observations = (
        _observation(0.885, assessment=0.98),
        _observation(0.885, assessment=1.00),
    )
    assert rebased_disqualification_probability(
        bid_rate, observations, target_floor_rate=0.90
    ) == pytest.approx(expected)


def test_rebased_estimators_reject_invalid_arguments():
    with pytest.raises(ValueError):
        rebased_win_proxy(0.88, [], target_floor_rate=0.88)
    with pytest.raises(ValueError):
        rebased_win_proxy(0.88, LAYER_A_OBSERVATIONS, target_floor_rate=0.0)
    with pytest.raises(ValueError):
        rebased_win_proxy(
            float("nan"), LAYER_A_OBSERVATIONS, target_floor_rate=0.88
        )
    with pytest.raises(ValueError):
        rebased_disqualification_probability(0.88, [], target_floor_rate=0.88)
    with pytest.raises(ValueError):
        rebased_disqualification_probability(
            0.88, LAYER_A_OBSERVATIONS, target_floor_rate=-0.5
        )
