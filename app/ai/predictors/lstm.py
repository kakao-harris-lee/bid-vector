"""Experimental LSTM predictor with persisted-artifact inference."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from pydantic import JsonValue

from app.ai.predictors.artifact_contracts import (
    ArtifactScalar,
    PersistedLSTMArtifact,
    read_persisted_artifact,
)
from app.ai.predictors.base import (
    BasePricePredictor,
    PredictionResult,
    PredictorAvailability,
    PricePredictionContext,
)
from app.ai.predictors.historical import (
    HistoricalStatisticalPredictor,
    clamp_bid_rate,
    read_record_value,
    summarize_historical_records,
)
from app.core.config import settings

# 아티팩트 로딩 실패 문구의 주어(단일 출처 — 기존 오류 메시지를 그대로 유지한다).
_LSTM_ARTIFACT_LABEL = "LSTM model artifact"

_DEFAULT_LSTM_BLEND_WEIGHTS = {
    "lstm": 0.68,
    "historical": 0.22,
    "trend": 0.10,
}

# --- Scenario-spread / candidate constants (moved verbatim from inline
# arithmetic; call sites keep their expression shape so floats are exact). ---
_SPREAD_FLOOR = 0.008
_SPREAD_GAP_WEIGHT = 0.45
_SPREAD_MULTIPLIER_FLOOR = 0.2
_AGGRESSIVE_SPREAD_MULTIPLIER = 0.85
_CANDIDATE_CONFIDENCE_WEIGHT_TAIL = 0.22
_CANDIDATE_CONFIDENCE_WEIGHT_BASE = 0.56

# --- Trend component blend weights (call-site operand order: mean, last,
# projected). ---
_TREND_MEAN_WEIGHT = 0.45
_TREND_LAST_WEIGHT = 0.35
_TREND_PROJECTED_WEIGHT = 0.20

# --- LSTM confidence formula constants (base + per-signal weights, score
# normalizers, output clamp). ---
_CONFIDENCE_BASE = 0.54
_CONFIDENCE_SAMPLE_WEIGHT = 0.18
_CONFIDENCE_STABILITY_WEIGHT = 0.15
_CONFIDENCE_AGREEMENT_WEIGHT = 0.1
_CONFIDENCE_SEQUENCE_MULTIPLIER = 2
_CONFIDENCE_STD_NORMALIZER = 0.06
_CONFIDENCE_AGREEMENT_NORMALIZER = 0.08
_CONFIDENCE_MIN = 0.45
_CONFIDENCE_MAX = 0.97


class LSTMBidRatePredictor(BasePricePredictor):
    """Sequence-model predictor backed by a persisted lightweight LSTM artifact."""

    name = "lstm_sequence"
    family = "sequence_model"

    def check_availability(self, context: PricePredictionContext) -> PredictorAvailability:
        """Validate whether the predictor is configured well enough to run."""
        if not settings.PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS:
            return PredictorAvailability(False, "Experimental predictors are disabled.")
        if context.historical_sample_size < settings.PRICE_PREDICTION_LSTM_MIN_SAMPLES:
            return PredictorAvailability(
                False,
                f"At least {settings.PRICE_PREDICTION_LSTM_MIN_SAMPLES} historical samples are required for LSTM inference.",
            )
        model_path = str(settings.PRICE_PREDICTION_LSTM_MODEL_PATH or "").strip()
        if not model_path:
            return PredictorAvailability(False, "No LSTM model artifact is configured.")
        if not Path(model_path).exists():
            return PredictorAvailability(False, f"Configured LSTM model artifact was not found: {model_path}")
        try:
            load_lstm_artifact(model_path)
        except Exception as exc:
            return PredictorAvailability(False, f"Configured LSTM model artifact is invalid: {exc}")
        return PredictorAvailability(True)

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        """Predict a bid rate from a persisted LSTM model artifact."""
        model_path = str(settings.PRICE_PREDICTION_LSTM_MODEL_PATH or "").strip()
        artifact = load_lstm_artifact(model_path)
        signal = infer_lstm_sequence_signal(context, artifact=artifact)
        return PredictionResult.model_validate(
            build_lstm_prediction_payload(context, artifact=artifact, signal=signal)
        )


def load_lstm_artifact(model_source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Load one persisted LSTM artifact from JSON or reuse an embedded dictionary.

    The persisted JSON is promoted into the declared
    :class:`~app.ai.predictors.artifact_contracts.PersistedLSTMArtifact` contract
    first; the normalized mapping returned here (and every coercion expression
    that builds it) is unchanged, so inference output is identical.
    """
    raw_artifact = read_persisted_artifact(
        model_source, model=PersistedLSTMArtifact, label=_LSTM_ARTIFACT_LABEL
    )
    weights = raw_artifact.weight_block()
    gate_weights = {
        "W_i": _coerce_matrix(weights.W_i, name="W_i"),
        "U_i": _coerce_matrix(weights.U_i, name="U_i"),
        "b_i": _coerce_vector(weights.b_i, name="b_i"),
        "W_f": _coerce_matrix(weights.W_f, name="W_f"),
        "U_f": _coerce_matrix(weights.U_f, name="U_f"),
        "b_f": _coerce_vector(weights.b_f, name="b_f"),
        "W_o": _coerce_matrix(weights.W_o, name="W_o"),
        "U_o": _coerce_matrix(weights.U_o, name="U_o"),
        "b_o": _coerce_vector(weights.b_o, name="b_o"),
        "W_c": _coerce_matrix(weights.W_c, name="W_c"),
        "U_c": _coerce_matrix(weights.U_c, name="U_c"),
        "b_c": _coerce_vector(weights.b_c, name="b_c"),
        "dense_W": _coerce_matrix(weights.dense_W or weights.dense_weight, name="dense_W"),
        "dense_b": _coerce_vector(weights.dense_b or weights.dense_bias, name="dense_b"),
    }
    hidden_size = int(gate_weights["b_i"].shape[0])
    _validate_lstm_shapes(gate_weights, hidden_size=hidden_size)

    return {
        "artifact_version": str(raw_artifact.artifact_version or "1"),
        "model_version": str(raw_artifact.model_version or "v2.0-lstm"),
        "sequence_length": max(3, int(raw_artifact.sequence_length or 8)),
        "input_center": float(raw_artifact.input_center or 0.9),
        "input_scale": max(float(raw_artifact.input_scale or 0.05), 1e-6),
        "output_scale": max(float(raw_artifact.output_scale or 0.03), 1e-6),
        "output_bias": float(raw_artifact.output_bias or 0.9),
        "scenario_spread_multiplier": max(float(raw_artifact.scenario_spread_multiplier or 1.0), 0.2),
        "confidence_bias": float(raw_artifact.confidence_bias or 0.0),
        "blend_weights": _normalize_weights(
            raw_artifact.blend_weights,
            defaults=_DEFAULT_LSTM_BLEND_WEIGHTS,
        ),
        "weights": gate_weights,
    }


