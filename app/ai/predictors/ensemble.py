"""Experimental ensemble predictor with persisted-artifact inference."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.ai.predictors.base import (
    BasePricePredictor,
    PredictionResult,
    PredictorAvailability,
    PricePredictionContext,
)
from app.ai.predictors.historical import (
    HistoricalStatisticalPredictor,
    apply_high_rate_distribution_adjustment,
    apply_procurement_candidate_band,
    apply_procurement_rate_band,
    clamp_bid_rate,
    resolve_procurement_rate_band,
    summarize_historical_records,
)
from app.ai.predictors.lstm import extract_bid_rate_series, infer_lstm_sequence_signal, load_lstm_artifact
from app.ai.predictors.rate_band_spec import band_explanation_clause
from app.core.config import settings

_DEFAULT_COMPONENT_WEIGHTS = {
    "historical": 0.52,
    "momentum": 0.18,
    "mean_reversion": 0.15,
    "lstm": 0.15,
}

# --- Scenario-spread / candidate constants (moved verbatim from the inline
# arithmetic; the call sites keep their expression shape so floats are exact). ---
_SPREAD_FLOOR = 0.008
_SPREAD_HISTORICAL_STD_WEIGHT = 0.7
_AGGRESSIVE_SPREAD_MULTIPLIER = 0.85
_CANDIDATE_CONFIDENCE_WEIGHT_TAIL = 0.23
_CANDIDATE_CONFIDENCE_WEIGHT_BASE = 0.54

# --- Momentum / mean-reversion component blend weights (call-site operand order
# preserved: momentum = mean, last, projected; mean-reversion = recent, anchor). ---
_MOMENTUM_MEAN_WEIGHT = 0.35
_MOMENTUM_LAST_WEIGHT = 0.45
_MOMENTUM_PROJECTED_WEIGHT = 0.20
_MEAN_REVERSION_RECENT_WEIGHT = 0.7
_MEAN_REVERSION_ANCHOR_WEIGHT = 0.3

# --- Ensemble confidence formula constants (base + per-signal weights, score
# normalizers, diversity step/cap, output clamp). ---
_CONFIDENCE_BASE = 0.55
_CONFIDENCE_SAMPLE_WEIGHT = 0.16
_CONFIDENCE_AGREEMENT_WEIGHT = 0.13
_CONFIDENCE_STABILITY_WEIGHT = 0.1
_CONFIDENCE_SAMPLE_SATURATION = 32
_CONFIDENCE_SPREAD_NORMALIZER = 0.06
_CONFIDENCE_STD_NORMALIZER = 0.06
_CONFIDENCE_DIVERSITY_STEP = 0.03
_CONFIDENCE_DIVERSITY_CAP = 0.09
_CONFIDENCE_MIN = 0.45
_CONFIDENCE_MAX = 0.97


class EnsembleBidRatePredictor(BasePricePredictor):
    """Ensemble predictor that blends persisted component weights at inference time."""

    name = "ensemble_blend"
    family = "ensemble"

    def check_availability(self, context: PricePredictionContext) -> PredictorAvailability:
        """Validate whether the predictor is configured well enough to run."""
        if not settings.PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS:
            return PredictorAvailability(False, "Experimental predictors are disabled.")
        if context.historical_sample_size < settings.PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES:
            return PredictorAvailability(
                False,
                f"At least {settings.PRICE_PREDICTION_ENSEMBLE_MIN_SAMPLES} historical samples are required for ensemble inference.",
            )
        model_path = str(settings.PRICE_PREDICTION_ENSEMBLE_MODEL_PATH or "").strip()
        if not model_path:
            return PredictorAvailability(False, "No ensemble model artifact is configured.")
        if not Path(model_path).exists():
            return PredictorAvailability(False, f"Configured ensemble model artifact was not found: {model_path}")
        try:
            load_ensemble_artifact(model_path)
        except Exception as exc:
            return PredictorAvailability(False, f"Configured ensemble model artifact is invalid: {exc}")
        return PredictorAvailability(True)

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        """Predict a bid rate from a persisted ensemble model artifact."""
        model_path = str(settings.PRICE_PREDICTION_ENSEMBLE_MODEL_PATH or "").strip()
        artifact = load_ensemble_artifact(model_path)
        return PredictionResult.model_validate(
            build_ensemble_prediction_payload(context, artifact=artifact)
        )


def load_ensemble_artifact(model_source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Load one persisted ensemble artifact from JSON or reuse an embedded dictionary."""
    if isinstance(model_source, dict):
        raw_artifact = dict(model_source)
    else:
        path = Path(model_source)
        try:
            raw_artifact = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Ensemble model artifact was not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ensemble model artifact is not valid JSON: {path}") from exc

    return {
        "artifact_version": str(raw_artifact.get("artifact_version") or "1"),
        "model_version": str(raw_artifact.get("model_version") or "v2.0-ensemble"),
        "sequence_length": max(3, int(raw_artifact.get("sequence_length") or 8)),
        "momentum_window": max(3, int(raw_artifact.get("momentum_window") or 6)),
        "scenario_spread_multiplier": max(float(raw_artifact.get("scenario_spread_multiplier") or 1.0), 0.2),
        "confidence_bias": float(raw_artifact.get("confidence_bias") or 0.0),
        "component_weights": _normalize_component_weights(raw_artifact.get("component_weights")),
        "lstm_artifact": raw_artifact.get("lstm_artifact"),
        "lstm_artifact_path": str(raw_artifact.get("lstm_artifact_path") or "").strip() or None,
    }


