"""Predictor selection and the ``predict_price`` orchestration entry point.

Split out of the former single-file ``price_prediction`` module (§4.5 size
decomposition). ``predict_price`` and the selector/runner helpers are relocated
verbatim; the pipeline order (select → run → feedback → guardrails → granularity
→ regime → metadata) and every call site are unchanged. Feedback, guardrail, and
regime stages are imported from the sibling decomposition modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Dict, Iterable

from app.ai.predictors import (
    BasePricePredictor,
    PricePredictionContext,
)
from app.ai.predictor_backtest import build_predictor_backtest_report
from app.ai.predictors.registry import (
    normalize_predictor_registry,
)
from app.core.config import settings

from app.ai.price_prediction.feedback import _apply_feedback_calibration
from app.ai.price_prediction.guardrails_apply import (
    _apply_bid_price_granularity,
    _apply_prediction_guardrails,
)
from app.ai.price_prediction.price_regime import _attach_price_regime_metadata


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
    estimation_amount: float | None = None,
    reference_date: date | datetime | None = None,
    predictor_registry: Mapping[str, BasePricePredictor] | None = None,
) -> Dict[str, Any]:
    """Predict project price using the configured predictor stack with safe fallback.

    ``estimation_amount`` (추정가격) and ``reference_date`` (공고 기준일) drive the
    construction legal 낙찰하한 tier (2026-01-30 +2%p). Both default to ``None`` so
    non-construction / date-less callers are unaffected (tier not applied — the
    existing flat floor is preserved). ``estimation_amount`` is the notice 추정가격,
    NOT the 기초금액 ``budget`` used for pricing (#162).
    """
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
        estimation_amount=estimation_amount,
        reference_date=reference_date,
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
