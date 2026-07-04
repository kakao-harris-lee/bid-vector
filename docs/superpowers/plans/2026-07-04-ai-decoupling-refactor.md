# AI Decoupling Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce coupling around AI/prediction workflows while preserving current API responses, persistence fields, and deterministic prediction behavior.

**Architecture:** Keep existing public functions such as `predict_price()` as compatibility facades. Add narrow ports/adapters around AI helpers, extract route orchestration into a workflow service, and split predictor construction/selection behind a registry seam. Use `app/ai/llm_interfaces.py` only for prompt-shaped LLM adapters; do not force statistical price predictors through `LLMRequest`.

**Tech Stack:** Python 3.11+, FastAPI dependency injection, SQLAlchemy sessions, pytest, dataclass/ABC contracts.

---

## Expert Findings Integrated

- Architecture explorer: `app/ai/price_prediction.py` is the main coupling point; `llm_interfaces.py` is relevant to document/rationale LLM paths, not numeric predictors.
- Service/API explorer: `app/api/predictions.py` should delegate to a `PredictionWorkflowService`; domain ports should wrap price prediction, bid recommendation, and document analysis.
- Test strategy explorer: focused baseline currently passes with `57 passed`; write hand fakes for interfaces instead of SDK mocks or broad `MagicMock`.

## File Structure

- Create `app/ai/service_interfaces.py`: domain ABCs for price prediction, bid recommendation, and document analysis.
- Create `app/ai/service_adapters.py`: default adapters that wrap current functions plus optional executor-backed document analysis using `LLMRequest`.
- Create `app/ai/factory.py`: default factory functions for AI ports and workflow assembly.
- Create `app/services/prediction_workflow.py`: DB-backed workflow extracted from `app/api/predictions.py`.
- Create `app/ai/predictors/registry.py`: predictor registry construction and normalization seam.
- Modify `app/api/predictions.py`: route handlers depend on `PredictionWorkflowService` instead of concrete AI functions.
- Modify `app/ai/price_prediction.py`: accept optional predictor registry/fallback injection while preserving the existing signature for callers.
- Modify `app/services/opportunity_analysis.py`: constructor-inject price/recommendation ports while preserving default behavior.
- Later wave: modify `app/services/paper_bidding_backtest.py`, `app/services/smoke_test.py`, and `app/services/bid_summary.py` after primary seams are stable.

## Parallelization Map

Safe to parallelize:
- Read-only review of each task after implementation.
- Secondary consumer migration only after Task 4 is green, with disjoint write scopes.

Do not parallelize:
- Tasks 1-4 implementation. They share `app/ai` and route/workflow contracts and must land sequentially.

---

### Task 1: Add AI Domain Ports And LLM-Capable Adapters

**Files:**
- Create: `app/ai/service_interfaces.py`
- Create: `app/ai/service_adapters.py`
- Create: `app/ai/factory.py`
- Create: `tests/test_ai_service_ports.py`
- Prerequisite input already created before this plan: `app/ai/llm_interfaces.py`

- [x] **Step 1: Write failing port tests**

Create `tests/test_ai_service_ports.py`:

```python
import json

from app.ai.llm_interfaces import LLMRequest, LLMResponse, RequestExecutor, TokenUsage
from app.ai.service_adapters import (
    ExecutorDocumentAnalysisPort,
    FunctionPricePredictionPort,
    HeuristicBidRecommendationPort,
    HeuristicDocumentAnalysisPort,
)


class RecordingExecutor(RequestExecutor):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def execute(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text=json.dumps(
                {
                    "key_requirements": ["secure login"],
                    "complexity_score": 0.4,
                    "estimated_effort": 1.2,
                    "risks": ["security concerns"],
                }
            ),
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
    assert result["predictor_name"] == "historical_statistical"
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
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_ai_service_ports.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.service_adapters'`.

- [x] **Step 3: Add domain interfaces**

Create `app/ai/service_interfaces.py`:

