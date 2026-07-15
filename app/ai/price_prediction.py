"""Price prediction AI module."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Iterable

import numpy as np

from app.ai.predictors import (
    BasePricePredictor,
    PricePredictionContext,
)
from app.ai.predictor_backtest import build_predictor_backtest_report
from app.ai.predictors.historical import (
    clamp_bid_rate,
    normalize_agency_name,
    normalize_category_key,
    resolve_procurement_rate_band,
)
from app.ai.predictors.registry import (
    normalize_predictor_registry,
)
from app.core.config import settings


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

_PREDICTOR_KEY_ALIASES = {
    "default": "historical",
    "historical": "historical",
    "historical_blend": "historical",
    "historical_statistical": "historical",
    "statistical": "historical",
    "lstm": "lstm",
    "lstm_sequence": "lstm",
    "sequence": "lstm",
    "ensemble": "ensemble",
    "ensemble_blend": "ensemble",
    "auto": "auto",
    "best": "auto",
    "backtest": "auto",
}


def predict_price(
    budget: float,
    category: str,
    description: str,
    historical_records: Iterable[object] | None = None,
    agency_name: str | None = None,
    feedback_calibration: Dict[str, Any] | None = None,
    business_type_code: str | None = None,
    business_group: str | None = None,
    legal_floor_bid_rate: float | None = None,
    *,
    predictor_registry: Mapping[str, BasePricePredictor] | None = None,
) -> Dict[str, Any]:
    """Predict project price using the configured predictor stack with safe fallback."""
    context = PricePredictionContext(
        budget=float(budget or 0.0),
        category=str(category or "other"),
        description=str(description or ""),
        historical_records=tuple(historical_records or ()),
        agency_name=agency_name,
        business_type_code=business_type_code,
        business_group=business_group,
    )

    registry = normalize_predictor_registry(predictor_registry)
    predictor, fallback_reason, selection_metadata = _select_predictor(context, registry=registry)
    prediction, used_predictor, fallback_reason = _run_predictor(
        context=context,
        predictor=predictor,
        historical_predictor=registry["historical"],
        fallback_reason=fallback_reason,
    )
    prediction = _apply_feedback_calibration(prediction, feedback_calibration)
    prediction = _apply_prediction_guardrails(
        prediction,
        budget=context.budget,
        category=context.category,
        business_group=business_group,
        legal_floor_bid_rate=legal_floor_bid_rate,
        agency_name=agency_name,
    )
    prediction = _apply_bid_price_granularity(prediction, budget=context.budget)
    prediction = _attach_price_regime_metadata(prediction, context=context)
    return _attach_predictor_metadata(
        prediction,
        predictor=used_predictor,
        fallback_reason=fallback_reason,
        selection_metadata=selection_metadata,
    )


def _select_predictor(
    context: PricePredictionContext,
    *,
    registry: dict[str, BasePricePredictor],
) -> tuple[BasePricePredictor, str | None, dict[str, Any]]:
    """Choose the configured predictor or fall back to the stable baseline."""
    preferred_key = _normalize_predictor_key(settings.PRICE_PREDICTION_PREFERRED_PREDICTOR)
    historical_predictor = registry["historical"]
    requested_predictor = registry.get(preferred_key)

    if preferred_key == "auto":
        return _select_predictor_by_backtest(context, registry=registry, historical_predictor=historical_predictor)

    if requested_predictor is None:
        return historical_predictor, (
            f"Unknown predictor preference '{settings.PRICE_PREDICTION_PREFERRED_PREDICTOR}'. "
            "Falling back to the historical baseline."
        ), {
            "selector_name": "configured_preference",
            "selection_reason": "unknown predictor preference",
            "backtest_sample_count": 0,
            "backtest_average_absolute_error_rate": None,
        }

    availability = requested_predictor.check_availability(context)
    if availability.available:
        return requested_predictor, None, {
            "selector_name": "configured_preference",
            "selection_reason": f"Configured preference selected {requested_predictor.name}.",
            "backtest_sample_count": 0,
            "backtest_average_absolute_error_rate": None,
        }
    if requested_predictor.name == historical_predictor.name:
        return historical_predictor, None, {
            "selector_name": "configured_preference",
            "selection_reason": "Configured historical baseline selected.",
            "backtest_sample_count": 0,
            "backtest_average_absolute_error_rate": None,
        }
    return historical_predictor, f"Requested {requested_predictor.name} predictor is unavailable: {availability.reason}", {
        "selector_name": "configured_preference",
        "selection_reason": f"Configured {requested_predictor.name} predictor was unavailable; historical baseline selected.",
        "backtest_sample_count": 0,
        "backtest_average_absolute_error_rate": None,
    }


def _select_predictor_by_backtest(
    context: PricePredictionContext,
    *,
    registry: dict[str, BasePricePredictor],
    historical_predictor: BasePricePredictor,
) -> tuple[BasePricePredictor, str | None, dict[str, Any]]:
    """Select the best currently runnable predictor from a rolling backtest."""
    report = build_predictor_backtest_report(context, registry)
    best_key = str(report.get("best_predictor_key") or "")
    selected_predictor = registry.get(best_key) or historical_predictor
    best_error = report.get("best_average_absolute_error_rate")
    best_result = next(
        (result for result in report.get("results", []) if result.get("predictor_key") == best_key),
        None,
    )
    sample_count = int(best_result.get("sample_count", 0) or 0) if isinstance(best_result, dict) else 0
    selection_reason = (
        f"Auto selector chose {selected_predictor.name} from {sample_count} backtest sample(s)"
        if best_key
        else "Auto selector could not find an eligible backtest winner; historical baseline selected."
    )
    if best_error is not None:
        selection_reason = f"{selection_reason} with average absolute bid-rate error {float(best_error):.4f}."

    return selected_predictor, None, {
        "selector_name": "rolling_backtest",
        "selection_reason": selection_reason,
        "backtest_sample_count": sample_count,
        "backtest_average_absolute_error_rate": best_error,
        "backtest_report": report,
    }


def _normalize_predictor_key(value: Any) -> str:
    """Normalize a predictor preference value into a registry key."""
    normalized_value = str(value or "historical").strip().lower()
    return _PREDICTOR_KEY_ALIASES.get(normalized_value, normalized_value)


def _run_predictor(
    *,
    context: PricePredictionContext,
    predictor: BasePricePredictor,
    historical_predictor: BasePricePredictor,
    fallback_reason: str | None,
) -> tuple[dict[str, Any], BasePricePredictor, str | None]:
    """Run the selected predictor and recover to the historical baseline on failure."""
    try:
        return predictor.predict(context), predictor, fallback_reason
    except Exception as exc:
        if predictor is historical_predictor:
            raise
        merged_reason = _merge_fallback_reason(
            fallback_reason,
            f"Requested {predictor.name} predictor failed during inference: {exc}",
        )
        return historical_predictor.predict(context), historical_predictor, merged_reason


def _merge_fallback_reason(existing_reason: str | None, new_reason: str) -> str:
    """Combine fallback reasons without repeating the same message."""
    cleaned_existing = str(existing_reason or "").strip()
    cleaned_new = str(new_reason or "").strip()
    if not cleaned_existing:
        return cleaned_new
    if not cleaned_new or cleaned_new in cleaned_existing:
        return cleaned_existing
    return f"{cleaned_existing} {cleaned_new}"


def _attach_predictor_metadata(
    prediction: dict[str, Any],
    *,
    predictor: BasePricePredictor,
    fallback_reason: str | None,
    selection_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Attach predictor selection metadata to a normalized payload."""
    annotated_prediction = dict(prediction)
    annotated_prediction["predictor_name"] = predictor.name
    annotated_prediction["predictor_family"] = predictor.family
    annotated_prediction["fallback_reason"] = fallback_reason
    annotated_prediction["selector_name"] = selection_metadata.get("selector_name", "configured_preference")
    annotated_prediction["selection_reason"] = selection_metadata.get("selection_reason")
    annotated_prediction["backtest_sample_count"] = int(selection_metadata.get("backtest_sample_count", 0) or 0)
    annotated_prediction["backtest_average_absolute_error_rate"] = selection_metadata.get("backtest_average_absolute_error_rate")
    if selection_metadata.get("backtest_report") is not None:
        annotated_prediction["backtest_report"] = selection_metadata["backtest_report"]
    annotated_prediction["training_window_size"] = int(annotated_prediction.get("historical_sample_size", 0) or 0)
    return annotated_prediction


