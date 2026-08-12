"""분포 predictor 의 record-free 추출 산술 — typed 순수 헬퍼(§4.7).

ORM 행/dict 를 읽는 약한 경계(``read_record_value``)는 predictor 의 추출 루프
한 곳에만 남기고, 실제 판정 산술은 여기의 **typed 원시값 함수**로 내린다.
실현 사정률 정의(추첨분 선택·평균/base·개연 밴드 술어)는 이 모듈이 **단일
출처**다(§4.5-8). importer 는 세 곳이다: ``app/ai/predictors/historical/summary.py``
(realized_assessment_ratio — historical 핫패스, 리뷰 L2 통합),
``app/ai/predictors/distribution.py`` (observe_reserve_draw — 서빙 엔진 관측 관문),
``scripts/_yega_coverage.py`` (observe_reserve_draw — 캘리 커버리지 축, M 라운드
분해로 backtest_yega_distribution 에서 이동).
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, pvariance

from app.core.constants import (
    ASSESSMENT_RATE_PLAUSIBLE_MAX,
    ASSESSMENT_RATE_PLAUSIBLE_MIN,
)
from app.domain.assessment_shrinkage import LevelObservation
from app.domain.reserve_draw_distribution import (
    DEFAULT_DRAW_COUNT,
    EXPECTED_RESERVE_PRICE_COUNT,
    draw_mean_moments,
)
# 낙찰율/실현 사정률 비(투찰율 환산 계수)의 개연 밴드. 수치(0.5~1.5)는
# statistics.resolve_record_bid_rate 의 사용가능 밴드와 같지만 이 비의 **축은
# 행마다 다르다**: 분자 bid_rate 가 basis 혼재(base-relative/예정가-relative
# 48/52 — bid_to_assessment_ratio docstring 참조)이고, resolve 의 bid_rate<=0
# 폴백은 세 번째 provenance 다 — HistoricalData.predicted_price 의 write 경로는
# app/services/koneps/persistence.py:728-730 한 곳뿐이며 **공고 추정가격**, 없으면
# **기초금액 그 자체**를 적는다(모델 추천가는 PricePrediction.predicted_price,
# 다른 테이블). 즉 그 폴백 비는 추정가격/기초금액이고 추정가격 부재면 정확히 1.0
# — 투찰 정보가 0 인 예산 축 자기참조 값이 이 밴드를 그대로 통과한다. 단일 축
# 단정은 하지 않는다(리뷰 N3 — L4-8·M4-1 의 '추천가' 라벨은 축을 반대로 가렸다).
BID_RATIO_PLAUSIBLE_MIN = 0.5
BID_RATIO_PLAUSIBLE_MAX = 1.5


def is_plausible_assessment_rate(value: float) -> bool:
    """사정률 관측값이 개연 밴드(0.8~1.2, **경계 포함**) 안인가 — 판정 술어 한 벌.

    상수(app/core/constants.ASSESSMENT_RATE_PLAUSIBLE_*)만 나누고 비교식을 두 벌로
    두면 경계 포함성이 한쪽만 바뀌는 사고가 난다(§4.5-8 — BID_BASE_TRUST_RATIO_MAX
    ↔ exceeds_base_trust_ratio 선례와 같은 배치: 상수는 constants.py, 술어는 유일한
    소비 지점들이 있는 이 모듈. 리뷰 N1). realized 판정과 center 관문이 이 술어만
    쓴다 — 경계 포함성은 술어 단위 값표 테스트가 고정한다.
    """
    return ASSESSMENT_RATE_PLAUSIBLE_MIN <= value <= ASSESSMENT_RATE_PLAUSIBLE_MAX


def realized_assessment_ratio(
    *,
    reserve_prices: list[float],
    picked_numbers: list[int],
    base_amount: float,
) -> float | None:
    """실현 사정률 = 추첨된 예비가 평균 / 기초금액. 추첨번호 미보고 행은 ``None``.

    ``picked_numbers`` 는 1-기반 추첨번호다. 최소 2개는 있어야 평균을 실현값으로
    신뢰한다. 이 규칙의 **단일 출처**이며 historical summary 가 이 함수를 호출한다
    — 평균은 ``fmean`` 이다(구 인라인 ``np.mean`` 대비 코어 실측 26/6,010행에서
    마지막 1ulp 차이가 있으나, 모든 소비 경로가 4dp 반올림 집계라 특성화 골든
    byte-diff 0 으로 동치가 증명됐다. 리뷰 L2).
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
    if not is_plausible_assessment_rate(realized):
        return None
    return realized


def bid_to_assessment_ratio(
    bid_rate: float | None, realized_assessment: float | None
) -> float | None:
    """저장 낙찰율/실현 사정률 비 — 예정가 분포를 투찰율 축으로 환산하는 계수.

    basis 혼재 주의(summary.py 에서 상속한 선재 이슈): ``HistoricalData.bid_rate``
    는 두 basis 로 저장돼 있다 — 기초금액 확보 행은 win/기초금액, 미확보 행은
    KONEPS ``sucsfbidRate`` = win/예정가 (services/koneps/scsbid.py). 코어 실측
    (2026-08-11 스냅샷, 당시 코어 n=6,009 — 현 코어는 6,010): base-relative 48% /
    예정가-relative 52%. 이 비는
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


@dataclass(frozen=True)
class ReserveDrawObservation:
    """한 공고의 예비가에서 유도한 추첨 분포 관측(순수 값)."""

    ratios: tuple[float, ...]
    center: float
    draw_variance: float
    realized_assessment: float | None
    bid_to_assessment_ratio: float | None


def observe_reserve_draw(
    *,
    reserve_prices: list[float],
    base_amount: float,
    picked_numbers: list[int],
    bid_rate: float | None,
) -> ReserveDrawObservation | None:
    """엔진·캘리 스크립트 공용 행→관측 추출(§4.5-8 단일 출처).

    predictor 의 두 관문 — **정확히 15개** 양수 비율(다회차 누적·부분 결측 제외)과
    center 개연 밴드 — 를 여기서 적용한다. 캘리 스크립트가 이를 재구현하며
    빠뜨렸던 것이 리뷰 L4-1 이고, 검증 선행 덕에 ``draw_mean_moments`` 의
    ``ValueError`` 도 이 경로에서는 발생하지 않는다. ``None`` = 관측 불가(행 skip).
    """
    if base_amount <= 0:
        return None
    ratios = [price / base_amount for price in reserve_prices if price > 0]
    if len(ratios) != EXPECTED_RESERVE_PRICE_COUNT:
        return None
    center, draw_std = draw_mean_moments(ratios, DEFAULT_DRAW_COUNT)
    if not is_plausible_assessment_rate(center):
        return None
    realized = realized_assessment_ratio(
        reserve_prices=reserve_prices,
        picked_numbers=picked_numbers,
        base_amount=base_amount,
    )
    return ReserveDrawObservation(
        ratios=tuple(ratios),
        center=center,
        draw_variance=draw_std**2,
        realized_assessment=realized,
        bid_to_assessment_ratio=bid_to_assessment_ratio(bid_rate, realized),
    )
