"""Historical/statistical price predictor."""

from __future__ import annotations

import json
from math import sqrt
from pathlib import Path
from typing import Any

import numpy as np

from app.ai.predictors.base import BasePricePredictor, PricePredictionContext


def load_group_calibration() -> dict[str, dict[str, float | int]]:
    """Read summary.group_calibration from the active manifest, if present.

    Best-effort: any IO/JSON failure or missing block returns an empty dict so
    callers can safely fall through to the legacy historical statistics.
    """
    from app.core.config import settings

    manifest_path_raw = (settings.PRICE_PREDICTION_ENSEMBLE_MODEL_PATH or "").strip()
    if not manifest_path_raw:
        return {}
    candidate = Path(manifest_path_raw)
    if not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text())
    except Exception:
        return {}
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return {}
    calibration = summary.get("group_calibration")
    return calibration if isinstance(calibration, dict) else {}

_T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.16,
    14: 2.145,
    15: 2.131,
    16: 2.12,
    17: 2.11,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.08,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.06,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


class HistoricalStatisticalPredictor(BasePricePredictor):
    """Current production-grade predictor based on heuristics and historical statistics."""

    name = "historical_statistical"
    family = "statistical"

    def predict(self, context: PricePredictionContext) -> dict[str, Any]:
        """Predict a bid-rate scenario using the stable historical baseline."""
        heuristic_prediction = build_heuristic_prediction(
            budget=context.budget,
            category=context.category,
            description=context.description,
        )
        historical_summary = summarize_historical_records(
            context.historical_records,
            agency_name=context.agency_name,
        )

        if float(context.budget or 0.0) <= 0:
            return heuristic_prediction

        sample_size = int(historical_summary["sample_size"])

        if sample_size <= 0:
            calibration = load_group_calibration().get(context.business_group or "") or {}
            prior_median = calibration.get("median_rate")
            if prior_median is not None:
                prior_median = float(prior_median)
                heuristic_rate = float(heuristic_prediction.get("predicted_bid_rate") or prior_median)
                blended_rate = round((heuristic_rate * 0.4) + (prior_median * 0.6), 4)
                budget = float(context.budget or 0.0)
                heuristic_prediction = {
                    **heuristic_prediction,
                    "predicted_bid_rate": blended_rate,
                    "predicted_price": round(budget * blended_rate, 2),
                    "price_range_min": round(budget * blended_rate * 0.8, 2),
                    "price_range_max": round(budget * blended_rate * 1.2, 2),
                }
            return heuristic_prediction

        return build_historical_prediction(
            budget=float(context.budget or 0.0),
            category=context.category,
            description=context.description,
            heuristic_prediction=heuristic_prediction,
            historical_summary=historical_summary,
            business_group=context.business_group,
        )


def build_heuristic_prediction(budget: float, category: str, description: str) -> dict[str, Any]:
    """Fallback prediction when no useful historical samples exist."""
    category_multipliers = {
        "software": 1.2,
        "hardware": 1.0,
        "design": 0.9,
        "consulting": 1.1,
        "infrastructure": 1.3,
        "other": 1.0,
    }

    normalized_budget = float(budget or 0.0)
    multiplier = category_multipliers.get((category or "other").lower(), 1.0)
    complexity_factor = min(len(description or "") / 500, 1.5)
    predicted_price = normalized_budget * multiplier * (0.8 + complexity_factor)
    confidence_score = min(0.9 if len(description or "") > 200 else 0.7, 0.95)

    price_range_min = round(predicted_price * 0.8, 2)
    price_range_max = round(predicted_price * 1.2, 2)
    predicted_bid_rate = round(predicted_price / normalized_budget, 4) if normalized_budget > 0 else 0.0

    return {
        "predicted_price": round(predicted_price, 2),
        "price_range_min": price_range_min,
        "price_range_max": price_range_max,
        "confidence_score": confidence_score,
        "model_version": "v1.0",
        "pricing_mode": "heuristic",
        "historical_sample_size": 0,
        "agency_match_sample_size": 0,
        "predicted_bid_rate": predicted_bid_rate,
        "bid_rate_candidates": [
            {
                "label": "conservative",
                "bid_rate": round(price_range_min / normalized_budget, 4) if normalized_budget > 0 else 0.0,
                "predicted_price": price_range_min,
                "confidence_weight": 0.22,
            },
            {
                "label": "base",
                "bid_rate": predicted_bid_rate,
                "predicted_price": round(predicted_price, 2),
                "confidence_weight": 0.56,
            },
            {
                "label": "aggressive",
                "bid_rate": round(price_range_max / normalized_budget, 4) if normalized_budget > 0 else 0.0,
                "predicted_price": price_range_max,
                "confidence_weight": 0.22,
            },
        ],
        "reserve_price_context": None,
        "feedback_calibration": None,
        "guardrail_applied": False,
        "guardrail_reason": None,
        "floor_bid_rate": None,
        "floor_price": None,
        "explanation": "히스토리컬 데이터가 부족해 카테고리·설명 길이 기반 휴리스틱 시나리오를 사용했습니다.",
    }


