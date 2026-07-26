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
            125,
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
        (
            "app.tasks.jobs",
            "backfill_scsbid_reserve_detail",
            "run",
            135,
        ),
        (
            "app.services.ml_release",
            "RemoteObjectStorageClient",
            "_preflight_s3_storage",
            130,
        ),
        (
            "app.services.ml_release",
            "MLReleasePromotionService",
            "preflight_release_rollout",
            140,
        ),
        (
            "app.services.opportunity_monitoring",
            "StrategyMonitoringService",
            "execute_monitoring",
            130,
        ),
        (
            "app.services.analytics_reporting",
            "AnalyticsReportingService",
            "_build_smoke_test_summary",
            125,
        ),
        (
            "app.services.ml_training",
            "PricePredictionTrainingService",
            "train_price_predictor",
            125,
        ),
        (
            "app.services.koneps.persistence",
            None,
            "persist_crawl_results",
            125,
        ),
        (
            "app.services.koneps.persistence",
            None,
            "update_project_from_item",
            125,
        ),
        (
            "app.services.analytics_reporting",
            "AnalyticsReportingService",
            "_build_synthetic_validation_summary",
            125,
        ),
        (
            "app.ai.predictors.historical",
            None,
            "resolve_procurement_rate_band",
            125,
        ),
        (
            "app.services.analytics_reporting",
            "AnalyticsReportingService",
            "_build_cards",
            125,
        ),
        (
            "app.services.synthetic_experiment",
            "SyntheticExperimentService",
            "build_sample_gap_run_candidate",
            125,
        ),
        (
            "app.api.operator_dashboard",
            None,
            "get_operator_dashboard_impl",
            125,
        ),
        (
            "app.services.synthetic_experiment",
            None,
            "build_sample_report",
            125,
        ),
        (
            "app.services.decision_analytics",
            "DecisionAnalyticsService",
            "build_recommendation_feedback_labels",
            125,
        ),
        (
            "app.services.classifier",
            "NoticeClassifierService",
            "_assess_budget",
            125,
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
