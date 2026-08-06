"""``expected_margin`` 축의 분모를 기초금액으로 정합 — #354(capture 축) 후속.

WHY
---
``_estimate_expected_margin_score`` 는 ``recommended_rate`` 를
``recommended_amount / project.budget_estimate`` (추정가격, ex-VAT)로 만든 뒤,
``recommended_rate - floor_bid_rate`` 를 계산하고 ``predicted_bid_rate`` 와의 차를
봅니다. 그런데 그 두 rate 는 predictor 가 기초금액(``bid_base``)을 base 로 받아
산출한 값이라 기초금액-relative 입니다. 과세 공고에서 두 금액은 ~10% 다르므로
서로 다른 자로 잰 수치를 빼는 셈이 되고, 결과가 두 방향으로 동시에 틀립니다:

- ``floor_headroom`` **과대** → 낙찰하한까지 여유가 있는 것처럼 보임.
- ``prediction_alignment`` **과소** → predictor 자신의 추천율과 정확히 일치하는
  추천가를 "예측과 어긋난다"고 감점.

왜 중요한가 — 이 저장소의 실투찰 3건은 **실격 1건 + 패찰 2건**이고, 두 방향 모두
basis 혼동 계열입니다. 실격 건(2026-08-03 R26BK01654006)은 하한에 밀착해 사정률
추첨에서 탈락했고, 패찰 2건은 반대로 낙찰가보다 **높게** 추천된 과추천 방향이었습니다.
그래서 이 축의 목표는 "하한을 피하라"가 아니라 **두 rate 를 같은 자로 재는 것**입니다.

값표는 :mod:`tests.support.basis_fixtures` 가 단일 출처이며 #354 테스트와 공유합니다.
"""

from __future__ import annotations

import pytest

from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.opportunity_analysis import OpportunityAnalysisService
from app.services.opportunity_analysis.score_tables import (
    _EXPECTED_MARGIN_COMPOSITE_WEIGHTS,
)
from app.services.opportunity_analysis.scoring import _resolve_margin_bid_base
from tests.support.basis_fixtures import (
    BASE_AMOUNT,
    BUDGET_ESTIMATE,
    RATE_ON_BASE,
    RATE_ON_ESTIMATE,
    RECOMMENDED_AMOUNT,
    build_vat_notice,
    persist_vat_notice,
)

_PREDICTED_RATE = 0.90
_CONFIDENCE = 0.7
_CAPACITY = 0.5
_ALIGNMENT_SCALE = 0.12  # prediction_alignment 의 정규화 폭 (프로덕션 상수)

# 두 시나리오를 모두 돌린다. floor 0.88 은 ``1 - floor == _ALIGNMENT_SCALE`` 라
# headroom 증가분과 alignment 감소분이 **정확히 상쇄**되는 퇴화 케이스여서, 그것만
# 보면 "합성값이 거의 안 변한다"는 잘못된 인상을 준다. floor 0.80 을 함께 고정해
# 상쇄가 우연임을 드러낸다(그 경우 정합 후 점수가 오히려 **올라간다**).
_FLOOR_TIGHT = 0.88
_FLOOR_LOOSE = 0.80


def _prediction(*, bid_base: float | None = BASE_AMOUNT, floor_rate: float = _FLOOR_TIGHT) -> dict:
    payload: dict = {
        "floor_bid_rate": floor_rate,
        "predicted_bid_rate": _PREDICTED_RATE,
        "confidence_score": _CONFIDENCE,
    }
    if bid_base is not None:
        payload["bid_base"] = bid_base
    return payload


def _margin(*, project, prediction: dict) -> float:
    """The production function under test."""
    return OpportunityAnalysisService()._estimate_expected_margin_score(
        project=project,
        recommended_amount=RECOMMENDED_AMOUNT,
        price_prediction=prediction,
        competitiveness_score=0.5,
        capacity_score=_CAPACITY,
    )


def _expected_composite(*, rate: float, floor_rate: float) -> float:
    """Independently declared expectation — never calls the function under test.

    가중치는 프로덕션 선언표를 import 해 쓰고(리터럴 중복 금지), 구성요소 산식만
    테스트가 독립적으로 적는다.
    """
    headroom = max(0.0, min(1.0, (rate - floor_rate) / (1.0 - floor_rate)))
    alignment = max(0.0, 1.0 - min(abs(rate - _PREDICTED_RATE) / _ALIGNMENT_SCALE, 1.0))
    w = _EXPECTED_MARGIN_COMPOSITE_WEIGHTS
    return round(
        rate * w["recommended_rate"]
        + headroom * w["floor_headroom"]
        + alignment * w["prediction_alignment"]
        + _CONFIDENCE * w["price_confidence"]
        + _CAPACITY * w["normalized_capacity"],
        2,
    )