def build_ensemble_prediction_payload(
    context: PricePredictionContext,
    *,
    artifact: dict[str, Any],
    historical_predictor: BasePricePredictor | None = None,
) -> dict[str, Any]:
    """Blend multiple bid-rate components into the normalized prediction payload.

    ``historical_predictor`` is an injectable seam over the historical
    statistical anchor. When it is ``None`` the payload lazily constructs the
    default :class:`HistoricalStatisticalPredictor` at the same point (and with
    the same per-call lifetime) as before, so production behavior is unchanged.
    """
    sequence_rates = extract_bid_rate_series(context.historical_records)
    if not sequence_rates:
        raise ValueError("No usable historical bid-rate sequence was available for ensemble inference.")

    historical_predictor = historical_predictor or HistoricalStatisticalPredictor()
    historical_prediction = historical_predictor.predict(context)
    historical_summary = summarize_historical_records(
        context.historical_records,
        agency_name=context.agency_name,
    )
    historical_rate = float(historical_prediction.predicted_bid_rate or 0.0)
    components = {
        "historical": historical_rate,
        "momentum": _estimate_momentum_rate(sequence_rates, window_size=int(artifact["momentum_window"])),
        "mean_reversion": _estimate_mean_reversion_rate(
            sequence_rates,
            anchor=historical_rate,
        ),
    }
    lstm_component = _load_optional_lstm_component(context, artifact=artifact)
    if lstm_component is not None:
        components["lstm"] = float(lstm_component["blended_rate"])

    component_weights = _normalize_available_weights(
        artifact["component_weights"],
        available_keys=components.keys(),
    )
    base_rate = clamp_bid_rate(
        sum(float(components[key]) * float(component_weights[key]) for key in component_weights)
    )
    base_rate, high_rate_adjustment = apply_high_rate_distribution_adjustment(
        base_rate,
        category=context.category,
        description=context.description,
        historical_summary=historical_summary,
        business_group=context.business_group,
        budget=context.budget,
        historical_rate=components.get("historical"),
    )
    base_rate = clamp_bid_rate(
        apply_procurement_rate_band(
            base_rate,
            category=context.category,
            description=context.description,
        )
    )
    rate_band = resolve_procurement_rate_band(category=context.category, description=context.description)

    component_values = list(components.values())
    component_spread = float(np.std(component_values)) if len(component_values) > 1 else 0.0
    historical_std = float(historical_summary.get("std_bid_rate") or 0.0)
    spread = max(
        _SPREAD_FLOOR,
        component_spread * float(artifact["scenario_spread_multiplier"]),
        historical_std * _SPREAD_HISTORICAL_STD_WEIGHT,
    )
    conservative_rate = clamp_bid_rate(base_rate - spread)
    aggressive_rate = clamp_bid_rate(
        base_rate + (spread * _AGGRESSIVE_SPREAD_MULTIPLIER)
    )
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
            "predicted_price": round(context.budget * conservative_rate, 2),
            "confidence_weight": _CANDIDATE_CONFIDENCE_WEIGHT_TAIL,
        },
        {
            "label": "base",
            "bid_rate": round(base_rate, 4),
            "predicted_price": round(context.budget * base_rate, 2),
            "confidence_weight": _CANDIDATE_CONFIDENCE_WEIGHT_BASE,
        },
        {
            "label": "aggressive",
            "bid_rate": round(aggressive_rate, 4),
            "predicted_price": round(context.budget * aggressive_rate, 2),
            "confidence_weight": _CANDIDATE_CONFIDENCE_WEIGHT_TAIL,
        },
    ]
    confidence_score = _estimate_ensemble_confidence(
        sample_size=len(sequence_rates),
        component_spread=component_spread,
        historical_std=historical_std,
        component_count=len(component_weights),
        confidence_bias=float(artifact["confidence_bias"]),
    )
    reserve_pattern = historical_summary.get("reserve_price_context")
    agency_match_sample_size = int(historical_summary.get("agency_match_sample_size", 0) or 0)

    return {
        "predicted_price": round(context.budget * base_rate, 2),
        "price_range_min": min(candidate["predicted_price"] for candidate in candidates),
        "price_range_max": max(candidate["predicted_price"] for candidate in candidates),
        "confidence_score": confidence_score,
        "model_version": str(artifact["model_version"]),
        "pricing_mode": "historical_blend",
        "historical_sample_size": int(historical_summary.get("sample_size", len(sequence_rates)) or 0),
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
        "explanation": _build_ensemble_explanation(
            components=components,
            component_weights=component_weights,
            artifact=artifact,
            agency_match_sample_size=agency_match_sample_size,
            rate_band=rate_band,
            high_rate_adjustment=high_rate_adjustment,
        ),
    }


