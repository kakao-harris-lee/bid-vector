"""Probability / group calibration readers for the historical predictor.

``load_group_calibration`` intentionally lives in the package ``__init__`` (not
here) so tests can monkeypatch ``app.ai.predictors.historical.load_group_calibration``
and have the prediction core observe the patched value via late binding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Renormalized confidence/matched weights for the *calibration* raw signal.
# The legacy heuristic mixes three signals (matched 0.38 + confidence 0.42 +
# history 0.20). ``historical_sample_size`` is NOT persisted on ``PaperBid``, so
# the training dataset would always see history=0 while inference sees history>0
# — a train/serve skew that systematically miscalibrates the Platt curve. We drop
# the history term entirely for calibration and renormalize the surviving two
# weights to sum to 1 (0.38/0.80, 0.42/0.80), so training and serving feed the
# Platt curve a byte-identical raw built from the SAME two signals.
_CALIBRATION_MATCHED_WEIGHT = 0.38 / 0.80  # 0.475
_CALIBRATION_CONFIDENCE_WEIGHT = 0.42 / 0.80  # 0.525


def calibration_raw_signal(confidence_score: float, matched_score: float) -> float:
    """Single source of truth for the calibration Platt-curve input.

    Imported by training (``ml_training._raw_probability_signal``), inference
    (``apply_probability_calibration``) and the backtest path so all three feed the
    fitted curve an identical raw value. Uses ONLY ``confidence_score`` and
    ``matched_score`` — the two signals that are reliably available at both train
    and serve time — and is independent of the legacy 3-signal heuristic fallback.
    """
    confidence = max(0.0, min(1.0, float(confidence_score or 0.0)))
    matched = max(0.0, min(1.0, float(matched_score or 0.0)))
    raw = (
        matched * _CALIBRATION_MATCHED_WEIGHT
        + confidence * _CALIBRATION_CONFIDENCE_WEIGHT
    )
    return max(0.0, min(1.0, raw))


def load_probability_calibration() -> dict[str, dict[str, Any]]:
    """Read summary.probability_calibration from the active ensemble artifact.

    Mirrors :func:`load_group_calibration`: best-effort, any IO/JSON failure or
    missing block returns an empty dict so callers fall back to the legacy
    heuristic probability with no crash (offline / fresh environments included).
    """
    from app.core.config import settings

    manifest_path_raw = (settings.PRICE_PREDICTION_ENSEMBLE_MODEL_PATH or "").strip()
    if not manifest_path_raw:
        return {}
    candidate = Path(manifest_path_raw)
    if not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text())
    except Exception:
        return {}
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return {}
    calibration = summary.get("probability_calibration")
    return calibration if isinstance(calibration, dict) else {}


def apply_probability_calibration(
    features: dict[str, Any],
    *,
    calibration: dict[str, dict[str, Any]] | None = None,
) -> float | None:
    """Map inference-time signals onto a calibrated P(낙찰) via the active curve.

    ``features`` carries inference-time signals; only ``confidence_score`` and
    ``matched_score`` feed the calibration raw (see :func:`calibration_raw_signal`
    — ``historical_sample_size`` is intentionally excluded to keep train and serve
    raw values identical). The raw is then passed through the per-group Platt (or
    base-rate) curve fitted on settled outcomes.

    Returns ``None`` when no usable calibration artifact exists so callers fall back
    to the legacy heuristic. Never raises on malformed artifacts.
    """
    import math

    table = calibration if calibration is not None else load_probability_calibration()
    if not table:
        return None

    group_key = str(features.get("business_group") or "")
    curve = table.get(group_key) or table.get("__global__")
    if not isinstance(curve, dict):
        return None

    raw = calibration_raw_signal(
        features.get("confidence_score", 0.0),
        features.get("matched_score", 0.0),
    )

    try:
        scale = float(curve.get("scale", 0.0) or 0.0)
        bias = float(curve.get("bias", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    z = (scale * raw) + bias
    z = max(-30.0, min(30.0, z))
    probability = 1.0 / (1.0 + math.exp(-z))
    return round(max(0.0, min(1.0, probability)), 2)
