"""Competitive base-rate selection and high-rate tail adjustment."""

from __future__ import annotations

from typing import Any

from app.ai.predictors.blend_tables import (
    COMPETITIVE_SAMPLE_TIER_DEEP,
    COMPETITIVE_SAMPLE_TIER_MODERATE,
    COMPETITIVE_SAMPLE_TIER_SPARSE,
    CONSTRUCTION_GROUP_DEEP_BLEND,
    CONSTRUCTION_SMALL_BUDGET_REASON,
    GOODS_HIGH_RATE_TAIL_RULE,
    HIGH_RATE_EXCLUDED_BANDS,
    HIGH_RATE_MIN_LIFT_EPSILON,
    HIGH_RATE_MIN_RECENT_SAMPLE_SIZE,
    HIGH_RATE_MIN_SAMPLE_SIZE,
    MINIMAL_SAMPLE_BLEND,
    MODERATE_SAMPLE_BLEND,
    NOGROUP_DEEP_FALLBACK_BLEND,
    PRESERVE_HISTORICAL_COMPONENT_REASON,
    SERVICE_GROUP_DEEP_BLEND,
    SERVICE_HIGH_NEGOTIATED_REASON,
    SERVICE_HIGH_NEGOTIATED_TARGET_RATE,
    SERVICE_HIGH_RATE_TAIL_RULE,
    SPARSE_SAMPLE_BLEND,
)
from app.ai.predictors.historical.procurement_bands import (
    apply_procurement_rate_band,
    resolve_procurement_rate_band,
)
from app.ai.predictors.historical.reserve import blend_reserve_prior
from app.ai.predictors.historical.statistics import normalize_category_key
from app.ai.predictors.rate_band_spec import (
    GROUP_BRANCH_BASE_BANDS,
    apply_band_to_base,
)


def select_competitive_base_rate(
    *,
    category: str,
    description: str,
    sample_size: int,
    mean_rate: float,
    median_rate: float,
    recent_median_rate: float,
    competitive_quantile_rate: float,
    heuristic_rate: float,
    business_group: str | None = None,
    reserve_context: dict[str, Any] | None = None,
) -> float:
    """Choose the bidding target, avoiding heuristic drag when history is deep.

    When ``reserve_context`` (복수예비가격 mechanism statistics) is supplied and
    usable, the reserve-implied bid rate is blended into the statistical base
    rate as a small prior. The blend stays UPSTREAM of
    ``apply_procurement_rate_band`` and the downstream clamp/guardrail, so it can
    only nudge the target toward the reserve-implied winning line and can never
    push the recommendation below the category bidding floor.

    ``reserve_context=None`` reproduces the legacy output byte-for-byte.
    """
    normalized_category = normalize_category_key(category)
    robust_median = median_rate or mean_rate
    recent_target = recent_median_rate or robust_median
    quantile_target = competitive_quantile_rate or robust_median

    if business_group == "construction" and sample_size >= COMPETITIVE_SAMPLE_TIER_DEEP:
        base_rate = (
            (recent_target * CONSTRUCTION_GROUP_DEEP_BLEND.w0)
            + (robust_median * CONSTRUCTION_GROUP_DEEP_BLEND.w1)
            + (heuristic_rate * CONSTRUCTION_GROUP_DEEP_BLEND.w2)
        )
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        rate_band = resolve_procurement_rate_band(category=category, description=description)
        return apply_band_to_base(base_rate, rate_band, only_bands=GROUP_BRANCH_BASE_BANDS)
    if business_group == "service" and sample_size >= COMPETITIVE_SAMPLE_TIER_DEEP:
        base_rate = (
            (quantile_target * SERVICE_GROUP_DEEP_BLEND.w0)
            + (robust_median * SERVICE_GROUP_DEEP_BLEND.w1)
            + (heuristic_rate * SERVICE_GROUP_DEEP_BLEND.w2)
        )
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        rate_band = resolve_procurement_rate_band(category=category, description=description)
        return apply_band_to_base(base_rate, rate_band, only_bands=GROUP_BRANCH_BASE_BANDS)

    if sample_size >= COMPETITIVE_SAMPLE_TIER_DEEP:
        if normalized_category in {"service", "technical-service", "general-service"}:
            base_rate = recent_target
            base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
            return apply_procurement_rate_band(base_rate, category=category, description=description)
        if normalized_category == "construction":
            base_rate = quantile_target
            base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
            return apply_procurement_rate_band(base_rate, category=category, description=description)
        base_rate = (
            (robust_median * NOGROUP_DEEP_FALLBACK_BLEND.w0)
            + (recent_target * NOGROUP_DEEP_FALLBACK_BLEND.w1)
            + (mean_rate * NOGROUP_DEEP_FALLBACK_BLEND.w2)
        )
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    if sample_size >= COMPETITIVE_SAMPLE_TIER_MODERATE:
        base_rate = (
            (robust_median * MODERATE_SAMPLE_BLEND.w0)
            + (mean_rate * MODERATE_SAMPLE_BLEND.w1)
            + (heuristic_rate * MODERATE_SAMPLE_BLEND.w2)
        )
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    if sample_size >= COMPETITIVE_SAMPLE_TIER_SPARSE:
        base_rate = (
            (robust_median * SPARSE_SAMPLE_BLEND.w0)
            + (mean_rate * SPARSE_SAMPLE_BLEND.w1)
            + (heuristic_rate * SPARSE_SAMPLE_BLEND.w2)
        )
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    base_rate = (mean_rate * MINIMAL_SAMPLE_BLEND.w0) + (
        heuristic_rate * MINIMAL_SAMPLE_BLEND.w1
    )
    base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
    return apply_procurement_rate_band(base_rate, category=category, description=description)