def _load_optional_lstm_component(context: PricePredictionContext, *, artifact: dict[str, Any]) -> dict[str, Any] | None:
    """Load an optional embedded/configured LSTM artifact for the ensemble."""
    raw_lstm_artifact = artifact.get("lstm_artifact")
    configured_lstm_path = artifact.get("lstm_artifact_path") or str(settings.PRICE_PREDICTION_LSTM_MODEL_PATH or "").strip()

    if isinstance(raw_lstm_artifact, dict):
        lstm_artifact = load_lstm_artifact(raw_lstm_artifact)
        return infer_lstm_sequence_signal(context, artifact=lstm_artifact)

    if configured_lstm_path and Path(configured_lstm_path).exists():
        lstm_artifact = load_lstm_artifact(configured_lstm_path)
        return infer_lstm_sequence_signal(context, artifact=lstm_artifact)
    return None


def _estimate_momentum_rate(sequence_rates: list[float], *, window_size: int) -> float:
    """Estimate a momentum component from the most recent sequence window."""
    recent_window = np.asarray(sequence_rates[-min(len(sequence_rates), window_size):], dtype=float)
    if recent_window.size == 1:
        return clamp_bid_rate(float(recent_window[-1]))
    slope = float(np.polyfit(np.arange(recent_window.size), recent_window, 1)[0])
    projected_rate = float(recent_window[-1]) + slope
    momentum_rate = (
        (float(recent_window.mean()) * _MOMENTUM_MEAN_WEIGHT)
        + (float(recent_window[-1]) * _MOMENTUM_LAST_WEIGHT)
        + (projected_rate * _MOMENTUM_PROJECTED_WEIGHT)
    )
    return clamp_bid_rate(momentum_rate)


