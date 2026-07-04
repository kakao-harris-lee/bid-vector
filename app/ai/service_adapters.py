"""Default AI service adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping as MappingABC
from typing import Any, Iterable, Mapping

from app.ai.bid_recommendation import get_bid_recommendation
from app.ai.document_analyzer import analyze_document
from app.ai.llm_interfaces import LLMRequest, RequestExecutor, TokenBudget
from app.ai.price_prediction import predict_price
from app.ai.service_interfaces import (
    BidRecommendationPort,
    DocumentAnalysisPort,
    PricePredictionPort,
)


class FunctionPricePredictionPort(PricePredictionPort):
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
    ) -> dict[str, Any]:
        return predict_price(
            budget=budget,
            category=category,
            description=description,
            historical_records=historical_records,
            agency_name=agency_name,
            feedback_calibration=dict(feedback_calibration or {}),
            business_type_code=business_type_code,
            business_group=business_group,
            legal_floor_bid_rate=legal_floor_bid_rate,
        )


class HeuristicBidRecommendationPort(BidRecommendationPort):
    def recommend(
        self,
        *,
        project_data: Mapping[str, Any],
        user_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        return get_bid_recommendation(dict(project_data), dict(user_data))


class HeuristicDocumentAnalysisPort(DocumentAnalysisPort):
    def analyze(self, content: str, *, document_type: str = "specification") -> dict[str, Any]:
        return analyze_document(content=content, document_type=document_type)


class ExecutorDocumentAnalysisPort(DocumentAnalysisPort):
    def __init__(
        self,
        *,
        executor: RequestExecutor,
        model: str | None = None,
        budget: TokenBudget | None = None,
    ) -> None:
        self._executor = executor
        self._model = model
        self._budget = budget

    def analyze(self, content: str, *, document_type: str = "specification") -> dict[str, Any]:
        request = LLMRequest(
            operation="document_analysis",
            prompt=f"Analyze this {document_type} document and return JSON:\n\n{content}",
            model=self._model,
            budget=self._budget,
            metadata={"document_type": document_type},
        )
        response = self._executor.execute(request)
        payload = _load_document_analysis_payload(response.text)
        return _normalize_document_analysis_payload(payload)


def _load_document_analysis_payload(text: str) -> MappingABC[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, MappingABC):
        return {}
    return payload


def _normalize_document_analysis_payload(payload: MappingABC[str, Any]) -> dict[str, Any]:
    return {
        "key_requirements": _coerce_list(payload.get("key_requirements")),
        "complexity_score": _coerce_float(payload.get("complexity_score")),
        "estimated_effort": _coerce_float(payload.get("estimated_effort")),
        "risks": _coerce_list(payload.get("risks")),
    }


def _coerce_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _coerce_float(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
