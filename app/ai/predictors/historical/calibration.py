"""Probability / group calibration readers for the historical predictor.

``load_group_calibration`` intentionally lives in the package ``__init__`` (not
here) so tests can monkeypatch ``app.ai.predictors.historical.load_group_calibration``
and have the prediction core observe the patched value via late binding.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.ai.predictors.artifact_contracts import (
    CalibrationValue,
    PersistedArtifactCalibrationBlocks,
    PersistedArtifactSummaryDocument,
)

logger = logging.getLogger(__name__)


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


def load_active_artifact_calibration_blocks() -> PersistedArtifactCalibrationBlocks:
    """Read the ``summary`` calibration blocks from the active ensemble artifact.

    Single decode path for both calibration readers (group + probability) so the
    two cannot drift apart: one file read, one contract, one degrade policy.

    Best-effort by design: an unset path, a missing file, a corrupt file or a
    payload that violates the contract all degrade to **empty blocks**, and the
    callers fall back to the legacy statistics/heuristic with no crash (offline or
    freshly provisioned environments included). The broad ``except Exception`` is
    deliberate — this runs on the inference path and optional calibration must
    never take a prediction down; the previous implementation had the same policy.
    Corruption is no longer silent (``logger.warning``), and the artifact payload
    itself is never logged.
    """
    from app.core.config import settings

    manifest_path_raw = (settings.PRICE_PREDICTION_ENSEMBLE_MODEL_PATH or "").strip()
    if not manifest_path_raw:
        return PersistedArtifactCalibrationBlocks()
    candidate = Path(manifest_path_raw)
    if not candidate.is_file():
        return PersistedArtifactCalibrationBlocks()
    try:
        document = PersistedArtifactSummaryDocument.model_validate_json(
            candidate.read_text()
        )
    except Exception as exc:
        logger.warning(
            "predictor 아티팩트 calibration summary 해석 실패 — 보정 없이 "
            "legacy 통계로 degrade (path=%s, reason=%s)",
            candidate,
            type(exc).__name__,
        )
        return PersistedArtifactCalibrationBlocks()
    return document.summary


def load_probability_calibration() -> dict[str, dict[str, CalibrationValue]]:
    """Read summary.probability_calibration from the active ensemble artifact.

    Mirrors :func:`load_group_calibration`: best-effort, any IO/JSON failure or
    missing block returns an empty dict so callers fall back to the legacy
    heuristic probability with no crash (offline / fresh environments included).
    """
    return load_active_artifact_calibration_blocks().probability_calibration


def apply_probability_calibration(
    features: dict[str, Any],
    *,
    calibration: dict[str, dict[str, CalibrationValue]] | None = None,
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
