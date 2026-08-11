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
    """저장 낙찰율/실현 사정률 비 — 예정가 분포를 투찰율 축으로 환산하는 계수.

    basis 혼재 주의(summary.py 에서 상속한 선재 이슈): ``HistoricalData.bid_rate``
    는 두 basis 로 저장돼 있다 — 기초금액 확보 행은 win/기초금액, 미확보 행은
    KONEPS ``sucsfbidRate`` = win/예정가 (services/koneps/scsbid.py). 코어 실측
    (2026-08-11, n=6,009): base-relative 48% / 예정가-relative 52%. 이 비는
    base-relative 행에서만 "bid/예정가"로 차원이 정확하고, 예정가-relative 행에서는
    win·base/예정가² 이 된다. 소비는 **중앙값**이라 혼재 영향은 실측 +0.021%p 로
    미미하지만, 라벨 정합(재캘리브레이션 트랙) 전까지 이 계수는 근사다. 이 변환
    축은 분포 캘리브레이션(PIT)으로는 검증되지 않고 점추정 오차 축으로만 커버된다
    — 한계는 캘리브레이션 리포트·PR 본문에 공시한다.
    """
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
