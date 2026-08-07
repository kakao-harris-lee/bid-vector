"""예산 상한이 **낙찰하한**을 깎지 못하게 한다 — #354/#355 후속.

WHY
---
``_resolve_recommended_amount`` 는 추천 투찰가를 예산 상한(``budget_cap``) 안으로
clamp 한다. 그 상한은 ``project.budget_estimate``(추정가격, ex-VAT)인데, 함께 clamp
되는 predictor 출력은 기초금액-relative 다. 그래서 낙찰하한 × 기초금액이 추정가격을
넘는 공고에서 하한까지 깎여, 추천가가 **법정 하한 아래**로 내려갔다.

git 이력이 원인을 특정한다: 상한 도입 시점(2026-05-10 ``6b7f185``)에는 predictor 도
추정가격 기준이라 basis 가 일치했고, #162(``4645ce4``)가 predictor 만 기초금액으로
옮기면서 자가 갈렸다 — 설계가 아니라 부수 피해다.

무엇을 지키고 무엇을 지키지 않는가 (리뷰에서 교정된 핵심)
------------------------------------------------------
``price_range_min`` 은 후보 예측가의 최솟값, 즉 **시나리오 범위 하단**이지 법정 하한이
아니다. 그것까지 상한 위로 풀면 하한 여유가 충분한 공고의 추천가만 올라가 과추천이
된다(반사실 실측: 그 구간 34건 중 28건이 낙찰가에서 멀어짐).

``floor_price`` 도 "법정 하한 그 자체"가 아니라 **max(config category floor, published
legal floor)** 라, 그것이 보고됐다는 것만으로는 신뢰의 근거가 되지 않는다(미보고 공고에서
config floor 로 상한을 넘겼더니 상승 12건이 12/12 낙찰가에서 멀어졌다). 그래서 상한을
넘길 권한은 **V3 계약**의 두 조건을 모두 만족할 때로 한정한다:

1. 공고가 published 법정하한을 실제로 보고했을 것(``legal_floor_bid_rate``)
2. 기초금액 ÷ 추정가격 ≤ ``BID_BASE_TRUST_RATIO_MAX``(1.15)

두 번째 조건이 필요한 이유는 하한 금액이 ``rate × 기초금액`` 이라 base 가 부풀면 하한도
함께 부풀기 때문이다 — 그 코호트에서 실낙찰자 전원이 그 임계 미만으로 낙찰했다(허수 임계).

실투찰 3건은 실격 1건(하한·사정률 축) + 패찰 2건(과추천 축)이다. 이 수정은 **실격과 같은
계열(하한 미달)의 별개 결함**을 제거하며, 그 사건의 원인은 아니다 — 8/3 공고는
기초금액/추정가격 비가 1.100 이라 **V3 가 값을 바꾸는 창(1.136 < 비 ≤ 1.15) 밖**이고
(1.136 = 1/0.88 — legacy clamp 가 하한을 깎기 시작하는 지점), 제출가 자체가 이 함수를
통과하지도 않았다(수기 제출). 실격 원인은 예정가 추첨 +0.558% 로 확정됐다.
"""

from __future__ import annotations

import pytest

from app.services.bid_base import prepare_prediction_inputs
from app.services.opportunity_analysis import OpportunityAnalysisService
from tests.support.basis_fixtures import (
    BASE_AMOUNT,
    BUDGET_ESTIMATE,
    build_vat_notice,
    persist_vat_notice,
)

# 기초금액 110,000,000 / 추정가격 100,000,000 (비 1.10).
# 낙찰하한 0.95 × 기초금액 = 104,500,000 > 추정가격 → 예전 상한이 하한을 깎던 구간.
_HIGH_FLOOR_PRICE = 0.95 * BASE_AMOUNT      # 104,500,000
_RANGE_MIN = 0.96 * BASE_AMOUNT             # 105,600,000 (범위 하단 — 하한보다 위)
_RANGE_MAX = 0.99 * BASE_AMOUNT             # 108,900,000
_HEURISTIC_BID = BUDGET_ESTIMATE * 0.85     # 85,000,000 (휴리스틱 포트의 실제 형태)