def apply_high_rate_distribution_adjustment(
    base_rate: float,
    *,
    category: str,
    description: str,
    historical_summary: dict[str, Any],
    business_group: str | None = None,
    budget: float | None = None,
    historical_rate: float | None = None,
) -> tuple[float, dict[str, Any] | None]:
    """Lift base recommendations when recent settled history is high-rate heavy.

    This adjustment is intentionally upstream of final guardrails. Category/group
    ceilings still cap the result, but the recommended/base scenario no longer
    stays below the visible high-rate cluster while only the aggressive scenario
    reaches it.
    """
    from app.core.config import settings

    if not settings.PREDICTION_HIGH_RATE_TAIL_ADJUSTMENT_ENABLED:
        return base_rate, None

    sample_size = int(historical_summary.get("sample_size", 0) or 0)
    recent_sample_size = int(historical_summary.get("recent_sample_size", 0) or 0)
    if (
        sample_size < HIGH_RATE_MIN_SAMPLE_SIZE
        or recent_sample_size < HIGH_RATE_MIN_RECENT_SAMPLE_SIZE
    ):
        return base_rate, None

    normalized_category = normalize_category_key(category)
    group = business_group or normalized_category
    rate_band = resolve_procurement_rate_band(category=category, description=description)
    if rate_band in HIGH_RATE_EXCLUDED_BANDS:
        return base_rate, None
    if rate_band == "service_high_negotiated":
        adjusted_rate = max(base_rate, SERVICE_HIGH_NEGOTIATED_TARGET_RATE)
        return adjusted_rate, build_high_rate_adjustment_context(
            group=group,
            reason=SERVICE_HIGH_NEGOTIATED_REASON,
            original_rate=base_rate,
            adjusted_rate=adjusted_rate,
            historical_summary=historical_summary,
        )

    recent_median_rate = float(historical_summary.get("recent_median_bid_rate", 0.0) or 0.0)
    recent_upper_rate = float(historical_summary.get("recent_upper_quantile_bid_rate", 0.0) or 0.0)
    upper_rate = float(historical_summary.get("upper_quantile_bid_rate", 0.0) or 0.0)
    recent_ge_93_share = float(historical_summary.get("recent_rate_ge_0_93_share", 0.0) or 0.0)
    recent_ge_95_share = float(historical_summary.get("recent_rate_ge_0_95_share", 0.0) or 0.0)
    recent_ge_98_share = float(historical_summary.get("recent_rate_ge_0_98_share", 0.0) or 0.0)
    high_rate_anchor = max(recent_median_rate, recent_upper_rate, upper_rate)
    candidate_base_rate = max(float(base_rate or 0.0), float(historical_rate or 0.0))

    reason: str | None = None
    target_rate: float | None = None
    goods_rule = GOODS_HIGH_RATE_TAIL_RULE
    service_rule = SERVICE_HIGH_RATE_TAIL_RULE
    if group == "goods" and (
        recent_ge_95_share >= goods_rule.recent_ge_95_share_min
        or recent_ge_98_share >= goods_rule.recent_ge_98_share_min
        or recent_upper_rate >= goods_rule.recent_upper_rate_min
    ):
        target_rate = (
            (candidate_base_rate * goods_rule.candidate_weight)
            + (max(recent_median_rate, upper_rate) * goods_rule.mid_weight)
            + (high_rate_anchor * goods_rule.anchor_weight)
        )
        reason = goods_rule.reason
    elif group == "service" and (
        recent_median_rate >= service_rule.recent_median_min
        and (
            recent_ge_93_share >= service_rule.recent_ge_93_share_min
            or recent_ge_95_share >= service_rule.recent_ge_95_share_min
        )
    ):
        service_tail_anchor = min(
            high_rate_anchor, recent_median_rate + service_rule.tail_anchor_offset
        )
        target_rate = (
            (candidate_base_rate * service_rule.candidate_weight)
            + (recent_median_rate * service_rule.recent_median_weight)
            + (service_tail_anchor * service_rule.tail_anchor_weight)
        )
        reason = service_rule.reason
    elif group == "construction":
        budget_value = float(budget or 0.0)
        small_budget_limit = float(settings.PREDICTION_SMALL_BUDGET_HIGH_RATE_BUDGET_MAX or 0.0)
        small_budget_target = float(settings.PREDICTION_SMALL_BUDGET_HIGH_RATE_TARGET or 0.0)
        small_budget_min_rate = float(settings.PREDICTION_SMALL_BUDGET_HIGH_RATE_MIN_RATE or 0.0)
        if (
            small_budget_limit > 0
            and budget_value > 0
            and budget_value <= small_budget_limit
            and candidate_base_rate >= small_budget_min_rate
            and small_budget_target > candidate_base_rate
        ):
            target_rate = small_budget_target
            reason = CONSTRUCTION_SMALL_BUDGET_REASON

    if target_rate is None or reason is None:
        return candidate_base_rate, None if candidate_base_rate == base_rate else build_high_rate_adjustment_context(
            group=group,
            reason=PRESERVE_HISTORICAL_COMPONENT_REASON,
            original_rate=base_rate,
            adjusted_rate=candidate_base_rate,
            historical_summary=historical_summary,
        )

    adjusted_rate = max(candidate_base_rate, target_rate)
    if adjusted_rate <= float(base_rate or 0.0) + HIGH_RATE_MIN_LIFT_EPSILON:
        return base_rate, None

    return adjusted_rate, build_high_rate_adjustment_context(
        group=group,
        reason=reason,
        original_rate=base_rate,
        adjusted_rate=adjusted_rate,
        historical_summary=historical_summary,
    )


def build_high_rate_adjustment_context(
    *,
    group: str | None,
    reason: str,
    original_rate: float,
    adjusted_rate: float,
    historical_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build compact metadata for high-rate tail adjustments."""
    return {
        "reason": reason,
        "business_group": group,
        "original_bid_rate": round(float(original_rate or 0.0), 6),
        "adjusted_bid_rate": round(float(adjusted_rate or 0.0), 6),
        "recent_sample_size": int(historical_summary.get("recent_sample_size", 0) or 0),
        "recent_median_bid_rate": round(float(historical_summary.get("recent_median_bid_rate", 0.0) or 0.0), 6),
        "recent_upper_quantile_bid_rate": round(float(historical_summary.get("recent_upper_quantile_bid_rate", 0.0) or 0.0), 6),
        "recent_rate_ge_0_93_share": round(float(historical_summary.get("recent_rate_ge_0_93_share", 0.0) or 0.0), 4),
        "recent_rate_ge_0_95_share": round(float(historical_summary.get("recent_rate_ge_0_95_share", 0.0) or 0.0), 4),
        "recent_rate_ge_0_98_share": round(float(historical_summary.get("recent_rate_ge_0_98_share", 0.0) or 0.0), 4),
    }
