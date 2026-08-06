"""예산 상한(budget_cap)이 낙찰하한을 끌어내리지 못하게 한다 — #354/#355 후속.

WHY
---
``_resolve_recommended_amount`` 는 추천 투찰가를 예산 범위 안으로 clamp 한다. 그
상한이 ``project.budget_estimate``(추정가격, ex-VAT)였는데, 함께 clamp 되는
``price_range_min/max`` 는 predictor 가 기초금액을 base 로 산출한 값이다.

git 이력이 원인을 특정한다: 이 상한은 2026-05-10(``6b7f185``) 도입 당시엔
predictor 도 추정가격 기준이라 **basis 가 일치**했다. #162(``4645ce4``)가
predictor 만 기초금액으로 옮기고 이 상한을 남겨 두면서 자가 갈렸다 — 설계가 아니라
부수 피해다.

가장 위험한 결과는 상한이 **하한을 깎던 것**이다:

    price_lower = min(price_lower, budget_cap)

낙찰하한 × 기초금액이 추정가격보다 큰 과세 공고에서 추천가가 법정 하한 아래로
내려간다 — 제출하면 **즉시 실격**이다. 운영 DB 홀드아웃에서 400건 중 78건이 그
상태였다. 하한을 깎지 않으면 78 → 0 이 되고, 이미 합법이던 공고의 추천가는
소수점까지 그대로다(과세 n=22 / 면세 n=300 실측 동일).

실투찰 3건은 실격 1건(하한·사정률 축) + 패찰 2건(과추천 축)이며, 이 수정은 전자
축의 구조적 원인을 제거한다.
"""

from __future__ import annotations

import pytest

from app.services.opportunity_analysis import OpportunityAnalysisService
from app.services.opportunity_analysis.market import (
    BUDGET_CAP_PLAUSIBLE_BASE_RATIO,
    resolve_budget_cap,
)
from tests.support.basis_fixtures import (
    BASE_AMOUNT,
    BUDGET_ESTIMATE,
    build_vat_notice,
)

# 낙찰하한이 기초금액에 곱해지면 추정가격을 넘어서는 구간을 만든다.
# 0.95 × 110,000,000 = 104,500,000 > 100,000,000(추정가격) → 예전 상한이 하한을 깎았다.
_HIGH_FLOOR_RATE = 0.95
_FLOOR_PRICE = _HIGH_FLOOR_RATE * BASE_AMOUNT          # 104,500,000
_CEILING_PRICE = 0.98 * BASE_AMOUNT                    # 107,800,000
_HEURISTIC_BID = BUDGET_ESTIMATE * 0.85                # 85,000,000 (휴리스틱 포트 형태)


def _prediction(*, bid_base: float | None = BASE_AMOUNT, floor_price: float = _FLOOR_PRICE) -> dict:
    payload: dict = {"price_range_min": floor_price, "price_range_max": _CEILING_PRICE}
    if bid_base is not None:
        payload["bid_base"] = bid_base
    return payload


def _resolve(*, project=None, prediction: dict | None = None, rec_bid: float = _HEURISTIC_BID) -> float:
    return OpportunityAnalysisService()._resolve_recommended_amount(
        project if project is not None else build_vat_notice(),
        prediction if prediction is not None else _prediction(),
        {"recommended_bid": rec_bid},
    )


# --------------------------------------------------------------------------- #
# RED LINE — 추천가는 낙찰하한 아래로 내려가지 않는다
# --------------------------------------------------------------------------- #


def test_recommendation_never_falls_below_the_predictor_floor():
    """과세 공고: 하한×기초금액 > 추정가격이어도 추천가가 하한 위에 남는다."""
    amount = _resolve()

    assert amount == pytest.approx(_FLOOR_PRICE)
    # 예전 동작(상한이 하한을 깎음)이면 추정가격에 착지해 하한 미달이었다.
    assert amount > BUDGET_ESTIMATE
    assert amount >= _FLOOR_PRICE


def test_floor_is_preserved_even_when_base_is_unavailable():
    """base 를 못 받아 상한이 추정가격이어도 하한은 깎이지 않는다.

    red line 은 상한의 basis 가 아니라 '상한이 하한을 깎는가'에 달려 있다 —
    홀드아웃에서 두 설계가 모든 지표에서 동일했던 이유다.
    """
    amount = _resolve(prediction=_prediction(bid_base=None))

    assert amount == pytest.approx(_FLOOR_PRICE)


@pytest.mark.parametrize("floor_price", [0.80 * BASE_AMOUNT, 0.88 * BASE_AMOUNT, _FLOOR_PRICE])
def test_floor_is_never_lowered_for_any_floor_rate(floor_price):
    """하한이 어디에 있든 추천가는 그 위다(값표)."""
    amount = _resolve(prediction=_prediction(floor_price=floor_price))

    assert amount >= floor_price - 1e-6