def _prediction(
    *,
    floor_price: float | None = _HIGH_FLOOR_PRICE,
    range_min: float = _RANGE_MIN,
    range_max: float = _RANGE_MAX,
    bid_base: float | None = BASE_AMOUNT,
    legal_floor_bid_rate: float | None = 0.95,
) -> dict:
    """guarded predictor dict 형태.

    ``legal_floor_bid_rate`` 는 공고가 published 법정하한을 실제로 보고했을 때만 값이
    있고, 미보고 공고에서는 ``None`` 이다(``guardrails_apply`` 계약).
    """
    payload: dict = {
        "price_range_min": range_min,
        "price_range_max": range_max,
        "legal_floor_bid_rate": legal_floor_bid_rate,
    }
    if floor_price is not None:
        payload["floor_price"] = floor_price
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
# RED LINE — 보고된 법정 하한은 예산 상한을 넘어서라도 지킨다
# --------------------------------------------------------------------------- #


def test_legal_floor_is_honoured_above_the_budget_cap():
    """``floor_price`` 가 추정가격을 넘어도 추천가가 그 아래로 내려가지 않는다."""
    amount = _resolve()

    assert amount >= _HIGH_FLOOR_PRICE
    assert amount > BUDGET_ESTIMATE  # 예전 동작은 추정가격에 착지해 하한 미달이었다


@pytest.mark.parametrize(
    "floor_price",
    [0.92 * BASE_AMOUNT, 0.95 * BASE_AMOUNT, 1.01 * BUDGET_ESTIMATE],
)
def test_legal_floor_is_never_undercut(floor_price):
    """하한이 어디에 있든 추천가는 그 위다(값표)."""
    amount = _resolve(prediction=_prediction(floor_price=floor_price))

    assert amount >= floor_price


def test_rounding_cannot_break_the_floor():
    """2자리 반올림이 내림이 되어도 하한을 밑돌지 않는다(홀드아웃 잔차 1/743 제거)."""
    # 소수 셋째 자리가 있어 round() 가 내림이 되는 하한.
    floor_price = 104_500_000.005
    amount = _resolve(prediction=_prediction(floor_price=floor_price))

    assert amount >= floor_price


# --------------------------------------------------------------------------- #
# RED LINE — 성립 불가한 게시 하한율은 상한을 넘길 권한을 얻지 못한다
#
# V3 게이트의 1번 조건("공고가 published 법정하한을 보고했다")은 값의 **존재**만 보고
# 값이 하한으로 성립하는지는 보지 않았다. KONEPS 원문 ``sucsfbidLwltRate`` 가 "100"
# (또는 분수 "1")인 공고 47건이 그대로 적재돼 있어, base/추정가격 ≤ 1.15 인 공고에서
# ``1.0 × 기초금액`` 이 하한으로 강제돼 기초금액 전액이 추천가가 됐다(라이브 400건 중
# 1건 실측, 최대 +10% 과추천).
# --------------------------------------------------------------------------- #


def test_implausible_published_floor_cannot_escape_the_budget_cap(test_db):
    """게시 하한 1.00000 은 V3 두 조건이 모두 성립하는 상황에서도 상한을 못 넘는다."""
    project = persist_vat_notice(test_db)  # base/est = 1.10 ≤ 1.15 → 2번 조건 통과
    project.award_floor_rate = 1.0
    test_db.commit()

    inputs = prepare_prediction_inputs(test_db, project)
    # 1번 조건이 여기서 닫힌다: 성립 불가값은 "하한 미보고"와 동일 취급.
    assert inputs.legal_floor_bid_rate is None

    amount = _resolve(
        project=project,
        prediction=_prediction(
            floor_price=1.0 * BASE_AMOUNT,  # 110,000,000 — 상한 위
            legal_floor_bid_rate=inputs.legal_floor_bid_rate,
        ),
    )

    assert amount <= BUDGET_ESTIMATE