# --------------------------------------------------------------------------- #
# 분모 해석 커널 — 값 표로 직접 구동
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reported_base, budget_estimate, expected",
    [
        (BASE_AMOUNT, BUDGET_ESTIMATE, BASE_AMOUNT),   # 보고된 base 우선
        (None, BUDGET_ESTIMATE, BUDGET_ESTIMATE),      # 미보고 → 추정가격 폴백
        (0.0, BUDGET_ESTIMATE, BUDGET_ESTIMATE),       # 0 은 미확보와 동일 취급
        (-1.0, BUDGET_ESTIMATE, BUDGET_ESTIMATE),      # 음수도 마찬가지
        (None, None, 0.0),                             # 어디에도 없으면 0 (중립 신호)
        (None, 0.0, 0.0),
        ("bad", BUDGET_ESTIMATE, BUDGET_ESTIMATE),     # 비수치는 건너뛴다
        ("", BUDGET_ESTIMATE, BUDGET_ESTIMATE),        # 빈 문자열도
        ("110000000", BUDGET_ESTIMATE, BASE_AMOUNT),   # 수치 문자열은 받는다
        # 세 번째 동작 변경: 추정가격이 없어도 base 만 있으면 이제 실점수가 나온다
        # (예전에는 무조건 중립 0.5). 라이브 0건이지만 계약으로 고정한다.
        (BASE_AMOUNT, 0.0, BASE_AMOUNT),
        (BASE_AMOUNT, None, BASE_AMOUNT),
    ],
)
def test_resolve_margin_bid_base_value_table(reported_base, budget_estimate, expected):
    assert _resolve_margin_bid_base(reported_base, budget_estimate) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# 값표 — 과세 공고에서 분모가 기초금액이어야 한다 (전부 프로덕션 함수 구동)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("floor_rate", [_FLOOR_TIGHT, _FLOOR_LOOSE])
def test_margin_uses_the_bid_base_not_the_budget_estimate(floor_rate):
    """과세 공고: 보고된 기초금액이 분모여야 하고, 추정가격 분모 값이면 안 된다."""
    score = _margin(project=build_vat_notice(), prediction=_prediction(floor_rate=floor_rate))

    assert score == pytest.approx(_expected_composite(rate=RATE_ON_BASE, floor_rate=floor_rate))
    assert score != pytest.approx(
        _expected_composite(rate=RATE_ON_ESTIMATE, floor_rate=floor_rate)
    )


def test_floor_headroom_no_longer_overstates_safety():
    """하한 여유 과대 표시가 사라졌다 — 실제 함수를 구동해 확인한다.

    합성 점수에서 headroom 축만 떼어낼 수 없으므로, headroom 가중치만 다른 두 실행의
    **차이**로 그 축의 기여를 분리한다: floor 를 tight(0.88)/loose(0.80)로 바꾸면
    headroom 만 달라지고 나머지 네 축은 그대로다. 정합 전이라면 두 실행 모두
    추정가격 rate(0.99) 위에서 계산돼 차이가 달라진다.
    """
    project = build_vat_notice()
    tight = _margin(project=project, prediction=_prediction(floor_rate=_FLOOR_TIGHT))
    loose = _margin(project=project, prediction=_prediction(floor_rate=_FLOOR_LOOSE))

    aligned_gap = _expected_composite(
        rate=RATE_ON_BASE, floor_rate=_FLOOR_LOOSE
    ) - _expected_composite(rate=RATE_ON_BASE, floor_rate=_FLOOR_TIGHT)
    misaligned_gap = _expected_composite(
        rate=RATE_ON_ESTIMATE, floor_rate=_FLOOR_LOOSE
    ) - _expected_composite(rate=RATE_ON_ESTIMATE, floor_rate=_FLOOR_TIGHT)

    assert loose - tight == pytest.approx(aligned_gap, abs=1e-9)
    assert loose - tight != pytest.approx(misaligned_gap, abs=1e-9)

    # 그리고 정합된 headroom 은 하한 밀착을 실제로 밀착으로 보고한다:
    # 0.90 은 0.88 바로 위이므로 여유가 거의 없다.
    headroom = (RATE_ON_BASE - _FLOOR_TIGHT) / (1.0 - _FLOOR_TIGHT)
    misreported = (RATE_ON_ESTIMATE - _FLOOR_TIGHT) / (1.0 - _FLOOR_TIGHT)
    assert headroom == pytest.approx(0.1667, abs=1e-4)
    assert misreported == pytest.approx(0.9167, abs=1e-4)


def test_alignment_no_longer_penalises_the_predictors_own_rate():
    """추천가가 predictor 추천율과 정확히 일치하면 alignment 는 만점이어야 한다."""
    # 정합된 rate 는 predicted_bid_rate 와 같다 → 감점 0.
    assert RATE_ON_BASE == pytest.approx(_PREDICTED_RATE)

    score = _margin(project=build_vat_notice(), prediction=_prediction())
    perfect_alignment = _expected_composite(rate=RATE_ON_BASE, floor_rate=_FLOOR_TIGHT)

    assert score == pytest.approx(perfect_alignment)


