"""Domain ports for AI-backed workflows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any, Iterable, Mapping


class PricePredictionPort(ABC):
    @abstractmethod
    def predict_price(
        self,
        *,
        budget: float,
        category: str,
        description: str,
        historical_records: Iterable[object] | None = None,
        agency_name: str | None = None,
        feedback_calibration: Mapping[str, Any] | None = None,
        business_type_code: str | None = None,
        business_group: str | None = None,
        legal_floor_bid_rate: float | None = None,
        estimation_amount: float | None = None,
        reference_date: date | datetime | None = None,
    ) -> dict[str, Any]:
        """Return a normalized price prediction payload.

        ``estimation_amount`` (추정가격) and ``reference_date`` (공고 기준일) drive the
        construction legal 낙찰하한 tier; both default to ``None`` (tier not applied).
        """


class BidRecommendationPort(ABC):
    @abstractmethod
    def recommend(
        self,
        *,
        project_data: Mapping[str, Any],
        user_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a normalized bid recommendation payload."""


class DocumentAnalysisPort(ABC):
    @abstractmethod
    def analyze(self, content: str, *, document_type: str = "specification") -> dict[str, Any]:
        """Return normalized document analysis fields."""
