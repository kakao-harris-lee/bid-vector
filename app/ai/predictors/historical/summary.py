"""Historical record summarization into weighted bid-rate statistics."""

from __future__ import annotations

from typing import Any

import numpy as np

from app.ai.predictors.distribution_extraction import realized_assessment_ratio
from app.ai.predictors.historical.reserve import build_reserve_pattern_context
from app.ai.predictors.historical.statistics import (
    normalize_agency_name,
    rate_share_at_or_above,
    read_record_value,
    resolve_record_bid_rate,
    resolve_record_weight,
    weighted_median,
    weighted_quantile,
    weighted_std,
)
from app.utils.sequence_coercion import (
    coerce_integer_list,
    coerce_numeric_list,
)


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
        # 읽기 규칙은 statistics 의 단일 출처를 쓴다(§4.5-8). 여기서는 종전과 같이
        # **반올림하지 않은** 값을 담는다 — digits=None 이 그 한 가지 차이를 표현한다.
        bid_rate = resolve_record_bid_rate(record)

        if bid_rate is not None:
            bid_rates.append(bid_rate)
            weight, matched_agency = resolve_record_weight(record, normalized_target_agency)
            weights.append(weight)
            if matched_agency:
                agency_match_sample_size += 1

        reserve_prices = coerce_numeric_list(read_record_value(record, "reserve_prices"))
        base_amount = float(read_record_value(record, "base_amount") or 0.0)
        if len(reserve_prices) >= 2 and base_amount > 0:
            reserve_span_rates.append((max(reserve_prices) - min(reserve_prices)) / base_amount)
            # 실현 사정률 산술(1-기반 추첨분·최소 2개·평균/base·0.8~1.2 밴드)의 단일
            # 출처는 distribution_extraction 이다(§4.5-8, #360 원칙: 두 벌이면 밴드
            # 경계를 한쪽만 고친다). #361 은 같은 원칙이 **다른 밴드**(bid_rate
            # 0.5~1.5)에서 깨진 사례고, 이 0.8~1.2 복사본은 리뷰 L2 가 통합했다.
            estimated_price_rate = realized_assessment_ratio(
                reserve_prices=reserve_prices,
                picked_numbers=coerce_integer_list(
                    read_record_value(record, "selected_numbers")
                ),
                base_amount=base_amount,
            )
            if estimated_price_rate is not None:
                estimated_price_rates.append(estimated_price_rate)
                # resolve_record_bid_rate 가 0.5~1.5 밴드를 이미 강제한다(밖=None
                # — float 비교 시 TypeError 실데이터 회귀). 운영 실측 도달분은
                # **하단 이탈**(0<rate<0.5) 69행뿐이다(상단 percent-스케일은 수집의
                # to_bid_rate_fraction 이 /100 으로 접는다).
                if bid_rate is not None:
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
    recent_tail_window_size = min(20, len(bid_rates))
    recent_tail_rates = bid_rates[:recent_tail_window_size]
    recent_tail_weights = weights[:recent_tail_window_size]
    competitive_quantile_value = weighted_quantile(bid_rates, weights, 0.45)
    upper_quantile_value = weighted_quantile(bid_rates, weights, 0.75)
    recent_upper_quantile_value = weighted_quantile(recent_tail_rates, recent_tail_weights, 0.75)

    return {
        "sample_size": len(bid_rates),
        "mean_bid_rate": weighted_mean_value,
        "median_bid_rate": weighted_median_value,
        "recent_median_bid_rate": recent_median_value,
        "recent_sample_size": recent_tail_window_size,
        "competitive_quantile_bid_rate": competitive_quantile_value,
        "upper_quantile_bid_rate": upper_quantile_value,
        "recent_upper_quantile_bid_rate": recent_upper_quantile_value,
        "rate_ge_0_93_share": rate_share_at_or_above(bid_rates, 0.93),
        "rate_ge_0_95_share": rate_share_at_or_above(bid_rates, 0.95),
        "rate_ge_0_98_share": rate_share_at_or_above(bid_rates, 0.98),
        "recent_rate_ge_0_93_share": rate_share_at_or_above(recent_tail_rates, 0.93),
        "recent_rate_ge_0_95_share": rate_share_at_or_above(recent_tail_rates, 0.95),
        "recent_rate_ge_0_98_share": rate_share_at_or_above(recent_tail_rates, 0.98),
        "std_bid_rate": weighted_std_value,
        "agency_match_sample_size": agency_match_sample_size,
        "reserve_price_context": build_reserve_pattern_context(
            reserve_span_rates=reserve_span_rates,
            estimated_price_rates=estimated_price_rates,
            bid_to_estimated_price_rates=bid_to_estimated_price_rates,
            selected_numbers=selected_numbers,
        ),
    }
