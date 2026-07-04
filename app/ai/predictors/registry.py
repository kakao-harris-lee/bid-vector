"""Predictor registry construction."""

from __future__ import annotations

from collections.abc import Mapping

from app.ai.predictors.base import BasePricePredictor
from app.ai.predictors.ensemble import EnsembleBidRatePredictor
from app.ai.predictors.historical import HistoricalStatisticalPredictor
from app.ai.predictors.lstm import LSTMBidRatePredictor


def build_default_predictor_registry() -> dict[str, BasePricePredictor]:
    """Build the default in-process predictor registry."""
    return {
        "historical": HistoricalStatisticalPredictor(),
        "lstm": LSTMBidRatePredictor(),
        "ensemble": EnsembleBidRatePredictor(),
    }


def normalize_predictor_registry(
    registry: Mapping[str, BasePricePredictor] | None,
) -> dict[str, BasePricePredictor]:
    """Return a mutable predictor registry with the historical fallback present."""
    if registry is None:
        resolved = build_default_predictor_registry()
    else:
        resolved = dict(registry)
    if "historical" not in resolved:
        resolved["historical"] = HistoricalStatisticalPredictor()
    return resolved
