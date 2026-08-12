"""값표 테스트 — 사정률 계층 수축(발주기관 → 공종 → 전역) 커널."""

import pytest

from app.domain.assessment_shrinkage import (
    AGENCY_PRIOR_STRENGTH,
    CATEGORY_PRIOR_STRENGTH,
    LEVEL_AGENCY,
    LEVEL_CATEGORY,
    LEVEL_GLOBAL,
    MIN_PREDICTIVE_STD,
    LevelObservation,
    resolve_assessment_posterior,
    shrink_toward,
)


def _level(count: int, mean: float, variance: float = 0.0001) -> LevelObservation:
    return LevelObservation(sample_count=count, mean=mean, variance=variance)


def test_shrink_toward_value_table():
    # n=κ 지점에서 정확히 절반씩 섞인다.
    blended, weight = shrink_toward(
        1.02, 12, prior_mean=1.00, prior_strength=AGENCY_PRIOR_STRENGTH
    )
    assert weight == pytest.approx(0.5)
    assert blended == pytest.approx(1.01)


def test_global_only_posterior_passes_through_global_mean():
    posterior = resolve_assessment_posterior(
        agency=None, category=None, global_level=_level(1000, 0.9974)
    )
    assert posterior.mean == pytest.approx(0.9974)
    assert posterior.level_weights == {
        LEVEL_AGENCY: 0.0,
        LEVEL_CATEGORY: 0.0,
        LEVEL_GLOBAL: 1.0,
    }
    assert posterior.effective_sample_count == pytest.approx(1000.0)


def test_deep_agency_dominates_its_own_mean():
    posterior = resolve_assessment_posterior(
        agency=_level(10_000, 1.005),
        category=_level(3_000, 0.995),
        global_level=_level(60_000, 0.99),
    )
    assert posterior.level_weights[LEVEL_AGENCY] == pytest.approx(
        10_000 / (10_000 + AGENCY_PRIOR_STRENGTH)
    )
    assert posterior.mean == pytest.approx(1.005, abs=2e-5)


def test_shallow_agency_is_strongly_shrunk_to_upper_levels():
    # 실측 다수 케이스: 기관 표본 2건 — 자기 가중치는 2/14 ≈ 14.3% 에 그친다.
    posterior = resolve_assessment_posterior(
        agency=_level(2, 1.05),
        category=_level(38, 1.00),
        global_level=_level(6_000, 0.98),
    )
    agency_weight = posterior.level_weights[LEVEL_AGENCY]
    assert agency_weight == pytest.approx(2 / (2 + AGENCY_PRIOR_STRENGTH))
    assert posterior.mean < 1.02  # 자기 평균 1.05 에서 강하게 끌려 내려온다


def test_three_level_hand_computed_value_table():
    # 손계산: cat = (38·1.00 + 40·0.98)/78, agency 자기 w = 2/14,
    # posterior = (2/14)·1.02 + (12/14)·cat
    category_blend = ((38 * 1.00) + (CATEGORY_PRIOR_STRENGTH * 0.98)) / (
        38 + CATEGORY_PRIOR_STRENGTH
    )
    expected = ((2 / 14) * 1.02) + ((12 / 14) * category_blend)

    posterior = resolve_assessment_posterior(
        agency=_level(2, 1.02),
        category=_level(38, 1.00),
        global_level=_level(6_000, 0.98),
    )
    assert posterior.mean == pytest.approx(expected)
    # 가중치 손계산: w_c = (12/14)·(38/78), w_g = (12/14)·(40/78)
    assert posterior.level_weights[LEVEL_CATEGORY] == pytest.approx((12 / 14) * (38 / 78))
    assert posterior.level_weights[LEVEL_GLOBAL] == pytest.approx((12 / 14) * (40 / 78))


def test_level_weights_always_sum_to_one():
    for agency, category in [
        (None, None),
        (_level(0, 1.0), _level(0, 1.0)),
        (_level(3, 1.01), None),
        (None, _level(50, 1.0)),
        (_level(150, 1.0), _level(2_000, 0.99)),
    ]:
        posterior = resolve_assessment_posterior(
            agency=agency, category=category, global_level=_level(5_000, 0.99)
        )
        assert sum(posterior.level_weights.values()) == pytest.approx(1.0)


def test_zero_count_levels_behave_exactly_like_missing_levels():
    with_zero = resolve_assessment_posterior(
        agency=_level(0, 5.0), category=_level(0, 5.0), global_level=_level(100, 0.99)
    )
    with_none = resolve_assessment_posterior(
        agency=None, category=None, global_level=_level(100, 0.99)
    )
    assert with_zero == with_none


def test_predictive_std_is_floored_when_variances_collapse():
    posterior = resolve_assessment_posterior(
        agency=_level(30, 1.0, variance=0.0),
        category=_level(500, 1.0, variance=0.0),
        global_level=_level(9_000, 1.0, variance=0.0),
    )
    assert posterior.std == MIN_PREDICTIVE_STD


def test_single_sample_level_inherits_parent_variance_not_its_own_zero():
    # 기관 표본 1건(분산 0)은 미관측이지 확신이 아니다 — 공종 분산을 상속해야 한다.
    posterior = resolve_assessment_posterior(
        agency=_level(1, 1.0, variance=0.0),
        category=_level(400, 1.0, variance=0.0004),
        global_level=_level(9_000, 1.0, variance=0.0001),
    )
    lone_agency_weight = posterior.level_weights[LEVEL_AGENCY]
    category_weight = posterior.level_weights[LEVEL_CATEGORY]
    global_weight = posterior.level_weights[LEVEL_GLOBAL]
    expected_variance = (
        (lone_agency_weight * 0.0004)  # 상속된 공종 분산
        + (category_weight * 0.0004)
        + (global_weight * 0.0001)
    )
    assert posterior.std == pytest.approx(expected_variance**0.5)


def test_empty_global_level_fails_loudly():
    with pytest.raises(ValueError):
        resolve_assessment_posterior(
            agency=None, category=None, global_level=_level(0, 0.0)
        )
