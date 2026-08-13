"""반사실 삽입 win-proxy ``WW(b)`` 추정량 — 하한 위 착지점 선택의 순수 코어.

왜 이 모듈인가
--------------
낙찰은 "하한 이상 최저가 생존자"다. 그래서 개찰 1건의 관측 ``(φ, ω)``(실현 하한율·
낙찰자 투찰률, 둘 다 기초-basis)만으로 **"내 후보 b 를 그 개찰에 그대로 끼워 넣었다면
최저 생존자였는가"** 라는 반사실 질문이 편향 없이 계산된다::

    WW(b) = P( φ ≤ b ≤ ω )

참가자 전체 분포가 필요 없다 — winner-only 데이터가 이 estimand 를 **정확히 식별**
한다(설계 §3.1). 이 값은 정직 명세의 ``would_have_won_price_only``("가격 근접 기반
추정 낙찰, 실제 낙찰 아님")의 ``b``-함수 그 자체다. **실제 낙찰 확률이 아니다**:
내 진입이 경쟁자 투찰을 바꾸는 균형 효과는 "타 투찰 고정" 가정으로 제외되고, 가격 외
적격심사도 모형에 없다(설계 §11-8 — 관측 낙찰자는 정의상 자격 통과자라 표본에서
적격 확률이 보이지 않는다). "적격 통과 조건 하의 가격 승리"로 읽는다.

세 추정량을 제공한다:

* **층 A — :func:`empirical_win_proxy`**: 관측 쌍을 직접 센다. 가정 0개. 각 행이
  **자기 ``f_i``** 를 담으므로 **자기 모집단 채점 전용**이다(설계 §5.1).
* **층 C — :func:`composed_win_proxy`**: 열린 공고용 합성. 생존은 사정률 이산분포
  ``F_a``(Phase 1), 경쟁은 마진 분포 ``G``(:mod:`app.domain.award_margin_distribution`)
  가 담당하며 ``a ⊥ Δ`` 가정 하나만 쓴다(실측 ``corr(a,Δ) = −0.0045``, n=2,152).
* **f-재기준 fallback — :func:`rebased_win_proxy`**(설계 §6 식 3): ``(a_i, Δ_i)``
  **결합** 관측을 대상 공고의 ``f_tgt`` 로 옮긴다. 셀 안에서도 ``f`` 는 이질적이라
  (실측 service 셀: 0.88 66.8% · 0.90 14.3% · 0.87745 7.2%) 층 A 원형을 다른 공고로
  그대로 전이할 수 없고, 독립 가정이 의심스러울 때는 이쪽이 층 C 보다 안전하다.

**폐기된 층 B**: 실현 ``ω`` 를 고정한 채 생존항만 추첨 CDF 로 적분하는 "정밀화"는
Rao-Blackwell 화가 아니라 편향이다(``corr(a, ω) = +0.587`` — 하한이 내려가면 낙찰자도
함께 내려간다). 정산 채점은 분해 없이 층 A 결합추정만 쓴다(설계 §5.1·§11-9).

**하한을 선점하지 않는다**(red line, 설계 §8.2): ``b`` 가 실현 하한 아래면 생존항이
0 이라 곡선이 스스로 0 을 낸다. 최종 추천의 clamp 는 기존 guardrail 이 그대로 한다.
``WW`` 는 실격과 패찰을 등가로 보므로, 실격 예산 제약(``P(φ>b) ≤ α``)은
:mod:`app.domain.award_landing_curve` 가 이 모듈의 실격확률 추정량 위에 얹는다.

순수 함수(I/O 0), stdlib 전용 — Phase 1 커널과 같은 mypy strict 아일랜드.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isclose, isfinite
from typing import Final

from app.domain.award_margin_distribution import LandingObservation, MarginDistribution

# 이산 지지집합의 확률 합 검증 허용오차(정규화 나눗셈의 누적 오차 폭).
PROBABILITY_SUM_TOLERANCE: Final[float] = 1e-9


@dataclass(frozen=True)
class AssessmentSupport:
    """사정률 ``a`` 의 이산 분포 — (값, 확률) 쌍.

    Phase 1 ``DrawMeanDistribution.support``(C(15,4)=1,365 완전 열거, 등확률)는
    :func:`equiprobable_assessment_support` 로 그대로 올라오고, 서빙에서는 사정률
    사후분포를 이산화한 (값, 확률) 쌍을 받는다. 어느 쪽이든 합성은 **정확 합산**이라
    수치적분 격자 오차가 0 이다.
    """

    values: tuple[float, ...]
    probabilities: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("assessment support must not be empty")
        if len(self.values) != len(self.probabilities):
            raise ValueError("assessment values and probabilities must align")
        if any(not isfinite(value) or value <= 0.0 for value in self.values):
            raise ValueError("assessment ratios must be finite positive numbers")
        if any(
            not isfinite(probability) or probability < 0.0
            for probability in self.probabilities
        ):
            raise ValueError("assessment probabilities must be finite and non-negative")
        if not isclose(
            sum(self.probabilities), 1.0, rel_tol=0.0, abs_tol=PROBABILITY_SUM_TOLERANCE
        ):
            raise ValueError("assessment probabilities must sum to 1")


def equiprobable_assessment_support(values: Sequence[float]) -> AssessmentSupport:
    """등확률 지지집합(Phase 1 완전 열거 ``support`` 형태)을 그대로 받는다."""
    if not values:
        raise ValueError("assessment support must not be empty")
    weight = 1.0 / len(values)
    return AssessmentSupport(
        values=tuple(float(value) for value in values),
        probabilities=tuple(weight for _ in values),
    )


def assessment_support_from_pairs(
    pairs: Sequence[tuple[float, float]],
) -> AssessmentSupport:
    """(값, 확률) 쌍 목록에서 지지집합을 만든다(서빙 사후분포 이산화용)."""
    return AssessmentSupport(
        values=tuple(float(value) for value, _ in pairs),
        probabilities=tuple(float(probability) for _, probability in pairs),
    )


def empirical_win_proxy(
    bid_rate: float, observations: Sequence[LandingObservation]
) -> float:
    """층 A — ``WW_emp(b) = (1/N) Σ 1[φ_i ≤ b ≤ ω_i]``. 가정 0개.

    관측 쌍을 직접 세므로 독립 가정도 KDE 도 필요 없다. ``b`` 는 기초-basis 투찰률
    (``P/B``)이라 공고 크기와 무관하게 셀 안에서 비교 가능하다.

    각 행이 자기 ``f_i`` 를 담으므로 **자기 모집단 채점(백테스트 접지 진리) 전용**
    이다. 다른 ``f`` 의 대상 공고로 옮기려면 :func:`rebased_win_proxy` 를 쓴다.
    """
    if not observations:
        raise ValueError("empirical win proxy needs at least one observation")
    wins = sum(
        1
        for observation in observations
        if observation.realized_floor_rate
        <= bid_rate
        <= observation.realized_winning_rate
    )
    return wins / len(observations)


def empirical_disqualification_probability(
    bid_rate: float, observations: Sequence[LandingObservation]
) -> float:
    """``P(φ > b)`` 경험추정 — 후보가 실현 하한 아래로 떨어져 **실격**될 확률.

    ``WW`` 는 실격과 패찰을 등가로 취급하지만 운영자는 아니다(3번째 실투찰이 사정률
    추첨 실격 사고). 소박한 ``argmax WW`` 지점의 실측 실격률은 공사 32.5% · 용역
    52.6% 다(설계 §7 M2) — 그래서 판정에는 실격 예산 제약이 함께 간다.
    """
    if not observations:
        raise ValueError("disqualification probability needs at least one observation")
    disqualified = sum(
        1
        for observation in observations
        if observation.realized_floor_rate > bid_rate
    )
    return disqualified / len(observations)


def composed_win_proxy(
    bid_rate: float,
    *,
    floor_rate: float,
    assessment: AssessmentSupport,
    margins: MarginDistribution,
) -> float:
    """층 C — ``WW(b) = Σ_{a·f ≤ b} p_a · P(Δ ≥ b/a − f)``.

    유도(설계 §6): 생존 ``φ = f·a ≤ b``, 이김 ``b ≤ ω = a(f+Δ) ⟺ Δ ≥ b/a − f``.
    잘라낸 영역 안에서는 ``b/a − f ≥ 0`` 이 보장되므로 Δ 의 지지집합(≥0)을 벗어나지
    않는다. 이산 지지집합 위 **정확 합산**이며 ``a ⊥ Δ`` 가정 하나만 쓴다 — 그 가정이
    의심스러우면 :func:`rebased_win_proxy`(결합 유지)로 후퇴한다.

    **생존 판정은 곱셈형이다**(``a·f ≤ b``, 동치인 ``a ≤ b/f`` 아님). 두 식은 실수
    산술에서 같지만 부동소수에서 다르다: ``b`` 가 정확히 ``f·a`` 일 때 ``b/f`` 가
    ``a`` 보다 1 ulp 아래로 떨어져 **그 사정률 원자의 기여 전체를 잃는다**. 임의
    격자에서는 안 보이다가 하필 경계에서만 틀리는 결함이고, 하한 밀착이 이 도메인의
    핵심 구간이라 경계가 곧 관심 지점이다. 실측: 나눗셈형은 자기 경계에서 6,178/
    200,000(3.1%) 탈락, 곱셈형은 0/200,000.

    ``b < f·min(a)`` 면 지지집합 전체가 잘려 0 이다. 곡선이 스스로 법정 하한 아래를
    배제하므로 하한을 선점·우회하지 않는다(red line #221).

    **알려진 잔여(제거 불가)**: 이김 임계 ``δ = b/a − f`` 는 나눗셈을 피할 수 없다 —
    :class:`MarginDistribution` 계약이 δ 하나만 받기 때문이다(KDE 는 표본을 갖고 있지
    않으므로 곱셈형 비교를 대신 할 수 없다). 그래서 ``b`` 가 정확히 ``ω_i`` 인 좌표
    에서는 ``(a·w)/a ≠ w`` 로 원자 1개를 잃을 수 있고, 층 A 와 최대 **관측 1건 몫**
    (1/N)만큼 갈린다. 값표 테스트가 이 잔여를 0 이라 주장하지 않고 **측정해 고정**
    한다. 대안(스케일 인자를 받는 생존 질의)은 계약 확장이라 PR1 범위 밖이다.
    """
    if not isfinite(floor_rate) or floor_rate <= 0.0:
        raise ValueError("floor_rate must be a finite positive rate")
    if not isfinite(bid_rate):
        raise ValueError("bid_rate must be finite")
    total = 0.0
    for value, probability in zip(
        assessment.values, assessment.probabilities, strict=True
    ):
        if value * floor_rate > bid_rate:
            continue
        total += probability * margins.survival_at_least((bid_rate / value) - floor_rate)
    return total


def composed_disqualification_probability(
    bid_rate: float, *, floor_rate: float, assessment: AssessmentSupport
) -> float:
    """``P(φ > b) = Σ_{a·f > b} p_a`` — 합성 경로의 실격확률.

    생존 판정은 :func:`composed_win_proxy` 와 **같은 곱셈형**이라 두 곡선이 같은
    경계를 본다(한쪽만 나눗셈이면 ``WW>0`` 인데 실격확률도 1 인 좌표가 생긴다).
    """
    if not isfinite(floor_rate) or floor_rate <= 0.0:
        raise ValueError("floor_rate must be a finite positive rate")
    if not isfinite(bid_rate):
        raise ValueError("bid_rate must be finite")
    return sum(
        probability
        for value, probability in zip(
            assessment.values, assessment.probabilities, strict=True
        )
        if value * floor_rate > bid_rate
    )


def rebased_win_proxy(
    bid_rate: float,
    observations: Sequence[LandingObservation],
    *,
    target_floor_rate: float,
) -> float:
    """f-재기준 결합 경험추정(설계 §6 식 3)::

        WW_fallback(b; f_tgt) = (1/N) Σ_i 1[ a_i·f_tgt ≤ b ≤ a_i·(f_tgt + Δ_i) ]

    각 관측의 ``(a_i, Δ_i)`` **결합**을 유지한 채 하한만 대상 공고의 ``f_tgt`` 로
    바꿔 φ·ω 를 다시 만든다. 식 2 가 가정으로 지우는 ``(a, Δ)`` 의존을 보존하므로
    ``a ⊥ Δ`` 가 의심스러울 때의 서빙 fallback 이다.

    ``f_tgt`` 가 모든 관측의 ``f_i`` 와 같으면 :func:`empirical_win_proxy` 와 정확히
    일치한다(값표 테스트가 고정). 두 경계 판정 모두 곱셈형이라 ``b`` 가 정확히 φ·ω 인
    좌표에서도 층 A 와 어긋나지 않는다 — 층 C 와 달리 여기서는 Δ_i 를 개별로 갖고
    있어 나눗셈이 필요 없다.
    """
    if not observations:
        raise ValueError("rebased win proxy needs at least one observation")
    if not isfinite(target_floor_rate) or target_floor_rate <= 0.0:
        raise ValueError("target_floor_rate must be a finite positive rate")
    if not isfinite(bid_rate):
        raise ValueError("bid_rate must be finite")
    wins = sum(
        1
        for observation in observations
        if _rebased_floor(observation, target_floor_rate)
        <= bid_rate
        <= _rebased_winning(observation, target_floor_rate)
    )
    return wins / len(observations)


def rebased_disqualification_probability(
    bid_rate: float,
    observations: Sequence[LandingObservation],
    *,
    target_floor_rate: float,
) -> float:
    """``(1/N) Σ 1[a_i·f_tgt > b]`` — f-재기준 경로의 실격확률(α-제약용)."""
    if not observations:
        raise ValueError("disqualification probability needs at least one observation")
    if not isfinite(target_floor_rate) or target_floor_rate <= 0.0:
        raise ValueError("target_floor_rate must be a finite positive rate")
    disqualified = sum(
        1
        for observation in observations
        if _rebased_floor(observation, target_floor_rate) > bid_rate
    )
    return disqualified / len(observations)


def _rebased_floor(observation: LandingObservation, target_floor_rate: float) -> float:
    """φ(f_tgt) = a_i · f_tgt."""
    return observation.assessment_ratio * target_floor_rate


def _rebased_winning(
    observation: LandingObservation, target_floor_rate: float
) -> float:
    """ω(f_tgt) = a_i · (f_tgt + Δ_i) — 마진은 관측 그대로 옮긴다."""
    return observation.assessment_ratio * (target_floor_rate + observation.margin)
