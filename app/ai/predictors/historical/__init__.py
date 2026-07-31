"""Historical/statistical price predictor (decomposed package).

The prediction-orchestration core — ``HistoricalStatisticalPredictor``,
``build_heuristic_prediction``, ``build_historical_prediction``,
``build_historical_explanation`` and ``load_group_calibration`` — is defined here
in the package namespace. Keeping ``load_group_calibration`` and its two call
sites (``predict`` / ``build_historical_prediction``) together in this module
preserves the ``app.ai.predictors.historical.load_group_calibration`` monkeypatch
seam that the golden / business-group tests rely on.

Statistical helpers, calibration readers, reserve blending, procurement bands and
base-rate selection live in sibling submodules and are re-exported below so the
historic ``from app.ai.predictors.historical import <name>`` surface is unchanged.
"""

from __future__ import annotations

from math import sqrt
from typing import Any

from app.ai.predictors.artifact_contracts import CalibrationValue
from app.ai.predictors.base import (
    BasePricePredictor,
    PredictionResult,
    PricePredictionContext,
)
from app.ai.predictors.historical.base_rate import (
    apply_high_rate_distribution_adjustment,
    build_high_rate_adjustment_context,
    select_competitive_base_rate,
)
from app.ai.predictors.historical.calibration import (
    apply_probability_calibration,
    calibration_raw_signal,
    load_active_artifact_calibration_blocks,
    load_probability_calibration,
)
from app.ai.predictors.historical.procurement_bands import (
    GOODS_BAND_RULES,
    _is_goods_deep_discount_notice,
    _is_goods_narrow_control_price_competitive,
    _resolve_goods_procurement_rate_band,
    _resolve_service_procurement_rate_band,
    apply_procurement_candidate_band,
    apply_procurement_rate_band,
    resolve_procurement_rate_band,
)
from app.ai.predictors.historical.reserve import (
    blend_reserve_prior,
    build_reserve_pattern_context,
    resolve_reserve_prior_rate,
)
from app.ai.predictors.historical.statistics import (
    _T_CRITICAL_95,
    clamp_bid_rate,
    estimate_historical_confidence,
    get_t_critical,
    normalize_agency_name,
    normalize_category_key,
    rate_share_at_or_above,
    read_record_value,
    resolve_record_weight,
    weighted_median,
    weighted_quantile,
    weighted_std,
)
from app.ai.predictors.historical.summary import summarize_historical_records
from app.ai.predictors.rate_band_spec import band_explanation_clause

__all__ = [
    "HistoricalStatisticalPredictor",
    "_T_CRITICAL_95",
    "_is_goods_deep_discount_notice",
    "_is_goods_narrow_control_price_competitive",
    "_resolve_goods_procurement_rate_band",
    "_resolve_service_procurement_rate_band",
    "apply_high_rate_distribution_adjustment",
    "apply_probability_calibration",
    "apply_procurement_candidate_band",
    "apply_procurement_rate_band",
    "band_explanation_clause",
    "blend_cold_start_prior",
    "blend_reserve_prior",
    "build_heuristic_prediction",
    "build_high_rate_adjustment_context",
    "build_historical_explanation",
    "build_historical_prediction",
    "build_reserve_pattern_context",
    "calibration_raw_signal",
    "clamp_bid_rate",
    "estimate_historical_confidence",
    "get_t_critical",
    "GOODS_BAND_RULES",
    "load_active_artifact_calibration_blocks",
    "load_group_calibration",
    "load_probability_calibration",
    "normalize_agency_name",
    "normalize_category_key",
    "rate_share_at_or_above",
    "read_record_value",
    "resolve_procurement_rate_band",
    "resolve_record_weight",
    "resolve_reserve_prior_rate",
    "select_competitive_base_rate",
    "summarize_historical_records",
    "weighted_median",
    "weighted_quantile",
    "weighted_std",
]


def load_group_calibration() -> dict[str, dict[str, CalibrationValue]]:
    """Read summary.group_calibration from the active manifest, if present.

    Best-effort: any IO/JSON failure or missing block returns an empty dict so
    callers can safely fall through to the legacy historical statistics. The read
    itself is delegated to
    :func:`~app.ai.predictors.historical.calibration.load_active_artifact_calibration_blocks`
    so the group and probability blocks share one decode + degrade policy; this
    thin wrapper stays in the package namespace because it is the monkeypatch seam
    the golden / business-group tests rely on.
    """
    return load_active_artifact_calibration_blocks().group_calibration