def _attach_price_regime_metadata(
    prediction: dict[str, Any],
    *,
    context: PricePredictionContext,
) -> dict[str, Any]:
    """Attach explainable price-regime and final-candidate selector metadata."""
    annotated_prediction = dict(prediction)
    features = _build_price_regime_features(prediction, context=context)
    selector = _select_recommended_candidate(
        annotated_prediction.get("bid_rate_candidates", []),
        price_regime_label=str(features["price_regime_label"]),
    )
    annotated_prediction["price_regime_features"] = features
    annotated_prediction["price_regime_label"] = features["price_regime_label"]
    annotated_prediction["price_regime_confidence"] = features["price_regime_confidence"]
    annotated_prediction["review_required"] = bool(features["review_required"])
    annotated_prediction["recommended_candidate_label"] = selector["recommended_candidate_label"]
    annotated_prediction["recommended_selector_reason"] = selector["recommended_selector_reason"]
    return annotated_prediction


def _build_price_regime_features(
    prediction: dict[str, Any],
    *,
    context: PricePredictionContext,
) -> dict[str, Any]:
    """Classify the procurement price regime before interpreting model output."""
    category = normalize_category_key(context.category)
    text = str(context.description or "").strip().lower()
    rate_band = (
        prediction.get("procurement_rate_band")
        or resolve_procurement_rate_band(category=context.category, description=context.description)
    )
    signal_flags = _detect_price_regime_signals(category=category, text=text, rate_band=rate_band)

    if signal_flags["conflicting"]:
        price_regime_label = "ambiguous"
        confidence = 0.58
        review_required = True
    elif signal_flags["deep_discount"]:
        price_regime_label = "deep_discount"
        confidence = 0.86
        review_required = False
    elif signal_flags["near_100"]:
        price_regime_label = "near_100"
        confidence = 0.84
        review_required = False
    elif signal_flags["floor_bound"]:
        price_regime_label = "floor_bound"
        confidence = 0.82
        review_required = False
    else:
        price_regime_label = "ambiguous"
        confidence = 0.45
        review_required = True

    return {
        "buyer_sector": "unknown",
        "buyer_type": "unknown",
        "notice_category": category or "unknown",
        "business_type_code": context.business_type_code,
        "business_group": context.business_group,
        "construction_or_service_type": _infer_service_type(text),
        "contract_method": _infer_contract_method(signal_flags),
        "award_method": _infer_award_method(signal_flags),
        "evaluation_method": _infer_evaluation_method(signal_flags),
        "price_submission_mode": _infer_price_submission_mode(signal_flags),
        "denominator_type": "base_amount",
        "legal_floor_bid_rate": prediction.get("legal_floor_bid_rate"),
        "reserve_price_context_available": prediction.get("reserve_price_context") is not None,
        "amount_bucket": _amount_bucket(context.budget),
        "agency_recent_rate_profile": {
            "historical_sample_size": int(prediction.get("historical_sample_size", 0) or 0),
            "agency_match_sample_size": int(prediction.get("agency_match_sample_size", 0) or 0),
        },
        "data_quality_flags": [],
        "procurement_rate_band": rate_band,
        "price_regime_label": price_regime_label,
        "price_regime_confidence": confidence,
        "review_required": review_required,
        "regime_signals": signal_flags["signals"],
    }


