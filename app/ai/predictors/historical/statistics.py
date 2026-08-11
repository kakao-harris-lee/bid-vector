"""Numeric and normalization helpers for the historical predictor."""

from __future__ import annotations

from math import sqrt
from typing import Any

import numpy as np

# 기관명 정규화 단일 출처. 여기서는 re-export 만 한다 — 발주처 밴드 키와 예측 이력 매칭이
# ``app.ai.predictors.historical.normalize_agency_name`` 경로로 계속 import 하기 때문이다.
from app.services.koneps.parsing import normalize_agency_name


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


def extract_bid_rate_series(historical_records: tuple[object, ...]) -> list[float]:
    """Extract a time-ordered bid-rate series from ORM rows or dictionaries.

    Rows without a usable ``opened_at``/``created_at`` cannot be ordered, so a
    partially dated batch falls back to insertion order rather than mixing the
    two orderings.
    """
    sequence_points: list[tuple[str, int, float]] = []
    fallback_sequence: list[float] = []
    for index, record in enumerate(historical_records):
        # 시퀀스는 6dp 로 양자화한다(summary 는 반올림하지 않는다 — 그 차이가 digits).
        bid_rate = resolve_record_bid_rate(record, digits=6)
        if bid_rate is None:
            continue
        fallback_sequence.append(bid_rate)
        opened_at = read_record_value(record, "opened_at") or read_record_value(record, "created_at")
        if opened_at is not None:
            sortable_key = opened_at.isoformat() if hasattr(opened_at, "isoformat") else str(opened_at)
            sequence_points.append((sortable_key, index, bid_rate))

    if sequence_points and len(sequence_points) == len(fallback_sequence):
        sequence_points.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in sequence_points]
    return fallback_sequence


def resolve_record_bid_rate(record: object, *, digits: int | None = None) -> float | None:
    """이력 행에서 쓸 수 있는 사정률을 읽는 **단일 출처** (§4.5-8).

    규칙: ``bid_rate`` 를 읽고, 없거나 0 이하면 ``predicted_price / base_amount`` 로
    역산하며, ``0.5~1.5`` 밴드를 벗어나면 사용 불가(``None``)로 본다.

    이 규칙은 원래 두 벌이었다(``statistics`` 의 시퀀스 추출과 ``summary`` 의 인라인
    루프). 상수도 분기도 같고 **6dp 반올림 유무만** 달랐는데, 두 벌이면 밴드 경계나
    역산 조건을 한쪽만 고치는 사고가 난다. 그 차이는 ``digits`` 로 파라미터화한다.
    """
    bid_rate = float(read_record_value(record, "bid_rate") or 0.0)
    if bid_rate <= 0:
        predicted_price = float(read_record_value(record, "predicted_price") or 0.0)
        base_amount = float(read_record_value(record, "base_amount") or 0.0)
        if predicted_price > 0 and base_amount > 0:
            bid_rate = predicted_price / base_amount
    if not 0.5 <= bid_rate <= 1.5:
        return None
    return float(bid_rate) if digits is None else round(float(bid_rate), digits)


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


def rate_share_at_or_above(values: list[float], threshold: float) -> float:
    """Return the share of values at or above a bid-rate threshold."""
    if not values:
        return 0.0
    return round(sum(1 for value in values if value >= threshold) / len(values), 6)


def weighted_std(values: list[float], weights: list[float], mean_value: float) -> float:
    """Compute a weighted standard deviation."""
    if not values:
        return 0.0
    if not weights:
        return float(np.std(values))
    variance = np.average((np.array(values) - mean_value) ** 2, weights=np.array(weights))
    return float(sqrt(float(variance)))
