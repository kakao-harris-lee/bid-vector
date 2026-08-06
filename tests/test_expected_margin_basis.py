"""``expected_margin`` 축의 분모를 기초금액으로 정합 — #354(capture 축) 후속.

WHY
---
``_estimate_expected_margin_score`` 는 ``recommended_rate`` 를
``recommended_amount / project.budget_estimate`` (추정가격, ex-VAT)로 만든 뒤,
그 값을 ``floor_bid_rate`` · ``predicted_bid_rate`` 와 **직접 뺍니다**. 그런데 그
두 rate 는 predictor 가 기초금액(``bid_base``)을 base 로 받아 산출한 값이라
기초금액-relative 입니다. 과세 공고에서 두 금액은 ~10% 다르므로 서로 다른 자로
잰 수치를 빼는 셈이 되고, 결과가 두 방향으로 동시에 틀립니다:

- ``floor_headroom`` 이 크게 **과대** 표시 → 낙찰하한까지 여유가 있는 것처럼 보임.
  이 저장소의 실투찰 실격 3건이 전부 하한 밀착 축이라 방향이 나쁩니다.
- ``prediction_alignment`` 이 **과소** 표시 → predictor 자신의 추천율과 정확히
  일치하는 추천가를 "예측과 어긋난다"고 감점.

수정은 분모를 predictor 가 실제로 곱한 base(``price_prediction["bid_base"]``,
#354 가 응답 계약으로 실어둔 값)로 바꾸는 것입니다. 아래 값표가 그 전후를 고정합니다.
"""

from __future__ import annotations

import pytest

from app.models.models import HistoricalData, Project
from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.opportunity_analysis import OpportunityAnalysisService
from app.services.opportunity_analysis.scoring import _resolve_margin_bid_base

# 과세 공고 값표: 기초금액 = 추정가격 × 1.1.
_BUDGET_ESTIMATE = 100_000_000.0
_BASE_AMOUNT = 110_000_000.0
_RECOMMENDED_AMOUNT = 99_000_000.0  # 0.90 × 기초금액 = 0.99 × 추정가격
_FLOOR_RATE = 0.88
_PREDICTED_RATE = 0.90
_CONFIDENCE = 0.7
_CAPACITY = 0.5

# 정합된 분모(기초금액)에서 나와야 하는 값들 — 실측으로 고정.
_ALIGNED_RATE = 0.90
_ALIGNED_HEADROOM = pytest.approx(0.1667, abs=1e-4)
_ALIGNED_ALIGNMENT = 1.0
_ALIGNED_MARGIN = 0.70
# 추정가격 분모(버그)에서 나오던 값 — 회귀하면 이 값으로 되돌아간다.
_MISALIGNED_MARGIN = 0.73


def _prediction(*, bid_base: float | None = _BASE_AMOUNT) -> dict:
    payload = {
        "floor_bid_rate": _FLOOR_RATE,
        "predicted_bid_rate": _PREDICTED_RATE,
        "confidence_score": _CONFIDENCE,
    }
    if bid_base is not None:
        payload["bid_base"] = bid_base
    return payload


def _project(budget_estimate: float = _BUDGET_ESTIMATE) -> Project:
    return Project(
        title="expected_margin basis 검증",
        description="",
        requirements="",
        budget_estimate=budget_estimate,
        category="construction",
    )


def _margin(*, project: Project, prediction: dict) -> float:
    return OpportunityAnalysisService()._estimate_expected_margin_score(
        project=project,
        recommended_amount=_RECOMMENDED_AMOUNT,
        price_prediction=prediction,
        competitiveness_score=0.5,
        capacity_score=_CAPACITY,
    )


# --------------------------------------------------------------------------- #
# 분모 해석 커널 — 값 표로 직접 구동 (dict 없이)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reported_base, budget_estimate, expected",
    [
        (_BASE_AMOUNT, _BUDGET_ESTIMATE, _BASE_AMOUNT),   # 보고된 base 우선
        (None, _BUDGET_ESTIMATE, _BUDGET_ESTIMATE),       # 미보고 → 추정가격 폴백
        (0.0, _BUDGET_ESTIMATE, _BUDGET_ESTIMATE),        # 0 은 미확보와 동일 취급
        (-1.0, _BUDGET_ESTIMATE, _BUDGET_ESTIMATE),       # 음수도 마찬가지
        (None, None, 0.0),                                # 어디에도 없으면 0 (중립 신호)
        (None, 0.0, 0.0),
        ("bad", _BUDGET_ESTIMATE, _BUDGET_ESTIMATE),      # 비수치는 건너뛴다
    ],
)
def test_resolve_margin_bid_base_value_table(reported_base, budget_estimate, expected):
    assert _resolve_margin_bid_base(reported_base, budget_estimate) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# 값표 — 과세 공고에서 분모가 기초금액이어야 한다
# --------------------------------------------------------------------------- #


def test_margin_uses_the_bid_base_not_the_budget_estimate():
    """과세 공고: 분모가 기초금액이면 합성 점수가 0.70 (추정가격 분모의 0.73 아님)."""
    score = _margin(project=_project(), prediction=_prediction())

    assert score == pytest.approx(_ALIGNED_MARGIN, abs=1e-9)
    assert score != pytest.approx(_MISALIGNED_MARGIN, abs=1e-9)