def _detect_price_regime_signals(*, category: str, text: str, rate_band: Any) -> dict[str, Any]:
    """Return broad mechanism signals used by the price-regime classifier."""
    signals: set[str] = set()
    has_two_stage = _contains_any(
        text,
        ("2단계", "규격·가격", "규격 가격", "규격가격", "가격분리", "가격 분리", "동시입찰", "동시 평가"),
    )
    has_price_competition = _contains_any(
        text,
        ("가격입찰", "적격심사", "pq", "소액수의 견적", "견적 제출", "견적제출"),
    )
    has_negotiation = _contains_any(text, ("협상에 의한 계약", "협상", "제안서 평가", "제안"))
    has_direct = _contains_any(text, ("수의시담", "수의 시담", "수의계약", "수의 계약", "(수의)", "[수의]"))
    has_deep_discount = bool(rate_band == "goods_deep_discount")

    if rate_band in {"service_price_competitive", "goods_price_competitive"} or has_price_competition:
        signals.add("price_competitive")
    if has_two_stage:
        signals.add("two_stage_or_separated")
    if has_negotiation or rate_band == "service_high_negotiated":
        signals.add("negotiated")
    if has_direct or rate_band == "service_direct_negotiated":
        signals.add("direct_negotiated")
    if has_deep_discount:
        signals.add("deep_discount")

    floor_bound = bool(rate_band in {"service_price_competitive", "goods_price_competitive"} or has_price_competition)
    near_100 = bool(rate_band in {"service_high_negotiated", "service_direct_negotiated"} or has_negotiation or has_direct)
    deep_discount = bool(has_deep_discount)
    conflicting = (near_100 and floor_bound) or (deep_discount and near_100)
    if category == "goods" and has_two_stage and _contains_any(text, ("급식", "농산물")):
        deep_discount = True
        floor_bound = False
        signals.add("deep_discount")

    return {
        "floor_bound": floor_bound,
        "near_100": near_100,
        "deep_discount": deep_discount,
        "conflicting": conflicting,
        "signals": sorted(signals),
    }


def _select_recommended_candidate(
    candidates: Any,
    *,
    price_regime_label: str,
) -> dict[str, str]:
    """Select the final candidate label separately from candidate generation."""
    candidate_list = candidates if isinstance(candidates, list) else []
    labels = {str(candidate.get("label")) for candidate in candidate_list if isinstance(candidate, dict)}
    selected_label = "base" if "base" in labels else (next(iter(labels), "base"))
    if price_regime_label == "ambiguous":
        reason = "ambiguous/conflicting price regime; retaining base candidate and requiring review."
    else:
        reason = f"{price_regime_label} regime; retaining {selected_label} candidate after guardrails."
    return {
        "recommended_candidate_label": selected_label,
        "recommended_selector_reason": reason,
    }


def _infer_contract_method(signals: dict[str, Any]) -> str:
    if signals["conflicting"]:
        return "conflicting"
    if signals["deep_discount"]:
        return "two_stage_or_separated"
    if "direct_negotiated" in signals["signals"]:
        return "direct_negotiated"
    if "negotiated" in signals["signals"]:
        return "negotiated"
    if signals["floor_bound"]:
        return "price_competitive"
    return "unknown"


def _infer_award_method(signals: dict[str, Any]) -> str:
    if signals["conflicting"]:
        return "review_required"
    if signals["near_100"]:
        return "negotiated_or_direct"
    if signals["deep_discount"]:
        return "separated_price_competition"
    if signals["floor_bound"]:
        return "price_competition"
    return "unknown"


