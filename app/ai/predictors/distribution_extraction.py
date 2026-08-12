"""분포 predictor 의 record-free 추출 산술 — typed 순수 헬퍼(§4.7).

ORM 행/dict 를 읽는 약한 경계(``read_record_value``)는 predictor 의 추출 루프
한 곳에만 남기고, 실제 판정 산술은 여기의 **typed 원시값 함수**로 내린다.
실현 사정률 정의(추첨분 선택·평균/base·개연 밴드)는 이 모듈이 **단일 출처**다
(§4.5-8): ``app/ai/predictors/historical/summary.py`` (historical 핫패스)와
캘리브레이션 스크립트(``scripts/backtest_yega_distribution.py``)가 여기서
import 해 쓴다 — 리뷰 L2 로 summary 의 인라인 복사본을 이쪽으로 통합했다.
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
# 폴백(추천가/기초금액)이라는 세 번째 provenance 도 있다. base-relative 행에서만
# bid/예정가로 차원이 정확하고, 나머지는 근사다 — 단일 축 단정은 하지 않는다
# (리뷰 M4-1, L4-8 재정정).
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
    if not ASSESSMENT_RATE_PLAUSIBLE_MIN <= realized <= ASSESSMENT_RATE_PLAUSIBLE_MAX:
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
    if not ASSESSMENT_RATE_PLAUSIBLE_MIN <= center <= ASSESSMENT_RATE_PLAUSIBLE_MAX:
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