def infer_lstm_sequence_signal(
    context: PricePredictionContext,
    *,
    artifact: dict[str, Any],
    historical_predictor: BasePricePredictor | None = None,
) -> dict[str, Any]:
    """Run the persisted sequence model and blend it with stable historical signals.

    ``historical_predictor`` is an injectable seam over the historical
    statistical anchor. When it is ``None`` the signal lazily constructs the
    default :class:`HistoricalStatisticalPredictor` at the same point (and with
    the same per-call lifetime) as before, so production behavior is unchanged.
    """
    sequence_rates = extract_bid_rate_series(context.historical_records)
    if not sequence_rates:
        raise ValueError("No usable historical bid-rate sequence was available for LSTM inference.")

    historical_predictor = historical_predictor or HistoricalStatisticalPredictor()
    historical_prediction = historical_predictor.predict(context)
    historical_summary = summarize_historical_records(
        context.historical_records,
        agency_name=context.agency_name,
    )
    window_size = min(len(sequence_rates), int(artifact["sequence_length"]))
    window = np.asarray(sequence_rates[-window_size:], dtype=float)
    raw_lstm_rate = _run_lstm_window(window, artifact=artifact)
    historical_rate = float(historical_prediction.predicted_bid_rate or 0.0)
    trend_rate = _estimate_trend_rate(sequence_rates)
    blended_rate = _blend_predicted_rate(
        artifact["blend_weights"],
        lstm_rate=raw_lstm_rate,
        historical_rate=historical_rate,
        trend_rate=trend_rate,
    )

    return {
        "sequence_rates": sequence_rates,
        "window_size": window_size,
        "window_std": float(np.std(window)) if len(window) > 1 else 0.0,
        "raw_lstm_rate": raw_lstm_rate,
        "historical_rate": historical_rate,
        "trend_rate": trend_rate,
        "blended_rate": blended_rate,
        "historical_summary": historical_summary,
    }