def test_plausible_published_floor_still_reaches_the_gate(test_db):
    """게이트는 진짜 게시값을 막지 않는다 — 관측된 최대 실값이 그대로 흐른다."""
    project = persist_vat_notice(test_db)
    project.award_floor_rate = 0.89995
    test_db.commit()

    inputs = prepare_prediction_inputs(test_db, project)

    assert inputs.legal_floor_bid_rate == pytest.approx(0.89995)


# --------------------------------------------------------------------------- #
# 범위 하단(price_range_min)은 상한을 넘기지 않는다 — 과추천 방지
# --------------------------------------------------------------------------- #


def test_range_min_alone_does_not_escape_the_cap():
    """``floor_price`` 없이 ``price_range_min`` 만 상한을 넘으면 상한에 머문다.

    범위 하단은 법정 하한이 아니다. 이것까지 풀면 하한 여유가 충분한 공고의 추천가만
    올라가 과추천이 된다(홀드아웃 34건 중 28건이 낙찰가에서 멀어짐) — 리뷰 F2.
    """
    amount = _resolve(prediction=_prediction(floor_price=None))

    assert amount == pytest.approx(BUDGET_ESTIMATE)
    assert amount < _RANGE_MIN


def test_low_legal_floor_leaves_the_legacy_result_untouched():
    """하한이 상한 아래면 결과가 예전과 같다 — 대다수 공고가 여기 해당한다."""
    prediction = _prediction(
        floor_price=0.80 * BUDGET_ESTIMATE,
        range_min=0.80 * BUDGET_ESTIMATE,
        range_max=0.95 * BUDGET_ESTIMATE,
    )

    assert _resolve(prediction=prediction) == pytest.approx(_HEURISTIC_BID)


def test_tax_free_notice_is_a_no_op():
    """면세 공고(기초금액 == 추정가격)에서는 상한 basis 정렬이 무의미하다."""
    project = build_vat_notice(budget_estimate=BUDGET_ESTIMATE)
    prediction = _prediction(
        floor_price=0.80 * BUDGET_ESTIMATE,
        range_min=0.80 * BUDGET_ESTIMATE,
        range_max=0.95 * BUDGET_ESTIMATE,
        bid_base=BUDGET_ESTIMATE,
    )

    assert _resolve(project=project, prediction=prediction) == pytest.approx(_HEURISTIC_BID)


def test_menu_path_is_untouched():
    """메뉴가 있으면 메뉴 투찰가를 그대로 쓴다(이 PR 범위 밖)."""
    prediction = _prediction()
    prediction["bid_target_menu"] = {"options": [{"label": "recommended", "bid_price": 96_500_000.0}]}

    assert _resolve(prediction=prediction) == pytest.approx(96_500_000.0)


# --------------------------------------------------------------------------- #
# 축퇴 경로 — 예산이 없거나 값이 뒤집혀도 하한은 지켜진다
# --------------------------------------------------------------------------- #


def test_zero_budget_still_honours_the_floor():
    """추정가격도 기초금액도 없으면 상한이 없지만, 하한은 여전히 지킨다."""
    project = build_vat_notice(budget_estimate=0.0)
    amount = _resolve(project=project, prediction=_prediction(bid_base=None))

    assert amount >= _HIGH_FLOOR_PRICE


def test_inverted_range_does_not_produce_a_below_floor_amount():
    """범위가 뒤집혀(min > max) 들어와도 하한 아래로 내려가지 않는다."""
    amount = _resolve(prediction=_prediction(range_min=_RANGE_MAX, range_max=_RANGE_MIN))

    assert amount >= _HIGH_FLOOR_PRICE