```python
"""Domain ports for AI-backed workflows."""

from __future__ import annotations

from abc import ABC, abstractmethod
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
    ) -> dict[str, Any]:
        """Return a normalized price prediction payload."""


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
```

- [x] **Step 4: Add adapters**

Create `app/ai/service_adapters.py`:

```python
"""Default AI service adapters."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from app.ai.bid_recommendation import get_bid_recommendation
from app.ai.document_analyzer import analyze_document
from app.ai.llm_interfaces import LLMRequest, RequestExecutor, TokenBudget
from app.ai.price_prediction import predict_price
from app.ai.service_interfaces import BidRecommendationPort, DocumentAnalysisPort, PricePredictionPort


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
        payload = json.loads(response.text)
        return {
            "key_requirements": list(payload.get("key_requirements") or []),
            "complexity_score": float(payload.get("complexity_score") or 0.0),
            "estimated_effort": float(payload.get("estimated_effort") or 0.0),
            "risks": list(payload.get("risks") or []),
        }
```

- [x] **Step 5: Add factory**

Create `app/ai/factory.py`:

```python
"""Factory helpers for default AI ports."""

from __future__ import annotations

from app.ai.service_adapters import (
    FunctionPricePredictionPort,
    HeuristicBidRecommendationPort,
    HeuristicDocumentAnalysisPort,
)
from app.ai.service_interfaces import BidRecommendationPort, DocumentAnalysisPort, PricePredictionPort


def build_price_prediction_port() -> PricePredictionPort:
    return FunctionPricePredictionPort()


def build_bid_recommendation_port() -> BidRecommendationPort:
    return HeuristicBidRecommendationPort()


def build_document_analysis_port() -> DocumentAnalysisPort:
    return HeuristicDocumentAnalysisPort()
```

- [x] **Step 6: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_ai_service_ports.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/ai/service_interfaces.py app/ai/service_adapters.py app/ai/factory.py tests/test_ai_service_ports.py
git commit -m "Introduce AI service ports and adapters"
```

---

### Task 2: Extract Prediction Workflow From Routes

**Files:**
- Create: `app/services/prediction_workflow.py`
- Modify: `app/api/predictions.py`
- Create: `tests/test_prediction_api_decoupling.py`

- [x] **Step 1: Write failing route dependency tests**

Create `tests/test_prediction_api_decoupling.py`:

```python
from app.api.predictions import get_prediction_workflow


class StubPredictionWorkflow:
    def predict_project_price(self, db, request):
        return {
            "predicted_price": 9000.0,
            "price_range_min": 8800.0,
            "price_range_max": 9200.0,
            "confidence_score": 0.7,
            "model_version": "stub",
            "predictor_name": "stub_predictor",
            "predictor_family": "stub",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "predicted_bid_rate": 0.9,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "stub",
        }

    def build_bid_recommendation(self, db, request):
        return {
            "recommended_bid": 9000.0,
            "confidence_score": 0.8,
            "reasoning": "stub recommendation",
            "market_analysis": {"source": "stub"},
        }

    def analyze_project_document(self, db, request):
        return {
            "key_requirements": ["stub requirement"],
            "complexity_score": 0.2,
            "estimated_effort": 1.0,
            "risks": [],
        }


def test_prediction_routes_use_workflow_dependency_for_bid_recommendation(client):
    client.app.dependency_overrides[get_prediction_workflow] = lambda: StubPredictionWorkflow()
    try:
        project = client.post(
            "/api/v1/projects/",
            json={
                "title": "Dependency Project",
                "description": "desc",
                "requirements": "req",
                "budget_estimate": 10_000.0,
                "category": "software",
            },
        ).json()

        response = client.post(
            "/api/v1/predictions/bid-recommendation",
            json={"project_id": project["id"], "user_historical_data": {}},
        )

        assert response.status_code == 200
        assert response.json()["reasoning"] == "stub recommendation"
    finally:
        client.app.dependency_overrides.clear()


