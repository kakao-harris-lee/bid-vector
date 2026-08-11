"""분포 predictor 의 record-free 추출 산술 — typed 순수 헬퍼(§4.7).

ORM 행/dict 를 읽는 약한 경계(``read_record_value``)는 predictor 의 추출 루프
한 곳에만 남기고, 실제 판정 산술은 여기의 **typed 원시값 함수**로 내린다.
캘리브레이션 스크립트(``scripts/backtest_yega_distribution.py``)도 같은 실현
사정률 정의를 써야 하므로(§4.5-8 중복 금지), 정의는 이 모듈이 단일 출처다.
"""

from __future__ import annotations

from statistics import fmean, pvariance

from app.domain.assessment_shrinkage import LevelObservation

# 사정률(예정가/기초금액) 개연 밴드 — historical summary 의 estimated_price_rate
# 밴드와 동일 축. 밖이면 오적재(스케일·오염)로 보고 관측에서 제외한다.
ASSESSMENT_PLAUSIBLE_MIN = 0.8
ASSESSMENT_PLAUSIBLE_MAX = 1.2
# 낙찰율/실현 사정률 비(투찰율 환산 계수)의 개연 밴드 — reserve prior 와 동일 축.
BID_RATIO_PLAUSIBLE_MIN = 0.5
BID_RATIO_PLAUSIBLE_MAX = 1.5


def realized_assessment_ratio(
    *,
    reserve_prices: list[float],
    picked_numbers: list[int],
    base_amount: float,
) -> float | None:
    """실현 사정률 = 추첨된 예비가 평균 / 기초금액. 추첨번호 미보고 행은 ``None``.

    ``picked_numbers`` 는 1-기반 추첨번호다. 최소 2개는 있어야 평균을 실현값으로
    신뢰한다(historical summary 와 같은 규칙).
    """
    if base_amount <= 0:
        return None
    picked_prices = [
        reserve_prices[number - 1]
        for number in picked_numbers
        if 1 <= number <= len(reserve_prices)
    ]
    if len(picked_prices) < 2:
        return None
    realized = fmean(picked_prices) / base_amount
    if not ASSESSMENT_PLAUSIBLE_MIN <= realized <= ASSESSMENT_PLAUSIBLE_MAX:
        return None
    return realized


def bid_to_assessment_ratio(
    bid_rate: float | None, realized_assessment: float | None
) -> float | None:
    """낙찰율/실현 사정률 비 — 예정가 분포를 투찰율 축으로 환산하는 계수."""
    if bid_rate is None or realized_assessment is None:
        return None
    ratio = bid_rate / realized_assessment
    if not BID_RATIO_PLAUSIBLE_MIN <= ratio <= BID_RATIO_PLAUSIBLE_MAX:
        return None
    return ratio


def aggregate_level_observation(centers: list[float]) -> LevelObservation | None:
    """공고별 중심들을 한 계층의 (count, mean, 모분산) 관측으로 집계한다."""
    if not centers:
        return None
    return LevelObservation(
        sample_count=len(centers),
        mean=fmean(centers),
        variance=pvariance(centers) if len(centers) >= 2 else 0.0,
    )