# --------------------------------------------------------------------------- #
# 합법이던 공고는 한 건도 바뀌지 않는다 (홀드아웃의 핵심 안전 결과)
# --------------------------------------------------------------------------- #


def test_already_legal_recommendation_is_unchanged():
    """하한이 추정가격보다 낮으면(=예전에도 합법) 결과가 그대로다.

    홀드아웃에서 legacy 가 이미 합법이던 공고는 세 설계가 소수점까지 같았다
    (과세 n=22, 면세 n=300). 이 테스트가 그 성질을 단위 수준에서 고정한다.
    """
    low_floor = 0.80 * BUDGET_ESTIMATE  # 80,000,000 — 추정가격 아래
    prediction = {
        "price_range_min": low_floor,
        "price_range_max": 0.95 * BUDGET_ESTIMATE,
        "bid_base": BASE_AMOUNT,
    }
    amount = _resolve(prediction=prediction)

    # 휴리스틱 추천가(85,000,000)가 [80,000,000, 95,000,000] 안에 있으므로 그대로 통과.
    assert amount == pytest.approx(_HEURISTIC_BID)


def test_tax_free_notice_is_a_no_op():
    """면세 공고(기초금액 == 추정가격)는 상한 basis 변경이 무의미하다."""
    project = build_vat_notice(budget_estimate=BUDGET_ESTIMATE)
    prediction = {
        "price_range_min": 0.80 * BUDGET_ESTIMATE,
        "price_range_max": 0.95 * BUDGET_ESTIMATE,
        "bid_base": BUDGET_ESTIMATE,
    }

    assert _resolve(project=project, prediction=prediction) == pytest.approx(_HEURISTIC_BID)


def test_menu_path_is_untouched():
    """메뉴가 있으면 예전과 같이 메뉴 투찰가를 그대로 쓴다(이 PR 범위 밖)."""
    prediction = _prediction()
    prediction["bid_target_menu"] = {
        "options": [{"label": "recommended", "bid_price": 96_500_000.0}]
    }

    assert _resolve(prediction=prediction) == pytest.approx(96_500_000.0)


# --------------------------------------------------------------------------- #
# 상한 해석 커널 — 값표 (오염 가드 포함)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reported_base, budget_estimate, expected",
    [
        (BASE_AMOUNT, BUDGET_ESTIMATE, BASE_AMOUNT),        # 정상 과세(1.1x) → 기초금액
        (BUDGET_ESTIMATE, BUDGET_ESTIMATE, BUDGET_ESTIMATE),  # 면세 → 동일
        (None, BUDGET_ESTIMATE, BUDGET_ESTIMATE),           # 미보고 → 추정가격
        (0.0, BUDGET_ESTIMATE, BUDGET_ESTIMATE),
        ("bad", BUDGET_ESTIMATE, BUDGET_ESTIMATE),          # 비수치
        (BASE_AMOUNT, 0.0, BASE_AMOUNT),                    # 추정가격이 없으면 base 그대로
        (None, None, 0.0),                                  # 아무것도 없으면 0(상한 없음)
        # 오염 가드: 부가세는 10% 이므로 1.5배를 넘는 비는 세금이 아니라 수집 오염이다.
        # 그 base 를 상한으로 삼으면 추천가가 함께 부푼다(홀드아웃 과세 12/185 실측).
        (BUDGET_ESTIMATE * 10, BUDGET_ESTIMATE, BUDGET_ESTIMATE),
        (BUDGET_ESTIMATE * 2, BUDGET_ESTIMATE, BUDGET_ESTIMATE),
        (BUDGET_ESTIMATE * BUDGET_CAP_PLAUSIBLE_BASE_RATIO, BUDGET_ESTIMATE,
         BUDGET_ESTIMATE * BUDGET_CAP_PLAUSIBLE_BASE_RATIO),  # 경계값은 통과
    ],
)
def test_resolve_budget_cap_value_table(reported_base, budget_estimate, expected):
    assert resolve_budget_cap(reported_base, budget_estimate) == pytest.approx(expected)


def test_polluted_base_does_not_inflate_the_recommendation():
    """오염된 base(추정가격의 10배)가 상한으로 올라가지 않는다."""
    prediction = {
        "price_range_min": 0.80 * BUDGET_ESTIMATE,
        "price_range_max": 0.95 * BUDGET_ESTIMATE,
        "bid_base": BUDGET_ESTIMATE * 10,
    }
    amount = _resolve(prediction=prediction)

    assert amount == pytest.approx(_HEURISTIC_BID)
    assert amount <= BUDGET_ESTIMATE