def test_prediction_routes_use_workflow_dependency_for_document_analysis(client):
    client.app.dependency_overrides[get_prediction_workflow] = lambda: StubPredictionWorkflow()
    try:
        project = client.post(
            "/api/v1/projects/",
            json={
                "title": "Document Project",
                "description": "desc",
                "requirements": "req",
                "budget_estimate": 10_000.0,
                "category": "software",
            },
        ).json()

        response = client.post(
            "/api/v1/predictions/analyze-document",
            json={
                "project_id": project["id"],
                "document_content": "content",
                "document_type": "specification",
            },
        )

        assert response.status_code == 200
        assert response.json()["key_requirements"] == ["stub requirement"]
    finally:
        client.app.dependency_overrides.clear()
```

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_prediction_api_decoupling.py -q
```

Expected: FAIL with `ImportError` for `get_prediction_workflow` or with route still bypassing dependency.

- [x] **Step 3: Add workflow service**

Create `app/services/prediction_workflow.py` by moving the existing route bodies into methods. Keep the persistence field assignments identical to `app/api/predictions.py`.

```python
"""Prediction workflow orchestration outside FastAPI route handlers."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.ai.factory import (
    build_bid_recommendation_port,
    build_document_analysis_port,
    build_price_prediction_port,
)
from app.ai.service_interfaces import BidRecommendationPort, DocumentAnalysisPort, PricePredictionPort
from app.core.single_user import ensure_operator_account
from app.models.models import PricePrediction, Project
from app.schemas.schemas import BidRecommendationRequest, DocumentAnalysisRequest, PricePredictionRequest
from app.services.prediction_dataset import PredictionDatasetService
from app.services.prediction_feedback import PredictionFeedbackService


class PredictionWorkflowService:
    def __init__(
        self,
        *,
        price_prediction_port: PricePredictionPort | None = None,
        bid_recommendation_port: BidRecommendationPort | None = None,
        document_analysis_port: DocumentAnalysisPort | None = None,
        dataset_service: PredictionDatasetService | None = None,
        feedback_service: PredictionFeedbackService | None = None,
    ) -> None:
        self.price_prediction_port = price_prediction_port or build_price_prediction_port()
        self.bid_recommendation_port = bid_recommendation_port or build_bid_recommendation_port()
        self.document_analysis_port = document_analysis_port or build_document_analysis_port()
        self.dataset_service = dataset_service or PredictionDatasetService()
        self.feedback_service = feedback_service or PredictionFeedbackService()

    def predict_project_price(self, db: Session, request: PricePredictionRequest) -> dict:
        project = self._load_project(db, request.project_id)
        operator = ensure_operator_account(db)
        feedback_calibration = self.feedback_service.build_calibration_context(
            db,
            operator_id=operator.id,
            category=request.category or project.category,
            agency_name=request.agency_name,
        )
        prediction = self.price_prediction_port.predict_price(
            budget=request.budget_estimate,
            category=request.category,
            description=request.description,
            historical_records=self._load_price_history(db, category=request.category or project.category),
            agency_name=request.agency_name,
            feedback_calibration=feedback_calibration,
            legal_floor_bid_rate=request.legal_floor_bid_rate,
        )
        db.add(self._build_price_prediction_row(operator_id=operator.id, project_id=request.project_id, prediction=prediction))
        db.commit()
        return prediction

    def build_bid_recommendation(self, db: Session, request: BidRecommendationRequest) -> dict:
        project = self._load_project(db, request.project_id)
        return self.bid_recommendation_port.recommend(
            project_data={
                "budget": project.budget_estimate,
                "category": project.category,
                "description": project.description,
            },
            user_data=request.user_historical_data or {},
        )

    def analyze_project_document(self, db: Session, request: DocumentAnalysisRequest) -> dict:
        self._load_project(db, request.project_id)
        return self.document_analysis_port.analyze(
            request.document_content,
            document_type=request.document_type,
        )

    def _load_project(self, db: Session, project_id: int) -> Project:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        return project

    def _load_price_history(self, db: Session, *, category: str | None, limit: int = 80) -> list[dict[str, object]]:
        return self.dataset_service.load_historical_series(
            db,
            category=category,
            limit=limit,
            explicit_bid_rate_only=True,
        )

    def _build_price_prediction_row(self, *, operator_id: int, project_id: int, prediction: dict) -> PricePrediction:
        return PricePrediction(
            user_id=operator_id,
            project_id=project_id,
            predicted_price=prediction["predicted_price"],
            price_range_min=prediction["price_range_min"],
            price_range_max=prediction["price_range_max"],
            confidence_score=prediction["confidence_score"],
            model_version=prediction["model_version"],
            predictor_name=prediction.get("predictor_name", "historical_statistical"),
            predictor_family=prediction.get("predictor_family", "statistical"),
            fallback_reason=prediction.get("fallback_reason"),
            selector_name=prediction.get("selector_name", "configured_preference"),
            selection_reason=prediction.get("selection_reason"),
            backtest_sample_count=int(prediction.get("backtest_sample_count", 0) or 0),
            backtest_average_absolute_error_rate=prediction.get("backtest_average_absolute_error_rate"),
            training_window_size=int(prediction.get("training_window_size", 0) or 0),
            pricing_mode=prediction.get("pricing_mode", "heuristic"),
            historical_sample_size=int(prediction.get("historical_sample_size", 0) or 0),
            agency_match_sample_size=int(prediction.get("agency_match_sample_size", 0) or 0),
            predicted_bid_rate=float(prediction.get("predicted_bid_rate", 0.0) or 0.0),
            guardrail_applied=bool(prediction.get("guardrail_applied", False)),
            guardrail_reason=prediction.get("guardrail_reason"),
            floor_bid_rate=prediction.get("floor_bid_rate"),
            floor_price=prediction.get("floor_price"),
        )
```

