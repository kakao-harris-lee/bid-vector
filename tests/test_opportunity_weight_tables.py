"""Invariant tests for the frozen composite-score weight tables.

The three weighted-sum scores in ``opportunity_analysis`` (auto workload estimate,
expected-margin proxy, and execution-complexity estimate) blend their signals with
weights that must sum to 1.0 for the resulting score to stay on a 0-1 scale. These
tables were extracted from inline literals; the sum-to-1.0 assertion is the guard
that a future weight edit cannot silently skew the scale. The tables are also frozen
(read-only), so an accidental mutation at runtime raises instead of corrupting the
shared weights.
"""

from __future__ import annotations

import pytest

from app.services.opportunity_analysis import (
    _EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS,
    _EXPECTED_MARGIN_COMPOSITE_WEIGHTS,
    _WORKLOAD_COMPOSITE_WEIGHTS,
)


@pytest.mark.parametrize(
    "weights",
    [
        _WORKLOAD_COMPOSITE_WEIGHTS,
        _EXPECTED_MARGIN_COMPOSITE_WEIGHTS,
        _EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS,
    ],
    ids=["workload", "expected_margin", "execution_complexity"],
)
def test_composite_weights_sum_to_one(weights) -> None:
    assert sum(weights.values()) == pytest.approx(1.0)


def test_workload_weight_table_matches_declared_signals() -> None:
    assert tuple(_WORKLOAD_COMPOSITE_WEIGHTS.keys()) == (
        "active_load_ratio",
        "average_priority",
        "urgent_ratio",
        "review_ratio",
    )


def test_expected_margin_weight_table_matches_declared_signals() -> None:
    assert tuple(_EXPECTED_MARGIN_COMPOSITE_WEIGHTS.keys()) == (
        "recommended_rate",
        "floor_headroom",
        "prediction_alignment",
        "price_confidence",
        "normalized_capacity",
    )


def test_execution_complexity_weight_table_matches_declared_signals() -> None:
    assert tuple(_EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS.keys()) == (
        "budget_signal",
        "keyword_signal",
        "deadline_signal",
        "active_load_ratio",
        "match_friction",
        "capacity_friction",
    )


@pytest.mark.parametrize(
    "weights",
    [
        _WORKLOAD_COMPOSITE_WEIGHTS,
        _EXPECTED_MARGIN_COMPOSITE_WEIGHTS,
        _EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS,
    ],
    ids=["workload", "expected_margin", "execution_complexity"],
)
def test_weight_tables_are_frozen(weights) -> None:
    with pytest.raises(TypeError):
        weights["active_load_ratio"] = 0.99