def build_lstm_prediction_payload(
    context: PricePredictionContext,
    *,
    artifact: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any]:
    """Convert one sequence-model signal into the normalized prediction payload."""
    sample_size = int(signal["historical_summary"].get("sample_size", len(signal["sequence_rates"])))
    reserve_pattern = signal["historical_summary"].get("reserve_price_context")
    agency_match_sample_size = int(signal["historical_summary"].get("agency_match_sample_size", 0) or 0)
    base_rate = clamp_bid_rate(signal["blended_rate"])
    spread = _estimate_lstm_spread(signal=signal, artifact=artifact)
    conservative_rate = clamp_bid_rate(base_rate - spread)
    aggressive_rate = clamp_bid_rate(
        base_rate + (spread * _AGGRESSIVE_SPREAD_MULTIPLIER)
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
    confidence_score = _estimate_lstm_confidence(signal=signal, artifact=artifact)

    return {
        "predicted_price": round(context.budget * base_rate, 2),
        "price_range_min": min(candidate["predicted_price"] for candidate in candidates),
        "price_range_max": max(candidate["predicted_price"] for candidate in candidates),
        "confidence_score": confidence_score,
        "model_version": str(artifact["model_version"]),
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
        "explanation": _build_lstm_explanation(
            signal=signal,
            artifact=artifact,
            sample_size=sample_size,
            agency_match_sample_size=agency_match_sample_size,
        ),
    }


def extract_bid_rate_series(historical_records: tuple[object, ...]) -> list[float]:
    """Extract a time-ordered bid-rate series from ORM rows or dictionaries."""
    sequence_points: list[tuple[str, int, float]] = []
    fallback_sequence: list[float] = []
    for index, record in enumerate(historical_records):
        bid_rate = _resolve_bid_rate(record)
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


def _resolve_bid_rate(record: object) -> float | None:
    """Read a usable bid rate from a historical record payload."""
    bid_rate = float(read_record_value(record, "bid_rate") or 0.0)
    if bid_rate <= 0:
        predicted_price = float(read_record_value(record, "predicted_price") or 0.0)
        base_amount = float(read_record_value(record, "base_amount") or 0.0)
        if predicted_price > 0 and base_amount > 0:
            bid_rate = predicted_price / base_amount
    if not 0.5 <= bid_rate <= 1.5:
        return None
    return round(float(bid_rate), 6)


def _run_lstm_window(window: np.ndarray, *, artifact: dict[str, Any]) -> float:
    """Run one single-step LSTM forward pass over the most recent sequence window."""
    normalized_window = ((window - float(artifact["input_center"])) / float(artifact["input_scale"]))
    weights = artifact["weights"]
    hidden_size = int(weights["b_i"].shape[0])
    hidden_state = np.zeros(hidden_size, dtype=float)
    cell_state = np.zeros(hidden_size, dtype=float)

    for raw_value in normalized_window:
        input_vector = np.asarray([float(raw_value)], dtype=float)
        input_gate = _sigmoid(weights["W_i"] @ input_vector + weights["U_i"] @ hidden_state + weights["b_i"])
        forget_gate = _sigmoid(weights["W_f"] @ input_vector + weights["U_f"] @ hidden_state + weights["b_f"])
        output_gate = _sigmoid(weights["W_o"] @ input_vector + weights["U_o"] @ hidden_state + weights["b_o"])
        candidate_state = np.tanh(weights["W_c"] @ input_vector + weights["U_c"] @ hidden_state + weights["b_c"])
        cell_state = (forget_gate * cell_state) + (input_gate * candidate_state)
        hidden_state = output_gate * np.tanh(cell_state)

    dense_output = weights["dense_W"] @ hidden_state + weights["dense_b"]
    if dense_output.ndim == 0:
        output_value = float(dense_output)
    else:
        output_value = float(np.ravel(dense_output)[0])
    predicted_rate = float(artifact["output_bias"]) + (float(artifact["output_scale"]) * output_value)
    return clamp_bid_rate(predicted_rate)


def _estimate_trend_rate(sequence_rates: list[float]) -> float:
    """Estimate a simple momentum-aware trend rate from the recent sequence."""
    if not sequence_rates:
        return 0.0
    recent_window = np.asarray(sequence_rates[-min(len(sequence_rates), 6):], dtype=float)
    if recent_window.size == 1:
        return clamp_bid_rate(float(recent_window[-1]))
    slope = float(np.polyfit(np.arange(recent_window.size), recent_window, 1)[0])
    projected_rate = float(recent_window[-1]) + slope
    trend_rate = (
        (float(recent_window.mean()) * _TREND_MEAN_WEIGHT)
        + (float(recent_window[-1]) * _TREND_LAST_WEIGHT)
        + (projected_rate * _TREND_PROJECTED_WEIGHT)
    )
    return clamp_bid_rate(trend_rate)


def _blend_predicted_rate(weights: dict[str, float], *, lstm_rate: float, historical_rate: float, trend_rate: float) -> float:
    """Blend the raw sequence output with stable statistical anchors."""
    blended_rate = (
        (lstm_rate * float(weights.get("lstm", 0.0)))
        + (historical_rate * float(weights.get("historical", 0.0)))
        + (trend_rate * float(weights.get("trend", 0.0)))
    )
    return clamp_bid_rate(blended_rate)


def _estimate_lstm_spread(*, signal: dict[str, Any], artifact: dict[str, Any]) -> float:
    """Estimate conservative/base/aggressive spread around the sequence-model rate."""
    base_gap = (
        abs(float(signal["raw_lstm_rate"]) - float(signal["historical_rate"]))
        * _SPREAD_GAP_WEIGHT
    )
    volatility_gap = float(signal["window_std"]) * max(
        float(artifact["scenario_spread_multiplier"]), _SPREAD_MULTIPLIER_FLOOR
    )
    return max(_SPREAD_FLOOR, base_gap, volatility_gap)


def _estimate_lstm_confidence(*, signal: dict[str, Any], artifact: dict[str, Any]) -> float:
    """Estimate confidence from sequence depth, stability, and agreement with the baseline."""
    sample_size = len(signal["sequence_rates"])
    sample_score = min(
        sample_size
        / max(int(artifact["sequence_length"]) * _CONFIDENCE_SEQUENCE_MULTIPLIER, 1),
        1.0,
    )
    stability_score = max(
        0.0, 1.0 - min(float(signal["window_std"]) / _CONFIDENCE_STD_NORMALIZER, 1.0)
    )
    agreement_score = max(
        0.0,
        1.0
        - min(
            abs(float(signal["raw_lstm_rate"]) - float(signal["historical_rate"]))
            / _CONFIDENCE_AGREEMENT_NORMALIZER,
            1.0,
        ),
    )
    confidence = (
        _CONFIDENCE_BASE
        + (sample_score * _CONFIDENCE_SAMPLE_WEIGHT)
        + (stability_score * _CONFIDENCE_STABILITY_WEIGHT)
        + (agreement_score * _CONFIDENCE_AGREEMENT_WEIGHT)
        + float(artifact["confidence_bias"])
    )
    return round(max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, confidence)), 2)


