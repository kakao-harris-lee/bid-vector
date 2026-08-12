"""Default AI service adapters."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from app.ai.bid_recommendation import get_bid_recommendation
from app.ai.document_analyzer import analyze_document
from app.ai.llm_interfaces import LLMRequest, RequestExecutor, TokenBudget
from app.ai.llm_output_contracts import LLMDocumentAnalysisOutput
from app.ai.price_prediction import predict_price
from app.ai.service_interfaces import (
    BidRecommendationPort,
    DocumentAnalysisPort,
    PricePredictionPort,
)

logger = logging.getLogger(__name__)


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
        estimation_amount: float | None = None,
        reference_date: date | datetime | None = None,
        published_floor_bid_rate: float | None = None,
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
            estimation_amount=estimation_amount,
            reference_date=reference_date,
            published_floor_bid_rate=published_floor_bid_rate,
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
    """LLM-executor implementation of ``DocumentAnalysisPort``.

    This is an intentional seam in the ports/adapters design (introduced by
    commit ``bcde4e3``): it routes document analysis through the
    ``app.ai.llm_interfaces`` request/response contract and a
    :class:`~app.ai.llm_interfaces.RequestExecutor`.

    It is deliberately NOT wired into production yet — the factory
    :func:`app.ai.factory.build_document_analysis_port` currently returns
    :class:`HeuristicDocumentAnalysisPort`. This LLM path is the deferred
    alternative that will be swapped in once an executor is configured.

    Its request/response contract is exercised by
    ``tests/test_ai_service_ports.py``. Keep it — it is not dead code.
    """

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


def _load_document_analysis_payload(text: str) -> LLMDocumentAnalysisOutput:
    """Validate one LLM response body against the document-analysis contract.

    An unparseable body or a non-object top level degrades to the empty contract
    (the previous ``{}`` behavior), but no longer silently: the operator gets a
    warning telling them the LLM path produced nothing usable. The response text
    is deliberately NOT logged — it can carry the analyzed document itself.
    Per-field type tolerance lives in the contract, so one bad field no longer
    discards the rest of the response.
    """
    try:
        return LLMDocumentAnalysisOutput.model_validate_json(text)
    except (TypeError, ValidationError) as exc:
        logger.warning(
            "문서 분석 LLM 출력 해석 실패 — 빈 분석 결과로 degrade (reason=%s)",
            type(exc).__name__,
        )
        return LLMDocumentAnalysisOutput()


def _normalize_document_analysis_payload(
    payload: LLMDocumentAnalysisOutput,
) -> dict[str, Any]:
    """Render the validated contract into the ``DocumentAnalysisPort`` payload.

    Field order and value types match the previous inline dict exactly, so the
    port's response shape is unchanged.
    """
    return {
        "key_requirements": list(payload.key_requirements),
        "complexity_score": payload.complexity_score,
        "estimated_effort": payload.estimated_effort,
        "risks": list(payload.risks),
    }