def build_historical_prediction(
    *,
    budget: float,
    category: str,
    description: str,
    heuristic_prediction: dict[str, Any],
    historical_summary: dict[str, Any],
    business_group: str | None = None,
) -> dict[str, Any]:
    """Blend historical bid-rate samples with the heuristic baseline."""
    sample_size = int(historical_summary["sample_size"])
    mean_rate = float(historical_summary["mean_bid_rate"])
    median_rate = float(historical_summary["median_bid_rate"])
    recent_median_rate = float(historical_summary.get("recent_median_bid_rate", 0.0) or 0.0)
    competitive_quantile_rate = float(historical_summary.get("competitive_quantile_bid_rate", 0.0) or 0.0)
    std_rate = float(historical_summary["std_bid_rate"])
    agency_match_sample_size = int(historical_summary.get("agency_match_sample_size", 0) or 0)
    reserve_pattern = historical_summary.get("reserve_price_context")
    heuristic_rate = float(heuristic_prediction.get("predicted_bid_rate", 0.0) or 0.0)

    if sample_size == 1:
        std_rate = max(std_rate, abs(mean_rate - heuristic_rate) * 0.5, 0.015)

    t_value = get_t_critical(sample_size - 1) if sample_size > 1 else _T_CRITICAL_95[1]
    margin = t_value * (std_rate / sqrt(sample_size)) if sample_size > 1 else std_rate

    calibration = load_group_calibration().get(business_group or "") or {}
    prior_median = calibration.get("median_rate")
    if sample_size < 5 and prior_median is not None:
        prior_median = float(prior_median)
        mean_rate = (float(mean_rate or prior_median) * 0.4) + (prior_median * 0.6)
        median_rate = (float(median_rate or prior_median) * 0.4) + (prior_median * 0.6)

    base_rate = select_competitive_base_rate(
        category=category,
        description=description,
        sample_size=sample_size,
        mean_rate=mean_rate,
        median_rate=median_rate,
        recent_median_rate=recent_median_rate,
        competitive_quantile_rate=competitive_quantile_rate,
        heuristic_rate=heuristic_rate,
        business_group=business_group,
        reserve_context=reserve_pattern if isinstance(reserve_pattern, dict) else None,
    )
    rate_band = resolve_procurement_rate_band(category=category, description=description)

    spread = max(std_rate * 0.6, margin * 0.4, 0.01)
    conservative_rate = clamp_bid_rate(base_rate - spread)
    base_rate = clamp_bid_rate(base_rate)
    aggressive_rate = clamp_bid_rate(base_rate + (spread * 0.8))
    conservative_rate, base_rate, aggressive_rate = apply_procurement_candidate_band(
        conservative_rate=conservative_rate,
        base_rate=base_rate,
        aggressive_rate=aggressive_rate,
        rate_band=rate_band,
    )

    candidates = [
        {
            "label": "conservative",
            "bid_rate": round(conservative_rate, 4),
            "predicted_price": round(budget * conservative_rate, 2),
            "confidence_weight": 0.24,
        },
        {
            "label": "base",
            "bid_rate": round(base_rate, 4),
            "predicted_price": round(budget * base_rate, 2),
            "confidence_weight": 0.52,
        },
        {
            "label": "aggressive",
            "bid_rate": round(aggressive_rate, 4),
            "predicted_price": round(budget * aggressive_rate, 2),
            "confidence_weight": 0.24,
        },
    ]

    confidence_score = estimate_historical_confidence(
        sample_size=sample_size,
        std_rate=std_rate,
        margin=margin,
    )

    return {
        "predicted_price": round(budget * base_rate, 2),
        "price_range_min": min(candidate["predicted_price"] for candidate in candidates),
        "price_range_max": max(candidate["predicted_price"] for candidate in candidates),
        "confidence_score": confidence_score,
        "model_version": "v1.1-historical",
        "pricing_mode": "historical_blend",
        "historical_sample_size": sample_size,
        "agency_match_sample_size": agency_match_sample_size,
        "predicted_bid_rate": round(base_rate, 4),
        "bid_rate_candidates": candidates,
        "reserve_price_context": reserve_pattern,
        "feedback_calibration": None,
        "guardrail_applied": False,
        "guardrail_reason": None,
        "floor_bid_rate": None,
        "floor_price": None,
        "competitive_target_bid_rate": round(base_rate, 4),
        "procurement_rate_band": rate_band,
        "explanation": build_historical_explanation(
            sample_size=sample_size,
            base_rate=base_rate,
            agency_match_sample_size=agency_match_sample_size,
            reserve_pattern=reserve_pattern,
            rate_band=rate_band,
        ),
    }


