"""Maintainability checks for high-coupling service functions."""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest


@pytest.mark.parametrize(
    ("module_name", "owner_name", "function_name", "max_lines"),
    [
        (
            "app.services.opportunity_analysis",
            "OpportunityAnalysisService",
            "analyze_project",
            130,
        ),
        (
            "app.services.paper_bidding_backtest",
            "PaperBiddingBacktestService",
            "_build_candidate_item",
            105,
        ),
        (
            "app.services.paper_bidding_backtest",
            "PaperBiddingBacktestService",
            "run_historical_backtest",
            130,
        ),
        (
            "app.services.paper_bidding_backtest",
            "PaperBiddingBacktestService",
            "run_forward_paper_bidding",
            105,
        ),
        (
            "app.ai.price_prediction",
            None,
            "_apply_prediction_guardrails",
            110,
        ),
    ],
)
def test_high_coupling_service_functions_stay_within_line_budget(
    module_name: str,
    owner_name: str | None,
    function_name: str,
    max_lines: int,
) -> None:
    module = importlib.import_module(module_name)
    owner: Any = getattr(module, owner_name) if owner_name is not None else module
    function = getattr(owner, function_name)

    source_lines, start_line = inspect.getsourcelines(function)
    line_count = len(source_lines)

    assert line_count <= max_lines, (
        f"{owner_name or module_name}.{function_name} at {module_name}:{start_line} "
        f"is {line_count} lines; expected <= {max_lines}"
    )