class HistoricalStatisticalPredictor(BasePricePredictor):
    """Current production-grade predictor based on heuristics and historical statistics."""

    name = "historical_statistical"
    family = "statistical"

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        """Predict a bid-rate scenario using the stable historical baseline.

        The three payload builders below are unchanged — the goldens compare them
        directly. This method is the single *promotion* point: whichever branch
        produced the payload, it is validated into the typed contract exactly
        once, so a missing/typo'd key fails here instead of silently defaulting
        somewhere downstream.
        """
        heuristic_prediction = build_heuristic_prediction(
            budget=context.budget,
            category=context.category,
            description=context.description,
        )
        historical_summary = summarize_historical_records(
            context.historical_records,
            agency_name=context.agency_name,
        )
        # Read before the branch: a pure lookup on the summary just built, so the
        # original branch order/semantics are preserved.
        sample_size = int(historical_summary["sample_size"])

        if float(context.budget or 0.0) <= 0:
            payload = heuristic_prediction
        elif sample_size <= 0:
            payload = blend_cold_start_prior(heuristic_prediction, context=context)
        else:
            payload = build_historical_prediction(
                budget=float(context.budget or 0.0),
                category=context.category,
                description=context.description,
                heuristic_prediction=heuristic_prediction,
                historical_summary=historical_summary,
                business_group=context.business_group,
            )
        return PredictionResult.model_validate(payload)


# Cold-start (sample_size == 0) blend between the description heuristic and the
# manifest group prior, plus the price-range spread around the blended rate.
# Declared here rather than inline so the weights are tunable in one place
# (§4.5-1); the operand order below is preserved exactly, so the arithmetic is
# bit-for-bit the same as before the extraction.
_COLD_START_HEURISTIC_WEIGHT = 0.4
_COLD_START_PRIOR_WEIGHT = 0.6
_COLD_START_PRICE_RANGE_MIN_MULTIPLIER = 0.8
_COLD_START_PRICE_RANGE_MAX_MULTIPLIER = 1.2


def blend_cold_start_prior(
    heuristic_prediction: dict[str, Any],
    *,
    context: PricePredictionContext,
) -> dict[str, Any]:
    """Pull a no-history heuristic payload toward the manifest group prior.

    Returns the payload untouched when the active manifest carries no
    ``group_calibration`` median for the context's business group. ``predict``
    only reaches this path when ``sample_size == 0`` and ``budget > 0``.

    ``load_group_calibration`` is called through the module global on purpose —
    that is the monkeypatch seam the golden / business-group tests rely on.
    """
    calibration = load_group_calibration().get(context.business_group or "") or {}
    prior_median = calibration.get("median_rate")
    if prior_median is None:
        return heuristic_prediction

    prior_median = float(prior_median)
    heuristic_rate = float(heuristic_prediction.get("predicted_bid_rate") or prior_median)
    blended_rate = round(
        (heuristic_rate * _COLD_START_HEURISTIC_WEIGHT)
        + (prior_median * _COLD_START_PRIOR_WEIGHT),
        4,
    )
    budget = float(context.budget or 0.0)
    return {
        **heuristic_prediction,
        "predicted_bid_rate": blended_rate,
        "predicted_price": round(budget * blended_rate, 2),
        "price_range_min": round(
            budget * blended_rate * _COLD_START_PRICE_RANGE_MIN_MULTIPLIER, 2
        ),
        "price_range_max": round(
            budget * blended_rate * _COLD_START_PRICE_RANGE_MAX_MULTIPLIER, 2
        ),
    }


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
    base_rate, high_rate_adjustment = apply_high_rate_distribution_adjustment(
        base_rate,
        category=category,
        description=description,
        historical_summary=historical_summary,
        business_group=business_group,
        budget=budget,
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
        "high_rate_tail_adjustment": high_rate_adjustment,
        "explanation": build_historical_explanation(
            sample_size=sample_size,
            base_rate=base_rate,
            agency_match_sample_size=agency_match_sample_size,
            reserve_pattern=reserve_pattern,
            rate_band=rate_band,
            high_rate_adjustment=high_rate_adjustment,
        ),
    }


def build_historical_explanation(
    *,
    sample_size: int,
    base_rate: float,
    agency_match_sample_size: int,
    reserve_pattern: dict[str, Any] | None,
    rate_band: str | None,
    high_rate_adjustment: dict[str, Any] | None = None,
) -> str:
    """Build a natural-language summary for weighted historical price prediction."""
    details: list[str] = []
    if agency_match_sample_size > 0:
        details.append(f"동일 기관 이력 {agency_match_sample_size}건에 추가 가중치를 적용했고")
    reserve_sample_count = int(reserve_pattern.get("sample_count", 0) or 0) if reserve_pattern else 0
    if reserve_sample_count > 0:
        details.append(f"예비가격 패턴 {reserve_sample_count}건도 함께 참고했습니다")
    band_clause = band_explanation_clause(rate_band)
    if band_clause is not None:
        details.append(band_clause)
    if high_rate_adjustment:
        details.append("최근 고율 낙찰 분포를 반영해 기준 사정률을 상향 보정했습니다")

    detail_text = f" {' '.join(details)}" if details else ""
    return (
        f"최근 히스토리컬 데이터 {sample_size}건의 사정률 분포를 반영해 기준 사정률 {base_rate:.4f}와 "
        f"보수/기준/공격 시나리오를 계산했습니다.{detail_text}"
    ).strip()
