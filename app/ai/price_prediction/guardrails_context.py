"""Guardrail context resolution — floors, ceilings, agency band, era tier.

Split out of the former single-file ``price_prediction`` module (§4.5 size
decomposition). Every function is relocated verbatim from the original module,
so the floor/ceiling resolution, the max()-only legal-floor fold (#221), the
agency-band attribution, the safe-margin bypass, and the construction era-tier
anchor gate are unchanged. Boundary reads (settings, guardrail_core,
construction_scenario, rate_normalization #256) keep their original call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.ai import guardrail_core
from app.ai.guardrail_core import GuardrailConfig
from app.ai.construction_scenario import (
    is_construction_era_floor_resolved,
    resolve_scenario_anchor_rates,
)
from app.core.config import settings
from app.domain.rate_normalization import to_bid_rate_fraction


@dataclass(frozen=True)
class GuardrailContext:
    normalized_legal_floor_bid_rate: float | None
    floor_guardrail_source: str | None
    floor_bid_rate: float | None
    safe_floor_bid_rate: float | None
    ceiling_bid_rate: float | None
    floor_price: float | None
    safe_floor_price: float | None
    ceiling_price: float | None
    floor_from_agency: bool = False
    ceiling_from_agency: bool = False
    agency_name: str | None = None
    # 공사 era-correct 법정 하한 앵커(#197 tier 해석 + 발주처 밴드 부재 시). stance→rate
    # (aggressive/recommended/safe), floor+offset 을 [floor, ceiling] 로 clamp 한 값.
    # None 이면 앵커 미적용(기존 clamp 동작 그대로).
    construction_scenario_anchor: dict[str, float] | None = None


def _resolve_guardrail_context(
    *,
    budget: float,
    category: str | None,
    business_group: str | None,
    legal_floor_bid_rate: float | None,
    agency_name: str | None = None,
    estimation_amount: float | None = None,
    reference_date: date | datetime | None = None,
) -> GuardrailContext:
    # Collect every guardrail setting read once at the boundary (§4.7.4), then run
    # the pure core on the immutable snapshot.
    config = GuardrailConfig.from_settings(settings)
    # Resolve the category/group baseline and the agency-tightened band separately so
    # the guardrail reason can attribute the BINDING edge to the correct source. The
    # agency band only RAISES the floor / LOWERS the ceiling, so a resolved value that
    # differs from the baseline can only have been moved by the agency band. The
    # construction legal tier (estimation_amount/reference_date) is passed to BOTH so
    # it sits in the baseline too — that keeps the agency attribution isolated to the
    # agency band and lets a binding tier floor still receive the safety margin.
    base_configured_floor = guardrail_core.resolve_floor_bid_rate(
        config,
        category,
        business_group=business_group,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )
    configured_floor_bid_rate = guardrail_core.resolve_floor_bid_rate(
        config,
        category,
        business_group=business_group,
        agency_name=agency_name,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )
    normalized_legal_floor_bid_rate = _normalize_optional_bid_rate(legal_floor_bid_rate)
    floor_bid_rate = _max_optional_rate(
        configured_floor_bid_rate,
        normalized_legal_floor_bid_rate,
    )
    floor_guardrail_source = _resolve_floor_guardrail_source(
        configured_floor_bid_rate,
        normalized_legal_floor_bid_rate,
        floor_bid_rate,
    )
    base_configured_ceiling = guardrail_core.resolve_ceiling_bid_rate(
        config,
        category,
        business_group=business_group,
    )
    ceiling_bid_rate = guardrail_core.resolve_ceiling_bid_rate(
        config,
        category,
        business_group=business_group,
        agency_name=agency_name,
    )
    # Attribution: the agency floor only binds when it raised the configured floor AND
    # the legal floor did not override it (floor_bid_rate still equals the agency floor).
    agency_raised_floor = configured_floor_bid_rate is not None and (
        base_configured_floor is None
        or configured_floor_bid_rate > base_configured_floor + 1e-9
    )
    floor_from_agency = (
        agency_raised_floor
        and floor_bid_rate is not None
        and abs(floor_bid_rate - configured_floor_bid_rate) < 1e-9
    )
    ceiling_from_agency = ceiling_bid_rate is not None and (
        base_configured_ceiling is None
        or ceiling_bid_rate < base_configured_ceiling - 1e-9
    )
    if (
        floor_bid_rate is not None
        and ceiling_bid_rate is not None
        and ceiling_bid_rate < floor_bid_rate
    ):
        ceiling_bid_rate = floor_bid_rate
    # Safe-margin bypass (review Finding 1): the agency floor is a calibrated
    # recommendation target already ABOVE the realized 낙찰하한 — NOT a hard legal
    # floor — so it must NOT receive the generic PREDICTION_FLOOR_SAFETY_MARGIN_RATE.
    # The recommendation should sit exactly at the calibrated target.
    if floor_from_agency:
        safe_floor_bid_rate = floor_bid_rate
    else:
        safe_floor_bid_rate = guardrail_core.resolve_safe_floor_bid_rate(
            config,
            floor_bid_rate,
            ceiling_bid_rate=ceiling_bid_rate,
        )
    # 공사 시나리오 floor 앵커: era-correct tier floor(#197)가 해석되고 발주처 밴드가
    # 없을 때만 켠다. 발주처 밴드가 있으면(agency 가 더 특이적) 기존 밴드/positioning 이
    # 우선이므로 앵커를 적용하지 않는다(우선순위를 선언적으로 고정). 앵커 기준은 위에서
    # 최종 해석한 floor_bid_rate 다(basis 일관성은 construction_scenario docstring 참조).
    construction_scenario_anchor: dict[str, float] | None = None
    if (
        not floor_from_agency
        and not ceiling_from_agency
        and is_construction_era_floor_resolved(category, estimation_amount, reference_date)
    ):
        construction_scenario_anchor = resolve_scenario_anchor_rates(
            floor_bid_rate=floor_bid_rate,
            ceiling_bid_rate=ceiling_bid_rate,
            offsets=config.construction_scenario_floor_offsets,
        )
    return GuardrailContext(
        normalized_legal_floor_bid_rate=normalized_legal_floor_bid_rate,
        floor_guardrail_source=floor_guardrail_source,
        floor_bid_rate=floor_bid_rate,
        safe_floor_bid_rate=safe_floor_bid_rate,
        ceiling_bid_rate=ceiling_bid_rate,
        floor_price=_guardrail_price(budget, floor_bid_rate),
        safe_floor_price=_guardrail_price(budget, safe_floor_bid_rate),
        ceiling_price=_guardrail_price(budget, ceiling_bid_rate),
        floor_from_agency=floor_from_agency,
        ceiling_from_agency=ceiling_from_agency,
        agency_name=agency_name,
        construction_scenario_anchor=construction_scenario_anchor,
    )


def _guardrail_price(budget: float, bid_rate: float | None) -> float | None:
    if bid_rate is None or budget <= 0:
        return None
    return round(float(budget or 0.0) * bid_rate, 2)


def _normalize_optional_bid_rate(value: Any) -> float | None:
    """Normalize ratio or percent-like bid rates into a ratio."""
    if value in (None, ""):
        return None
    try:
        numeric = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    numeric = to_bid_rate_fraction(numeric)
    if numeric <= 0:
        return None
    return round(float(numeric), 6)


def _max_optional_rate(*rates: float | None) -> float | None:
    usable_rates = [float(rate) for rate in rates if rate is not None]
    if not usable_rates:
        return None
    return max(usable_rates)


def _resolve_floor_guardrail_source(
    configured_floor_bid_rate: float | None,
    legal_floor_bid_rate: float | None,
    effective_floor_bid_rate: float | None,
) -> str | None:
    if effective_floor_bid_rate is None:
        return None
    if legal_floor_bid_rate is not None and configured_floor_bid_rate is not None:
        if abs(legal_floor_bid_rate - configured_floor_bid_rate) <= 1e-9:
            return "category_and_legal"
        if legal_floor_bid_rate >= effective_floor_bid_rate - 1e-9:
            return "legal"
        return "category"
    if legal_floor_bid_rate is not None:
        return "legal"
    return "category"


def _floor_guardrail_label(source: str | None) -> str:
    if source == "legal":
        return "공고별 법정 최소 투찰률"
    if source == "category_and_legal":
        return "공고별 법정/업종별 최소 투찰률"
    return "업종별 최소 투찰률"


def _resolve_agency_bid_rate(agency_name: str | None, rate_map: dict[str, float] | None) -> float | None:
    """Backward-compatible shim; delegates to the pure guardrail core.

    Preserved so external importers (paper_bidding_backtest, tests) keep the
    ``app.ai.price_prediction`` symbol. The resolution itself lives in
    :mod:`app.ai.guardrail_core`.
    """
    return guardrail_core.resolve_agency_bid_rate(agency_name, rate_map)


def _resolve_floor_bid_rate(
    category: str | None,
    business_group: str | None = None,
    agency_name: str | None = None,
) -> float | None:
    """Backward-compatible shim; delegates to the pure guardrail core."""
    config = GuardrailConfig.from_settings(settings)
    return guardrail_core.resolve_floor_bid_rate(
        config, category, business_group=business_group, agency_name=agency_name
    )


def _resolve_ceiling_bid_rate(
    category: str | None,
    business_group: str | None = None,
    agency_name: str | None = None,
) -> float | None:
    """Backward-compatible shim; delegates to the pure guardrail core."""
    config = GuardrailConfig.from_settings(settings)
    return guardrail_core.resolve_ceiling_bid_rate(
        config, category, business_group=business_group, agency_name=agency_name
    )
