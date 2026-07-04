"""Factory helpers for default AI ports."""

from __future__ import annotations

from app.ai.service_adapters import (
    FunctionPricePredictionPort,
    HeuristicBidRecommendationPort,
    HeuristicDocumentAnalysisPort,
)
from app.ai.service_interfaces import (
    BidRecommendationPort,
    DocumentAnalysisPort,
    PricePredictionPort,
)


def build_price_prediction_port() -> PricePredictionPort:
    return FunctionPricePredictionPort()


def build_bid_recommendation_port() -> BidRecommendationPort:
    return HeuristicBidRecommendationPort()


def build_document_analysis_port() -> DocumentAnalysisPort:
    return HeuristicDocumentAnalysisPort()
