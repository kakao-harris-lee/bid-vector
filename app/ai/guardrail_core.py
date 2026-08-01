"""Pure guardrail computation core (I/O-free).

§4.7.4 (I/O ↔ 계산 분리): every ``settings.PREDICTION_*`` value that the price
guardrails need is collected ONCE, at the boundary, into :class:`GuardrailConfig`
via :meth:`GuardrailConfig.from_settings`. The functions below then perform only
arithmetic on that frozen config plus the prediction payloads — they never read
``settings`` themselves.

Bypass note (predictor guardrail 우회 금지): these helpers RESOLVE the
category/group/agency floor & ceiling and ROUND prices; they do not clamp the
final prediction. The single guardrail gate is still
``app.ai.price_prediction._apply_prediction_guardrails`` — this module is only
reached from there (and from ``_apply_bid_price_granularity``). Exposing the pure
core as public does NOT add a path that returns a value below the category floor;
callers must still go through the guardrail entry point to clamp a prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from app.ai.predictors.legal_floor_spec import (
    resolve_construction_qualification_floor,
)
from app.domain.basis_conversion import (
    convert_yega_band_to_base,
    resolve_agency_bid_rate,
    # re-exported for existing callers/tests that import it from guardrail_core
    resolve_band_assessment_rate as resolve_band_assessment_rate,
)
from app.utils.numeric import optional_float


_CATEGORY_FLOOR_RATE_ALIASES = {
    "general-service": "service",
    "service": "service",
    "일반용역": "service",
    "technical-service": "technical-service",
    "기술용역": "technical-service",
    "goods": "goods",
    "물품": "goods",
    "construction": "construction",
    "공사": "construction",
    "software": "software",
    "소프트웨어": "software",
}


@dataclass(frozen=True)
class GuardrailConfig:
    """Boundary snapshot of the settings the price guardrails read.

    ``from_settings`` applies the SAME normalization that used to sit inline in
    ``price_prediction`` (``int(... or 0)`` / ``max(0.0, float(... or 0.0))`` /
    ``str(... or "floor").strip().lower()``) so the pure core downstream can use
    the fields verbatim.
    """

    bid_price_granularity: int
    bid_price_granularity_min_budget: float
    bid_price_rounding_mode: str
    floor_safety_margin_rate: float
    business_group_calibration_enabled: bool
    category_minimum_bid_rates: Mapping[str, float]
    group_minimum_bid_rates: Mapping[str, float]
    default_minimum_bid_rate: float
    agency_minimum_bid_rates: Mapping[str, float] | None
    category_maximum_bid_rates: Mapping[str, float]
    group_maximum_bid_rates: Mapping[str, float]
    default_maximum_bid_rate: float
    agency_maximum_bid_rates: Mapping[str, float] | None
    agency_band_assessment_rates: Mapping[str, float] | None
    default_band_assessment_rate: float
    # Scenario-anchor calibration (read by price_prediction, not by the floor/ceiling
    # resolvers here). Defaulted so manual GuardrailConfig(...) construction stays
    # backward-compatible; from_settings always supplies it.
    construction_scenario_floor_offsets: Mapping[str, float] | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "GuardrailConfig":
        """Collect every guardrail-relevant setting read at the boundary once."""
        return cls(
            bid_price_granularity=int(settings.PREDICTION_BID_PRICE_GRANULARITY or 0),
            bid_price_granularity_min_budget=float(
                settings.PREDICTION_BID_PRICE_GRANULARITY_MIN_BUDGET or 0.0
            ),
            bid_price_rounding_mode=str(
                settings.PREDICTION_BID_PRICE_ROUNDING_MODE or "floor"
            ).strip().lower(),
            floor_safety_margin_rate=max(
                0.0, float(settings.PREDICTION_FLOOR_SAFETY_MARGIN_RATE or 0.0)
            ),
            business_group_calibration_enabled=settings.BUSINESS_GROUP_CALIBRATION_ENABLED,
            category_minimum_bid_rates=settings.PREDICTION_CATEGORY_MINIMUM_BID_RATES or {},
            group_minimum_bid_rates=settings.PREDICTION_GROUP_MINIMUM_BID_RATES or {},
            default_minimum_bid_rate=max(
                0.0, float(settings.PREDICTION_DEFAULT_MINIMUM_BID_RATE or 0.0)
            ),
            agency_minimum_bid_rates=settings.PREDICTION_AGENCY_MINIMUM_BID_RATES,
            category_maximum_bid_rates=settings.PREDICTION_CATEGORY_MAXIMUM_BID_RATES or {},
            group_maximum_bid_rates=settings.PREDICTION_GROUP_MAXIMUM_BID_RATES or {},
            default_maximum_bid_rate=max(
                0.0, float(settings.PREDICTION_DEFAULT_MAXIMUM_BID_RATE or 0.0)
            ),
            agency_maximum_bid_rates=settings.PREDICTION_AGENCY_MAXIMUM_BID_RATES,
            agency_band_assessment_rates=settings.PREDICTION_AGENCY_BAND_ASSESSMENT_RATES,
            default_band_assessment_rate=max(
                0.0, float(settings.PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE or 0.0)
            ),
            construction_scenario_floor_offsets=(
                settings.PREDICTION_CONSTRUCTION_SCENARIO_FLOOR_OFFSETS or None
            ),
        )


def normalize_category_key(value: Any) -> str:
    """Normalize category labels for configuration lookups."""
    normalized_value = str(value or "").strip().lower()
    return _CATEGORY_FLOOR_RATE_ALIASES.get(normalized_value, normalized_value)


def resolve_floor_bid_rate(
    config: GuardrailConfig,
    category: str | None,
    business_group: str | None = None,
    agency_name: str | None = None,
    *,
    estimation_amount: float | None = None,
    reference_date: date | datetime | None = None,
) -> float | None:
    """Resolve a configured minimum bid-rate floor for the given category/group/agency.

    §4.7 guardrail: when both a group rate and a category rate exist, the group
    rate can never be LOWER than the category floor — the category floor is the
    hard lower bound.  Return max(group_rate, category_rate).

    Construction legal tier (2026-01-30 +2%p): for the ``construction`` category the
    national 적격심사 낙찰하한 depends on the notice 추정가격 (estimation_amount) and its
    effective date (reference_date). The 예정가-basis tier value is converted to a
    기초금액 basis via E[사정률] (resolve_band_assessment_rate — 1.0 default is a no-op)
    and then folded in with ``max()``. Because it is ONLY ever a ``max()`` term, the
    tier can never LOWER the floor: for every input the result is
    ``max(existing floor, tier)`` ≥ the existing floor (red line — see
    tests/test_guardrail_legal_floor.py). The tier is skipped (no change) unless the
    category is construction AND both estimation_amount and reference_date resolve.

    Agency band (Lever 1): an agency-keyed floor layers on top and TIGHTENS the
    band by RAISING the floor (max wins), clamped to the hard clamp_bid_rate
    [0.7, 1.4] bounds.  Non-matching agencies leave the category/group result
    unchanged.
    """
    configured_floor_rates = config.category_minimum_bid_rates or {}
    normalized_category = normalize_category_key(category)
    category_rate: float | None = None
    for raw_category, raw_floor_rate in configured_floor_rates.items():
        if normalize_category_key(raw_category) == normalized_category:
            category_rate = max(0.0, float(raw_floor_rate or 0.0))
            break

    resolved_floor: float | None = None
    if business_group and config.business_group_calibration_enabled:
        group_rates = config.group_minimum_bid_rates or {}
        if business_group in group_rates:
            group_rate = float(group_rates[business_group])
            # Group floor must never undercut category floor (§4.7).
            resolved_floor = max(group_rate, category_rate) if category_rate is not None else group_rate

    if resolved_floor is None:
        if category_rate is not None:
            resolved_floor = category_rate
        else:
            default_floor_rate = config.default_minimum_bid_rate
            resolved_floor = default_floor_rate or None

    # Construction legal 낙찰하한 tier (구간·시행일 인지). Fold in with max() so it can
    # only RAISE the floor — the existing category/group flat still hard-bounds via
    # max(), preserving the red line for every input (incl. old-rate tiers < 0.87).
    if normalized_category == "construction":
        tier_floor = resolve_construction_qualification_floor(estimation_amount, reference_date)
        if tier_floor is not None:
            # 예정가-basis tier → 기초금액 basis via E[사정률] (same conversion as the
            # agency band edge; 1.0 default is a no-op; clamp keeps it in [0.7, 1.4]).
            # Single-source: basis_conversion.convert_yega_band_to_base.
            tier_floor = convert_yega_band_to_base(tier_floor, agency_name, config)
            resolved_floor = (
                max(resolved_floor, tier_floor) if resolved_floor is not None else tier_floor
            )

    agency_floor = resolve_agency_bid_rate(agency_name, config.agency_minimum_bid_rates)
    if agency_floor is not None:
        # Convert the 예정가-basis band edge to a 기초금액 basis (E[사정률]) with the hard
        # clamp, then TIGHTEN by RAISING the floor (max wins). The category/group floor
        # still hard-bounds via max(), so the conversion can never undercut it.
        agency_floor = convert_yega_band_to_base(agency_floor, agency_name, config)
        return max(resolved_floor, agency_floor) if resolved_floor is not None else agency_floor

    return resolved_floor


def resolve_ceiling_bid_rate(
    config: GuardrailConfig,
    category: str | None,
    business_group: str | None = None,
    agency_name: str | None = None,
) -> float | None:
    """Resolve a configured maximum bid-rate ceiling for the given category/group/agency.

    §4.7 guardrail: when both a group rate and a category rate exist, the group
    ceiling can never be HIGHER than the category ceiling — the category ceiling
    is the hard upper bound.  Return min(group_rate, category_rate).

    Agency band (Lever 1): an agency-keyed ceiling layers on top and TIGHTENS the
    band by LOWERING the ceiling (min wins), clamped to the hard clamp_bid_rate
    [0.7, 1.4] bounds.  Non-matching agencies leave the category/group result
    unchanged.
    """
    configured_ceiling_rates = config.category_maximum_bid_rates or {}
    normalized_category = normalize_category_key(category)
    category_rate: float | None = None
    for raw_category, raw_ceiling_rate in configured_ceiling_rates.items():
        if normalize_category_key(raw_category) == normalized_category:
            ceiling_rate = max(0.0, float(raw_ceiling_rate or 0.0))
            category_rate = ceiling_rate or None
            break

    resolved_ceiling: float | None = None
    if business_group and config.business_group_calibration_enabled:
        group_rates = config.group_maximum_bid_rates or {}
        if business_group in group_rates:
            group_rate = float(group_rates[business_group])
            # Group ceiling must never exceed category ceiling (§4.7).
            resolved_ceiling = min(group_rate, category_rate) if category_rate is not None else group_rate

    if resolved_ceiling is None:
        if category_rate is not None:
            resolved_ceiling = category_rate
        else:
            default_ceiling_rate = config.default_maximum_bid_rate
            resolved_ceiling = default_ceiling_rate or None

    agency_ceiling = resolve_agency_bid_rate(agency_name, config.agency_maximum_bid_rates)
    if agency_ceiling is not None:
        # Same 예정가→기초금액 conversion (E[사정률]) as the floor edge, so the whole band
        # shifts uniformly and keeps its calibrated width. TIGHTEN by LOWERING the
        # ceiling (min wins).
        agency_ceiling = convert_yega_band_to_base(agency_ceiling, agency_name, config)
        return min(resolved_ceiling, agency_ceiling) if resolved_ceiling is not None else agency_ceiling

    return resolved_ceiling


def resolve_safe_floor_bid_rate(
    config: GuardrailConfig,
    floor_bid_rate: float | None,
    *,
    ceiling_bid_rate: float | None,
) -> float | None:
    if floor_bid_rate is None:
        return None
    safety_margin = config.floor_safety_margin_rate
    safe_floor_bid_rate = float(floor_bid_rate) + safety_margin
    if ceiling_bid_rate is not None:
        safe_floor_bid_rate = min(safe_floor_bid_rate, float(ceiling_bid_rate))
    return max(float(floor_bid_rate), safe_floor_bid_rate)


def resolve_prediction_price(payload: dict[str, Any], *, budget: float) -> float:
    price = optional_float(payload.get("predicted_price") or payload.get("price"))
    if price is not None and price > 0:
        return price
    bid_rate = optional_float(payload.get("bid_rate") or payload.get("predicted_bid_rate"))
    if bid_rate is not None and bid_rate > 0:
        return float(budget) * bid_rate
    return 0.0


def round_price_to_granularity(
    price: float,
    *,
    granularity: int,
    rounding_mode: str,
    floor_price: float | None,
    ceiling_price: float | None,
) -> float:
    safe_price = max(0.0, float(price or 0.0))
    unit = max(1, int(granularity))
    if rounding_mode == "ceil":
        rounded_price = math.ceil(safe_price / unit) * unit
    elif rounding_mode == "nearest":
        rounded_price = round(safe_price / unit) * unit
    else:
        rounded_price = math.floor(safe_price / unit) * unit

    if floor_price is not None and rounded_price < floor_price:
        rounded_price = math.ceil(float(floor_price) / unit) * unit
    if ceiling_price is not None and rounded_price > ceiling_price:
        rounded_price = math.floor(float(ceiling_price) / unit) * unit
    return float(max(0, rounded_price))


def round_candidate_price_to_granularity(
    candidate: dict[str, Any],
    *,
    budget: float,
    granularity: int,
    rounding_mode: str,
    floor_price: float | None,
    ceiling_price: float | None,
) -> dict[str, Any]:
    original_price = resolve_prediction_price(candidate, budget=budget)
    rounded_price = round_price_to_granularity(
        original_price,
        granularity=granularity,
        rounding_mode=rounding_mode,
        floor_price=floor_price,
        ceiling_price=ceiling_price,
    )
    changed = abs(rounded_price - original_price) > 1e-9
    rounded_candidate = {
        **candidate,
        "bid_rate": round(rounded_price / budget, 6),
        "predicted_price": rounded_price,
        "price_granularity_applied": changed,
        "pre_granularity_price": round(original_price, 2) if changed else None,
    }
    return rounded_candidate


def apply_bid_price_granularity(
    prediction: dict[str, Any],
    *,
    budget: float,
    config: GuardrailConfig,
) -> dict[str, Any]:
    """Round final bid prices to an operator-facing currency unit.

    Contract (투찰가 정수 정합): the system's final bid price must be an integer 원
    amount the operator can submit to KONEPS verbatim — a fractional 투찰가 (e.g.
    77_529_785.2) must never leave the predictor. The configured currency
    granularity applies only when ``granularity > 1 AND budget >= min_budget``;
    outside that window we still quantize to the 1원 unit so the integer guarantee
    holds for every price. ``budget <= 0`` is the ONLY early return, because the
    bid-rate below divides by ``budget`` (a non-positive budget has no meaningful
    quantization). The floor/ceiling clamp inside ``round_price_to_granularity`` is
    unchanged (guardrail floor 보호 우회 금지) — it applies identically at unit=1.
    """
    granularity = config.bid_price_granularity
    min_budget = config.bid_price_granularity_min_budget
    if budget <= 0:
        return prediction

    # Effective currency unit: the configured granularity when it applies, else the
    # 1원 unit. round_*_to_granularity floor unit to max(1, int(...)), so unit=1 is a
    # plain integer-원 snap while preserving the same floor/ceiling protection.
    unit = granularity if (granularity > 1 and budget >= min_budget) else 1

    rounded_prediction = dict(prediction)
    rounding_mode = config.bid_price_rounding_mode
    floor_price = optional_float(prediction.get("safe_floor_price") or prediction.get("floor_price"))
    ceiling_price = optional_float(prediction.get("ceiling_price"))

    rounded_candidates: list[dict[str, Any]] = []
    for candidate in prediction.get("bid_rate_candidates", []):
        rounded_candidates.append(
            round_candidate_price_to_granularity(
                candidate,
                budget=budget,
                granularity=unit,
                rounding_mode=rounding_mode,
                floor_price=floor_price,
                ceiling_price=ceiling_price,
            )
        )

    if rounded_candidates:
        base_candidate = next(
            (candidate for candidate in rounded_candidates if candidate.get("label") == "base"),
            rounded_candidates[0],
        )
        rounded_prediction["bid_rate_candidates"] = rounded_candidates
        rounded_prediction["predicted_price"] = round(float(base_candidate.get("predicted_price", 0.0) or 0.0), 2)
        rounded_prediction["predicted_bid_rate"] = round(float(base_candidate.get("bid_rate", 0.0) or 0.0), 6)
        rounded_prediction["price_range_min"] = min(float(candidate["predicted_price"]) for candidate in rounded_candidates)
        rounded_prediction["price_range_max"] = max(float(candidate["predicted_price"]) for candidate in rounded_candidates)
        applied = any(candidate.get("price_granularity_applied") for candidate in rounded_candidates)
    else:
        original_price = resolve_prediction_price(prediction, budget=budget)
        rounded_price = round_price_to_granularity(
            original_price,
            granularity=unit,
            rounding_mode=rounding_mode,
            floor_price=floor_price,
            ceiling_price=ceiling_price,
        )
        rounded_prediction["predicted_price"] = rounded_price
        rounded_prediction["predicted_bid_rate"] = round(rounded_price / budget, 6)
        rounded_prediction["price_range_min"] = min(
            rounded_price,
            round_price_to_granularity(
                optional_float(prediction.get("price_range_min")) or rounded_price,
                granularity=unit,
                rounding_mode=rounding_mode,
                floor_price=floor_price,
                ceiling_price=ceiling_price,
            ),
        )
        rounded_prediction["price_range_max"] = max(
            rounded_price,
            round_price_to_granularity(
                optional_float(prediction.get("price_range_max")) or rounded_price,
                granularity=unit,
                rounding_mode=rounding_mode,
                floor_price=floor_price,
                ceiling_price=ceiling_price,
            ),
        )
        applied = abs(rounded_price - original_price) > 1e-9

    rounded_prediction["bid_price_granularity"] = unit
    rounded_prediction["bid_price_rounding_mode"] = rounding_mode
    rounded_prediction["price_granularity_applied"] = bool(applied)
    return rounded_prediction