def _estimate_mean_reversion_rate(sequence_rates: list[float], *, anchor: float) -> float:
    """Estimate a component that favors recent data but gently reverts toward the historical anchor."""
    recent_window = np.asarray(sequence_rates[-min(len(sequence_rates), 8):], dtype=float)
    recent_mean = float(np.mean(recent_window)) if recent_window.size else float(anchor)
    reverted_rate = (recent_mean * _MEAN_REVERSION_RECENT_WEIGHT) + (
        float(anchor) * _MEAN_REVERSION_ANCHOR_WEIGHT
    )
    return clamp_bid_rate(reverted_rate)


def _estimate_ensemble_confidence(
    *,
    sample_size: int,
    component_spread: float,
    historical_std: float,
    component_count: int,
    confidence_bias: float,
) -> float:
    """Estimate confidence from sample depth and agreement across ensemble components."""
    sample_score = min(sample_size / _CONFIDENCE_SAMPLE_SATURATION, 1.0)
    agreement_score = max(
        0.0, 1.0 - min(component_spread / _CONFIDENCE_SPREAD_NORMALIZER, 1.0)
    )
    stability_score = max(
        0.0, 1.0 - min(historical_std / _CONFIDENCE_STD_NORMALIZER, 1.0)
    )
    diversity_bonus = min(
        max(component_count - 1, 0) * _CONFIDENCE_DIVERSITY_STEP,
        _CONFIDENCE_DIVERSITY_CAP,
    )
    confidence = (
        _CONFIDENCE_BASE
        + (sample_score * _CONFIDENCE_SAMPLE_WEIGHT)
        + (agreement_score * _CONFIDENCE_AGREEMENT_WEIGHT)
        + (stability_score * _CONFIDENCE_STABILITY_WEIGHT)
        + diversity_bonus
        + confidence_bias
    )
    return round(max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, confidence)), 2)


def _build_ensemble_explanation(
    *,
    components: dict[str, float],
    component_weights: dict[str, float],
    artifact: dict[str, Any],
    agency_match_sample_size: int,
    rate_band: str | None,
    high_rate_adjustment: dict[str, Any] | None = None,
) -> str:
    """Build a natural-language explanation for the ensemble output."""
    ordered_components = [
        f"{name}({components[name]:.4f} × {component_weights[name]:.0%})"
        for name in component_weights
    ]
    explanation = (
        f"artifact v{artifact['artifact_version']} ensemble이 "
        f"{', '.join(ordered_components)} 조합으로 기준 사정률을 계산했습니다."
    )
    if agency_match_sample_size > 0:
        explanation += f" 동일 기관 이력 {agency_match_sample_size}건도 함께 반영했습니다."
    band_clause = band_explanation_clause(rate_band)
    if band_clause is not None:
        explanation += f" {band_clause}."
    if high_rate_adjustment:
        explanation += " 최근 고율 낙찰 분포를 반영해 기준 사정률을 상향 보정했습니다."
    return explanation


def _normalize_component_weights(raw_weights: Any) -> dict[str, float]:
    """Normalize optional component weights into a stable sum-to-one mapping."""
    if not isinstance(raw_weights, dict):
        normalized = dict(_DEFAULT_COMPONENT_WEIGHTS)
    else:
        normalized = {
            key: max(float(raw_weights.get(key, default_value) or 0.0), 0.0)
            for key, default_value in _DEFAULT_COMPONENT_WEIGHTS.items()
        }
    total_weight = sum(normalized.values())
    if total_weight <= 0:
        return dict(_DEFAULT_COMPONENT_WEIGHTS)
    return {key: value / total_weight for key, value in normalized.items()}


def _normalize_available_weights(raw_weights: dict[str, float], *, available_keys) -> dict[str, float]:
    """Drop unavailable components and renormalize the remaining weights."""
    filtered = {
        key: max(float(raw_weights.get(key, 0.0) or 0.0), 0.0)
        for key in available_keys
        if key in raw_weights
    }
    total_weight = sum(filtered.values())
    if total_weight <= 0:
        equal_weight = 1.0 / max(len(filtered), 1)
        return {key: equal_weight for key in filtered}
    return {key: value / total_weight for key, value in filtered.items()}
