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
from typing import Any, Mapping

from app.ai.predictors.historical import clamp_bid_rate, normalize_agency_name


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
        )


def normalize_category_key(value: Any) -> str:
    """Normalize category labels for configuration lookups."""
    normalized_value = str(value or "").strip().lower()
    return _CATEGORY_FLOOR_RATE_ALIASES.get(normalized_value, normalized_value)


def resolve_agency_bid_rate(agency_name: str | None, rate_map: dict[str, float] | None) -> float | None:
    """Look up an agency-keyed bid-rate band via normalized substring match.

    Keys are normalized agency tokens (whitespace-stripped, lowercased — see
    normalize_agency_name). A notice's issuing agency matches a key when the
    normalized key is a substring of the normalized agency name, so regional
    bureaus inherit the headquarters band (e.g. "한국수산자원공단동해본부" matches
    the "한국수산자원공단" key). When several keys match, the most specific
    (longest) key wins.
    """
    if not agency_name or not rate_map:
        return None
    normalized_agency = normalize_agency_name(agency_name)
    if not normalized_agency:
        return None
    best_rate: float | None = None
    best_key_len = -1
    for raw_key, raw_rate in rate_map.items():
        normalized_key = normalize_agency_name(raw_key)
        if not normalized_key or normalized_key not in normalized_agency:
            continue
        if len(normalized_key) > best_key_len:
            best_key_len = len(normalized_key)
            best_rate = max(0.0, float(raw_rate or 0.0))
    return best_rate


def resolve_band_assessment_rate(
    agency_name: str | None,
    config: GuardrailConfig,
) -> float:
    """E[예정가/기초금액] — converts a 예정가-basis agency band to a 기초금액 basis.

    The agency bands (PREDICTION_AGENCY_*_BID_RATES) were calibrated as 낙찰가/예정가,
    but the guardrail multiplies them by the notice 사업금액(기초금액, #162). 예정가 is a
    few tenths of a percent BELOW 기초금액, so a 예정가-basis rate applied to 기초금액
    lands ~+0.5%p too high. Multiply the agency band by E[사정률] (< 1) to recover the
    intended 예정가-basis target.

    Resolution mirrors the band lookup: per-agency empirical rate via normalized
    substring match, else the global default (1.0 == no-op). Any missing / non-positive
    value collapses to 1.0. With the shipped rates (all ≤ 1) the conversion only LOWERS
    the band; a configured 사정률 > 1 is legitimate (복수예비가격 추첨 can put 예정가
    ABOVE 기초금액 for some agencies) and would raise it — either way the red line is
    unaffected: resolve_floor_bid_rate re-applies max(category/group floor, converted
    agency floor) plus the legal 낙찰하한, so no value here can undercut the hard floor.
    """
    rate = resolve_agency_bid_rate(agency_name, config.agency_band_assessment_rates)
    if rate is not None and rate > 0:
        return rate
    default_rate = config.default_band_assessment_rate
    return default_rate if default_rate and default_rate > 0 else 1.0


def resolve_floor_bid_rate(
    config: GuardrailConfig,
    category: str | None,
    business_group: str | None = None,
    agency_name: str | None = None,
) -> float | None:
    """Resolve a configured minimum bid-rate floor for the given category/group/agency.

    §4.7 guardrail: when both a group rate and a category rate exist, the group
    rate can never be LOWER than the category floor — the category floor is the
    hard lower bound.  Return max(group_rate, category_rate).

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

    agency_floor = resolve_agency_bid_rate(agency_name, config.agency_minimum_bid_rates)
    if agency_floor is not None:
        # Convert the 예정가-basis band edge to a 기초금액 basis (E[사정률]) BEFORE the
        # hard clamp, then TIGHTEN by RAISING the floor (max wins). The category/group
        # floor still hard-bounds via max(), so the conversion can never undercut it.
        agency_floor = clamp_bid_rate(
            agency_floor * resolve_band_assessment_rate(agency_name, config)
        )
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
        agency_ceiling = clamp_bid_rate(
            agency_ceiling * resolve_band_assessment_rate(agency_name, config)
        )
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


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    """Round final bid prices to an operator-facing currency unit."""
    granularity = config.bid_price_granularity
    min_budget = config.bid_price_granularity_min_budget
    if granularity <= 1 or budget <= 0 or budget < min_budget:
        return prediction

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
                granularity=granularity,
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
            granularity=granularity,
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
                granularity=granularity,
                rounding_mode=rounding_mode,
                floor_price=floor_price,
                ceiling_price=ceiling_price,
            ),
        )
        rounded_prediction["price_range_max"] = max(
            rounded_price,
            round_price_to_granularity(
                optional_float(prediction.get("price_range_max")) or rounded_price,
                granularity=granularity,
                rounding_mode=rounding_mode,
                floor_price=floor_price,
                ceiling_price=ceiling_price,
            ),
        )
        applied = abs(rounded_price - original_price) > 1e-9

    rounded_prediction["bid_price_granularity"] = granularity
    rounded_prediction["bid_price_rounding_mode"] = rounding_mode
    rounded_prediction["price_granularity_applied"] = bool(applied)
    return rounded_prediction
