"""Price prediction AI module."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable

import numpy as np

from app.ai import guardrail_core
from app.ai.guardrail_core import GuardrailConfig
from app.ai.construction_scenario import (
    is_construction_era_floor_resolved,
    resolve_scenario_anchor_rates,
)
from app.ai.predictors import (
    BasePricePredictor,
    PricePredictionContext,
)
from app.ai.predictor_backtest import build_predictor_backtest_report
from app.ai.predictors.historical import (
    clamp_bid_rate,
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
    # 공사 era-correct 법정 하한 앵커(#197 tier 해석 + 발주처 밴드 부재 시). stance→rate
    # (aggressive/recommended/safe), floor+offset 을 [floor, ceiling] 로 clamp 한 값.
    # None 이면 앵커 미적용(기존 clamp 동작 그대로).
    construction_scenario_anchor: dict[str, float] | None = None


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


@dataclass(frozen=True)
class _PriceRegimeRule:
    """One first-match price-regime rule keyed on a boolean signal flag.

    The rule ORDER is the priority (the original elif chain), so this reads as an
    order-preserving rule list — deliberately NOT a dict dispatch, since section
    F of the characterization test locks both the routing order and the emitted
    (label, confidence, review_required) triple.
    """

    signal_flag: str
    label: str
    confidence: float
    review_required: bool


# First-match order == the original elif priority:
#   conflicting > deep_discount > near_100 > floor_bound; else ambiguous fallback.
_PRICE_REGIME_RULES: tuple[_PriceRegimeRule, ...] = (
    _PriceRegimeRule("conflicting", "ambiguous", 0.58, True),
    _PriceRegimeRule("deep_discount", "deep_discount", 0.86, False),
    _PriceRegimeRule("near_100", "near_100", 0.84, False),
    _PriceRegimeRule("floor_bound", "floor_bound", 0.82, False),
)
_PRICE_REGIME_AMBIGUOUS_FALLBACK = _PriceRegimeRule("", "ambiguous", 0.45, True)


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

    matched_rule = next(
        (rule for rule in _PRICE_REGIME_RULES if signal_flags[rule.signal_flag]),
        _PRICE_REGIME_AMBIGUOUS_FALLBACK,
    )
    price_regime_label = matched_rule.label
    confidence = matched_rule.confidence
    review_required = matched_rule.review_required

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


# --------------------------------------------------------------------------- #
# Price-regime signal keyword tables (declarative; interpreted by
# ``_detect_price_regime_signals``). Split by class so the title-semantic guards
# can treat each differently: explicit two-stage markers and strong price-
# competition cues always fire, while bare "2단계" and bare 견적/견적제출 are
# gated to suppress construction phase-noise and 수의 quote-submission false
# positives. These affect ONLY descriptive regime metadata (label / signals /
# review_required), never the recommended price — the price-moving band lives in
# ``resolve_procurement_rate_band`` and runs upstream of this metadata pass.
# --------------------------------------------------------------------------- #
_TWO_STAGE_EXPLICIT_MARKERS = (
    "규격·가격",
    "규격 가격",
    "규격가격",
    "가격분리",
    "가격 분리",
    "동시입찰",
    "동시 평가",
)
# Bare "2단계" only counts as 규격·가격 2단계 입찰 when a spec/price-separation cue
# co-occurs (otherwise it is construction phase-2 노이즈: "○○ 2단계 증설공사").
_TWO_STAGE_BARE_COOCCURRENCE_CUES = ("규격", "가격", "동시")
_NEGOTIATION_CUES = ("협상에 의한 계약", "협상", "제안서 평가", "제안")
_DIRECT_NEGOTIATED_CUES = ("수의시담", "수의 시담", "수의계약", "수의 계약", "(수의)", "[수의]")
# No-space form ONLY. "소액수의 견적"(소액수의 negotiated quote whose goods band settles
# at the competitive rate) contains "수의 견적" WITH a space, so the space variant would
# spuriously mark it as near_100 and collide with a competitive band → false conflict.
# "수의견적"(no space) cannot appear inside "소액수의 견적", so it is collision-free.
_NEGOTIATED_QUOTE_CUES = ("수의견적",)
_PRICE_COMPETITION_STRONG_CUES = ("가격입찰", "적격심사", "pq")
_PRICE_COMPETITION_QUOTE_CUES = ("소액수의 견적", "견적 제출", "견적제출")
_NEGOTIATED_RATE_BANDS = frozenset({"service_direct_negotiated", "service_high_negotiated"})


def _has_bare_two_stage(text: str) -> bool:
    """True when "2단계" appears not immediately preceded by a digit.

    The digit-boundary guard rejects substring matches inside a larger number
    such as "12단계"/"22단계" (which are not 2단계 규격·가격 입찰).
    """
    start = 0
    while True:
        pos = text.find("2단계", start)
        if pos < 0:
            return False
        if pos == 0 or not text[pos - 1].isdigit():
            return True
        start = pos + 1


def _has_two_stage_cue(text: str) -> bool:
    """True when the notice text carries a genuine 2단계(규격·가격 분리) bidding cue.

    Explicit 규격·가격/가격분리/동시입찰 markers always count. Bare "2단계" counts
    only when it clears the digit-boundary guard (not "12단계") AND a spec/price-
    separation cue (규격/가격/동시) co-occurs — otherwise it is construction
    phase-2 노이즈 (e.g. "태봉근린공원(2단계) 통신공사"). When the explicit markers
    are present a co-occurrence cue is already implied, so bare "2단계" is nearly
    inert by design.
    """
    if _contains_any(text, _TWO_STAGE_EXPLICIT_MARKERS):
        return True
    return _has_bare_two_stage(text) and _contains_any(
        text, _TWO_STAGE_BARE_COOCCURRENCE_CUES
    )


def _detect_price_regime_signals(*, category: str, text: str, rate_band: Any) -> dict[str, Any]:
    """Return broad mechanism signals used by the price-regime classifier."""
    signals: set[str] = set()
    has_two_stage = _has_two_stage_cue(text)
    has_negotiation = _contains_any(text, _NEGOTIATION_CUES)
    # 수의견적(no space) is a 수의(negotiated) quote context. For non-goods it reads as
    # 수의계약형(near-100), so fold it into the direct-negotiated cues (drives near_100
    # + direct_negotiated signal). Goods 소액수의 견적 is intentionally excluded — the
    # goods band settles it at the competitive rate (GOODS_PRICE_COMPETITIVE_KEYWORDS),
    # so its floor_bound regime stays consistent with the price.
    has_direct = _contains_any(text, _DIRECT_NEGOTIATED_CUES) or (
        category != "goods" and _contains_any(text, _NEGOTIATED_QUOTE_CUES)
    )
    # A bare 견적/견적제출 inside a 수의/negotiated context is 'submit a quote', not
    # competitive price bidding — suppress it so it does not raise a false
    # near_100 ∧ floor_bound conflict (→ ambiguous/review). Genuine 가격입찰/적격심사/pq
    # always fire (strong cues), and can still produce a real conflict.
    negotiated_context = (
        has_direct or has_negotiation or rate_band in _NEGOTIATED_RATE_BANDS
    )
    has_price_competition = _contains_any(text, _PRICE_COMPETITION_STRONG_CUES) or (
        _contains_any(text, _PRICE_COMPETITION_QUOTE_CUES) and not negotiated_context
    )
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
    estimation_amount: float | None = None,
    reference_date: date | datetime | None = None,
) -> Dict[str, Any]:
    """Apply category/group/agency bid-rate guardrails after all statistical adjustments."""
    guarded_prediction = dict(prediction)
    guardrail_context = _resolve_guardrail_context(
        budget=budget,
        category=category,
        business_group=business_group,
        legal_floor_bid_rate=legal_floor_bid_rate,
        agency_name=agency_name,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
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

    if guardrail_context.construction_scenario_anchor is not None:
        _annotate_construction_scenario_anchor(guarded_prediction, guardrail_context)

    if not floor_applied_labels and not ceiling_applied_labels:
        return guarded_prediction

    _mark_guardrail_application(
        guarded_prediction,
        context=guardrail_context,
        floor_labels=floor_applied_labels,
        ceiling_labels=ceiling_applied_labels,
    )
    return guarded_prediction


def _annotate_construction_scenario_anchor(
    guarded_prediction: Dict[str, Any],
    context: GuardrailContext,
) -> None:
    """공사 floor 앵커 적용을 설명 문구에 정직하게 남긴다(정직 명세 §2).

    낙찰 확률/단정 표현 없이, 앵커가 '과거 실낙찰 초과분 백분위수(historical percentile)
    기반'임과 공격 시나리오의 낙하(실격) 위험을 드러낸다.
    """
    anchor = context.construction_scenario_anchor or {}
    floor = context.floor_bid_rate
    if floor is None:
        return
    aggressive_rate = float(anchor.get("aggressive", floor))
    note = (
        f"공사 법정 낙찰하한 {floor:.3%} 위에 과거 실낙찰 초과분 백분위수(p25/p50/p75, "
        f"표본 기반 캘리브레이션)로 공격/추천/안전 시나리오를 앵커했습니다. 공격 시나리오"
        f"({aggressive_rate:.3%})는 최저적격 경쟁 타깃으로, 실현 낙찰하한이 더 높으면 "
        f"낙(실격) 위험이 있습니다."
    )
    guarded_prediction["explanation"] = _append_explanation_note(
        str(guarded_prediction.get("explanation", "") or ""),
        note,
    )


def _resolve_guardrail_context(
    *,
    budget: float,
    category: str | None,
    business_group: str | None,
    legal_floor_bid_rate: float | None,
    agency_name: str | None = None,
    estimation_amount: float | None = None,
    reference_date: date | datetime | None = None,
) -> GuardrailContext:
    # Collect every guardrail setting read once at the boundary (§4.7.4), then run
    # the pure core on the immutable snapshot.
    config = GuardrailConfig.from_settings(settings)
    # Resolve the category/group baseline and the agency-tightened band separately so
    # the guardrail reason can attribute the BINDING edge to the correct source. The
    # agency band only RAISES the floor / LOWERS the ceiling, so a resolved value that
    # differs from the baseline can only have been moved by the agency band. The
    # construction legal tier (estimation_amount/reference_date) is passed to BOTH so
    # it sits in the baseline too — that keeps the agency attribution isolated to the
    # agency band and lets a binding tier floor still receive the safety margin.
    base_configured_floor = guardrail_core.resolve_floor_bid_rate(
        config,
        category,
        business_group=business_group,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )
    configured_floor_bid_rate = guardrail_core.resolve_floor_bid_rate(
        config,
        category,
        business_group=business_group,
        agency_name=agency_name,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
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
    base_configured_ceiling = guardrail_core.resolve_ceiling_bid_rate(
        config,
        category,
        business_group=business_group,
    )
    ceiling_bid_rate = guardrail_core.resolve_ceiling_bid_rate(
        config,
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
        safe_floor_bid_rate = guardrail_core.resolve_safe_floor_bid_rate(
            config,
            floor_bid_rate,
            ceiling_bid_rate=ceiling_bid_rate,
        )
    # 공사 시나리오 floor 앵커: era-correct tier floor(#197)가 해석되고 발주처 밴드가
    # 없을 때만 켠다. 발주처 밴드가 있으면(agency 가 더 특이적) 기존 밴드/positioning 이
    # 우선이므로 앵커를 적용하지 않는다(우선순위를 선언적으로 고정). 앵커 기준은 위에서
    # 최종 해석한 floor_bid_rate 다(basis 일관성은 construction_scenario docstring 참조).
    construction_scenario_anchor: dict[str, float] | None = None
    if (
        not floor_from_agency
        and not ceiling_from_agency
        and is_construction_era_floor_resolved(category, estimation_amount, reference_date)
    ):
        construction_scenario_anchor = resolve_scenario_anchor_rates(
            floor_bid_rate=floor_bid_rate,
            ceiling_bid_rate=ceiling_bid_rate,
            offsets=config.construction_scenario_floor_offsets,
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
        construction_scenario_anchor=construction_scenario_anchor,
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


# 후보 taxonomy(통계 spread)와 시나리오 stance(투찰 전략)는 축이 다르다: 후보
# conservative=최저 추정가(낮은 율)=최경쟁=공격 stance, base=추천, aggressive=최고 추정가
# (높은 율)=낙하 안전=안전 stance. 값 기준(offset 오름차순)으로도 일치한다. 룩업으로
# 브리지를 선언한다(§4.5.2). 앵커가 켜졌을 때만 사용된다.
_CANDIDATE_LABEL_TO_SCENARIO_STANCE = {
    "conservative": "aggressive",
    "base": "recommended",
    "aggressive": "safe",
}


def _resolve_candidate_target_rate(
    candidate: dict[str, Any],
    *,
    context: GuardrailContext,
    original_bid_rate: float,
) -> float:
    """공사 앵커가 켜지면 후보 stance 의 floor-앵커 rate 로, 아니면 원래 rate 로 target 결정."""
    anchor = context.construction_scenario_anchor
    if not anchor:
        return original_bid_rate
    stance = _CANDIDATE_LABEL_TO_SCENARIO_STANCE.get(str(candidate.get("label") or "base"))
    if stance is None:
        return original_bid_rate
    anchored_rate = anchor.get(stance)
    return float(anchored_rate) if anchored_rate is not None else original_bid_rate


def _guard_candidate_bid_rate(
    candidate: dict[str, Any],
    *,
    budget: float,
    context: GuardrailContext,
) -> tuple[dict[str, Any], bool, bool]:
    original_bid_rate = float(candidate.get("bid_rate", 0.0) or 0.0)
    # 앵커 target(공사 floor 앵커) 또는 원래 rate 를 기존 guardrail clamp 에 그대로 통과시킨다
    # (후보 clamp 재사용 — 병렬 경로 없음). 결과는 항상 [safe_floor, ceiling] 안이다.
    target_bid_rate = _resolve_candidate_target_rate(
        candidate,
        context=context,
        original_bid_rate=original_bid_rate,
    )
    guarded_bid_rate = _clamp_rate_to_guardrails(
        target_bid_rate,
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
    config = GuardrailConfig.from_settings(settings)
    return guardrail_core.apply_bid_price_granularity(prediction, budget=budget, config=config)


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


def _floor_guardrail_label(source: str | None) -> str:
    if source == "legal":
        return "공고별 법정 최소 투찰률"
    if source == "category_and_legal":
        return "공고별 법정/업종별 최소 투찰률"
    return "업종별 최소 투찰률"


def _resolve_agency_bid_rate(agency_name: str | None, rate_map: dict[str, float] | None) -> float | None:
    """Backward-compatible shim; delegates to the pure guardrail core.

    Preserved so external importers (paper_bidding_backtest, tests) keep the
    ``app.ai.price_prediction`` symbol. The resolution itself lives in
    :mod:`app.ai.guardrail_core`.
    """
    return guardrail_core.resolve_agency_bid_rate(agency_name, rate_map)


def _resolve_floor_bid_rate(
    category: str | None,
    business_group: str | None = None,
    agency_name: str | None = None,
) -> float | None:
    """Backward-compatible shim; delegates to the pure guardrail core."""
    config = GuardrailConfig.from_settings(settings)
    return guardrail_core.resolve_floor_bid_rate(
        config, category, business_group=business_group, agency_name=agency_name
    )


def _resolve_ceiling_bid_rate(
    category: str | None,
    business_group: str | None = None,
    agency_name: str | None = None,
) -> float | None:
    """Backward-compatible shim; delegates to the pure guardrail core."""
    config = GuardrailConfig.from_settings(settings)
    return guardrail_core.resolve_ceiling_bid_rate(
        config, category, business_group=business_group, agency_name=agency_name
    )


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
