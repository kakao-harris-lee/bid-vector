"""Value-table tests for the base_amount provenance classifier + estimator."""
from __future__ import annotations

import json

import pytest

from app.core.constants import BID_BASE_TRUST_RATIO_MAX
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_VAT,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
    BASIS_SUSPECT_RATIO,
    RESERVE_PRICE_COUNT,
    classify_base_basis,
    estimate_base_amount_from_reserves,
    normalize_winning_rate,
)

# Real postmortem case: 43,996,200 ÷ 0.88035 = 49,975,805.0775 (예정가-basis 오염).
_YEGA_BASE = 43_996_200 / 0.88035
# 45,000,001 (VAT-inclusive, not ÷11) ÷ 1.1 = non-integer whose ×1.1 is integer.
_VAT_BASE = 45_000_001 / 1.1

_BUDGET = 100_000_000.0


@pytest.mark.parametrize(
    "base, winning_amount, winning_rate, expected",
    [
        # clean: strictly positive exact integer 원화
        (43_996_200.0, 0, 0, BASIS_CLEAN),
        (43_996_200, None, None, BASIS_CLEAN),
        # integer wins even when a winning result is present
        (43_996_200.0, 43_000_000, 0.977, BASIS_CLEAN),
        # derived-yega: base × winning_rate == winning_amount
        (_YEGA_BASE, 43_996_200, 0.88035, BASIS_DERIVED_YEGA),
        # derived-vat: base × 1.1 lands on an integer, no winning result
        (_VAT_BASE, 0, 0, BASIS_DERIVED_VAT),
        # suspect-fractional: fractional, not yega, not vat
        (12_345_678.4321, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        # guards: winning present but rate 0/None ⇒ yega skipped (no crash)
        (_YEGA_BASE, 43_996_200, 0, BASIS_SUSPECT_FRACTIONAL),
        (_YEGA_BASE, 43_996_200, None, BASIS_SUSPECT_FRACTIONAL),
        # guards: rate present but winning missing ⇒ yega skipped
        (_YEGA_BASE, None, 0.88035, BASIS_SUSPECT_FRACTIONAL),
        (_YEGA_BASE, 0, 0.88035, BASIS_SUSPECT_FRACTIONAL),
        # guards: missing / non-positive / non-numeric base ⇒ NOT clean
        (None, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        (0.0, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        (-5.0, 0, 0, BASIS_SUSPECT_FRACTIONAL),
        ("not-a-number", 0, 0, BASIS_SUSPECT_FRACTIONAL),
    ],
)
def test_classify_base_basis(base, winning_amount, winning_rate, expected):
    assert classify_base_basis(base, winning_amount, winning_rate) == expected


def test_classify_defaults_missing_winning_args():
    """winning_amount/winning_rate default to None without raising."""
    assert classify_base_basis(43_996_200.0) == BASIS_CLEAN
    assert classify_base_basis(_YEGA_BASE) == BASIS_SUSPECT_FRACTIONAL


# --------------------------------------------------------------------------- #
# suspect-ratio: 기초금액 ÷ 추정가격 이 부가세로 설명 안 되는 배수일 때
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "base, budget_estimate, expected",
    [
        # 면세(1.00)·과세(1.10)·경계(1.15) 는 부가세+측정 마진으로 설명된다 ⇒ clean 유지
        (_BUDGET, _BUDGET, BASIS_CLEAN),
        (_BUDGET * 1.10, _BUDGET, BASIS_CLEAN),
        (round(_BUDGET * BID_BASE_TRUST_RATIO_MAX), _BUDGET, BASIS_CLEAN),
        # 경계 바로 위 ⇒ suspect-ratio (엄격 부등호)
        (round(_BUDGET * 1.1501), _BUDGET, BASIS_SUSPECT_RATIO),
        # 운영 DB 실측 p50 1.408 — 부가세로 설명 불가한 "다른 금액 필드 혼입" 계열
        (round(_BUDGET * 1.408), _BUDGET, BASIS_SUSPECT_RATIO),
        # 추정가격 쪽 파싱이 깨진 극단(est 3,636원) 도 같은 버킷으로 떨어진다:
        # 어느 쪽이 깨졌는지 알 수 없으므로 그 base 를 clean 으로 신뢰하지 않는다.
        (_BUDGET, 3_636.0, BASIS_SUSPECT_RATIO),
        # 저측(<1.0)은 이 규칙의 대상이 아니다(기존 suspect-fractional 이 커버)
        (_BUDGET * 0.5, _BUDGET, BASIS_CLEAN),
        # 추정가격 결측/0/음수/비수치 ⇒ 규칙 비적용 (기존 동작 완전 불변)
        (_BUDGET * 10, None, BASIS_CLEAN),
        (_BUDGET * 10, 0.0, BASIS_CLEAN),
        (_BUDGET * 10, -1.0, BASIS_CLEAN),
        (_BUDGET * 10, "not-a-number", BASIS_CLEAN),
    ],
)
def test_classify_ratio_rule(base, budget_estimate, expected):
    assert classify_base_basis(base, 0, 0, budget_estimate) == expected


def test_ratio_rule_is_opt_in_by_caller():
    """budget_estimate 를 넘기지 않는 호출부는 동작이 바이트 동일하다(no-op)."""
    polluted = _BUDGET * 10
    assert classify_base_basis(polluted, 0, 0) == BASIS_CLEAN
    assert classify_base_basis(polluted) == BASIS_CLEAN
    assert classify_base_basis(polluted, 0, 0, _BUDGET) == BASIS_SUSPECT_RATIO


@pytest.mark.parametrize(
    "base, winning_amount, winning_rate, budget_estimate, without_ratio",
    [
        # 예정가-역산 패턴이면서 비율도 깨진 행 (49,975,805 ÷ 30,000,000 = 1.67)
        (_YEGA_BASE, 43_996_200, 0.88035, 30_000_000.0, BASIS_DERIVED_YEGA),
        # 정수 base 라 VAT 패턴에도 걸리는 행 (1,000,000,000 ÷ 100,000,000 = 10)
        (_BUDGET * 10, 0, 0, _BUDGET, BASIS_CLEAN),
    ],
)
def test_ratio_verdict_outranks_pattern_verdicts(
    base, winning_amount, winning_rate, budget_estimate, without_ratio
):
    """비율 판정이 first-match 테이블의 맨 앞에 선다 — 패턴 판정보다 강한 증거다.

    derived-yega(``base × rate == winning``)는 낙찰률이 ``winning ÷ base`` 로 정규화돼
    저장되는 settled 행에서 **자기충족**이고(tests/test_backtest_latest_award_holdouts.py
    참조), derived-vat(``base × 1.1`` 이 정수)은 모든 정수 base 에서 참이다. 둘 다 그
    base 가 진짜 기초금액인지에 대해서는 아무 말도 하지 않는다. 반면 추정가격 대비 비율은
    같은 공고의 **독립적인 두 번째 금액**과의 모순이라 더 강한 증거다.

    라벨만 바뀌고 소비자 동작은 불변이다: 세 라벨 모두 non-clean 버킷이다.
    """
    assert classify_base_basis(base, winning_amount, winning_rate) == without_ratio
    assert (
        classify_base_basis(base, winning_amount, winning_rate, budget_estimate)
        == BASIS_SUSPECT_RATIO
    )


def test_suspect_ratio_is_registered_in_the_basis_vocabulary():
    """새 라벨은 ``ALL_BASES`` 에 들어야 clean-only 소비자가 자동으로 배제한다."""
    from app.services.base_amount_basis import ALL_BASES

    assert BASIS_SUSPECT_RATIO in ALL_BASES
    # HistoricalData.base_amount_basis 는 String(30) — 컬럼 폭을 넘지 않는다.
    assert all(len(basis) <= 30 for basis in ALL_BASES)


def test_trust_ratio_constant_is_single_sourced():
    """분류기와 #356 budget_cap 게이트가 같은 상수를 쓴다(§4.5-1 단일 출처)."""
    from app.services.opportunity_analysis import market

    assert market.BID_BASE_TRUST_RATIO_MAX is BID_BASE_TRUST_RATIO_MAX
    assert BID_BASE_TRUST_RATIO_MAX == 1.15


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.88035, 0.88035),  # already a fraction (scsbid) — passthrough
        (88.035, 0.88035),  # percentage (HTML parsing) — divided by 100
        (0.4, 0.4),  # genuinely low fraction — no range gate
        (150.0, 1.5),  # percentage boundary case
        (1.5, 1.5),  # at threshold — not divided
        (0, None),  # non-positive → None
        (-5.0, None),
        (None, None),
        ("x", None),  # non-numeric → None
    ],
)
def test_normalize_winning_rate(value, expected):
    result = normalize_winning_rate(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def _reserves(base: float, count: int = RESERVE_PRICE_COUNT) -> list[float]:
    """Build ``count`` reserve prices straddling ``base`` by ~±2.5%."""
    spread = base * 0.025
    step = (2 * spread) / (count - 1)
    return [round(base - spread + step * i) for i in range(count)]


def test_estimate_from_15_reserves_returns_midpoint():
    base = 1_000_000_000.0
    reserves = _reserves(base)
    expected = round((min(reserves) + max(reserves)) / 2)
    assert estimate_base_amount_from_reserves(reserves) == expected
    # midpoint recovers base to within the ±2.5% band's rounding
    assert abs(estimate_base_amount_from_reserves(reserves) - base) <= 1


def test_estimate_accepts_json_string_column():
    reserves = _reserves(500_000_000.0)
    from_list = estimate_base_amount_from_reserves(reserves)
    from_json = estimate_base_amount_from_reserves(json.dumps(reserves))
    assert from_json == from_list
    assert from_json is not None


def test_estimate_requires_full_15():
    reserves = _reserves(1_000_000.0)[:14]
    assert estimate_base_amount_from_reserves(reserves) is None


@pytest.mark.parametrize("raw", [None, "", "[]", "not json", "{}", 12345])
def test_estimate_unrecoverable_inputs_return_none(raw):
    assert estimate_base_amount_from_reserves(raw) is None


def test_estimate_drops_nonpositive_and_nonnumeric_below_threshold():
    """A list padded with junk/zeros that leaves < 15 valid reserves ⇒ None."""
    reserves = _reserves(2_000_000.0)[:14] + [0, -1, "x", None]
    assert estimate_base_amount_from_reserves(reserves) is None