def _infer_evaluation_method(signals: dict[str, Any]) -> str:
    if "two_stage_or_separated" in signals["signals"]:
        return "two_stage_or_spec_price"
    if signals["near_100"]:
        return "proposal_or_direct"
    if signals["floor_bound"]:
        return "price_or_qualification"
    return "unknown"


def _infer_price_submission_mode(signals: dict[str, Any]) -> str:
    if "two_stage_or_separated" in signals["signals"]:
        return "separated"
    if signals["near_100"]:
        return "negotiated"
    if signals["floor_bound"]:
        return "standard_price"
    return "unknown"


def _infer_service_type(text: str) -> str | None:
    if _contains_any(text, ("해양", "항만", "수심측량", "해저지형", "바다숲", "인공어초")):
        return "marine_engineering"
    if _contains_any(text, ("보험", "차량", "버스", "수학여행")):
        return "transport_or_insurance"
    if _contains_any(text, ("시스템", "소프트웨어", "플랫폼", "클라우드", "유지관리")):
        return "software_or_operations"
    return None


def _amount_bucket(budget: float) -> str:
    value = float(budget or 0.0)
    if value <= 0:
        return "unknown"
    if value < 10_000_000:
        return "lt_10m"
    if value < 30_000_000:
        return "10m_30m"
    if value < 100_000_000:
        return "30m_100m"
    if value < 300_000_000:
        return "100m_300m"
    if value < 1_000_000_000:
        return "300m_1b"
    return "gte_1b"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _apply_feedback_calibration(prediction: Dict[str, Any], feedback_calibration: Dict[str, Any] | None) -> Dict[str, Any]:
    """Apply a feedback-derived adjustment rate to all price scenarios when available."""
    if not feedback_calibration:
        return prediction

    sample_count = int(feedback_calibration.get("sample_count", 0) or 0)
    adjustment_rate = float(feedback_calibration.get("applied_adjustment_rate", 0.0) or 0.0)
    if sample_count <= 0:
        return prediction

    if abs(adjustment_rate) < 0.0001:
        calibrated_prediction = dict(prediction)
        calibrated_prediction["feedback_calibration"] = feedback_calibration
        return calibrated_prediction

    factor = max(0.5, 1.0 + adjustment_rate)
    calibrated_candidates: list[dict[str, Any]] = []
    for candidate in prediction.get("bid_rate_candidates", []):
        calibrated_bid_rate = clamp_bid_rate(float(candidate.get("bid_rate", 0.0) or 0.0) * factor)
        calibrated_candidates.append({
            **candidate,
            "bid_rate": round(calibrated_bid_rate, 4),
            "predicted_price": round(float(candidate.get("predicted_price", 0.0) or 0.0) * factor, 2),
        })

    base_candidate = next(
        (candidate for candidate in calibrated_candidates if candidate.get("label") == "base"),
        None,
    )
    calibrated_prediction = {
        **prediction,
        "predicted_price": round(float(prediction.get("predicted_price", 0.0) or 0.0) * factor, 2),
        "price_range_min": round(float(prediction.get("price_range_min", 0.0) or 0.0) * factor, 2),
        "price_range_max": round(float(prediction.get("price_range_max", 0.0) or 0.0) * factor, 2),
        "predicted_bid_rate": round(
            float(base_candidate.get("bid_rate")) if base_candidate is not None else float(prediction.get("predicted_bid_rate", 0.0) or 0.0) * factor,
            4,
        ),
        "bid_rate_candidates": calibrated_candidates,
        "feedback_calibration": feedback_calibration,
        "model_version": f"{prediction.get('model_version', 'v1.0')}+feedback",
        "explanation": (
            f"{prediction.get('explanation', '').rstrip('.')} 최근 피드백 보정률 {adjustment_rate:+.2%}를 반영했습니다."
        ).strip(),
    }
    if base_candidate is not None:
        calibrated_prediction["predicted_price"] = round(float(base_candidate.get("predicted_price", 0.0) or 0.0), 2)
    return calibrated_prediction


def _apply_prediction_guardrails(
    prediction: Dict[str, Any],
    *,
    budget: float,
    category: str | None,
    business_group: str | None = None,
    legal_floor_bid_rate: float | None = None,
    agency_name: str | None = None,
) -> Dict[str, Any]:
    """Apply category/group/agency bid-rate guardrails after all statistical adjustments."""
    guarded_prediction = dict(prediction)
    guardrail_context = _resolve_guardrail_context(
        budget=budget,
        category=category,
        business_group=business_group,
        legal_floor_bid_rate=legal_floor_bid_rate,
        agency_name=agency_name,
    )
    _apply_guardrail_metadata(guarded_prediction, guardrail_context)

    if (
        guardrail_context.floor_bid_rate is None
        and guardrail_context.ceiling_bid_rate is None
    ) or budget <= 0:
        return guarded_prediction

    guarded_candidates, floor_applied_labels, ceiling_applied_labels = (
        _guard_bid_rate_candidates(prediction, budget=budget, context=guardrail_context)
    )
    if guarded_candidates:
        _apply_guarded_candidate_prediction(
            guarded_prediction,
            guarded_candidates,
        )
    else:
        floor_applied_labels, ceiling_applied_labels = (
            _apply_base_prediction_guardrails(
                guarded_prediction,
                prediction,
                budget=budget,
                context=guardrail_context,
            )
        )

    if not floor_applied_labels and not ceiling_applied_labels:
        return guarded_prediction

    _mark_guardrail_application(
        guarded_prediction,
        context=guardrail_context,
        floor_labels=floor_applied_labels,
        ceiling_labels=ceiling_applied_labels,
    )
    return guarded_prediction