def summarize_historical_records(historical_records: tuple[object, ...], *, agency_name: str | None = None) -> dict[str, Any]:
    """Extract usable bid-rate samples from historical records."""
    bid_rates: list[float] = []
    weights: list[float] = []
    agency_match_sample_size = 0
    reserve_span_rates: list[float] = []
    estimated_price_rates: list[float] = []
    bid_to_estimated_price_rates: list[float] = []
    selected_numbers: list[int] = []
    normalized_target_agency = normalize_agency_name(agency_name)

    for record in historical_records:
        raw_bid_rate = read_record_value(record, "bid_rate")
        bid_rate = float(raw_bid_rate or 0.0)
        if bid_rate <= 0:
            predicted_price = float(read_record_value(record, "predicted_price") or 0.0)
            base_amount = float(read_record_value(record, "base_amount") or 0.0)
            if predicted_price > 0 and base_amount > 0:
                bid_rate = predicted_price / base_amount

        if 0.5 <= bid_rate <= 1.5:
            bid_rates.append(bid_rate)
            weight, matched_agency = resolve_record_weight(record, normalized_target_agency)
            weights.append(weight)
            if matched_agency:
                agency_match_sample_size += 1

        reserve_prices = coerce_numeric_list(read_record_value(record, "reserve_prices"))
        base_amount = float(read_record_value(record, "base_amount") or 0.0)
        if len(reserve_prices) >= 2 and base_amount > 0:
            reserve_span_rates.append((max(reserve_prices) - min(reserve_prices)) / base_amount)
            picked_prices = [
                reserve_prices[number - 1]
                for number in coerce_integer_list(read_record_value(record, "selected_numbers"))
                if 1 <= number <= len(reserve_prices)
            ]
            if len(picked_prices) >= 2:
                estimated_price_rate = float(np.mean(picked_prices)) / base_amount
                if 0.8 <= estimated_price_rate <= 1.2:
                    estimated_price_rates.append(estimated_price_rate)
                    if 0.5 <= bid_rate <= 1.5:
                        bid_to_estimated_price_rates.append(bid_rate / estimated_price_rate)

        selected_numbers.extend(coerce_integer_list(read_record_value(record, "selected_numbers")))

    if not bid_rates:
        return {
            "sample_size": 0,
            "mean_bid_rate": 0.0,
            "median_bid_rate": 0.0,
            "recent_median_bid_rate": 0.0,
            "competitive_quantile_bid_rate": 0.0,
            "std_bid_rate": 0.0,
            "agency_match_sample_size": 0,
            "reserve_price_context": None,
        }

    weighted_mean_value = float(np.average(bid_rates, weights=weights)) if weights else float(np.mean(bid_rates))
    weighted_median_value = weighted_median(bid_rates, weights) if weights else float(np.median(bid_rates))
    weighted_std_value = weighted_std(bid_rates, weights, weighted_mean_value) if len(bid_rates) > 1 else 0.0
    recent_window_size = min(10, len(bid_rates))
    recent_median_value = weighted_median(
        bid_rates[:recent_window_size],
        weights[:recent_window_size],
    )
    competitive_quantile_value = weighted_quantile(bid_rates, weights, 0.45)

    return {
        "sample_size": len(bid_rates),
        "mean_bid_rate": weighted_mean_value,
        "median_bid_rate": weighted_median_value,
        "recent_median_bid_rate": recent_median_value,
        "competitive_quantile_bid_rate": competitive_quantile_value,
        "std_bid_rate": weighted_std_value,
        "agency_match_sample_size": agency_match_sample_size,
        "reserve_price_context": build_reserve_pattern_context(
            reserve_span_rates=reserve_span_rates,
            estimated_price_rates=estimated_price_rates,
            bid_to_estimated_price_rates=bid_to_estimated_price_rates,
            selected_numbers=selected_numbers,
        ),
    }


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

    if business_group == "construction" and sample_size >= 10:
        base_rate = (recent_target * 0.6) + (robust_median * 0.3) + (heuristic_rate * 0.1)
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        rate_band = resolve_procurement_rate_band(category=category, description=description)
        if rate_band == "service_high_negotiated":
            base_rate = max(base_rate, 1.0)
        elif rate_band == "service_price_competitive":
            base_rate = min(base_rate, 0.90)
        return base_rate
    if business_group == "service" and sample_size >= 10:
        base_rate = (quantile_target * 0.5) + (robust_median * 0.35) + (heuristic_rate * 0.15)
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        rate_band = resolve_procurement_rate_band(category=category, description=description)
        if rate_band == "service_high_negotiated":
            base_rate = max(base_rate, 1.0)
        elif rate_band == "service_price_competitive":
            base_rate = min(base_rate, 0.90)
        return base_rate

    if sample_size >= 10:
        if normalized_category in {"service", "technical-service", "general-service"}:
            base_rate = recent_target
            base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
            return apply_procurement_rate_band(base_rate, category=category, description=description)
        if normalized_category == "construction":
            base_rate = quantile_target
            base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
            return apply_procurement_rate_band(base_rate, category=category, description=description)
        base_rate = (robust_median * 0.7) + (recent_target * 0.2) + (mean_rate * 0.1)
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    if sample_size >= 5:
        base_rate = (robust_median * 0.55) + (mean_rate * 0.35) + (heuristic_rate * 0.10)
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    if sample_size >= 2:
        base_rate = (robust_median * 0.45) + (mean_rate * 0.35) + (heuristic_rate * 0.20)
        base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
        return apply_procurement_rate_band(base_rate, category=category, description=description)
    base_rate = (mean_rate * 0.55) + (heuristic_rate * 0.45)
    base_rate = blend_reserve_prior(base_rate, reserve_context=reserve_context)
    return apply_procurement_rate_band(base_rate, category=category, description=description)


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


