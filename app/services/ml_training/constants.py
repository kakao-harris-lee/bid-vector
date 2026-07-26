"""Training-time constants and value objects for price-predictor training.

Split out from the original single ``ml_training`` module so the training-time
artifact hyperparameters (#184 named constants) live behind one declarative source
of truth. Values and arithmetic are preserved verbatim (float-identical); the
package re-export surface (``from app.services.ml_training import ...``) is
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Training-time artifact hyperparameters
#
# These numeric constants are baked into the predictor artifacts (LSTM/ensemble)
# and the dataset-quality gate at TRAINING time. They are intentionally kept as
# module-level named constants and NOT promoted to Settings: each value is
# written into the on-disk artifact and covered by the signed release manifest's
# sha256, so making them env-tunable would let two runs over the same dataset
# emit divergent artifacts and break reproducibility / the promotion gate.
# Literals and arithmetic order are preserved verbatim (float-identical).
# Characterized by tests/test_ml_training_constants.py.
# ---------------------------------------------------------------------------

# Sequence/window bounds shared by the LSTM and ensemble artifact builders.
_SEQUENCE_LENGTH_MIN = 3
_SEQUENCE_LENGTH_MAX = 12
_MOMENTUM_WINDOW_MIN = 3
_MOMENTUM_WINDOW_MAX = 6

# LSTM artifact.
_LSTM_DEFAULT_STD = 0.025  # std used when a run has <2 bid-rate samples
_LSTM_MIN_STD = 0.01  # floor so a zero-variance history still yields a usable scale
_LSTM_OUTPUT_SCALE_STD_FACTOR = 0.35
_LSTM_SCENARIO_SPREAD_MULTIPLIER = 1.0
_LSTM_CONFIDENCE_BIAS_CAP = 0.08
_LSTM_CONFIDENCE_BIAS_SAMPLE_DIVISOR = 1000
# Flat dict of immutable floats; copied on use so the artifact never aliases it.
_LSTM_BLEND_WEIGHTS = {"lstm": 0.6, "historical": 0.3, "trend": 0.1}

# Ensemble artifact.
_SCENARIO_SPREAD_STD_THRESHOLD = 0.04
_SCENARIO_SPREAD_MULTIPLIER_NARROW = 1.0
_SCENARIO_SPREAD_MULTIPLIER_WIDE = 1.15
_ENSEMBLE_CONFIDENCE_BIAS_CAP = 0.06
_ENSEMBLE_CONFIDENCE_BIAS_SAMPLE_DIVISOR = 1200
_ENSEMBLE_COMPONENT_WEIGHTS = {
    "historical": 0.5,
    "momentum": 0.2,
    "mean_reversion": 0.15,
    "lstm": 0.15,
}

# Dataset-quality gate thresholds & scoring weights.
_QUALITY_PROJECT_DIVERSITY_TARGET = 3
_QUALITY_AGENCY_DIVERSITY_TARGET = 2
_QUALITY_LINKED_RESULT_COVERAGE_MIN = 0.25
_QUALITY_RESERVE_PATTERN_COVERAGE_MIN = 0.25
_QUALITY_BID_RATE_VARIANCE_MAX = 0.08
_QUALITY_SCORE_BASE = 100
_QUALITY_SCORE_BLOCKING_PENALTY = 40
_QUALITY_SCORE_WARNING_PENALTY = 10


@dataclass(frozen=True)
class TrainingRunOptions:
    release_tag: str
    category: str | None
    agency_name: str | None
    limit: int
    notes: str | None
    publish_remote: bool
    create_manifest: bool


@dataclass(frozen=True)
class TrainingRunPaths:
    training_dir: Path
    predictor_lstm_dir: Path
    predictor_ensemble_dir: Path
    dataset_path: Path
    summary_path: Path
    dataset_quality_path: Path
    comparison_report_path: Path
    lstm_artifact_path: Path
    ensemble_artifact_path: Path
