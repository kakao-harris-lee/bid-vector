"""시나리오 후보 선언·조립기 — 두 엔진이 공유하는 한 벌의 계약.

분포 엔진(Phase 1)과 낙찰률 GBM(Phase 2)이 각자 z·가중치·payload 모양을 선언하면
한쪽만 조정될 때 후보 폭이 조용히 갈린다. 여기 단언이 그 공유를 고정한다.
"""

import pytest

from app.ai.predictors.historical import clamp_bid_rate
from app.ai.predictors.scenario_spec import (
    CANDIDATE_SCENARIOS,
    SCENARIO_INTERVAL_Z,
    build_scenario_candidates,
    scenario_bid_rates,
)


def test_scenario_table_is_ordered_conservative_base_aggressive():
    """호출부가 ``candidates[1]`` 로 기준 후보를 집으므로 순서는 선언이지 우연이 아니다."""
    assert [label for label, _sign, _weight in CANDIDATE_SCENARIOS] == [
        "conservative",
        "base",
        "aggressive",
    ]
    assert [sign for _label, sign, _weight in CANDIDATE_SCENARIOS] == [-1.0, 0.0, 1.0]
    assert sum(weight for _label, _sign, weight in CANDIDATE_SCENARIOS) == pytest.approx(1.0)


def test_rates_are_centre_plus_minus_z_sigma():
    rates = scenario_bid_rates(center=0.90, std=0.02)
    assert rates[1] == pytest.approx(0.90)
    assert rates[0] == pytest.approx(0.90 - SCENARIO_INTERVAL_Z * 0.02)
    assert rates[2] == pytest.approx(0.90 + SCENARIO_INTERVAL_Z * 0.02)


def test_scale_multiplies_outside_the_parentheses():
    """분포 엔진의 기존 산술과 **부동소수점까지** 같아야 한다.

    분배법칙으로 펴면(``scale*center + sign*z*scale*std``) 마지막 비트가 달라져 반올림된
    payload 가 바뀔 수 있다. 그래서 배율은 괄호 밖 한 번만 곱한다.
    """
    center, std, scale = 0.9931, 0.0177, 0.8713
    expected = tuple(
        clamp_bid_rate(scale * (center + sign * SCENARIO_INTERVAL_Z * std))
        for _label, sign, _weight in CANDIDATE_SCENARIOS
    )
    assert scenario_bid_rates(center=center, std=std, scale=scale) == expected


def test_candidates_carry_rounded_rate_price_and_weight():
    """금액은 **반올림 전** 투찰율에서 나온다 — 분포 엔진의 기존 계약을 그대로 옮겼다.

    표시용 ``bid_rate`` 는 4자리로 접히지만 ``predicted_price`` 는 원본 율로 계산되므로
    ``budget × 표시율`` 과 마지막 자리가 다를 수 있다. 두 값이 같다고 가정하는 소비자가
    생기지 않도록 여기서 비대칭을 명시한다.
    """
    budget = 100_000_000.0
    raw_rates = scenario_bid_rates(center=0.9, std=0.02)
    candidates = build_scenario_candidates(center=0.9, std=0.02, budget=budget)

    assert [candidate["label"] for candidate in candidates] == [
        "conservative",
        "base",
        "aggressive",
    ]
    for candidate, raw_rate in zip(candidates, raw_rates):
        assert candidate["bid_rate"] == round(raw_rate, 4)
        assert candidate["predicted_price"] == round(budget * raw_rate, 2)
    assert [candidate["confidence_weight"] for candidate in candidates] == [
        weight for _label, _sign, weight in CANDIDATE_SCENARIOS
    ]
    assert candidates[0]["bid_rate"] < candidates[1]["bid_rate"] < candidates[2]["bid_rate"]


def test_rates_are_clamped_to_the_shared_bid_rate_window():
    """폭이 극단적으로 넓어도 투찰율 창을 벗어나지 않는다(clamp 는 historical 한 벌)."""
    rates = scenario_bid_rates(center=0.9, std=10.0)
    assert all(rate == clamp_bid_rate(rate) for rate in rates)