def test_component_value_table():
    """구성요소 단위 고정 — 합성값의 부분 상쇄에 가려지지 않게 각각을 못박는다.

    두 오차가 반대 방향(headroom 과대 / alignment 과소)이라 합성 점수만 보면
    -0.03 로 작아 보이지만, 운영자가 읽는 신호 자체는 각각 크게 틀려 있었다.
    """
    rate = _RECOMMENDED_AMOUNT / _BASE_AMOUNT
    headroom = (rate - _FLOOR_RATE) / (1.0 - _FLOOR_RATE)
    alignment = 1.0 - min(abs(rate - _PREDICTED_RATE) / 0.12, 1.0)

    assert rate == pytest.approx(_ALIGNED_RATE)
    assert headroom == _ALIGNED_HEADROOM
    assert alignment == pytest.approx(_ALIGNED_ALIGNMENT)

    # 그리고 그 구성요소들이 실제 함수의 합성값을 만든다.
    expected = round(
        rate * 0.35 + headroom * 0.2 + alignment * 0.2 + _CONFIDENCE * 0.15 + 0.5 * 0.1,
        2,
    )
    assert _margin(project=_project(), prediction=_prediction()) == pytest.approx(expected)


def test_floor_headroom_no_longer_overstates_safety():
    """하한 여유가 과대 표시되지 않는다 — 실격 리스크를 낙관적으로 보이게 하던 축."""
    aligned_rate = _RECOMMENDED_AMOUNT / _BASE_AMOUNT
    misaligned_rate = _RECOMMENDED_AMOUNT / _BUDGET_ESTIMATE

    aligned_headroom = (aligned_rate - _FLOOR_RATE) / (1.0 - _FLOOR_RATE)
    misaligned_headroom = (misaligned_rate - _FLOOR_RATE) / (1.0 - _FLOOR_RATE)

    assert aligned_headroom == _ALIGNED_HEADROOM
    assert misaligned_headroom == pytest.approx(0.9167, abs=1e-4)
    # 버그는 여유를 5배 이상 부풀렸다.
    assert misaligned_headroom > aligned_headroom * 5


# --------------------------------------------------------------------------- #
# 폴백 — 면세/기초금액 미확보 공고에서 동작 불변
# --------------------------------------------------------------------------- #


def test_tax_free_notice_is_a_no_op():
    """면세 공고(두 금액 동일)는 분모가 같아 결과가 바뀌지 않는다."""
    project = _project(budget_estimate=_BASE_AMOUNT)

    with_base = _margin(project=project, prediction=_prediction(bid_base=_BASE_AMOUNT))
    without_base = _margin(project=project, prediction=_prediction(bid_base=None))

    assert with_base == pytest.approx(without_base)


@pytest.mark.parametrize("bid_base", [None, 0.0, -1.0])
def test_falls_back_to_budget_estimate_when_base_unavailable(bid_base):
    """base 를 못 받으면 추정가격으로 폴백 — resolve_notice_bid_base 가 기초금액
    미확보 시 돌려주는 값과 같으므로 basis 뒤바뀜이 아니라 같은 해석의 연장이다."""
    score = _margin(project=_project(), prediction=_prediction(bid_base=bid_base))

    assert score == pytest.approx(_MISALIGNED_MARGIN, abs=1e-9)


def test_zero_budget_and_no_base_returns_neutral():
    """분모를 어디서도 못 구하면 중립값(기존 동작 보존)."""
    score = _margin(project=_project(budget_estimate=0.0), prediction=_prediction(bid_base=None))

    assert score == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 배선 — analyze_project 가 실제로 base 를 흘려보내는지 (4번째 생성 지점 커버리지)
# --------------------------------------------------------------------------- #


def test_analysis_path_scores_margin_on_the_collected_base(test_db):
    """라이브 분석이 수집된 기초금액 위에서 margin 을 매긴다.

    #354 리뷰(U7)가 지적한 orchestration 지점 커버리지 갭을 함께 닫는다: 예측 dict 가
    base 를 싣는 것만으로는 부족하고, 스코어링이 그 값을 실제로 분모로 써야 한다.
    """
    project = _project()
    test_db.add(project)
    test_db.flush()
    test_db.add(
        HistoricalData(
            project_id=project.id, base_amount=_BASE_AMOUNT, bid_rate=0.0, category="construction"
        )
    )
    test_db.commit()
    test_db.refresh(project)

    analysis = OpportunityAnalysisService().analyze_project(
        test_db, project, OpportunityAnalysisRequest(project_id=project.id)
    )

    # 전제: 예측이 수집된 기초금액을 싣는다 (#354).
    assert analysis["price_prediction"]["bid_base"] == pytest.approx(_BASE_AMOUNT)

    # 본론: 그 base 를 분모로 쓴 값과 일치한다. 분모를 강제로 명시한 스텁이라
    # 순환 검증이 아니며, 수정 전에는 추정가격으로 나눠 값이 달랐다.
    expected = OpportunityAnalysisService()._estimate_expected_margin_score(
        project=_project(budget_estimate=_BASE_AMOUNT),
        recommended_amount=float(analysis["recommended_amount"]),
        price_prediction=analysis["price_prediction"],
        competitiveness_score=float(analysis["market_insights"]["competitiveness_score"]),
        capacity_score=0.0,
    )
    assert analysis["decision"]["expected_margin_score"] == pytest.approx(expected)
