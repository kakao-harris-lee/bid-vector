"""Pure builder for the per-notice 투찰가 메뉴 (bid target menu).

Given the agency guardrail band [floor, ceiling] (a % of the notice's
사업금액/기초금액), the notice's base amount, and a small signal set, produce a
3-option menu: recommended (per-notice positioned within the band), aggressive
(band floor — most competitive, 낙하 위험), safe (band ceiling — safe from 낙하,
less competitive).

Honesty (CLAUDE.md §2): options carry qualitative stance + factual basis, never
win/낙하 probabilities. The true 낙찰하한 depends on the 복수예비가격 추첨
(개찰 전 비공개), so ``CAVEAT`` states this is decision support, not a guarantee.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.ai.construction_scenario import resolve_scenario_anchor_rates

# recommended anchor: 15% up from the floor → near the competitive 최저적격 target.
_BASE_ADJUSTMENT = 0.15
# winning-rate std at which dispersion fully lifts the recommended toward mid.
_DISPERSION_REFERENCE = 0.02
# never let the signal alone push the recommended past this fraction of the band.
_MAX_ADJUSTMENT = 0.85

CAVEAT = (
    "예정가격은 복수예비가격 추첨(개찰 전 비공개)으로 결정되어 정확한 낙찰하한을 "
    "보장할 수 없습니다. 이 값들은 투찰서 초안·의사결정 지원이며 KONEPS 자동 제출·"
    "확정 낙찰이 아닙니다."
)


@dataclass(frozen=True)
class BidTargetSignals:
    """Per-notice signals that position the recommended bid within the band."""

    win_rate_dispersion: float | None
    data_sufficient: bool


def _resolve_position_adjustment(signals: BidTargetSignals | None) -> float:
    """Fraction in [0, 1] of the band width above the floor for the recommended bid.

    Anchored near the floor (competitive 최저적격 target); more past winning-rate
    dispersion means more 예정가격 uncertainty, so the recommended nudges toward
    the safer ceiling. Insufficient data keeps the neutral base anchor.
    """
    if signals is None or not signals.data_sufficient or signals.win_rate_dispersion is None:
        return _BASE_ADJUSTMENT
    dispersion = max(0.0, float(signals.win_rate_dispersion))
    lift = min(1.0, dispersion / _DISPERSION_REFERENCE) * (0.5 - _BASE_ADJUSTMENT)
    return max(0.0, min(_MAX_ADJUSTMENT, _BASE_ADJUSTMENT + lift))


def _price(budget: float | None, rate: float) -> float | None:
    if budget is None or float(budget) <= 0:
        return None
    return round(float(budget) * rate, 2)


def _build_signals_summary(signals: BidTargetSignals | None, adjustment: float) -> str:
    if signals is None or not signals.data_sufficient or signals.win_rate_dispersion is None:
        return "과거 낙찰률 산포 데이터 부족 → 추천을 밴드 하한 근처(기본값)에 배치했습니다."
    return (
        f"과거 낙찰률 산포 {signals.win_rate_dispersion:.3%}를 반영해 추천을 "
        f"밴드 하한에서 {adjustment:.0%} 지점에 배치했습니다."
    )


def build_bid_target_menu(
    *,
    floor_bid_rate: float | None,
    ceiling_bid_rate: float | None,
    budget: float | None,
    signals: BidTargetSignals | None,
    floor_anchor_offsets: Mapping[str, float] | None = None,
) -> dict | None:
    """Return a 3-option bid target menu, or None when there is no band.

    Two positioning modes share this one menu surface (no parallel path):

    - **발주처 밴드**(default): the recommended is positioned *within* the agency
      band [floor, ceiling] by the per-notice ``signals``. aggressive=floor,
      safe=ceiling.
    - **공사 floor 앵커**(``floor_anchor_offsets`` 제공 시): the three options are
      anchored to ``floor + 선언 offset``(과거 실낙찰 초과분 백분위수), clamped inside
      the same [floor, ceiling] guardrail band. Used when an era-correct 공사 법정
      하한(#197)이 해석되고 발주처 밴드가 없을 때 — 상위 gate 가 우선순위(밴드 우선)를
      정한다. 낙찰 확률 표현 없이 stance/basis 로 percentile 근거만 드러낸다(§2).
    """
    if floor_bid_rate is None or ceiling_bid_rate is None:
        return None
    floor = float(floor_bid_rate)
    ceiling = float(ceiling_bid_rate)
    collapsed = ceiling <= floor + 1e-9
    if floor_anchor_offsets:
        return _build_floor_anchored_menu(
            floor=floor,
            ceiling=ceiling,
            budget=budget,
            offsets=floor_anchor_offsets,
            collapsed=collapsed,
        )
    adjustment = _resolve_position_adjustment(signals)
    recommended = floor if collapsed else floor + adjustment * (ceiling - floor)
    recommended = min(max(recommended, floor), ceiling)

    band_note = f"발주처 밴드 {floor:.2%}~{ceiling:.2%}"
    options = [
        {
            "label": "recommended",
            "stance": "신호 종합 균형",
            "bid_rate": recommended,
            "bid_price": _price(budget, recommended),
            "risk_note": "공고별 신호로 밴드 내 배치한 균형 투찰가입니다.",
            "basis": band_note,
        },
        {
            "label": "aggressive",
            "stance": "경쟁력 높음 · 낙하 위험 있음",
            "bid_rate": floor,
            "bid_price": _price(budget, floor),
            "risk_note": "밴드 하한(최저적격 경쟁 타겟). 실현 낙찰하한이 더 높으면 낙(실격) 위험.",
            "basis": band_note,
        },
        {
            "label": "safe",
            "stance": "낙하 위험 낮음 · 경쟁력 낮음",
            "bid_rate": ceiling,
            "bid_price": _price(budget, ceiling),
            "risk_note": "밴드 상한. 낙 위험은 낮지만 더 낮게 투찰한 적격자에게 밀릴 수 있음.",
            "basis": band_note,
        },
    ]
    return {
        "options": options,
        "band_floor_rate": floor,
        "band_ceiling_rate": ceiling,
        "signals_summary": _build_signals_summary(signals, adjustment),
        "caveat": CAVEAT,
        "collapsed": collapsed,
    }


# 공사 floor 앵커 시나리오 문구(공격/추천/안전 = p25/p50/p75). 낙찰 확률 표현 금지(§2):
# stance/risk_note/basis 는 percentile 근거·낙하 위험만 서술한다.
_ANCHORED_OPTION_SPECS = {
    "recommended": (
        "과거 중앙값(p50) 앵커",
        "과거 실낙찰 중앙값 수준의 균형 투찰가입니다.",
        "p50",
    ),
    "aggressive": (
        "경쟁력 높음 · 낙하 위험 있음",
        "하한 근접(p25). 실현 낙찰하한이 더 높으면 낙(실격) 위험이 있습니다.",
        "p25",
    ),
    "safe": (
        "낙하 위험 낮음 · 경쟁력 낮음",
        "여유 상향(p75). 낙 위험은 낮지만 더 낮게 투찰한 적격자에게 밀릴 수 있습니다.",
        "p75",
    ),
}


def _build_floor_anchored_menu(
    *,
    floor: float,
    ceiling: float,
    budget: float | None,
    offsets: Mapping[str, float],
    collapsed: bool,
) -> dict | None:
    """공사 법정 하한 + 백분위수 offset 으로 앵커한 3종 메뉴(공고별 밴드 대체 경로 아님).

    앵커 rate 는 ``resolve_scenario_anchor_rates`` 단일 출처를 써 predict_price 후보
    앵커와 항상 일치한다. red line: 결과는 반드시 [floor, ceiling] 안이다.
    """
    anchored = resolve_scenario_anchor_rates(
        floor_bid_rate=floor,
        ceiling_bid_rate=ceiling,
        offsets=offsets,
    )
    if anchored is None:
        return None
    band_note = f"법정 낙찰하한 {floor:.3%} + 과거 실낙찰 초과분 백분위수"
    options = []
    for label in ("recommended", "aggressive", "safe"):
        stance, risk_note, percentile = _ANCHORED_OPTION_SPECS[label]
        rate = anchored[label]
        options.append(
            {
                "label": label,
                "stance": stance,
                "bid_rate": rate,
                "bid_price": _price(budget, rate),
                "risk_note": risk_note,
                "basis": f"{band_note}({percentile})",
            }
        )
    return {
        "options": options,
        "band_floor_rate": floor,
        "band_ceiling_rate": ceiling,
        "signals_summary": (
            "공사 법정 하한 앵커: 과거 실낙찰 초과분 백분위수(p25/p50/p75, 표본 기반 "
            "캘리브레이션 — 개찰 누적 시 재캘리브레이션)로 시나리오를 배치했습니다."
        ),
        "caveat": CAVEAT,
        "collapsed": collapsed,
    }
