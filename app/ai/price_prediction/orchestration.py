"""Predictor selection and the ``predict_price`` orchestration entry point.

Split out of the former single-file ``price_prediction`` module (§4.5 size
decomposition). The pipeline order (select → run → feedback → guardrails →
granularity → regime → metadata) and every call site are unchanged. Feedback,
guardrail, and regime stages are imported from the sibling decomposition modules.

Typed predictor output (P4.1)
-----------------------------
``BasePricePredictor.predict`` now returns a validated
:class:`~app.ai.predictors.base.PredictionResult` instead of a raw dict, and the
predictor-selection metadata travels as :class:`PredictorSelection` instead of a
loose ``dict[str, Any]``. The feedback / guardrail / granularity / regime stages
still take and return plain dicts — they are migrated separately — so this module
demotes once before that chain and promotes once after it. ``predict_price``
keeps its public ``dict`` contract (``PricePredictionResponse`` is unchanged);
``serialize_prediction_result`` is the single demotion point.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable

from app.ai.predictors import (
    BasePricePredictor,
    PredictionResult,
    PricePredictionContext,
    serialize_prediction_result,
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
    "distribution": "distribution",
    "reserve_distribution": "distribution",
    "reserve_draw_distribution": "distribution",
    "award_rate_gbm": "award_rate_gbm",
    "gbm": "award_rate_gbm",
    "lightgbm": "award_rate_gbm",
    "auto": "auto",
    "best": "auto",
    "backtest": "auto",
}

# 은퇴한 predictor 키 → 은퇴 사유(선언 데이터). 운영 ``.env`` 의 선호 설정은 코드와 함께
# 바뀌지 않으므로, 은퇴한 키를 계속 가리키는 상태가 정상 경로다. 그때 폴백 사유가 "알 수
# 없는 키"로 나오면 운영자가 오타를 의심하게 되므로 은퇴 사실을 그대로 남긴다.
_RETIRED_PREDICTOR_KEYS = {
    "lstm": (
        "예정가가 복수예비가격 추첨으로 정해져 사정률 시계열에 학습할 신호가 없다는 "
        "판정에 따라 2026-08-09 은퇴했습니다"
    ),
}

# Selector identities, declared once instead of repeated as inline literals at
# every return site (§4.5-1).
_SELECTOR_CONFIGURED_PREFERENCE = "configured_preference"
_SELECTOR_ROLLING_BACKTEST = "rolling_backtest"


@dataclass(frozen=True)
class PredictorSelection:
    """Why one predictor was chosen, in a typed shape instead of a loose dict.

    Previously this travelled as ``dict[str, Any]`` and every consumer re-read it
    with ``.get(key, default)``, so a renamed key silently fell back to the
    default. The fields map 1:1 onto the selection metadata that
    ``_attach_predictor_metadata`` stamps onto the result.
    """

    selector_name: str
    selection_reason: str | None
    backtest_sample_count: int = 0
    backtest_average_absolute_error_rate: float | None = None
    backtest_report: dict[str, Any] | None = None


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
    predictor, fallback_reason, selection = _select_predictor(context, registry=registry)
    result, used_predictor, fallback_reason = _run_predictor(
        context=context,
        predictor=predictor,
        historical_predictor=registry["historical"],
        fallback_reason=fallback_reason,
    )
    # The stages below still take/return plain dicts (migrated separately), so the
    # typed result is demoted once here and promoted once after the chain. The
    # guardrail arithmetic and its application order are untouched.
    payload = serialize_prediction_result(result)
    payload = _apply_feedback_calibration(payload, feedback_calibration)
    payload = _apply_prediction_guardrails(
        payload,
        budget=context.budget,
        category=context.category,
        business_group=business_group,
        legal_floor_bid_rate=legal_floor_bid_rate,
        agency_name=agency_name,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )
    payload = _apply_bid_price_granularity(payload, budget=context.budget)
    payload = _attach_price_regime_metadata(payload, context=context)
    annotated = _attach_predictor_metadata(
        PredictionResult.model_validate(payload),
        predictor=used_predictor,
        fallback_reason=fallback_reason,
        selection=selection,
    )
    return serialize_prediction_result(annotated)


def _select_predictor(
    context: PricePredictionContext,
    *,
    registry: dict[str, BasePricePredictor],
) -> tuple[BasePricePredictor, str | None, PredictorSelection]:
    """Choose the configured predictor or fall back to the stable baseline."""
    preferred_key = _normalize_predictor_key(settings.PRICE_PREDICTION_PREFERRED_PREDICTOR)
    historical_predictor = registry["historical"]
    requested_predictor = registry.get(preferred_key)

    if preferred_key == "auto":
        return _select_predictor_by_backtest(context, registry=registry, historical_predictor=historical_predictor)

    retirement_reason = _RETIRED_PREDICTOR_KEYS.get(preferred_key)
    if requested_predictor is None and retirement_reason is not None:
        return historical_predictor, (
            f"Predictor '{preferred_key}' was retired: {retirement_reason}. "
            "Falling back to the historical baseline."
        ), PredictorSelection(
            selector_name=_SELECTOR_CONFIGURED_PREFERENCE,
            selection_reason=f"retired predictor preference '{preferred_key}'",
        )

    if requested_predictor is None:
        return historical_predictor, (
            f"Unknown predictor preference '{settings.PRICE_PREDICTION_PREFERRED_PREDICTOR}'. "
            "Falling back to the historical baseline."
        ), PredictorSelection(
            selector_name=_SELECTOR_CONFIGURED_PREFERENCE,
            selection_reason="unknown predictor preference",
        )

    availability = requested_predictor.check_availability(context)
    if availability.available:
        return requested_predictor, None, PredictorSelection(
            selector_name=_SELECTOR_CONFIGURED_PREFERENCE,
            selection_reason=f"Configured preference selected {requested_predictor.name}.",
        )
    if requested_predictor.name == historical_predictor.name:
        return historical_predictor, None, PredictorSelection(
            selector_name=_SELECTOR_CONFIGURED_PREFERENCE,
            selection_reason="Configured historical baseline selected.",
        )
    return historical_predictor, f"Requested {requested_predictor.name} predictor is unavailable: {availability.reason}", PredictorSelection(
        selector_name=_SELECTOR_CONFIGURED_PREFERENCE,
        selection_reason=f"Configured {requested_predictor.name} predictor was unavailable; historical baseline selected.",
    )


def _select_predictor_by_backtest(
    context: PricePredictionContext,
    *,
    registry: dict[str, BasePricePredictor],
    historical_predictor: BasePricePredictor,
) -> tuple[BasePricePredictor, str | None, PredictorSelection]:
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

    return selected_predictor, None, PredictorSelection(
        selector_name=_SELECTOR_ROLLING_BACKTEST,
        selection_reason=selection_reason,
        backtest_sample_count=sample_count,
        backtest_average_absolute_error_rate=best_error,
        backtest_report=report,
    )


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
) -> tuple[PredictionResult, BasePricePredictor, str | None]:
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


def _copy_with_validated_keys(
    result: PredictionResult,
    update: dict[str, Any],
) -> PredictionResult:
    """``model_copy(update=...)`` with pydantic's unknown-key hole closed.

    ``model_copy`` does NOT validate the update mapping: an unknown key such as
    ``predictor_nam`` is silently dropped rather than raising, which bypasses
    ``extra="forbid"`` and re-admits the exact "quiet typo'd key" defect class
    this DTO exists to kill. So the key set is checked against the declared fields
    first and a violation fails loudly instead of vanishing.
    """
    unknown = sorted(set(update) - set(PredictionResult.model_fields))
    if unknown:
        raise ValueError(f"unknown PredictionResult field(s) in update: {unknown}")
    return result.model_copy(update=update)


def _attach_predictor_metadata(
    prediction: PredictionResult,
    *,
    predictor: BasePricePredictor,
    fallback_reason: str | None,
    selection: PredictorSelection,
) -> PredictionResult:
    """Return a copy of ``prediction`` carrying the predictor-selection metadata.

    Immutable update: the previous version built a shallow dict copy and then
    assigned nine keys into it one at a time, so the annotated payload passed
    through intermediate half-annotated states and nothing stopped a caller from
    mutating it further. The copy produces the finished result in one step and
    leaves ``prediction`` untouched. Values are statically typed here
    (``PredictorSelection`` + the predictor class attributes), so field values
    need no re-validation — but the KEYS go through
    ``_copy_with_validated_keys`` because raw ``model_copy`` would swallow a typo.

    ``backtest_report`` stays ABSENT unless the auto selector produced one — that
    key is conditional in the payload contract, not merely nullable.
    """
    updates: dict[str, Any] = {
        "predictor_name": predictor.name,
        "predictor_family": predictor.family,
        "fallback_reason": fallback_reason,
        "selector_name": selection.selector_name,
        "selection_reason": selection.selection_reason,
        "backtest_sample_count": int(selection.backtest_sample_count or 0),
        "backtest_average_absolute_error_rate": selection.backtest_average_absolute_error_rate,
        "training_window_size": int(prediction.historical_sample_size or 0),
    }
    if selection.backtest_report is not None:
        updates["backtest_report"] = selection.backtest_report
    return _copy_with_validated_keys(prediction, updates)
