"""Predictor registry construction."""

from __future__ import annotations

from collections.abc import Mapping

from app.ai.predictors.base import BasePricePredictor
from app.ai.predictors.ensemble import EnsembleBidRatePredictor
from app.ai.predictors.historical import HistoricalStatisticalPredictor


def build_default_predictor_registry() -> dict[str, BasePricePredictor]:
    """Build the default in-process predictor registry.

    ``lstm`` 은 2026-08-09 은퇴했다. 은퇴한 키를 선호 설정이 계속 가리킬 수 있으므로,
    그 폴백 사유는 ``price_prediction.orchestration._RETIRED_PREDICTOR_KEYS`` 가 선언한다.
    """
    return {
        "historical": HistoricalStatisticalPredictor(),
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
