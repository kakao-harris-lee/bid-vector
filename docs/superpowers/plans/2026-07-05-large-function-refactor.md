# Large Function Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce high-coupling service function size in the largest current refactor candidates without changing public behavior.

**Architecture:** Add a small maintainability regression test that measures selected Python function lengths, then split orchestration-heavy service methods into private helpers. Preserve existing API/service payload shapes and reuse current focused tests as behavior contracts.

**Tech Stack:** Python 3.11+, pytest, AST/inspect-based maintainability checks, existing service tests.

---

## File Structure

- Create `tests/test_large_function_budgets.py`: maintainability guard for selected high-risk large functions.
- Modify `app/services/opportunity_analysis.py`: split `OpportunityAnalysisService.analyze_project` orchestration into small helper methods.
- Modify `app/services/paper_bidding_backtest.py`: split `PaperBiddingBacktestService._build_candidate_item` candidate construction into helper methods.

## Task 1: Add Large Function Budget Tests

**Files:**
- Create: `tests/test_large_function_budgets.py`

- [x] **Step 1: Write failing maintainability test**

Add an inspect-based test that asserts:

```python
("app.services.opportunity_analysis", "OpportunityAnalysisService", "analyze_project", 130)
("app.services.paper_bidding_backtest", "PaperBiddingBacktestService", "_build_candidate_item", 105)
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_large_function_budgets.py -q
```

Expected: FAIL because both target functions exceed their budgets.

## Task 2: Split Opportunity Analysis Orchestration

**Files:**
- Modify: `app/services/opportunity_analysis.py`
- Test: `tests/test_large_function_budgets.py`
- Test: `tests/test_predictor_business_group.py`

- [x] **Step 1: Extract profile/strategy context helper**

Create `_resolve_operator_context(db, operator)` returning `(operator, profile, strategy)`.

- [x] **Step 2: Extract price prediction helper**

Create `_build_price_prediction(db, project, request, operator_id)` preserving the current `price_prediction_port.predict_price(...)` call arguments.

- [x] **Step 3: Extract bid recommendation helper**

Create `_build_bid_recommendation(project, user_historical_data)` preserving the current recommendation payload.

- [x] **Step 4: Run opportunity tests**

Run:

```bash
pytest tests/test_large_function_budgets.py tests/test_predictor_business_group.py -q
```

Expected: PASS.

## Task 3: Split Paper Bidding Candidate Builder

**Files:**
- Modify: `app/services/paper_bidding_backtest.py`
- Test: `tests/test_large_function_budgets.py`
- Test: `tests/test_paper_bidding_backtest.py`

- [x] **Step 1: Extract candidate prediction helper**

Create `_build_candidate_prediction(...)` returning `(history, business_group, prediction)`.

- [x] **Step 2: Extract decision request helper**

Create `_build_candidate_decision(...)` returning the existing decision payload result from `BidDecisionService.evaluate_opportunity`.

- [x] **Step 3: Extract candidate response helper**

Create `_build_candidate_payload(...)` preserving all keys currently returned by `_build_candidate_item`.

- [x] **Step 4: Run paper bidding tests**

Run:

```bash
pytest tests/test_large_function_budgets.py tests/test_paper_bidding_backtest.py -q
```

Expected: PASS.

## Task 4: Regression Gate

**Files:**
- No additional code changes.

- [x] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_large_function_budgets.py tests/test_predictor_business_group.py tests/test_paper_bidding_backtest.py tests/test_smoke_test_service.py tests/test_prediction_predictors.py -q
```

Expected: PASS.

- [x] **Step 2: Run static checks**

Run:

```bash
python3 -m py_compile app/services/opportunity_analysis.py app/services/paper_bidding_backtest.py tests/test_large_function_budgets.py
ruff check app/services/opportunity_analysis.py app/services/paper_bidding_backtest.py tests/test_large_function_budgets.py
git diff --check
```

Expected: all commands exit 0.

## Task 5: Split Remaining Paper Bidding Entry Points

**Files:**
- Modify: `tests/test_large_function_budgets.py`
- Modify: `app/services/paper_bidding_backtest.py`

- [x] **Step 1: Extend line-budget tests**

Added budget checks for:

```python
("app.services.paper_bidding_backtest", "PaperBiddingBacktestService", "run_historical_backtest", 130)
("app.services.paper_bidding_backtest", "PaperBiddingBacktestService", "run_forward_paper_bidding", 105)
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_large_function_budgets.py -q
```

Observed: FAIL because `run_historical_backtest` was 170 lines and `run_forward_paper_bidding` was 126 lines.

- [x] **Step 3: Extract historical award processing**

Created `_process_historical_awards(...)` and `_complete_historical_backtest(...)`.

- [x] **Step 4: Extract forward project processing**

Created `_process_forward_projects(...)` and `_complete_forward_paper_run(...)`.

- [x] **Step 5: Run paper bidding tests**

Run:

```bash
pytest tests/test_large_function_budgets.py tests/test_paper_bidding_backtest.py -q
```

Observed: PASS.

## Task 6: Split Price Prediction Guardrail Application

**Files:**
- Modify: `tests/test_large_function_budgets.py`
- Modify: `app/ai/price_prediction.py`

- [x] **Step 1: Extend line-budget tests**

Added budget check for:

```python
("app.ai.price_prediction", None, "_apply_prediction_guardrails", 110)
```

- [x] **Step 2: Run test to verify RED**

Run:

```bash
pytest tests/test_large_function_budgets.py -q
```

Observed: FAIL because `_apply_prediction_guardrails` was 145 lines.

- [x] **Step 3: Extract guardrail context and application helpers**

Created `GuardrailContext`, `_resolve_guardrail_context(...)`, metadata helpers, candidate clamp helpers, base prediction clamp helper, and final guardrail marker helper.

- [x] **Step 4: Run guardrail tests**

Run:

```bash
pytest tests/test_large_function_budgets.py tests/test_predictions.py::test_predict_price_applies_minimum_bid_rate_guardrail tests/test_predictions.py::test_predict_price_applies_notice_legal_floor_to_conservative_scenario tests/test_predictions.py::test_predict_price_applies_maximum_bid_rate_guardrail tests/test_predictions.py::test_price_prediction_endpoint_surfaces_guardrail_metadata tests/test_predictions.py::test_price_prediction_endpoint_accepts_notice_legal_floor_rate tests/test_predictor_business_group.py -q
```

Observed: PASS.