def apply_procurement_rate_band(base_rate: float, *, category: str, description: str) -> float:
    """Apply service subtype bid-rate bands inferred from notice text."""
    rate_band = resolve_procurement_rate_band(category=category, description=description)
    if rate_band == "service_high_negotiated":
        return max(base_rate, 1.0)
    if rate_band == "service_price_competitive":
        return min(base_rate, 0.90)
    return base_rate


def apply_procurement_candidate_band(
    *,
    conservative_rate: float,
    base_rate: float,
    aggressive_rate: float,
    rate_band: str | None,
) -> tuple[float, float, float]:
    """Keep all scenarios consistent with the inferred procurement band."""
    if rate_band == "service_high_negotiated":
        return max(conservative_rate, 0.98), max(base_rate, 1.0), max(aggressive_rate, 1.0)
    if rate_band == "service_price_competitive":
        return min(conservative_rate, 0.90), min(base_rate, 0.90), min(aggressive_rate, 0.90)
    return conservative_rate, base_rate, aggressive_rate


def resolve_procurement_rate_band(*, category: str, description: str) -> str | None:
    """Infer broad service bidding bands from the notice text."""
    normalized_category = normalize_category_key(category)
    if normalized_category not in {"service", "technical-service", "general-service", "software"}:
        return None

    normalized_text = str(description or "").strip().lower()
    if not normalized_text:
        return None

    price_competitive_keywords = (
        "폐기물",
        "기술지도",
        "사후환경",
        "환경영향",
        "pq",
        "가격입찰",
        "설계용역",
        "감리",
        "건설사업관리",
        "처리용역",
        "재활용",
    )
    if any(keyword in normalized_text for keyword in price_competitive_keywords):
        return "service_price_competitive"

    high_negotiated_keywords = (
        "협상에 의한 계약",
        "협상",
        "제안",
        "위탁",
        "운영",
        "홍보",
        "영상",
        "콘텐츠",
        "시스템",
        "개발",
        "조사",
        "출판",
        "제작",
        "마스터플랜",
        "플랫폼",
        "sns",
        "서포터",
    )
    if any(keyword in normalized_text for keyword in high_negotiated_keywords):
        return "service_high_negotiated"
    return None


