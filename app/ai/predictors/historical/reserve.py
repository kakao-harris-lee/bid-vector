"""Reserve-price (복수예비가격) prior blending and pattern context."""

from __future__ import annotations

from typing import Any

import numpy as np


def resolve_reserve_prior_rate(reserve_context: dict[str, Any] | None) -> tuple[float, int]:
    """Extract the reserve-implied bid rate (in bid/budget units) and its count.

    The reserve statistics expose two ratios in DIFFERENT denominators:

    * ``median_bid_to_estimated_price_rate`` — bid / 예가(예정가격), i.e. bid over
      the selected reserve-price mean.
    * ``median_estimated_price_rate`` — 예가 / budget(기초금액), i.e. the 예가율.

    ``base_rate``, the category floor, and ``budget × base_rate`` are all bid /
    budget. Blending the bid/예가 ratio directly would over-bid systematically
    because 예가 < budget (예가율 < 1) on KONEPS. We therefore convert back to
    bid/budget units before use::

        implied_bid_to_budget = (bid / 예가) × (예가 / budget) = bid / budget
                              = median_bid_to_estimated_price_rate
                                × median_estimated_price_rate

    Returns ``(0.0, 0)`` — which signals callers to leave the statistical base
    rate untouched — when the context is missing, non-numeric, lacks usable
    evidence, or the converted rate falls outside a realistic bidding band.
    """
    if not isinstance(reserve_context, dict):
        return 0.0, 0

    # Harden against non-numeric payloads: any coercion failure means "no usable
    # prior", preserving the base-rate-unchanged contract.
    try:
        sample_count = int(reserve_context.get("sample_count", 0) or 0)
        estimated_price_sample_count = int(
            reserve_context.get("estimated_price_sample_count", 0) or 0
        )
        bid_to_estimated_price_rate = float(
            reserve_context.get("median_bid_to_estimated_price_rate", 0.0) or 0.0
        )
        estimated_price_rate = float(
            reserve_context.get("median_estimated_price_rate", 0.0) or 0.0
        )
    except (TypeError, ValueError):
        return 0.0, 0

    # The reserve-implied bid rate is only meaningful when we actually observed
    # selected reserve numbers (estimated_price_sample_count) AND a non-trivial
    # bid/예가 ratio.
    if estimated_price_sample_count <= 0 or bid_to_estimated_price_rate <= 0.0:
        return 0.0, 0

    # 예가율 must be present and inside the realistic 예가/budget band; without a
    # valid 예가율 we cannot convert to bid/budget, so fall back to no prior.
    if not (0.5 <= estimated_price_rate <= 1.5):
        return 0.0, 0

    # Convert bid/예가 → bid/budget so the prior shares units with base_rate.
    implied_bid_to_budget = bid_to_estimated_price_rate * estimated_price_rate

    # Validate the converted (bid/budget) rate against the realistic bidding band.
    if not (0.5 <= implied_bid_to_budget <= 1.5):
        return 0.0, 0

    # Confidence is driven by how many openings actually contributed an
    # estimated-price observation, not just the raw reserve span count.
    confidence_sample_count = min(sample_count, estimated_price_sample_count)
    return implied_bid_to_budget, max(0, confidence_sample_count)


def blend_reserve_prior(base_rate: float, *, reserve_context: dict[str, Any] | None) -> float:
    """Nudge the statistical base rate toward the reserve-implied bid rate.

    The reserve-implied rate is mixed in with a small, sample-scaled weight so
    that thin reserve evidence barely moves the target while deeper evidence pulls
    it more firmly toward the 복수예비가격-implied winning line. The blend stays
    strictly upstream of the procurement band / clamp / final category guardrail,
    so it can never lower the recommendation below the bidding floor.
    """
    from app.core.config import settings

    if reserve_context is None:
        return base_rate

    implied_rate, confidence_sample_count = resolve_reserve_prior_rate(reserve_context)
    if confidence_sample_count <= 0 or implied_rate <= 0.0:
        return base_rate

    configured_weight = max(0.0, float(settings.PREDICTION_RESERVE_PRIOR_WEIGHT or 0.0))
    if configured_weight <= 0.0:
        return base_rate

    full_confidence_samples = max(
        1, int(settings.PREDICTION_RESERVE_PRIOR_FULL_CONFIDENCE_SAMPLES or 1)
    )
    confidence_scale = min(1.0, confidence_sample_count / full_confidence_samples)
    effective_weight = min(0.5, configured_weight * confidence_scale)
    if effective_weight <= 0.0:
        return base_rate

    return (base_rate * (1.0 - effective_weight)) + (implied_rate * effective_weight)


def build_reserve_pattern_context(
    *,
    reserve_span_rates: list[float],
    estimated_price_rates: list[float],
    bid_to_estimated_price_rates: list[float],
    selected_numbers: list[int],
) -> dict[str, Any] | None:
    """Summarize reserve price and selected-number patterns from historical openings."""
    sample_count = len(reserve_span_rates)
    if sample_count == 0 and not selected_numbers and not estimated_price_rates:
        return None

    number_counts: dict[int, int] = {}
    for number in selected_numbers:
        if number < 0:
            continue
        number_counts[number] = number_counts.get(number, 0) + 1

    frequent_selected_numbers = [
        number
        for number, _count in sorted(number_counts.items(), key=lambda item: (-item[1], item[0]))[:4]
    ]

    return {
        "sample_count": sample_count,
        "average_reserve_span_rate": round(float(np.mean(reserve_span_rates)), 4) if reserve_span_rates else 0.0,
        "estimated_price_sample_count": len(estimated_price_rates),
        "average_estimated_price_rate": round(float(np.mean(estimated_price_rates)), 4) if estimated_price_rates else 0.0,
        "median_estimated_price_rate": round(float(np.median(estimated_price_rates)), 4) if estimated_price_rates else 0.0,
        "median_bid_to_estimated_price_rate": (
            round(float(np.median(bid_to_estimated_price_rates)), 4)
            if bid_to_estimated_price_rates
            else 0.0
        ),
        "average_selected_number": round(float(np.mean(selected_numbers)), 2) if selected_numbers else 0.0,
        "frequent_selected_numbers": frequent_selected_numbers,
    }
