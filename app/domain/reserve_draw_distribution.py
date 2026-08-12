"""복수예비가격 추첨(4/15) 예정가 분포 커널 — 완전 열거.

왜 이 모듈인가
--------------
KONEPS 예정가는 시계열이 아니라 **복권형 생성 메커니즘**이다: 공고마다 게시된
복수예비가격 15개 중 4개를 추첨해 평균낸 값이 예정가가 된다. 추첨 조합은
C(15, 4) = 1,365 가지**뿐**이므로, 몬테카를로 샘플링 없이 전체 조합을 **완전
열거(exact enumeration)** 하면 샘플링 오차 0 의 결정적·재현 가능한 분포를 얻는다.

프로덕션 소비는 두 경로다: :func:`draw_mean_moments` 는
``distribution_extraction.observe_reserve_draw``(엔진·캘리 스크립트 공용 관측
관문 — **정확히 15개** 강제도 그 함수 안이다)가 유일하게 호출하고,
:func:`exact_draw_mean_distribution` 은 캘리 스크립트가 그 관문을 통과한 행의
메커니즘 PIT 에 쓴다. 조합 수가 열거를 넘는 입력은 그래서 이 커널에 도달하지
않는다. 몬테카를로 폴백은 두지 않는다 — 도달 불가 죽은 코드였고(리뷰 L3),
Phase 3 가 실제로 필요로 하면 그때 실소비자와 함께 되살린다.

순수 함수(I/O 0), stdlib 전용. ``app.domain.rate_normalization`` 등과 같은
mypy strict 아일랜드다. 값 검증은 이 커널이 소유한다(잘못된 입력은 조용히
건너뛰지 않고 ``ValueError`` 로 실패시킨다) — 필터링은 호출부 책임이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from math import isfinite, sqrt
from statistics import fmean, pvariance
from typing import Final

# KONEPS 추첨 규칙: 게시된 복수예비가격 중 4개를 추첨해 평균낸다.
DEFAULT_DRAW_COUNT: Final[int] = 4
# 정상 공고의 복수예비가격 개수. 30/60/100개 행은 다회차 공고 누적으로 관측됐다
# (운영 DB 실측: 15개 = 27,397건/비어있지 않은 27,444건의 99.8%).
EXPECTED_RESERVE_PRICE_COUNT: Final[int] = 15


@dataclass(frozen=True)
class DrawMeanDistribution:
    """추첨 평균(예정가)의 이산 분포 — 지지집합의 각 원소는 등확률이다.

    ``support`` 는 오름차순 정렬된 추첨 평균들로, 완전 열거 조합당 1개
    (C(n, k)개 전부)다. 등확률이므로 분위수·구간·누적확률이 전부
    순서통계량으로 닫힌다.

    소비 현황을 정직하게: ``cumulative_probability`` 는 캘리 스크립트의 메커니즘
    PIT 가 쓰고, ``quantile``/``central_interval`` 은 **현재 프로덕션 소비자가
    없다** — ``support`` 와 함께 PR 본문이 예고한 Phase 3(KDE·기대가치) 표면으로
    유지한다(리뷰 M4-8; MC 폴백과 달리 존재 근거 서술이 거짓이 아니다).
    """

    support: tuple[float, ...]
    draw_count: int
    source_count: int

    @property
    def mean(self) -> float:
        """분포 평균. 완전 열거에서는 원본 예비가격 평균과 정확히 일치한다(선형성)."""
        return fmean(self.support)

    @property
    def std(self) -> float:
        """분포 표준편차(모표준편차 — 지지집합이 곧 전체 모집단이다)."""
        return sqrt(pvariance(self.support))

    def quantile(self, q: float) -> float:
        """등확률 지지집합의 선형 보간 분위수. ``q`` 는 [0, 1] 로 clamp 된다."""
        safe_q = min(1.0, max(0.0, float(q)))
        position = (len(self.support) - 1) * safe_q
        lower_index = int(position)
        upper_index = min(lower_index + 1, len(self.support) - 1)
        fraction = position - lower_index
        return (self.support[lower_index] * (1.0 - fraction)) + (
            self.support[upper_index] * fraction
        )

    def central_interval(self, coverage: float) -> tuple[float, float]:
        """중앙 ``coverage`` 확률 구간(예: 0.8 → 10%·90% 분위수 쌍)."""
        safe_coverage = min(1.0, max(0.0, float(coverage)))
        tail = (1.0 - safe_coverage) / 2.0
        return self.quantile(tail), self.quantile(1.0 - tail)

    def cumulative_probability(self, value: float) -> float:
        """midrank CDF: P(X < v) + P(X = v)/2. 캘리브레이션 PIT 검정용.

        완전 열거 지지집합은 동률(같은 평균을 내는 다른 조합)이 흔하므로,
        동률을 절반씩 나누는 midrank 가 이산 분포에서 균등 PIT 에 가장 가깝다.
        """
        below = sum(1 for entry in self.support if entry < value)
        equal = sum(1 for entry in self.support if entry == value)
        return (below + (equal / 2.0)) / len(self.support)


def exact_draw_mean_distribution(
    values: Sequence[float], draw_count: int = DEFAULT_DRAW_COUNT
) -> DrawMeanDistribution:
    """모든 C(n, k) 추첨 조합을 열거한 정확한 추첨 평균 분포(샘플링 오차 0)."""
    validated = _validated_values(values, draw_count)
    support = sorted(
        fmean(combination) for combination in combinations(validated, draw_count)
    )
    return DrawMeanDistribution(
        support=tuple(support),
        draw_count=draw_count,
        source_count=len(validated),
    )


def draw_mean_moments(
    values: Sequence[float], draw_count: int = DEFAULT_DRAW_COUNT
) -> tuple[float, float]:
    """추첨 평균의 (평균, 표준편차)를 열거 없이 닫힌식으로 계산한다.

    비복원 k-추첨 평균의 분산은 유한모집단 보정(finite population correction)으로
    정확히 닫힌다::

        Var(x̄_k) = (σ²/k) · (n − k)/(n − 1)   (σ² = 모분산)

    완전 열거 분포의 (mean, std)와 부동소수 오차 안에서 정확히 일치한다 —
    이 동치는 값표 테스트가 고정한다. 이력 행마다 분포 전체가 필요 없는
    (모수 집계용) 경로는 이 함수를 쓴다.
    """
    validated = _validated_values(values, draw_count)
    population_size = len(validated)
    mean_value = fmean(validated)
    # n == k(전부 추첨)면 분산 0. n == 1 단독 케이스는 k >= 1, n >= k 검증에 의해
    # n == k 에 포섭되므로 별도 분기가 성립하지 않는다.
    if population_size == draw_count:
        return mean_value, 0.0
    variance = (pvariance(validated) / draw_count) * (
        (population_size - draw_count) / (population_size - 1)
    )
    return mean_value, sqrt(variance)


def _validated_values(values: Sequence[float], draw_count: int) -> list[float]:
    """추첨 대상 값 검증 — 유한 양수만 허용, 표본 부족은 즉시 실패."""
    if draw_count < 1:
        raise ValueError("draw_count must be a positive integer")
    validated = [float(value) for value in values]
    if any(not isfinite(value) or value <= 0.0 for value in validated):
        raise ValueError("reserve prices must be finite positive numbers")
    if len(validated) < draw_count:
        raise ValueError(
            f"need at least {draw_count} reserve prices, got {len(validated)}"
        )
    return validated
