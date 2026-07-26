"""공사 시나리오(공격/추천/안전) 법정 하한 앵커 — 선언 offset 해석기.

era-correct 공사 법정 낙찰하한 tier(#197 ``resolve_construction_qualification_floor``)가
해석되는 공고에서, 3종 투찰 시나리오를 "guardrail 이 최종 해석한 floor + 선언 offset"으로
앵커한다. offset 은 ``settings.PREDICTION_CONSTRUCTION_SCENARIO_FLOOR_OFFSETS`` 에 선언된
백분위수 캘리브레이션 데이터이며, 이 모듈은 그 데이터를 **해석만** 한다(§4.5.3 / §4.7.4).
predict_price 의 후보 앵커와 공고별 투찰가 메뉴(``bid_target``)가 이 **단일 출처**를
공유해 두 표면이 항상 일치한다(병렬 경로 아님).

전제(두 표면 일치 조건): 후보 경로는 [safe_floor(=floor+안전마진), ceiling], 메뉴 경로는
[floor, ceiling]로 clamp 된다. 따라서 일치는 **aggressive offset ≥
PREDICTION_FLOOR_SAFETY_MARGIN_RATE(기본 0.001)** 전제에서만 성립한다. 운영자가
aggressive offset 을 안전마진 아래로 튜닝하면 후보는 safe_floor 로 들리고 메뉴는
floor+offset 이 되어 두 표면이 갈라진다 — offset 튜닝 시 이 전제를 지킬 것.

basis 일관성(중요)
------------------
측정 offset 은 (win÷기초금액 − **무변환** tier floor)이므로, 앵커도 무변환 floor 위에
더해야 자기일관적이다. 호출부는 guardrail 이 최종 해석한 floor 를 넘긴다: 기본(E[사정률]
1.0)에서는 그 값이 무변환 tier floor 와 같아 자기일관적이다. 단 agency assessment
(E[사정률]≠1)가 설정된 발주처는 guardrail floor 자체가 변환되므로, 그 위에 무변환-basis
offset 을 더하면 **이중 보정** 우려가 있다 — 다만 그런 발주처는 대개 agency 밴드가 있어
상위 gate 가 앵커 대신 밴드 경로를 타므로 실무상 드물다. 개찰 누적 재캘리브레이션 시
재점검 대상이다.

red line(불변)
--------------
- guardrail floor/ceiling 자체는 이 모듈에서 절대 바꾸지 않는다. 앵커 결과는 반드시
  기존 guardrail band 안(floor ≤ 값 ≤ ceiling)으로 clamp 된다.
- 낙찰 확률 표현 금지(정직 명세 §2). 이 모듈은 rate 만 계산하고, 문구/경고는 호출부가
  붙인다. 공격의 낙하 위험은 상위 표면에서 유지·강화한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

from app.ai.predictors.historical import clamp_bid_rate, normalize_category_key
from app.ai.predictors.legal_floor_spec import (
    resolve_construction_qualification_floor,
)

# 시나리오 stance(투찰 전략) → offset 키. 앵커 결과는 항상 공격 < 추천 < 안전(offset
# 오름차순)이며, 공격이 floor 에 가장 가깝다(최저적격 경쟁·낙하 위험).
SCENARIO_STANCES: tuple[str, ...] = ("aggressive", "recommended", "safe")


CONSTRUCTION_CATEGORY_KEY = "construction"


def is_construction_category(category: str | None) -> bool:
    """정규화 카테고리가 공사인가 — 공사 전용 하한 게이트의 공통 전제.

    era-tier(#197)와 산림사업 하한이 둘 다 **공사 적격심사** 하한이라 같은 전제를 쓴다.
    호출부가 정규화를 각자 재구현하면 표기 변형에서 갈리므로 이 술어로 단일 출처화한다.
    """
    return normalize_category_key(category) == CONSTRUCTION_CATEGORY_KEY


def is_construction_era_floor_resolved(
    category: str | None,
    estimation_amount: float | None,
    reference_date: date | datetime | None,
) -> bool:
    """category=construction 이고 era-correct tier floor(#197)가 해석되면 True.

    tier 미해석(추정가격/기준일 불명, 100억+ 종심제, 비공사)이면 False — 호출부는
    기존 동작을 그대로 유지한다(앵커 미적용, 최소 놀람).
    """
    if not is_construction_category(category):
        return False
    return (
        resolve_construction_qualification_floor(estimation_amount, reference_date)
        is not None
    )


def resolve_scenario_anchor_rates(
    *,
    floor_bid_rate: float | None,
    ceiling_bid_rate: float | None,
    offsets: Mapping[str, float] | None,
) -> dict[str, float] | None:
    """``{stance: clamp(floor + offset, floor, ceiling)}`` 를 반환. floor/offsets 없으면 None.

    Args:
        floor_bid_rate: guardrail 이 최종 해석한 하한(앵커 기준·모듈 docstring 참조).
        ceiling_bid_rate: guardrail 상한(공사 0.93). 앵커 상한 clamp 용 — 불변.
        offsets: stance→offset 선언 데이터(``PREDICTION_CONSTRUCTION_SCENARIO_FLOOR_OFFSETS``).

    red line: offset 은 양수이므로 결과는 구조상 floor 위이고, ceiling clamp 로 상한을
    넘지 않는다(floor ≤ 값 ≤ ceiling). floor/ceiling 자체는 바꾸지 않는다.
    """
    if floor_bid_rate is None or not offsets:
        return None
    floor = float(floor_bid_rate)
    anchored: dict[str, float] = {}
    for stance in SCENARIO_STANCES:
        offset = float(offsets.get(stance, 0.0) or 0.0)
        rate = clamp_bid_rate(floor + offset)
        if ceiling_bid_rate is not None:
            rate = min(rate, float(ceiling_bid_rate))
        rate = max(rate, floor)
        # round(6): kill float noise for display while staying below the candidate
        # path's own round(4) precision (no golden shift).
        anchored[stance] = round(rate, 6)
    return anchored