def estimate_historical_confidence(*, sample_size: int, std_rate: float, margin: float) -> float:
    """Estimate confidence from sample depth and rate stability."""
    sample_score = min(sample_size / 12, 1.0)
    stability_score = max(0.0, 1.0 - min(std_rate / 0.06, 1.0))
    margin_penalty = min(margin / 0.08, 1.0) * 0.07
    confidence = 0.54 + (sample_score * 0.2) + (stability_score * 0.18) - margin_penalty
    return round(max(0.45, min(0.95, confidence)), 2)


def read_record_value(record: object, key: str) -> Any:
    """Read values from either ORM objects or dictionaries."""
    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def clamp_bid_rate(value: float) -> float:
    """Keep scenario bid rates inside a realistic bidding band."""
    return max(0.7, min(1.4, float(value)))


def get_t_critical(degrees_of_freedom: int) -> float:
    """Return an approximate 95% t critical value without requiring SciPy."""
    if degrees_of_freedom <= 1:
        return _T_CRITICAL_95[1]
    if degrees_of_freedom >= 30:
        return _T_CRITICAL_95[30]
    return _T_CRITICAL_95[degrees_of_freedom]


def resolve_record_weight(record: object, normalized_target_agency: str) -> tuple[float, bool]:
    """Assign a higher weight to same-agency historical rows."""
    if not normalized_target_agency:
        return 1.0, False

    record_agency = normalize_agency_name(read_record_value(record, "agency_name"))
    if not record_agency:
        return 1.0, False
    if record_agency == normalized_target_agency:
        return 3.0, True
    if normalized_target_agency in record_agency or record_agency in normalized_target_agency:
        return 1.8, False
    return 1.0, False


def normalize_agency_name(value: Any) -> str:
    """Normalize agency names for fuzzy equality checks."""
    return "".join(str(value or "").strip().lower().split())


def normalize_category_key(value: Any) -> str:
    """Normalize category labels used by category-specific bidding heuristics."""
    normalized = str(value or "").strip().lower()
    aliases = {
        "일반용역": "service",
        "general-service": "service",
        "기술용역": "technical-service",
        "공사": "construction",
        "물품": "goods",
        "소프트웨어": "software",
    }
    return aliases.get(normalized, normalized)