@pytest.mark.parametrize("bad_floor", [None, 0.0, -1.0, float("nan"), "bad"])
def test_unusable_floor_falls_back_to_the_legacy_bound(bad_floor):
    """하한이 없거나 쓸 수 없는 값이면 예전 경계를 그대로 쓴다(상한 초과 없음)."""
    prediction = _prediction(floor_price=None)
    if bad_floor is not None:
        prediction["floor_price"] = bad_floor

    amount = _resolve(prediction=prediction)

    assert amount == pytest.approx(BUDGET_ESTIMATE)


# --------------------------------------------------------------------------- #
# V3 게이트 — 믿을 수 있는 하한에만 상한 초과 권한을 준다
# --------------------------------------------------------------------------- #


def test_unpublished_legal_floor_does_not_escape_the_cap():
    """공고가 published 법정하한을 보고하지 않으면 상한을 넘지 않는다.

    미보고 공고의 ``floor_price`` 는 config category floor 에서 온 값이라 법적 구속력이
    없다. 반사실 실측에서 이 경로의 상승 12건이 **12/12 낙찰가에서 멀어졌다**.
    """
    amount = _resolve(prediction=_prediction(legal_floor_bid_rate=None))

    assert amount == pytest.approx(BUDGET_ESTIMATE)
    assert amount < _HIGH_FLOOR_PRICE


@pytest.mark.parametrize(
    "bid_base, escapes",
    [
        (BUDGET_ESTIMATE * 1.00, True),   # 면세
        (BUDGET_ESTIMATE * 1.10, True),   # 정상 과세(부가세 10%)
        (BUDGET_ESTIMATE * 1.15, True),   # 신뢰 경계값 — 포함
        (BUDGET_ESTIMATE * 1.1501, False),  # 경계 바로 위 — 차단
        (BUDGET_ESTIMATE * 1.50, False),
        (BUDGET_ESTIMATE * 10.0, False),  # 오염
    ],
)
def test_base_ratio_gate(bid_base, escapes):
    """기초금액이 추정가격의 1.15 배를 넘으면 그 하한은 신뢰하지 않는다.

    하한 금액은 ``rate × 기초금액`` 이라 base 가 부풀면 하한도 부푼다. 그렇게 만들어진
    임계는 허수였다 — 해당 코호트 78건에서 실낙찰자 78/78 이 그 임계 미만으로 낙찰했다.
    """
    floor_price = 0.95 * bid_base
    amount = _resolve(prediction=_prediction(floor_price=floor_price, bid_base=bid_base))

    if escapes:
        assert amount >= floor_price
    else:
        assert amount == pytest.approx(BUDGET_ESTIMATE)
        assert amount < floor_price


def test_gate_needs_both_conditions():
    """published 하한이 있어도 비율이 크면 차단, 비율이 작아도 미보고면 차단."""
    inflated = BUDGET_ESTIMATE * 1.5
    published_but_inflated = _prediction(floor_price=0.95 * inflated, bid_base=inflated)
    clean_but_unpublished = _prediction(legal_floor_bid_rate=None)

    assert _resolve(prediction=published_but_inflated) == pytest.approx(BUDGET_ESTIMATE)
    assert _resolve(prediction=clean_but_unpublished) == pytest.approx(BUDGET_ESTIMATE)


def test_missing_bid_base_closes_the_gate():
    """예측이 ``bid_base`` 를 싣지 않으면 비율을 검증할 수 없어 상한을 넘지 않는다.

    라이브 경로(``_build_price_prediction``)는 포트 호출 직후 ``bid_base`` 를 싣지만
    (#354), 그 배선이 빠진 dict 로 들어오면 base/추정가격 비를 확인할 방법이 없다.
    확인 불가일 때는 legacy bound 로 남는 **보수적** 선택을 계약으로 고정한다 —
    이 성질을 모른 채 포트를 직접 호출하면 게이트가 조용히 닫히므로 명시해 둔다.
    """
    amount = _resolve(prediction=_prediction(bid_base=None))

    assert amount == pytest.approx(BUDGET_ESTIMATE)
    assert amount < _HIGH_FLOOR_PRICE
