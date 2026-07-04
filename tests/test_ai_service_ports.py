import json

from app.ai.llm_interfaces import LLMRequest, LLMResponse, RequestExecutor, TokenUsage
from app.ai.service_adapters import (
    ExecutorDocumentAnalysisPort,
    FunctionPricePredictionPort,
    HeuristicBidRecommendationPort,
    HeuristicDocumentAnalysisPort,
)


class RecordingExecutor(RequestExecutor):
    def __init__(self, response_text: str | None = None) -> None:
        self.requests: list[LLMRequest] = []
        self._response_text = response_text or json.dumps(
            {
                "key_requirements": ["secure login"],
                "complexity_score": 0.4,
                "estimated_effort": 1.2,
                "risks": ["security concerns"],
            }
        )

    def execute(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text=self._response_text,
            usage=TokenUsage(context_tokens=12, output_tokens=18, total_tokens=30),
            model=request.model,
        )


def test_function_price_prediction_port_preserves_current_payload_shape():
    port = FunctionPricePredictionPort()

    result = port.predict_price(
        budget=100_000_000.0,
        category="software",
        description="port compatibility",
        historical_records=[{"bid_rate": 0.91}, {"bid_rate": 0.92}, {"bid_rate": 0.93}],
    )

    assert result["predicted_price"] > 0
    assert "predictor_name" in result
    assert "price_regime_features" in result


def test_heuristic_bid_recommendation_port_preserves_current_payload_shape():
    port = HeuristicBidRecommendationPort()

    result = port.recommend(
        project_data={"budget": 10_000.0, "category": "software"},
        user_data={"win_rate": 0.5},
    )

    assert result["recommended_bid"] > 0
    assert "confidence_score" in result
    assert "market_analysis" in result


def test_heuristic_document_analysis_port_preserves_current_payload_shape():
    port = HeuristicDocumentAnalysisPort()

    result = port.analyze("1. must support audit logging", document_type="specification")

    assert result["key_requirements"]
    assert 0 <= result["complexity_score"] <= 1
    assert "risks" in result


def test_executor_document_analysis_port_uses_llm_request_contract():
    executor = RecordingExecutor()
    port = ExecutorDocumentAnalysisPort(executor=executor, model="test-model")

    result = port.analyze("must support secure login", document_type="specification")

    assert result["key_requirements"] == ["secure login"]
    assert executor.requests
    request = executor.requests[0]
    assert request.operation == "document_analysis"
    assert request.model == "test-model"
    assert "secure login" in request.prompt


def test_executor_document_analysis_port_defaults_invalid_json_response():
    executor = RecordingExecutor(response_text="not json")
    port = ExecutorDocumentAnalysisPort(executor=executor)

    result = port.analyze("must support secure login", document_type="specification")

    assert result == {
        "key_requirements": [],
        "complexity_score": 0.0,
        "estimated_effort": 0.0,
        "risks": [],
    }


def test_executor_document_analysis_port_defaults_non_object_json_response():
    executor = RecordingExecutor(response_text=json.dumps(["secure login"]))
    port = ExecutorDocumentAnalysisPort(executor=executor)

    result = port.analyze("must support secure login", document_type="specification")

    assert result == {
        "key_requirements": [],
        "complexity_score": 0.0,
        "estimated_effort": 0.0,
        "risks": [],
    }


def test_executor_document_analysis_port_defaults_wrong_field_types():
    executor = RecordingExecutor(
        response_text=json.dumps(
            {
                "key_requirements": "secure login",
                "complexity_score": "high",
                "estimated_effort": {"days": 2},
                "risks": "security concerns",
            }
        )
    )
    port = ExecutorDocumentAnalysisPort(executor=executor)

    result = port.analyze("must support secure login", document_type="specification")

    assert result == {
        "key_requirements": [],
        "complexity_score": 0.0,
        "estimated_effort": 0.0,
        "risks": [],
    }