# --------------------------------------------------------------------------- #
# 폴백 — 면세/기초금액 미확보 공고에서 동작 불변
# --------------------------------------------------------------------------- #


def test_tax_free_notice_is_a_no_op():
    """면세 공고(두 금액 동일)는 분모가 같아 결과가 바뀌지 않는다."""
    project = build_vat_notice(budget_estimate=BASE_AMOUNT)

    with_base = _margin(project=project, prediction=_prediction(bid_base=BASE_AMOUNT))
    without_base = _margin(project=project, prediction=_prediction(bid_base=None))

    assert with_base == pytest.approx(without_base)


@pytest.mark.parametrize("bid_base", [None, 0.0, -1.0])
def test_falls_back_to_budget_estimate_when_base_unavailable(bid_base):
    """base 를 못 받으면 추정가격으로 폴백 — resolve_notice_bid_base 가 기초금액
    미확보 시 돌려주는 값과 같으므로 basis 뒤바뀜이 아니라 같은 해석의 연장이다."""
    score = _margin(project=build_vat_notice(), prediction=_prediction(bid_base=bid_base))

    assert score == pytest.approx(
        _expected_composite(rate=RATE_ON_ESTIMATE, floor_rate=_FLOOR_TIGHT)
    )


def test_zero_budget_and_no_base_returns_neutral():
    """분모를 어디서도 못 구하면 중립값(기존 동작 보존)."""
    score = _margin(
        project=build_vat_notice(budget_estimate=0.0), prediction=_prediction(bid_base=None)
    )

    assert score == pytest.approx(0.5)


def test_base_without_budget_estimate_now_scores(test_db):
    """추정가격이 없어도 base 가 있으면 실점수가 나온다 — 세 번째 동작 변경.

    예전에는 ``project.budget_estimate <= 0`` 하나로 중립 0.5 를 반환해, 기초금액을
    알면서도 신호를 버렸다. 라이브 해당 0건이지만 계약으로 고정한다.
    """
    score = _margin(
        project=build_vat_notice(budget_estimate=0.0), prediction=_prediction(bid_base=BASE_AMOUNT)
    )

    assert score == pytest.approx(_expected_composite(rate=RATE_ON_BASE, floor_rate=_FLOOR_TIGHT))
    assert score != pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 배선 — 라이브 분석이 실제로 base 를 분모로 흘려보내는지
# --------------------------------------------------------------------------- #


def test_analysis_path_scores_margin_on_the_collected_base(test_db):
    """라이브 분석(orchestration → scoring)이 수집된 기초금액 위에서 margin 을 매긴다.

    #354 리뷰(U7)가 지적한 커버리지 갭을 닫는다: 예측 dict 가 base 를 싣는 것만으로는
    부족하고 스코어링이 그 값을 실제로 분모로 써야 한다. 기대값은 이 테스트가
    ``BASE_AMOUNT`` 에서 **독립적으로** 조립하며, 시험 대상 함수를 호출하지 않는다.
    """
    project = persist_vat_notice(test_db)
    service = OpportunityAnalysisService()

    analysis = service.analyze_project(
        test_db, project, OpportunityAnalysisRequest(project_id=project.id)
    )
    prediction = analysis["price_prediction"]

    # 전제: 예측이 수집된 기초금액을 싣는다 (#354).
    assert prediction["bid_base"] == pytest.approx(BASE_AMOUNT)

    # 본론: 분석이 낸 점수가 "BASE_AMOUNT 로 나눈 rate" 로 조립한 값과 일치한다.
    rate = max(0.0, min(1.0, float(analysis["recommended_amount"]) / BASE_AMOUNT))
    floor_rate = max(0.0, min(1.0, float(prediction.get("floor_bid_rate") or 0.0)))
    predicted_rate = max(0.0, min(1.0, float(prediction.get("predicted_bid_rate") or rate)))
    confidence = max(0.0, min(1.0, float(prediction.get("confidence_score") or 0.0)))
    # capacity 는 이 축과 무관한 별개 헬퍼라 그대로 빌려 쓴다(시험 대상 아님).
    capacity = service._normalize_capacity_score(0.0)

    headroom = (
        max(0.0, min(1.0, (rate - floor_rate) / max(1e-6, 1.0 - floor_rate)))
        if floor_rate > 0
        else rate
    )
    alignment = max(0.0, 1.0 - min(abs(rate - predicted_rate) / _ALIGNMENT_SCALE, 1.0))
    w = _EXPECTED_MARGIN_COMPOSITE_WEIGHTS
    expected = round(
        rate * w["recommended_rate"]
        + headroom * w["floor_headroom"]
        + alignment * w["prediction_alignment"]
        + confidence * w["price_confidence"]
        + capacity * w["normalized_capacity"],
        2,
    )

    assert analysis["decision"]["expected_margin_score"] == pytest.approx(expected)
