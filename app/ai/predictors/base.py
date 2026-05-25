"""Base contracts for price prediction predictors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PricePredictionContext:
    """Immutable input payload shared across predictor implementations."""

    budget: float
    category: str
    description: str
    historical_records: tuple[object, ...]
    agency_name: str | None = None
    business_type_code: str | None = None
    business_group: str | None = None

    @property
    def historical_sample_size(self) -> int:
        """Return the number of available historical rows."""
        return len(self.historical_records)


@dataclass(frozen=True)
class PredictorAvailability:
    """Describe whether a predictor is currently runnable."""

    available: bool
    reason: str | None = None


class BasePricePredictor(ABC):
    """Base class for all predictor implementations."""

    name = "base"
    family = "generic"

    def check_availability(self, context: PricePredictionContext) -> PredictorAvailability:
        """Return whether the predictor can run for the given context."""
        return PredictorAvailability(available=True)

    @abstractmethod
    def predict(self, context: PricePredictionContext) -> dict[str, Any]:
        """Return a normalized prediction payload."""
