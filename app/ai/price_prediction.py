"""Price prediction AI module."""

from __future__ import annotations

from typing import Any, Dict, Iterable

import numpy as np

from app.ai.predictors import (
    BasePricePredictor,
    EnsembleBidRatePredictor,
    HistoricalStatisticalPredictor,
    LSTMBidRatePredictor,
    PricePredictionContext,
)
from app.ai.predictor_backtest import build_predictor_backtest_report
from app.ai.predictors.historical import clamp_bid_rate
from app.core.config import settings

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
) -> Dict[str, Any]:
    """Predict project price using the configured predictor stack with safe fallback."""
    context = PricePredictionContext(
        budget=float(budget or 0.0),
        category=str(category or "other"),
        description=str(description or ""),
        historical_records=tuple(historical_records or ()),
        agency_name=agency_name,
    )

    predictor, fallback_reason, selection_metadata = _select_predictor(context)
    prediction, used_predictor, fallback_reason = _run_predictor(
        context=context,
        predictor=predictor,
        fallback_reason=fallback_reason,
    )
    prediction = _apply_feedback_calibration(prediction, feedback_calibration)
    prediction = _apply_prediction_guardrails(
        prediction,
        budget=context.budget,
        category=context.category,
    )
    return _attach_predictor_metadata(
        prediction,
        predictor=used_predictor,
        fallback_reason=fallback_reason,
        selection_metadata=selection_metadata,
    )


def _select_predictor(context: PricePredictionContext) -> tuple[BasePricePredictor, str | None, dict[str, Any]]:
    """Choose the configured predictor or fall back to the stable baseline."""
    registry = _build_predictor_registry()
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


def _build_predictor_registry() -> dict[str, BasePricePredictor]:
    """Build the in-process predictor registry."""
    return {
        "historical": HistoricalStatisticalPredictor(),
        "lstm": LSTMBidRatePredictor(),
        "ensemble": EnsembleBidRatePredictor(),
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
    fallback_reason: str | None,
) -> tuple[dict[str, Any], BasePricePredictor, str | None]:
    """Run the selected predictor and recover to the historical baseline on failure."""
    try:
        return predictor.predict(context), predictor, fallback_reason
    except Exception as exc:
        historical_predictor = HistoricalStatisticalPredictor()
        if predictor.name == historical_predictor.name:
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


def _apply_prediction_guardrails(prediction: Dict[str, Any], *, budget: float, category: str | None) -> Dict[str, Any]:
    """Apply minimum bid-rate guardrails after all statistical adjustments."""
    guarded_prediction = dict(prediction)
    floor_bid_rate = _resolve_floor_bid_rate(category)
    floor_price = round(float(budget or 0.0) * floor_bid_rate, 2) if floor_bid_rate is not None and budget > 0 else None

    guarded_prediction["guardrail_applied"] = False
    guarded_prediction["guardrail_reason"] = None
    guarded_prediction["floor_bid_rate"] = round(floor_bid_rate, 4) if floor_bid_rate is not None else None
    guarded_prediction["floor_price"] = floor_price

    if floor_bid_rate is None or budget <= 0:
        return guarded_prediction

    guarded_candidates: list[dict[str, Any]] = []
    applied_labels: list[str] = []
    for candidate in prediction.get("bid_rate_candidates", []):
        label = str(candidate.get("label") or "base")
        original_bid_rate = float(candidate.get("bid_rate", 0.0) or 0.0)
        guarded_bid_rate = max(original_bid_rate, floor_bid_rate)
        if guarded_bid_rate > original_bid_rate + 1e-9:
            applied_labels.append(label)
        guarded_candidates.append({
            **candidate,
            "bid_rate": round(guarded_bid_rate, 4),
            "predicted_price": round(float(budget) * guarded_bid_rate, 2),
        })

    if guarded_candidates:
        base_candidate = next(
            (candidate for candidate in guarded_candidates if candidate.get("label") == "base"),
            guarded_candidates[0],
        )
        guarded_prediction["bid_rate_candidates"] = guarded_candidates
        guarded_prediction["predicted_bid_rate"] = round(float(base_candidate.get("bid_rate", 0.0) or 0.0), 4)
        guarded_prediction["predicted_price"] = round(float(base_candidate.get("predicted_price", 0.0) or 0.0), 2)
        guarded_prediction["price_range_min"] = min(candidate["predicted_price"] for candidate in guarded_candidates)
        guarded_prediction["price_range_max"] = max(candidate["predicted_price"] for candidate in guarded_candidates)
    else:
        original_bid_rate = float(prediction.get("predicted_bid_rate", 0.0) or 0.0)
        guarded_bid_rate = max(original_bid_rate, floor_bid_rate)
        if guarded_bid_rate > original_bid_rate + 1e-9:
            applied_labels.append("base")
        guarded_prediction["predicted_bid_rate"] = round(guarded_bid_rate, 4)
        guarded_prediction["predicted_price"] = round(float(budget) * guarded_bid_rate, 2)
        guarded_prediction["price_range_min"] = max(float(prediction.get("price_range_min", 0.0) or 0.0), floor_price or 0.0)
        guarded_prediction["price_range_max"] = max(float(prediction.get("price_range_max", 0.0) or 0.0), guarded_prediction["predicted_price"])

    if not applied_labels:
        return guarded_prediction

    unique_labels = list(dict.fromkeys(applied_labels))
    guardrail_reason = (
        f"업종별 최소 투찰률 {floor_bid_rate:.2%} 가드레일을 적용해 "
        f"{', '.join(unique_labels)} 시나리오를 보정했습니다."
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
    return guarded_prediction


def _resolve_floor_bid_rate(category: str | None) -> float | None:
    """Resolve a configured minimum bid-rate floor for the given category."""
    normalized_category = _normalize_category_key(category)
    configured_floor_rates = settings.PREDICTION_CATEGORY_MINIMUM_BID_RATES or {}
    for raw_category, raw_floor_rate in configured_floor_rates.items():
        if _normalize_category_key(raw_category) == normalized_category:
            return max(0.0, float(raw_floor_rate or 0.0))

    default_floor_rate = max(0.0, float(settings.PREDICTION_DEFAULT_MINIMUM_BID_RATE or 0.0))
    return default_floor_rate or None


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
