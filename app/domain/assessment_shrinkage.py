"""사정률(예정가/기초금액) 계층 수축 사전분포 커널 — 발주기관 → 공종 → 전역.

왜 계층 수축인가
----------------
발주기관별 사정률 점추정은 성립하지 않는다: 이 커널이 실제로 소비하는 **학습 코어**
(15개 예비가 ∩ clean ∩ 정산완료, 2026-08-11 실측)의 발주기관 1,837곳 중 **91.9% 가
표본 <10**(중앙값 n=1)이고 n≥30 은 8곳뿐이다. 표본이 얕은 기관을 자기 평균으로
점추정하면 노이즈를 그대로 추천에 싣는다. 그래서 관측 평균을 표본 수의 함수로
상위 계층(공종, 최종적으로 전역)으로 **수축(shrinkage)** 한다.

기법은 정확한 사후분포가 아니라 **conjugate 근사(pseudo-count 수축)** 다:
normal-normal 모형에서 사후 평균은 ``(n·x̄ + κ·μ_prior)/(n + κ)`` 로 닫히고,
κ 는 사전분포의 등가 표본 수(prior strength)다. κ 를 계층별 **선언 상수**(§4.5)로
두면 수축 강도가 코드 흐름이 아니라 데이터로 읽힌다.

순수 함수(I/O 0), stdlib 전용 — mypy strict 아일랜드. 계층 표본 집계(어느 행이
어느 기관·공종에 속하는가)는 호출부 책임이고, 이 커널은 집계된 수치만 받는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Final

# 계층별 prior strength (κ, 등가 표본 수). 관측 표본 n 이 κ 와 같아지는 지점에서
# 자기 평균과 상위 계층이 절반씩 섞인다.
#
# 근거는 **실제 학습 코어**(15개 예비가 ∩ clean ∩ 정산완료, 2026-08-11 실측:
# 기관 1,837곳, 91.9% 가 n<10, 중앙값 n=1, n≥30 은 8곳) 기준이다.
#
# * 기관 κ=12: method-of-moments κ=σ²_within/σ²_between 은 **추정기·부분집합·측정
#   축에 따라 3.0~36 으로 불안정하다**. 2026-08-11 리뷰 라운드의 독립 3자 측정이
#   전부 달랐다 — 리뷰어 4.4 / 빌더(공고 중심 축): naive ≈8 · 노이즈 보정 ≈19~∞ ·
#   ANOVA ≈36 / 컨트롤러(실현 사정률 축): naive 2.99 · 노이즈 보정 6.27. 어느
#   값도 정답으로 제시하지 않는다. 다만 **표본 노이즈를 보정하면 κ가 커지는
#   방향은 일관**했고(2.99→6.27, 8→19~∞), 이 불안정성 자체가 "기관 간 신호가
#   약하다"의 증거다 — 행별 컨텍스트 점추정에서 계층을 켜면 오차가 오히려 커진
#   결과(리뷰 J2)와 정합한다. 12 는 그 범위의 보수적 지점으로 유지하고, 점추정이
#   아니라 커버리지(PIT) 축이 이 선택을 end-to-end 로 검증한다. Phase 2 에서
#   릴리스 단위로 재추정한다.
# * 공종 κ=40: 공종은 코어에 3그룹뿐이라(df=2) κ 추정 자체가 불안정하다. 공종
#   표본은 수천 단위라 n/(n+40)≈1 로 어차피 자기 평균을 유지하고, 표본이 수십
#   건뿐인 신생 공종만 전역으로 절반쯤 수축된다 — 보수적 선언값을 유지한다.
AGENCY_PRIOR_STRENGTH: Final[float] = 12.0
CATEGORY_PRIOR_STRENGTH: Final[float] = 40.0

# 예측 표준편차 하한. 표본이 전부 같은 값(분산 0)이어도 "다음 공고의 사정률이
# 정확히 그 값"이라는 주장은 정직하지 않으므로, 추첨 밴드 해상도보다 한 자릿수
# 작은 최소 불확실성을 남긴다.
MIN_PREDICTIVE_STD: Final[float] = 0.002

# 계층 분산을 신뢰하기 위한 최소 표본 수 — 1개 표본의 분산 0 은 정보가 아니라
# 미관측이므로 상위 계층 분산을 상속한다.
MIN_SAMPLES_FOR_VARIANCE: Final[int] = 2

LEVEL_AGENCY: Final[str] = "agency"
LEVEL_CATEGORY: Final[str] = "category"
LEVEL_GLOBAL: Final[str] = "global"


@dataclass(frozen=True)
class LevelObservation:
    """한 계층에서 관측된 공고별 사정률 중심의 집계 통계.

    ``variance`` 는 공고별 중심들의 **모분산**(between-notice)이다 — 공고 안의
    추첨 분산(within-notice)은 이 커널의 대상이 아니고 호출부가 별도로 합성한다.
    """

    sample_count: int
    mean: float
    variance: float


@dataclass(frozen=True)
class AssessmentPosterior:
    """수축된 사정률 사전분포(다음 공고의 중심에 대한 예측 분포)."""

    mean: float
    std: float
    effective_sample_count: float
    level_weights: dict[str, float]


def shrink_toward(
    observed_mean: float,
    sample_count: int,
    *,
    prior_mean: float,
    prior_strength: float,
) -> tuple[float, float]:
    """pseudo-count 수축 한 단계: ``(n·x̄ + κ·μ)/(n + κ)`` 와 자기 가중치 ``n/(n+κ)``."""
    weight = sample_count / (sample_count + prior_strength)
    return (observed_mean * weight) + (prior_mean * (1.0 - weight)), weight


def resolve_assessment_posterior(
    *,
    agency: LevelObservation | None,
    category: LevelObservation | None,
    global_level: LevelObservation,
) -> AssessmentPosterior:
    """3계층 순차 수축: 전역 → 공종 → 기관 순으로 하위 계층이 상위로 수축된다.

    계층이 없거나 표본이 0이면 그 계층의 가중치는 **정확히 0**이 되어 상위 계층으로
    투명하게 떨어진다 — 이 경로는 미관측 계층 전용이다. 표본이 얕은 기관(코어
    91.9% 가 n<10, 중앙값 n=1)은 이 경로가 아니라 ``n/(n+κ)`` 로 **약하게 남는다**
    (κ=12 에서 n=1 → 7.7%, n=9 → 43%) — 두 경우를 섞어 읽으면 "얕은 기관은 어차피
    전역으로 떨어지니 κ 선택은 안전하다"는 오독으로 κ 재추정 판단이 왜곡된다(리뷰
    L4-7). 세 가중치의 합은 1이다.
    """
    if global_level.sample_count < 1:
        raise ValueError("global level needs at least one assessment sample")

    category_observed = category if category is not None and category.sample_count > 0 else None
    agency_observed = agency if agency is not None and agency.sample_count > 0 else None

    category_mean, category_self_weight = _shrink_optional_level(
        category_observed,
        prior_mean=global_level.mean,
        prior_strength=CATEGORY_PRIOR_STRENGTH,
    )
    posterior_mean, agency_self_weight = _shrink_optional_level(
        agency_observed,
        prior_mean=category_mean,
        prior_strength=AGENCY_PRIOR_STRENGTH,
    )
    level_weights = _compose_level_weights(agency_self_weight, category_self_weight)
    predictive_variance = _blend_predictive_variance(
        level_weights=level_weights,
        agency=agency_observed,
        category=category_observed,
        global_level=global_level,
    )

    return AssessmentPosterior(
        mean=posterior_mean,
        std=max(sqrt(predictive_variance), MIN_PREDICTIVE_STD),
        effective_sample_count=(
            (level_weights[LEVEL_AGENCY] * _sample_count(agency_observed))
            + (level_weights[LEVEL_CATEGORY] * _sample_count(category_observed))
            + (level_weights[LEVEL_GLOBAL] * global_level.sample_count)
        ),
        level_weights=level_weights,
    )


def _compose_level_weights(
    agency_self_weight: float, category_self_weight: float
) -> dict[str, float]:
    """순차 수축의 자기 가중치 둘을 합 1 인 3계층 가중치로 전개한다."""
    return {
        LEVEL_AGENCY: agency_self_weight,
        LEVEL_CATEGORY: (1.0 - agency_self_weight) * category_self_weight,
        LEVEL_GLOBAL: (1.0 - agency_self_weight) * (1.0 - category_self_weight),
    }


def _blend_predictive_variance(
    *,
    level_weights: dict[str, float],
    agency: LevelObservation | None,
    category: LevelObservation | None,
    global_level: LevelObservation,
) -> float:
    """계층 분산의 가중 결합 — 표본이 얕은 계층은 상위 분산을 상속한다."""
    global_variance = _trusted_variance(global_level, fallback=MIN_PREDICTIVE_STD**2)
    category_variance = _trusted_variance(category, fallback=global_variance)
    agency_variance = _trusted_variance(agency, fallback=category_variance)
    return (
        (level_weights[LEVEL_AGENCY] * agency_variance)
        + (level_weights[LEVEL_CATEGORY] * category_variance)
        + (level_weights[LEVEL_GLOBAL] * global_variance)
    )


def _shrink_optional_level(
    observed: LevelObservation | None,
    *,
    prior_mean: float,
    prior_strength: float,
) -> tuple[float, float]:
    """계층이 없으면 상위 평균을 가중치 0으로 그대로 통과시킨다."""
    if observed is None:
        return prior_mean, 0.0
    return shrink_toward(
        observed.mean,
        observed.sample_count,
        prior_mean=prior_mean,
        prior_strength=prior_strength,
    )


def _trusted_variance(observed: LevelObservation | None, *, fallback: float) -> float:
    """분산을 신뢰할 표본이 안 되면 상위 계층 분산을 상속한다."""
    if observed is None or observed.sample_count < MIN_SAMPLES_FOR_VARIANCE:
        return fallback
    return max(observed.variance, 0.0)


def _sample_count(observed: LevelObservation | None) -> int:
    return observed.sample_count if observed is not None else 0