- [x] **Step 4: Simplify routes**

Modify `app/api/predictions.py` so concrete AI functions are no longer imported:

```python
from app.services.prediction_workflow import PredictionWorkflowService


def get_prediction_workflow() -> PredictionWorkflowService:
    return PredictionWorkflowService()
```

Then each endpoint delegates:

```python
@router.post("/price", response_model=PricePredictionResponse)
def predict_project_price(
    request: PricePredictionRequest,
    db: Session = Depends(get_db),
    workflow: PredictionWorkflowService = Depends(get_prediction_workflow),
):
    return workflow.predict_project_price(db, request)
```

Apply the same pattern to `get_bid_recommendation_endpoint()` and `analyze_project_document()`.

- [x] **Step 5: Run route and contract tests**

Run:

```bash
pytest tests/test_prediction_api_decoupling.py tests/test_predictions.py::test_price_prediction -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/prediction_workflow.py app/api/predictions.py tests/test_prediction_api_decoupling.py
git commit -m "Extract prediction route workflow service"
```

---

### Task 3: Decouple Price Predictor Construction

**Files:**
- Create: `app/ai/predictors/registry.py`
- Modify: `app/ai/price_prediction.py`
- Modify: `tests/test_prediction_predictors.py`

- [x] **Step 1: Write failing injected registry tests**

Append to `tests/test_prediction_predictors.py`:

```python
from app.ai.predictors.base import BasePricePredictor, PricePredictionContext


class FakePredictor(BasePricePredictor):
    name = "fake_predictor"
    family = "test"

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    def predict(self, context: PricePredictionContext) -> dict:
        if self.should_fail:
            raise RuntimeError("fake failure")
        return {
            "predicted_price": context.budget * 0.91,
            "price_range_min": context.budget * 0.90,
            "price_range_max": context.budget * 0.92,
            "confidence_score": 0.7,
            "model_version": "fake-v1",
            "pricing_mode": "historical_blend",
            "historical_sample_size": context.historical_sample_size,
            "agency_match_sample_size": 0,
            "predicted_bid_rate": 0.91,
            "bid_rate_candidates": [
                {"label": "base", "bid_rate": 0.91, "predicted_price": context.budget * 0.91}
            ],
            "explanation": "fake predictor",
        }


def test_predict_price_accepts_injected_predictor_registry(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "fake")

    prediction = predict_price(
        budget=100_000_000.0,
        category="software",
        description="injected registry",
        historical_records=[{"bid_rate": 0.91}],
        predictor_registry={"historical": FakePredictor(), "fake": FakePredictor()},
    )

    assert prediction["predictor_name"] == "fake_predictor"
    assert prediction["predictor_family"] == "test"


def test_predict_price_uses_injected_historical_fallback_when_selected_predictor_fails(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "fake")

    prediction = predict_price(
        budget=100_000_000.0,
        category="software",
        description="fallback registry",
        historical_records=[{"bid_rate": 0.91}],
        predictor_registry={"historical": FakePredictor(), "fake": FakePredictor(should_fail=True)},
    )

    assert prediction["predictor_name"] == "fake_predictor"
    assert "fake failure" in prediction["fallback_reason"]
```

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_prediction_predictors.py::test_predict_price_accepts_injected_predictor_registry tests/test_prediction_predictors.py::test_predict_price_uses_injected_historical_fallback_when_selected_predictor_fails -q
```

Expected: FAIL with `TypeError: predict_price() got an unexpected keyword argument 'predictor_registry'`.

- [x] **Step 3: Add registry module**

Create `app/ai/predictors/registry.py`:

```python
"""Predictor registry construction."""

from __future__ import annotations

from collections.abc import Mapping

from app.ai.predictors.base import BasePricePredictor
from app.ai.predictors.ensemble import EnsembleBidRatePredictor
from app.ai.predictors.historical import HistoricalStatisticalPredictor
from app.ai.predictors.lstm import LSTMBidRatePredictor


def build_default_predictor_registry() -> dict[str, BasePricePredictor]:
    return {
        "historical": HistoricalStatisticalPredictor(),
        "lstm": LSTMBidRatePredictor(),
        "ensemble": EnsembleBidRatePredictor(),
    }


def normalize_predictor_registry(
    registry: Mapping[str, BasePricePredictor] | None,
) -> dict[str, BasePricePredictor]:
    resolved = dict(registry or build_default_predictor_registry())
    if "historical" not in resolved:
        resolved["historical"] = HistoricalStatisticalPredictor()
    return resolved
```

- [x] **Step 4: Update price prediction facade**

Modify `app/ai/price_prediction.py`:

```python
from collections.abc import Mapping
from app.ai.predictors.registry import normalize_predictor_registry
```

Add a keyword-only argument at the end of `predict_price()`:

```python
    predictor_registry: Mapping[str, BasePricePredictor] | None = None,
) -> Dict[str, Any]:
```

Replace registry construction:

```python
    registry = normalize_predictor_registry(predictor_registry)
    predictor, fallback_reason, selection_metadata = _select_predictor(context, registry=registry)
```

Change `_select_predictor()` signature:

```python
def _select_predictor(
    context: PricePredictionContext,
    *,
    registry: dict[str, BasePricePredictor],
) -> tuple[BasePricePredictor, str | None, dict[str, Any]]:
```

Change `_run_predictor()` to accept `historical_predictor`:

```python
def _run_predictor(
    *,
    context: PricePredictionContext,
    predictor: BasePricePredictor,
    historical_predictor: BasePricePredictor,
    fallback_reason: str | None,
) -> tuple[dict[str, Any], BasePricePredictor, str | None]:
```

Inside `_run_predictor()`, remove direct `HistoricalStatisticalPredictor()` construction and use the injected `historical_predictor`.

- [x] **Step 5: Run predictor tests**

Run:

```bash
pytest tests/test_prediction_predictors.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/ai/predictors/registry.py app/ai/price_prediction.py tests/test_prediction_predictors.py
git commit -m "Decouple price predictor registry construction"
```

---

### Task 4: Inject AI Ports Into Opportunity Analysis

**Files:**
- Modify: `app/services/opportunity_analysis.py`
- Modify: `tests/test_predictor_business_group.py`

- [x] **Step 1: Write failing injection test**

Add to `tests/test_predictor_business_group.py`:

```python
class CapturingPricePredictionPort:
    def __init__(self) -> None:
        self.kwargs = {}

    def predict_price(self, **kwargs):
        self.kwargs = kwargs
        return {
            "predicted_price": 90_000_000.0,
            "price_range_min": 89_000_000.0,
            "price_range_max": 91_000_000.0,
            "confidence_score": 0.7,
            "model_version": "fake",
            "predictor_name": "fake",
            "predictor_family": "fake",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "predicted_bid_rate": 0.9,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "fake",
        }