def _build_lstm_explanation(
    *,
    signal: dict[str, Any],
    artifact: dict[str, Any],
    sample_size: int,
    agency_match_sample_size: int,
) -> str:
    """Build a natural-language explanation for the sequence-model output."""
    details = [
        f"최근 {signal['window_size']}건 시계열을 LSTM artifact v{artifact['artifact_version']}로 추론하고",
        f"통계 baseline {signal['historical_rate']:.4f} 및 추세값 {signal['trend_rate']:.4f}와 가중 결합해",
        f"기준 사정률 {signal['blended_rate']:.4f}를 계산했습니다.",
    ]
    if agency_match_sample_size > 0:
        details.append(f"동일 기관 이력 {agency_match_sample_size}건도 함께 반영했습니다.")
    details.append(f"전체 학습 창은 {sample_size}건입니다.")
    return " ".join(details)


def _normalize_weights(
    raw_weights: dict[str, ArtifactScalar] | None, *, defaults: dict[str, float]
) -> dict[str, float]:
    """Normalize optional blend weights into a stable sum-to-one mapping.

    ``None`` covers both "absent" and "present but not a mapping" — the artifact
    contract already folds the latter into absence, so the previous
    ``isinstance(raw_weights, dict)`` branch has the same meaning here.
    """
    if raw_weights is None:
        normalized = dict(defaults)
    else:
        normalized = {
            key: max(float(raw_weights.get(key, default_value) or 0.0), 0.0)
            for key, default_value in defaults.items()
        }
    total_weight = sum(normalized.values())
    if total_weight <= 0:
        return dict(defaults)
    return {key: value / total_weight for key, value in normalized.items()}