def _resolve_guardrail_context(
    *,
    budget: float,
    category: str | None,
    business_group: str | None,
    legal_floor_bid_rate: float | None,
    agency_name: str | None = None,
) -> GuardrailContext:
    # Resolve the category/group baseline and the agency-tightened band separately so
    # the guardrail reason can attribute the BINDING edge to the correct source. The
    # agency band only RAISES the floor / LOWERS the ceiling, so a resolved value that
    # differs from the baseline can only have been moved by the agency band.
    base_configured_floor = _resolve_floor_bid_rate(
        category,
        business_group=business_group,
    )
    configured_floor_bid_rate = _resolve_floor_bid_rate(
        category,
        business_group=business_group,
        agency_name=agency_name,
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
    base_configured_ceiling = _resolve_ceiling_bid_rate(
        category,
        business_group=business_group,
    )
    ceiling_bid_rate = _resolve_ceiling_bid_rate(
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
        safe_floor_bid_rate = _resolve_safe_floor_bid_rate(
            floor_bid_rate,
            ceiling_bid_rate=ceiling_bid_rate,
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
    )


def _guardrail_price(budget: float, bid_rate: float | None) -> float | None:
    if bid_rate is None or budget <= 0:
        return None
    return round(float(budget or 0.0) * bid_rate, 2)


def _apply_guardrail_metadata(
    guarded_prediction: Dict[str, Any],
    context: GuardrailContext,
) -> None:
    guarded_prediction["guardrail_applied"] = False
    guarded_prediction["guardrail_reason"] = None
    guarded_prediction["legal_floor_bid_rate"] = (
        round(context.normalized_legal_floor_bid_rate, 6)
        if context.normalized_legal_floor_bid_rate is not None
        else None
    )
    guarded_prediction["floor_guardrail_source"] = context.floor_guardrail_source
    guarded_prediction["floor_bid_rate"] = (
        round(context.floor_bid_rate, 6) if context.floor_bid_rate is not None else None
    )
    guarded_prediction["floor_price"] = context.floor_price
    guarded_prediction["floor_safety_margin_rate"] = (
        round(max(0.0, context.safe_floor_bid_rate - context.floor_bid_rate), 6)
        if context.safe_floor_bid_rate is not None and context.floor_bid_rate is not None
        else None
    )
    guarded_prediction["safe_floor_bid_rate"] = (
        round(context.safe_floor_bid_rate, 6)
        if context.safe_floor_bid_rate is not None
        else None
    )
    guarded_prediction["safe_floor_price"] = context.safe_floor_price
    guarded_prediction["ceiling_bid_rate"] = (
        round(context.ceiling_bid_rate, 6)
        if context.ceiling_bid_rate is not None
        else None
    )
    guarded_prediction["ceiling_price"] = context.ceiling_price
    guarded_prediction["floor_from_agency"] = bool(context.floor_from_agency)
    guarded_prediction["ceiling_from_agency"] = bool(context.ceiling_from_agency)


def _guard_bid_rate_candidates(
    prediction: Dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    guarded_candidates: list[dict[str, Any]] = []
    floor_applied_labels: list[str] = []
    ceiling_applied_labels: list[str] = []
    for candidate in prediction.get("bid_rate_candidates", []):
        guarded_candidate, floor_applied, ceiling_applied = _guard_candidate_bid_rate(
            candidate,
            budget=budget,
            context=context,
        )
        if floor_applied:
            floor_applied_labels.append(str(candidate.get("label") or "base"))
        if ceiling_applied:
            ceiling_applied_labels.append(str(candidate.get("label") or "base"))
        guarded_candidates.append(guarded_candidate)
    return guarded_candidates, floor_applied_labels, ceiling_applied_labels


def _guard_candidate_bid_rate(
    candidate: dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[dict[str, Any], bool, bool]:
    original_bid_rate = float(candidate.get("bid_rate", 0.0) or 0.0)
    guarded_bid_rate = _clamp_rate_to_guardrails(
        original_bid_rate,
        floor_bid_rate=context.safe_floor_bid_rate,
        ceiling_bid_rate=context.ceiling_bid_rate,
    )
    guardrail_changed = abs(guarded_bid_rate - original_bid_rate) > 1e-9
    pre_guardrail_price = float(candidate.get("predicted_price", 0.0) or 0.0)
    return (
        {
            **candidate,
            "bid_rate": round(guarded_bid_rate, 4),
            "predicted_price": round(float(budget) * guarded_bid_rate, 2),
            "guardrail_applied": guardrail_changed,
            "pre_guardrail_bid_rate": round(original_bid_rate, 4)
            if guardrail_changed
            else None,
            "pre_guardrail_price": round(pre_guardrail_price, 2)
            if guardrail_changed
            else None,
        },
        context.safe_floor_bid_rate is not None
        and guarded_bid_rate > original_bid_rate + 1e-9,
        context.ceiling_bid_rate is not None
        and guarded_bid_rate < original_bid_rate - 1e-9,
    )


def _apply_guarded_candidate_prediction(
    guarded_prediction: Dict[str, Any],
    guarded_candidates: list[dict[str, Any]],
) -> None:
    base_candidate = next(
        (candidate for candidate in guarded_candidates if candidate.get("label") == "base"),
        guarded_candidates[0],
    )
    guarded_prediction["bid_rate_candidates"] = guarded_candidates
    guarded_prediction["predicted_bid_rate"] = round(
        float(base_candidate.get("bid_rate", 0.0) or 0.0),
        4,
    )
    guarded_prediction["predicted_price"] = round(
        float(base_candidate.get("predicted_price", 0.0) or 0.0),
        2,
    )
    guarded_prediction["price_range_min"] = min(
        candidate["predicted_price"] for candidate in guarded_candidates
    )
    guarded_prediction["price_range_max"] = max(
        candidate["predicted_price"] for candidate in guarded_candidates
    )


def _apply_base_prediction_guardrails(
    guarded_prediction: Dict[str, Any],
    prediction: Dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[list[str], list[str]]:
    original_bid_rate = float(prediction.get("predicted_bid_rate", 0.0) or 0.0)
    guarded_bid_rate = _clamp_rate_to_guardrails(
        original_bid_rate,
        floor_bid_rate=context.safe_floor_bid_rate,
        ceiling_bid_rate=context.ceiling_bid_rate,
    )
    guarded_prediction["predicted_bid_rate"] = round(guarded_bid_rate, 4)
    guarded_prediction["predicted_price"] = round(float(budget) * guarded_bid_rate, 2)
    price_candidates = [
        guarded_prediction["predicted_price"],
        _clamp_price_to_guardrails(
            float(prediction.get("price_range_min", 0.0) or 0.0),
            context.floor_price,
            context.ceiling_price,
        ),
        _clamp_price_to_guardrails(
            float(prediction.get("price_range_max", 0.0) or 0.0),
            context.floor_price,
            context.ceiling_price,
        ),
    ]
    guarded_prediction["price_range_min"] = min(price_candidates)
    guarded_prediction["price_range_max"] = max(price_candidates)
    return (
        ["base"]
        if context.safe_floor_bid_rate is not None
        and guarded_bid_rate > original_bid_rate + 1e-9
        else [],
        ["base"]
        if context.ceiling_bid_rate is not None
        and guarded_bid_rate < original_bid_rate - 1e-9
        else [],
    )


def _mark_guardrail_application(
    guarded_prediction: Dict[str, Any],
    *,
    context: GuardrailContext,
    floor_labels: list[str],
    ceiling_labels: list[str],
) -> None:
    guardrail_reason = _build_guardrail_reason(
        floor_bid_rate=context.floor_bid_rate,
        safe_floor_bid_rate=context.safe_floor_bid_rate,
        floor_guardrail_source=context.floor_guardrail_source,
        ceiling_bid_rate=context.ceiling_bid_rate,
        floor_labels=floor_labels,
        ceiling_labels=ceiling_labels,
        floor_from_agency=context.floor_from_agency,
        ceiling_from_agency=context.ceiling_from_agency,
        agency_name=context.agency_name,
    )
    guarded_prediction["guardrail_applied"] = True
    guarded_prediction["guardrail_reason"] = guardrail_reason
    guarded_prediction["model_version"] = _append_model_version_suffix(
        str(guarded_prediction.get("model_version", "v1.0")),
        "guardrail",
    )
    guarded_prediction["explanation"] = _append_explanation_note(
        str(guarded_prediction.get("explanation", "") or ""),
        guardrail_reason,
    )


def _apply_bid_price_granularity(prediction: Dict[str, Any], *, budget: float) -> Dict[str, Any]:
    """Round final bid prices to an operator-facing currency unit."""
    granularity = int(settings.PREDICTION_BID_PRICE_GRANULARITY or 0)
    min_budget = float(settings.PREDICTION_BID_PRICE_GRANULARITY_MIN_BUDGET or 0.0)
    if granularity <= 1 or budget <= 0 or budget < min_budget:
        return prediction

    rounded_prediction = dict(prediction)
    rounding_mode = str(settings.PREDICTION_BID_PRICE_ROUNDING_MODE or "floor").strip().lower()
    floor_price = _optional_float(prediction.get("safe_floor_price") or prediction.get("floor_price"))
    ceiling_price = _optional_float(prediction.get("ceiling_price"))

    rounded_candidates: list[dict[str, Any]] = []
    for candidate in prediction.get("bid_rate_candidates", []):
        rounded_candidates.append(
            _round_candidate_price_to_granularity(
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
        original_price = _resolve_prediction_price(prediction, budget=budget)
        rounded_price = _round_price_to_granularity(
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
            _round_price_to_granularity(
                _optional_float(prediction.get("price_range_min")) or rounded_price,
                granularity=granularity,
                rounding_mode=rounding_mode,
                floor_price=floor_price,
                ceiling_price=ceiling_price,
            ),
        )
        rounded_prediction["price_range_max"] = max(
            rounded_price,
            _round_price_to_granularity(
                _optional_float(prediction.get("price_range_max")) or rounded_price,
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


def _round_candidate_price_to_granularity(
    candidate: dict[str, Any],
    *,
    budget: float,
    granularity: int,
    rounding_mode: str,
    floor_price: float | None,
    ceiling_price: float | None,
) -> dict[str, Any]:
    original_price = _resolve_prediction_price(candidate, budget=budget)
    rounded_price = _round_price_to_granularity(
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


def _round_price_to_granularity(
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


def _resolve_prediction_price(payload: dict[str, Any], *, budget: float) -> float:
    price = _optional_float(payload.get("predicted_price") or payload.get("price"))
    if price is not None and price > 0:
        return price
    bid_rate = _optional_float(payload.get("bid_rate") or payload.get("predicted_bid_rate"))
    if bid_rate is not None and bid_rate > 0:
        return float(budget) * bid_rate
    return 0.0


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_rate_to_guardrails(
    bid_rate: float,
    *,
    floor_bid_rate: float | None,
    ceiling_bid_rate: float | None,
) -> float:
    guarded_bid_rate = float(bid_rate or 0.0)
    if floor_bid_rate is not None:
        guarded_bid_rate = max(guarded_bid_rate, floor_bid_rate)
    if ceiling_bid_rate is not None:
        guarded_bid_rate = min(guarded_bid_rate, ceiling_bid_rate)
    return guarded_bid_rate


def _clamp_price_to_guardrails(price: float, floor_price: float | None, ceiling_price: float | None) -> float:
    guarded_price = float(price or 0.0)
    if floor_price is not None:
        guarded_price = max(guarded_price, floor_price)
    if ceiling_price is not None:
        guarded_price = min(guarded_price, ceiling_price)
    return round(guarded_price, 2)


def _build_guardrail_reason(
    *,
    floor_bid_rate: float | None,
    safe_floor_bid_rate: float | None,
    floor_guardrail_source: str | None,
    ceiling_bid_rate: float | None,
    floor_labels: list[str],
    ceiling_labels: list[str],
    floor_from_agency: bool = False,
    ceiling_from_agency: bool = False,
    agency_name: str | None = None,
) -> str:
    """Build an auditable guardrail reason.

    Each edge is attributed to its ACTUAL source: when the binding floor/ceiling is the
    agency-keyed value (it tightened the band beyond the category/group rate), the edge
    reads "발주처 … 기관별 투찰률 밴드"; otherwise it stays "업종별"/"공고별 법정".
    """
    agency_label = str(agency_name or "").strip()
    agency_segment = f"발주처{f' {agency_label}' if agency_label else ''} 기관별 투찰률 밴드의"
    reasons: list[str] = []
    if floor_bid_rate is not None and floor_labels:
        unique_labels = list(dict.fromkeys(floor_labels))
        if floor_from_agency:
            floor_label = f"{agency_segment} 최소 투찰률"
        else:
            floor_label = _floor_guardrail_label(floor_guardrail_source)
        if safe_floor_bid_rate is not None and safe_floor_bid_rate > floor_bid_rate + 1e-9:
            reasons.append(
                f"{floor_label} {floor_bid_rate:.3%}에 안전마진 "
                f"{(safe_floor_bid_rate - floor_bid_rate) * 100:.2f}pp를 더한 "
                f"{safe_floor_bid_rate:.3%} 가드레일을 적용해 "
                f"{', '.join(unique_labels)} 시나리오를 상향 보정했습니다."
            )
        else:
            reasons.append(
                f"{floor_label} {floor_bid_rate:.3%} 가드레일을 적용해 "
                f"{', '.join(unique_labels)} 시나리오를 상향 보정했습니다."
            )
    if ceiling_bid_rate is not None and ceiling_labels:
        unique_labels = list(dict.fromkeys(ceiling_labels))
        ceiling_label = (
            f"{agency_segment} 최대 투찰률" if ceiling_from_agency else "업종별 최대 투찰률"
        )
        reasons.append(
            f"{ceiling_label} {ceiling_bid_rate:.2%} 가드레일을 적용해 "
            f"{', '.join(unique_labels)} 시나리오를 하향 보정했습니다."
        )
    return " ".join(reasons)


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
    if numeric > 1.5:
        numeric = numeric / 100.0
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


def _resolve_safe_floor_bid_rate(
    floor_bid_rate: float | None,
    *,
    ceiling_bid_rate: float | None,
) -> float | None:
    if floor_bid_rate is None:
        return None
    safety_margin = max(0.0, float(settings.PREDICTION_FLOOR_SAFETY_MARGIN_RATE or 0.0))
    safe_floor_bid_rate = float(floor_bid_rate) + safety_margin
    if ceiling_bid_rate is not None:
        safe_floor_bid_rate = min(safe_floor_bid_rate, float(ceiling_bid_rate))
    return max(float(floor_bid_rate), safe_floor_bid_rate)


def _floor_guardrail_label(source: str | None) -> str:
    if source == "legal":
        return "공고별 법정 최소 투찰률"
    if source == "category_and_legal":
        return "공고별 법정/업종별 최소 투찰률"
    return "업종별 최소 투찰률"


def _resolve_agency_bid_rate(agency_name: str | None, rate_map: dict[str, float] | None) -> float | None:
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


def _resolve_floor_bid_rate(
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
    configured_floor_rates = settings.PREDICTION_CATEGORY_MINIMUM_BID_RATES or {}
    normalized_category = _normalize_category_key(category)
    category_rate: float | None = None
    for raw_category, raw_floor_rate in configured_floor_rates.items():
        if _normalize_category_key(raw_category) == normalized_category:
            category_rate = max(0.0, float(raw_floor_rate or 0.0))
            break

    resolved_floor: float | None = None
    if business_group and settings.BUSINESS_GROUP_CALIBRATION_ENABLED:
        group_rates = settings.PREDICTION_GROUP_MINIMUM_BID_RATES or {}
        if business_group in group_rates:
            group_rate = float(group_rates[business_group])
            # Group floor must never undercut category floor (§4.7).
            resolved_floor = max(group_rate, category_rate) if category_rate is not None else group_rate

    if resolved_floor is None:
        if category_rate is not None:
            resolved_floor = category_rate
        else:
            default_floor_rate = max(0.0, float(settings.PREDICTION_DEFAULT_MINIMUM_BID_RATE or 0.0))
            resolved_floor = default_floor_rate or None

    agency_floor = _resolve_agency_bid_rate(agency_name, settings.PREDICTION_AGENCY_MINIMUM_BID_RATES)
    if agency_floor is not None:
        agency_floor = clamp_bid_rate(agency_floor)
        # Agency floor TIGHTENS the band by RAISING the floor (max wins).
        return max(resolved_floor, agency_floor) if resolved_floor is not None else agency_floor

    return resolved_floor


def _resolve_ceiling_bid_rate(
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
    configured_ceiling_rates = settings.PREDICTION_CATEGORY_MAXIMUM_BID_RATES or {}
    normalized_category = _normalize_category_key(category)
    category_rate: float | None = None
    for raw_category, raw_ceiling_rate in configured_ceiling_rates.items():
        if _normalize_category_key(raw_category) == normalized_category:
            ceiling_rate = max(0.0, float(raw_ceiling_rate or 0.0))
            category_rate = ceiling_rate or None
            break

    resolved_ceiling: float | None = None
    if business_group and settings.BUSINESS_GROUP_CALIBRATION_ENABLED:
        group_rates = settings.PREDICTION_GROUP_MAXIMUM_BID_RATES or {}
        if business_group in group_rates:
            group_rate = float(group_rates[business_group])
            # Group ceiling must never exceed category ceiling (§4.7).
            resolved_ceiling = min(group_rate, category_rate) if category_rate is not None else group_rate

    if resolved_ceiling is None:
        if category_rate is not None:
            resolved_ceiling = category_rate
        else:
            default_ceiling_rate = max(0.0, float(settings.PREDICTION_DEFAULT_MAXIMUM_BID_RATE or 0.0))
            resolved_ceiling = default_ceiling_rate or None

    agency_ceiling = _resolve_agency_bid_rate(agency_name, settings.PREDICTION_AGENCY_MAXIMUM_BID_RATES)
    if agency_ceiling is not None:
        agency_ceiling = clamp_bid_rate(agency_ceiling)
        # Agency ceiling TIGHTENS the band by LOWERING the ceiling (min wins).
        return min(resolved_ceiling, agency_ceiling) if resolved_ceiling is not None else agency_ceiling

    return resolved_ceiling


def _normalize_category_key(value: Any) -> str:
    """Normalize category labels for configuration lookups."""
    normalized_value = str(value or "").strip().lower()
    return _CATEGORY_FLOOR_RATE_ALIASES.get(normalized_value, normalized_value)


def _append_model_version_suffix(model_version: str, suffix: str) -> str:
    """Append a model-version suffix once without duplicating it."""
    token = f"+{suffix}"
    if token in model_version:
        return model_version
    if suffix == "guardrail" and model_version.endswith("+feedback"):
        return f"{model_version[:-len('+feedback')]}{token}+feedback"
    return f"{model_version}{token}"


def _append_explanation_note(explanation: str, note: str) -> str:
    """Append a readable note to an explanation sentence."""
    cleaned_explanation = str(explanation or "").strip()
    cleaned_note = str(note or "").strip()
    if not cleaned_note:
        return cleaned_explanation
    if not cleaned_explanation:
        return cleaned_note
    if cleaned_explanation.endswith("."):
        return f"{cleaned_explanation} {cleaned_note}"
    return f"{cleaned_explanation} {cleaned_note}"


def get_price_insights(historical_bids: list) -> Dict:
    """Get market insights from historical bid data."""
    if not historical_bids:
        return {"average_bid": 0, "median_bid": 0, "std_dev": 0}

    bid_amounts = [bid["amount"] for bid in historical_bids]

    return {
        "average_bid": float(np.mean(bid_amounts)),
        "median_bid": float(np.median(bid_amounts)),
        "std_dev": float(np.std(bid_amounts)),
        "min_bid": float(np.min(bid_amounts)),
        "max_bid": float(np.max(bid_amounts)),
    }