class StaticBidRecommendationPort:
    def recommend(self, *, project_data, user_data):
        return {
            "recommended_bid": 90_000_000.0,
            "confidence_score": 0.8,
            "reasoning": "fake",
            "market_analysis": {},
        }


def test_opportunity_analysis_uses_injected_prediction_ports(test_db):
    from app.services.opportunity_analysis import OpportunityAnalysisService

    price_port = CapturingPricePredictionPort()
    service = OpportunityAnalysisService(
        price_prediction_port=price_port,
        bid_recommendation_port=StaticBidRecommendationPort(),
    )
    project = Project(
        title="건축공사 포트 검증",
        description="-",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    test_db.add(project)
    test_db.flush()

    request = OpportunityAnalysisRequest(project_id=project.id, legal_floor_bid_rate=87.995)
    service.analyze_project(test_db, project=project, request=request)

    assert price_port.kwargs["business_type_code"] == "0411"
    assert price_port.kwargs["business_group"] == "construction"
    assert price_port.kwargs["legal_floor_bid_rate"] == 87.995
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_predictor_business_group.py::test_opportunity_analysis_uses_injected_prediction_ports -q
```

Expected: FAIL with `TypeError: OpportunityAnalysisService.__init__() got an unexpected keyword argument`.

- [x] **Step 3: Add constructor injection**

Modify `app/services/opportunity_analysis.py`:

```python
from app.ai.factory import build_bid_recommendation_port, build_price_prediction_port
from app.ai.service_interfaces import BidRecommendationPort, PricePredictionPort
```

Replace the current zero-argument `OpportunityAnalysisService.__init__` signature with optional ports while preserving all existing collaborator initialization:

```python
def __init__(
    self,
    *,
    price_prediction_port: PricePredictionPort | None = None,
    bid_recommendation_port: BidRecommendationPort | None = None,
) -> None:
    self.classifier = NoticeClassifierService()
    self.dataset_service = PredictionDatasetService()
    self.decision_service = BidDecisionService()
    self.feedback_service = PredictionFeedbackService()
    self.similarity_service = ProjectSimilarityService()
    self.price_prediction_port = price_prediction_port or build_price_prediction_port()
    self.bid_recommendation_port = bid_recommendation_port or build_bid_recommendation_port()
```

Replace direct calls:

```python
price_prediction = self.price_prediction_port.predict_price(
    budget=float(project.budget_estimate or 0.0),
    category=project.category or "other",
    description=f"{project.description or ''} {project.requirements or ''}".strip(),
    historical_records=self._load_price_history(db, project),
    agency_name=request.agency_name,
    feedback_calibration=feedback_calibration,
    business_type_code=business_type_code,
    business_group=business_group,
    legal_floor_bid_rate=request.legal_floor_bid_rate,
)
bid_recommendation = self.bid_recommendation_port.recommend(
    project_data={
        "budget": float(project.budget_estimate or 0.0),
        "category": project.category or "other",
        "description": f"{project.title} {project.description} {project.requirements}".strip(),
    },
    user_data=user_historical_data,
)
```

- [x] **Step 4: Run opportunity tests**

Run:

```bash
pytest tests/test_predictor_business_group.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/opportunity_analysis.py tests/test_predictor_business_group.py
git commit -m "Inject AI ports into opportunity analysis"
```

---

### Task 5: Migrate Secondary Price Consumers

**Files:**
- Modify: `app/services/paper_bidding_backtest.py`
- Modify: `app/services/smoke_test.py`
- Read-only check: `app/services/bid_summary.py` uses `_resolve_floor_bid_rate`, not `predict_price`; leave it unchanged until a separate floor-rate port exists.
- Test: targeted existing tests around each consumer

- [x] **Step 1: Add injection tests for secondary consumers**

Add this focused test to `tests/test_smoke_test_service.py`:

```python
class CapturingPredictionPort:
    def __init__(self) -> None:
        self.calls = []

    def predict_price(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "predicted_bid_rate": 0.9,
            "predictor_name": "fake",
            "predicted_price": 90_000_000.0,
            "price_range_min": 89_000_000.0,
            "price_range_max": 91_000_000.0,
            "confidence_score": 0.7,
            "model_version": "fake",
            "predictor_family": "fake",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "fake",
        }


def test_smoke_predict_price_phase_uses_injected_prediction_port(test_db):
    from app.models.models import Project
    from app.services.smoke_test import KonepsTelegramSmokeTestService

    project = Project(
        title="Smoke port project",
        description="port description",
        requirements="port requirements",
        budget_estimate=100_000_000.0,
        category="software",
        business_type_code="0621",
    )
    test_db.add(project)
    test_db.commit()

    port = CapturingPredictionPort()
    service = KonepsTelegramSmokeTestService(price_prediction_port=port)
    result = service._phase_predict_price(test_db, {"id": project.id})

    assert result.passed is True
    assert port.calls
    assert port.calls[0]["category"] == "software"
    assert "port description" in port.calls[0]["description"]
    assert port.calls[0]["business_type_code"] == "0621"
```

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_smoke_test_service.py::test_smoke_predict_price_phase_uses_injected_prediction_port -q
```

Expected: FAIL because the service does not accept the injected port.

- [x] **Step 3: Inject default price port into each secondary service**

For `PaperBiddingBacktestService` and `KonepsTelegramSmokeTestService`, add:

```python
from app.ai.factory import build_price_prediction_port
from app.ai.service_interfaces import PricePredictionPort
```

and:

```python
self.price_prediction_port = price_prediction_port or build_price_prediction_port()
```

Replace direct `predict_price` calls with the injected port call while keeping the same keyword arguments currently passed at each site. For example, in `SmokeTestService._phase_predict_price()`:

```python
pred = self.price_prediction_port.predict_price(
    budget=float(project.budget_estimate),
    category=project.category or "other",
    description=desc,
    historical_records=history,
    agency_name=project.issuing_agency or project.demand_agency,
    feedback_calibration=None,
    business_type_code=project.business_type_code,
    business_group=bg,
)
```

Keep any direct `_resolve_floor_bid_rate` imports, including `BidSummaryService`, until a separate floor-rate port exists.

- [x] **Step 4: Run focused secondary tests**

Run:

```bash
pytest tests/test_smoke_test_service.py tests/test_paper_bidding_backtest.py tests/test_bid_summary.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/paper_bidding_backtest.py app/services/smoke_test.py tests/test_smoke_test_service.py tests/test_paper_bidding_backtest.py
git commit -m "Migrate secondary prediction consumers to ports"
```

---

### Task 6: Regression Gate

**Files:**
- No code changes.

- [x] **Step 1: Run focused Python tests**

Run:

```bash
pytest tests/test_ai_service_ports.py tests/test_prediction_api_decoupling.py tests/test_predictions.py tests/test_prediction_predictors.py tests/test_prediction_reporting.py tests/test_prediction_dataset.py tests/test_dashboard_api.py tests/test_predictor_business_group.py -q
```

Expected: all tests PASS.

- [x] **Step 2: Compile touched modules**

Run:

```bash
python3 -m py_compile app/ai/llm_interfaces.py app/ai/service_interfaces.py app/ai/service_adapters.py app/ai/factory.py app/ai/price_prediction.py app/ai/predictors/registry.py app/api/predictions.py app/services/prediction_workflow.py app/services/opportunity_analysis.py app/services/paper_bidding_backtest.py app/services/smoke_test.py
```

Expected: exit code 0.

- [x] **Step 3: Check diff hygiene**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional changed files are present.