def _coerce_matrix(raw_value: JsonValue, *, name: str) -> np.ndarray:
    """Coerce one persisted matrix into a 2D numpy array."""
    if raw_value is None:
        raise ValueError(f"Missing required LSTM weight '{name}'")
    array = np.asarray(raw_value, dtype=float)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.ndim != 2:
        raise ValueError(f"LSTM weight '{name}' must be one- or two-dimensional")
    return array


def _coerce_vector(raw_value: JsonValue, *, name: str) -> np.ndarray:
    """Coerce one persisted vector into a 1D numpy array."""
    if raw_value is None:
        raise ValueError(f"Missing required LSTM weight '{name}'")
    array = np.asarray(raw_value, dtype=float)
    if array.ndim == 0:
        array = array.reshape(1)
    if array.ndim != 1:
        raise ValueError(f"LSTM weight '{name}' must be one-dimensional")
    return array


def _validate_lstm_shapes(weights: dict[str, np.ndarray], *, hidden_size: int) -> None:
    """Validate that all persisted tensors line up for inference."""
    gate_names = ["i", "f", "o", "c"]
    for gate_name in gate_names:
        input_matrix = weights[f"W_{gate_name}"]
        recurrent_matrix = weights[f"U_{gate_name}"]
        bias_vector = weights[f"b_{gate_name}"]
        if input_matrix.shape != (hidden_size, 1):
            raise ValueError(f"W_{gate_name} must have shape ({hidden_size}, 1)")
        if recurrent_matrix.shape != (hidden_size, hidden_size):
            raise ValueError(f"U_{gate_name} must have shape ({hidden_size}, {hidden_size})")
        if bias_vector.shape != (hidden_size,):
            raise ValueError(f"b_{gate_name} must have shape ({hidden_size},)")
    if weights["dense_W"].shape[1] != hidden_size:
        raise ValueError("dense_W must project from the LSTM hidden size")
    if weights["dense_b"].shape[0] != weights["dense_W"].shape[0]:
        raise ValueError("dense_b size must match dense_W output dimension")


def _sigmoid(value: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid used by the lightweight LSTM cell."""
    clipped = np.clip(value, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-clipped))
