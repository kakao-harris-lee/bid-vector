"""Historical/statistical price predictor."""

from __future__ import annotations

import json
from math import sqrt
from typing import Any

import numpy as np

from app.ai.predictors.base import BasePricePredictor, PricePredictionContext

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
        if historical_summary["sample_size"] <= 0:
            return heuristic_prediction

        return build_historical_prediction(
            budget=float(context.budget or 0.0),
            heuristic_prediction=heuristic_prediction,
            historical_summary=historical_summary,
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
    heuristic_prediction: dict[str, Any],
    historical_summary: dict[str, Any],
) -> dict[str, Any]:
    """Blend historical bid-rate samples with the heuristic baseline."""
    sample_size = int(historical_summary["sample_size"])
    mean_rate = float(historical_summary["mean_bid_rate"])
    median_rate = float(historical_summary["median_bid_rate"])
    std_rate = float(historical_summary["std_bid_rate"])
    agency_match_sample_size = int(historical_summary.get("agency_match_sample_size", 0) or 0)
    reserve_pattern = historical_summary.get("reserve_price_context")
    heuristic_rate = float(heuristic_prediction.get("predicted_bid_rate", 0.0) or 0.0)

    if sample_size == 1:
        std_rate = max(std_rate, abs(mean_rate - heuristic_rate) * 0.5, 0.015)

    t_value = get_t_critical(sample_size - 1) if sample_size > 1 else _T_CRITICAL_95[1]
    margin = t_value * (std_rate / sqrt(sample_size)) if sample_size > 1 else std_rate

    if sample_size >= 5:
        base_rate = (mean_rate * 0.62) + (median_rate * 0.23) + (heuristic_rate * 0.15)
    elif sample_size >= 2:
        base_rate = (mean_rate * 0.5) + (median_rate * 0.2) + (heuristic_rate * 0.3)
    else:
        base_rate = (mean_rate * 0.45) + (heuristic_rate * 0.55)

    spread = max(std_rate * 0.6, margin * 0.4, 0.01)
    conservative_rate = clamp_bid_rate(base_rate - spread)
    base_rate = clamp_bid_rate(base_rate)
    aggressive_rate = clamp_bid_rate(base_rate + (spread * 0.8))

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
        "explanation": build_historical_explanation(
            sample_size=sample_size,
            base_rate=base_rate,
            agency_match_sample_size=agency_match_sample_size,
            reserve_pattern=reserve_pattern,
        ),
    }


def summarize_historical_records(historical_records: tuple[object, ...], *, agency_name: str | None = None) -> dict[str, Any]:
    """Extract usable bid-rate samples from historical records."""
    bid_rates: list[float] = []
    weights: list[float] = []
    agency_match_sample_size = 0
    reserve_span_rates: list[float] = []
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

        selected_numbers.extend(coerce_integer_list(read_record_value(record, "selected_numbers")))

    if not bid_rates:
        return {
            "sample_size": 0,
            "mean_bid_rate": 0.0,
            "median_bid_rate": 0.0,
            "std_bid_rate": 0.0,
            "agency_match_sample_size": 0,
            "reserve_price_context": None,
        }

    weighted_mean_value = float(np.average(bid_rates, weights=weights)) if weights else float(np.mean(bid_rates))
    weighted_median_value = weighted_median(bid_rates, weights) if weights else float(np.median(bid_rates))
    weighted_std_value = weighted_std(bid_rates, weights, weighted_mean_value) if len(bid_rates) > 1 else 0.0

    return {
        "sample_size": len(bid_rates),
        "mean_bid_rate": weighted_mean_value,
        "median_bid_rate": weighted_median_value,
        "std_bid_rate": weighted_std_value,
        "agency_match_sample_size": agency_match_sample_size,
        "reserve_price_context": build_reserve_pattern_context(
            reserve_span_rates=reserve_span_rates,
            selected_numbers=selected_numbers,
        ),
    }


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


def build_reserve_pattern_context(*, reserve_span_rates: list[float], selected_numbers: list[int]) -> dict[str, Any] | None:
    """Summarize reserve price and selected-number patterns from historical openings."""
    sample_count = len(reserve_span_rates)
    if sample_count == 0 and not selected_numbers:
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
        "average_selected_number": round(float(np.mean(selected_numbers)), 2) if selected_numbers else 0.0,
        "frequent_selected_numbers": frequent_selected_numbers,
    }


def build_historical_explanation(
    *,
    sample_size: int,
    base_rate: float,
    agency_match_sample_size: int,
    reserve_pattern: dict[str, Any] | None,
) -> str:
    """Build a natural-language summary for weighted historical price prediction."""
    details: list[str] = []
    if agency_match_sample_size > 0:
        details.append(f"동일 기관 이력 {agency_match_sample_size}건에 추가 가중치를 적용했고")
    reserve_sample_count = int(reserve_pattern.get("sample_count", 0) or 0) if reserve_pattern else 0
    if reserve_sample_count > 0:
        details.append(f"예비가격 패턴 {reserve_sample_count}건도 함께 참고했습니다")

    detail_text = f" {' '.join(details)}" if details else ""
    return (
        f"최근 히스토리컬 데이터 {sample_size}건의 사정률 분포를 반영해 기준 사정률 {base_rate:.4f}와 "
        f"보수/기준/공격 시나리오를 계산했습니다.{detail_text}"
    ).strip()
