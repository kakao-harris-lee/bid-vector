"""Queued ML training helpers kept out of the API request path.

This package was decomposed from a single ``ml_training`` module; the import
surface (``from app.services.ml_training import PricePredictionTrainingService``)
is preserved verbatim via the re-exports below. The #184 training-time
hyperparameters and value objects live in :mod:`.constants` and are re-exported
so any ``app.services.ml_training.<name>`` attribute access keeps working.
"""

from __future__ import annotations

from .constants import (
    TrainingRunOptions,
    TrainingRunPaths,
    _ENSEMBLE_COMPONENT_WEIGHTS,
    _ENSEMBLE_CONFIDENCE_BIAS_CAP,
    _ENSEMBLE_CONFIDENCE_BIAS_SAMPLE_DIVISOR,
    _MOMENTUM_WINDOW_MAX,
    _MOMENTUM_WINDOW_MIN,
    _QUALITY_AGENCY_DIVERSITY_TARGET,
    _QUALITY_BID_RATE_VARIANCE_MAX,
    _QUALITY_LINKED_RESULT_COVERAGE_MIN,
    _QUALITY_PROJECT_DIVERSITY_TARGET,
    _QUALITY_RESERVE_PATTERN_COVERAGE_MIN,
    _QUALITY_SCORE_BASE,
    _QUALITY_SCORE_BLOCKING_PENALTY,
    _QUALITY_SCORE_WARNING_PENALTY,
    _SCENARIO_SPREAD_MULTIPLIER_NARROW,
    _SCENARIO_SPREAD_MULTIPLIER_WIDE,
    _SCENARIO_SPREAD_STD_THRESHOLD,
    _SEQUENCE_LENGTH_MAX,
    _SEQUENCE_LENGTH_MIN,
)
from .service import PricePredictionTrainingService

__all__ = [
    "PricePredictionTrainingService",
    "TrainingRunOptions",
    "TrainingRunPaths",
    "_ENSEMBLE_COMPONENT_WEIGHTS",
    "_ENSEMBLE_CONFIDENCE_BIAS_CAP",
    "_ENSEMBLE_CONFIDENCE_BIAS_SAMPLE_DIVISOR",
    "_MOMENTUM_WINDOW_MAX",
    "_MOMENTUM_WINDOW_MIN",
    "_QUALITY_AGENCY_DIVERSITY_TARGET",
    "_QUALITY_BID_RATE_VARIANCE_MAX",
    "_QUALITY_LINKED_RESULT_COVERAGE_MIN",
    "_QUALITY_PROJECT_DIVERSITY_TARGET",
    "_QUALITY_RESERVE_PATTERN_COVERAGE_MIN",
    "_QUALITY_SCORE_BASE",
    "_QUALITY_SCORE_BLOCKING_PENALTY",
    "_QUALITY_SCORE_WARNING_PENALTY",
    "_SCENARIO_SPREAD_MULTIPLIER_NARROW",
    "_SCENARIO_SPREAD_MULTIPLIER_WIDE",
    "_SCENARIO_SPREAD_STD_THRESHOLD",
    "_SEQUENCE_LENGTH_MAX",
    "_SEQUENCE_LENGTH_MIN",
]
