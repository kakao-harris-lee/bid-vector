"""Experimental ensemble predictor with persisted-artifact inference."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

import numpy as np

from app.ai.predictors.artifact_contracts import (
    ArtifactSource,
    ArtifactScalar,
    PersistedEnsembleArtifact,
    VersionAwareArtifactProvider,
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
    apply_high_rate_distribution_adjustment,
    apply_procurement_candidate_band,
    apply_procurement_rate_band,
    clamp_bid_rate,
    extract_bid_rate_series,
    resolve_procurement_rate_band,
    summarize_historical_records,
)
from app.ai.predictors.rate_band_spec import band_explanation_clause
from app.core.config import settings

# 아티팩트 로딩 실패 문구의 주어(단일 출처 — 기존 오류 메시지를 그대로 유지한다).
_ENSEMBLE_ARTIFACT_LABEL = "Ensemble model artifact"

# sequence-model(lstm) 축은 은퇴했다(2026-08-09). 남은 세 축의 **상대 비율**은 종전과
# 같으므로 그 비율을 그대로 선언하고(추적 가능성), 선언 상수는 합 1 의 확률 벡터로
# 파생한다.
#
# 이 상수의 역할 범위: **서빙측 fallback 기본값**이다 — 아티팩트가 ``component_weights`` 를
# 싣지 않았을 때만 쓰인다(아티팩트에 *기록*되는 값은 ``ml_training/constants.py`` 소관).
# 비율(합 0.85)을 그대로 두면 선언값이 확률 벡터가 아니게 되어, 이 기본값을 읽는 사람이
# "나머지 15%는 어디 갔나"로 오해한다.
#
# 산출 불변 근거: 나눗셈 결과가 float 상 정확히 합 1 이고 재정규화에 멱등이라, 호출부의
# ``_normalize_*`` 를 한 번 더 통과해도 값이 움직이지 않는다. 이를 고정하는 것은 ensemble
# 골든이 **아니다** — 골든 아티팩트는 명시 ``component_weights`` 를 실어 이 기본값 경로에
# 도달하지 않는다. 실제 pin 은
# ``test_ensemble_artifact_non_mapping_blocks_fall_back_to_defaults`` (비매핑 →
# 기본값 폴백 시 0.52/0.18/0.15 ÷ 0.85 를 단언)와
# ``test_declared_weights_are_idempotent_under_renormalization`` 이다.
_COMPONENT_WEIGHT_RATIOS = {
    "historical": 0.52,
    "momentum": 0.18,
    "mean_reversion": 0.15,
}
_DEFAULT_COMPONENT_WEIGHTS = {
    key: ratio / sum(_COMPONENT_WEIGHT_RATIOS.values())
    for key, ratio in _COMPONENT_WEIGHT_RATIOS.items()
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


class NormalizedEnsembleArtifact(TypedDict):
    """Validated, inference-ready representation of one ensemble release."""

    artifact_version: str
    model_version: str
    sequence_length: int
    momentum_window: int
    scenario_spread_multiplier: float
    confidence_bias: float
    component_weights: dict[str, float]


class EnsembleBidRatePredictor(BasePricePredictor):
    """Ensemble predictor that blends persisted component weights at inference time."""

    name = "ensemble_blend"
    family = "ensemble"

    def __init__(
        self,
        *,
        artifact_provider: VersionAwareArtifactProvider[NormalizedEnsembleArtifact]
        | None = None,
    ) -> None:
        self._artifact_provider = artifact_provider or _ENSEMBLE_ARTIFACT_PROVIDER

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
            self._artifact_provider.load(model_path)
        except Exception as exc:
            return PredictorAvailability(False, f"Configured ensemble model artifact is invalid: {exc}")
        return PredictorAvailability(True)

    def predict(self, context: PricePredictionContext) -> PredictionResult:
        """Predict a bid rate from a persisted ensemble model artifact."""
        model_path = str(settings.PRICE_PREDICTION_ENSEMBLE_MODEL_PATH or "").strip()
        artifact = self._artifact_provider.load(model_path)
        return PredictionResult.model_validate(
            build_ensemble_prediction_payload(context, artifact=artifact)
        )


def _load_ensemble_artifact_uncached(
    model_source: ArtifactSource,
) -> NormalizedEnsembleArtifact:
    """Load one persisted ensemble artifact from JSON or reuse an embedded dictionary.

    The persisted JSON is promoted into the declared
    :class:`~app.ai.predictors.artifact_contracts.PersistedEnsembleArtifact`
    contract first; the normalized mapping below (and every coercion expression
    that builds it) is unchanged, so inference output is identical.
    """
    raw_artifact = read_persisted_artifact(
        model_source, model=PersistedEnsembleArtifact, label=_ENSEMBLE_ARTIFACT_LABEL
    )

    return {
        "artifact_version": str(raw_artifact.artifact_version or "1"),
        "model_version": str(raw_artifact.model_version or "v2.0-ensemble"),
        "sequence_length": max(3, int(raw_artifact.sequence_length or 8)),
        "momentum_window": max(3, int(raw_artifact.momentum_window or 6)),
        "scenario_spread_multiplier": max(float(raw_artifact.scenario_spread_multiplier or 1.0), 0.2),
        "confidence_bias": float(raw_artifact.confidence_bias or 0.0),
        "component_weights": _normalize_component_weights(raw_artifact.component_weights),
    }


_ENSEMBLE_ARTIFACT_PROVIDER = VersionAwareArtifactProvider(
    _load_ensemble_artifact_uncached
)


def load_ensemble_artifact(
    model_source: ArtifactSource,
) -> NormalizedEnsembleArtifact:
    """Load a normalized ensemble artifact through the version cache."""
    return _ENSEMBLE_ARTIFACT_PROVIDER.load(model_source)


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


def _normalize_component_weights(
    raw_weights: dict[str, ArtifactScalar] | None,
) -> dict[str, float]:
    """Normalize optional component weights into a stable sum-to-one mapping.

    ``None`` covers both "absent" and "present but not a mapping" — the artifact
    contract folds the latter into absence, so the previous
    ``isinstance(raw_weights, dict)`` branch has the same meaning here.
    """
    if raw_weights is None:
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
