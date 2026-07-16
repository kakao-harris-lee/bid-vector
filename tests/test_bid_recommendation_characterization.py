"""Characterization tests for the legacy bid_recommendation heuristic.

These lock the *current* output of ``get_bid_recommendation`` and
``calculate_competitiveness_score`` so the magic-number → named-constant
refactor stays behavior-preserving. The opportunity-analysis path
(``_build_bid_recommendation`` + ``calculate_competitiveness_score`` for the
"시장 경쟁력" surface) consumes these live, so they are a regression guard, not
dead-code snapshots. Values were captured from the pre-refactor implementation.
"""

import pytest

from app.ai.bid_recommendation import (
    calculate_competitiveness_score,
    get_bid_recommendation,
)


@pytest.mark.parametrize(
    "project_data, user_data, expected_bid, expected_confidence, "
    "expected_position, expected_timeline, expected_win_pct",
    [
        # High win rate (>0.7): +5% multiplier, confidence clamped at ceiling.
        (
            {"budget": 10000, "category": "software"},
            {"win_rate": 0.8},
            8925.0,
            0.95,
            "competitive",
            30,
            "80.0%",
        ),
        # Low win rate (<0.3): -5% multiplier, aggressive position.
        (
            {"budget": 10000, "category": "software"},
            {"win_rate": 0.2},
            8075.0,
            0.8400000000000001,
            "aggressive",
            30,
            "20.0%",
        ),
        # Default win rate (0.5 when omitted): neutral multiplier.
        (
            {"budget": 100000, "category": "construction"},
            {},
            85000.0,
            0.9,
            "aggressive",
            30,
            "50.0%",
        ),
        # Upper boundary win_rate == 0.7: still neutral (strict >), competitive.
        (
            {"budget": 200000, "category": "software"},
            {"win_rate": 0.7},
            170000.0,
            0.9400000000000001,
            "competitive",
            30,
            "70.0%",
        ),
        # Lower boundary win_rate == 0.3: still neutral (strict <), aggressive.
        (
            {"budget": 50000, "category": "software"},
            {"win_rate": 0.3},
            42500.0,
            0.8600000000000001,
            "aggressive",
            30,
            "30.0%",
        ),
        # Large budget crosses the timeline divisor floor (int(budget/50000)).
        (
            {"budget": 2000000, "category": "x"},
            {"win_rate": 0.5},
            1700000.0,
            0.9,
            "aggressive",
            40,
            "50.0%",
        ),
        # Empty inputs: budget defaults to 0, win rate to 0.5.
        ({}, {}, 0.0, 0.9, "aggressive", 30, "50.0%"),
    ],
)
def test_get_bid_recommendation_is_behavior_preserving(
    project_data,
    user_data,
    expected_bid,
    expected_confidence,
    expected_position,
    expected_timeline,
    expected_win_pct,
):
    result = get_bid_recommendation(project_data, user_data)

    assert result["recommended_bid"] == pytest.approx(expected_bid)
    assert result["confidence_score"] == pytest.approx(expected_confidence)
    assert result["market_analysis"]["market_position"] == expected_position
    assert result["market_analysis"]["suggested_timeline_days"] == expected_timeline
    # reasoning embeds the unrounded bid (2dp) and the win-rate percentage.
    assert f"{expected_bid:.2f}" in result["reasoning"]
    assert f"win rate: {expected_win_pct}" in result["reasoning"]


@pytest.mark.parametrize(
    "bid_amount, project_data, market_data, expected",
    [
        # bid == 0.8 * market_avg boundary -> very competitive.
        (800, {"budget": 0}, {"average_bid": 1000}, 0.95),
        (900, {"budget": 0}, {"average_bid": 1000}, 0.75),
        # bid == market_avg boundary -> competitive.
        (1000, {"budget": 0}, {"average_bid": 1000}, 0.75),
        (1100, {"budget": 0}, {"average_bid": 1000}, 0.50),
        # bid == 1.2 * market_avg boundary -> moderate.
        (1200, {"budget": 0}, {"average_bid": 1000}, 0.50),
        (1300, {"budget": 0}, {"average_bid": 1000}, 0.25),
        # market_avg falls back to project budget when market_data lacks it.
        (400, {"budget": 500}, {}, 0.95),
        # market_avg falls back to bid_amount itself when both are absent.
        (100, {}, {}, 0.75),
    ],
)
def test_calculate_competitiveness_score_is_behavior_preserving(
    bid_amount, project_data, market_data, expected
):
    assert (
        calculate_competitiveness_score(bid_amount, project_data, market_data)
        == expected
    )