def weighted_median(values: list[float], weights: list[float]) -> float:
    """Compute a weighted median for historical bid rates."""
    if not values:
        return 0.0
    if not weights:
        return float(np.median(values))

    sorted_pairs = sorted(zip(values, weights), key=lambda item: item[0])
    cumulative_weight = 0.0
    total_weight = float(sum(weights))
    threshold = total_weight / 2
    for value, weight in sorted_pairs:
        cumulative_weight += float(weight)
        if cumulative_weight >= threshold:
            return float(value)
    return float(sorted_pairs[-1][0])


def weighted_quantile(values: list[float], weights: list[float], quantile: float) -> float:
    """Compute a weighted quantile for historical bid rates."""
    if not values:
        return 0.0
    safe_quantile = max(0.0, min(1.0, float(quantile)))
    if not weights:
        sorted_values = sorted(values)
        position = (len(sorted_values) - 1) * safe_quantile
        lower_index = int(position)
        upper_index = min(lower_index + 1, len(sorted_values) - 1)
        fraction = position - lower_index
        return float((sorted_values[lower_index] * (1 - fraction)) + (sorted_values[upper_index] * fraction))

    sorted_pairs = sorted(zip(values, weights), key=lambda item: item[0])
    total_weight = float(sum(weight for _value, weight in sorted_pairs))
    if total_weight <= 0:
        return float(np.quantile(values, safe_quantile))

    threshold = total_weight * safe_quantile
    cumulative_weight = 0.0
    for value, weight in sorted_pairs:
        cumulative_weight += float(weight)
        if cumulative_weight >= threshold:
            return float(value)
    return float(sorted_pairs[-1][0])


def weighted_std(values: list[float], weights: list[float], mean_value: float) -> float:
    """Compute a weighted standard deviation."""
    if not values:
        return 0.0
    if not weights:
        return float(np.std(values))
    variance = np.average((np.array(values) - mean_value) ** 2, weights=np.array(weights))
    return float(sqrt(float(variance)))


def coerce_numeric_list(raw_value: Any) -> list[float]:
    """Coerce a JSON string or list of numbers into floats."""
    parsed = coerce_sequence(raw_value)
    numbers: list[float] = []
    for item in parsed:
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            continue
    return numbers


def coerce_integer_list(raw_value: Any) -> list[int]:
    """Coerce a JSON string or list of numbers into integers."""
    parsed = coerce_sequence(raw_value)
    numbers: list[int] = []
    for item in parsed:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return numbers


def coerce_sequence(raw_value: Any) -> list[Any]:
    """Parse list-like values coming from ORM rows or dictionaries."""
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


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


def build_historical_explanation(
    *,
    sample_size: int,
    base_rate: float,
    agency_match_sample_size: int,
    reserve_pattern: dict[str, Any] | None,
    rate_band: str | None,
) -> str:
    """Build a natural-language summary for weighted historical price prediction."""
    details: list[str] = []
    if agency_match_sample_size > 0:
        details.append(f"동일 기관 이력 {agency_match_sample_size}건에 추가 가중치를 적용했고")
    reserve_sample_count = int(reserve_pattern.get("sample_count", 0) or 0) if reserve_pattern else 0
    if reserve_sample_count > 0:
        details.append(f"예비가격 패턴 {reserve_sample_count}건도 함께 참고했습니다")
    if rate_band == "service_high_negotiated":
        details.append("협상/위탁형 용역으로 보고 100% 근접 목표율을 적용했습니다")
    elif rate_band == "service_price_competitive":
        details.append("가격경쟁형 용역으로 보고 90% 상한 목표율을 적용했습니다")

    detail_text = f" {' '.join(details)}" if details else ""
    return (
        f"최근 히스토리컬 데이터 {sample_size}건의 사정률 분포를 반영해 기준 사정률 {base_rate:.4f}와 "
        f"보수/기준/공격 시나리오를 계산했습니다.{detail_text}"
    ).strip()
