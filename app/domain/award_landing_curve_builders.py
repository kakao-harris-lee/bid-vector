"""세 층의 win-proxy 곡선 조립 — 점 추정량을 격자에 대고 도는 순수 어셈블리.

:mod:`app.domain.award_landing_curve` 가 선언한 분할 seam 을 실제로 실행한 파일이다.
판정(동률 규칙·α-제약·진단)과 이 조립부는 상태를 공유하지 않는다 — 여기가 하는 일은
"격자 × 점 추정량 두 개 → :class:`WinProxyCurve`" 하나뿐이고, 세 층(층 A / 층 C /
f-재기준)의 차이는 **어떤 점 추정량을 꽂느냐**에만 있다(§4.7-3 주입).

그래서 세 빌더는 같은 :func:`_curve_from` 을 부르고, 새 층이 생기면 여기에 6줄이
붙는다. ``WW`` 곡선과 실격확률 곡선을 **함께** 채우는 것도 이 조립부의 계약이다 —
둘이 따로 만들어지면 α-제약이 서로 다른 격자를 보게 된다.

순수 함수(I/O 0), stdlib 전용 — Phase 1 커널과 같은 mypy strict 아일랜드.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.domain.award_landing_curve import (
    WIN_PROXY_TIE_TOLERANCE,
    PlateauChoice,
    WinProxyCurve,
)
from app.domain.award_landing_distribution import (
    AssessmentSupport,
    composed_disqualification_probability,
    composed_win_proxy,
    empirical_disqualification_probability,
    empirical_win_proxy,
    rebased_disqualification_probability,
    rebased_win_proxy,
)
from app.domain.award_margin_distribution import LandingObservation, MarginDistribution


def empirical_win_proxy_curve(
    bid_rates: Sequence[float],
    observations: Sequence[LandingObservation],
    *,
    tie_tolerance: float = WIN_PROXY_TIE_TOLERANCE,
    plateau_choice: PlateauChoice = PlateauChoice.UPPER,
) -> WinProxyCurve:
    """층 A 곡선 — 자기 모집단 채점용(각 행이 자기 ``f_i`` 를 담는다)."""
    return _curve_from(
        bid_rates,
        win_proxy=lambda rate: empirical_win_proxy(rate, observations),
        disqualification=lambda rate: empirical_disqualification_probability(
            rate, observations
        ),
        tie_tolerance=tie_tolerance,
        plateau_choice=plateau_choice,
    )


def composed_win_proxy_curve(
    bid_rates: Sequence[float],
    *,
    floor_rate: float,
    assessment: AssessmentSupport,
    margins: MarginDistribution,
    tie_tolerance: float = WIN_PROXY_TIE_TOLERANCE,
    plateau_choice: PlateauChoice = PlateauChoice.UPPER,
) -> WinProxyCurve:
    """층 C 곡선 — 열린 공고 합성(``a ⊥ Δ`` 가정)."""
    return _curve_from(
        bid_rates,
        win_proxy=lambda rate: composed_win_proxy(
            rate, floor_rate=floor_rate, assessment=assessment, margins=margins
        ),
        disqualification=lambda rate: composed_disqualification_probability(
            rate, floor_rate=floor_rate, assessment=assessment
        ),
        tie_tolerance=tie_tolerance,
        plateau_choice=plateau_choice,
    )


def rebased_win_proxy_curve(
    bid_rates: Sequence[float],
    observations: Sequence[LandingObservation],
    *,
    target_floor_rate: float,
    tie_tolerance: float = WIN_PROXY_TIE_TOLERANCE,
    plateau_choice: PlateauChoice = PlateauChoice.UPPER,
) -> WinProxyCurve:
    """f-재기준 곡선 — ``(a, Δ)`` 결합을 유지한 채 대상 공고 하한으로 전이(식 3)."""
    return _curve_from(
        bid_rates,
        win_proxy=lambda rate: rebased_win_proxy(
            rate, observations, target_floor_rate=target_floor_rate
        ),
        disqualification=lambda rate: rebased_disqualification_probability(
            rate, observations, target_floor_rate=target_floor_rate
        ),
        tie_tolerance=tie_tolerance,
        plateau_choice=plateau_choice,
    )


def _curve_from(
    bid_rates: Sequence[float],
    *,
    win_proxy: Callable[[float], float],
    disqualification: Callable[[float], float],
    tie_tolerance: float,
    plateau_choice: PlateauChoice,
) -> WinProxyCurve:
    """격자마다 두 점 추정량을 평가해 곡선을 만든다(세 층이 공유하는 조립부)."""
    grid = tuple(bid_rates)
    return WinProxyCurve(
        bid_rates=grid,
        values=tuple(win_proxy(bid_rate) for bid_rate in grid),
        disqualification_probabilities=tuple(
            disqualification(bid_rate) for bid_rate in grid
        ),
        tie_tolerance=tie_tolerance,
        plateau_choice=plateau_choice,
    )
