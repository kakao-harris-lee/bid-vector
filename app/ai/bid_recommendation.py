"""Bid recommendation engine.

Legacy heuristic. Not the ML predictor pipeline — this is a lightweight
scoring helper still consumed by ``opportunity_analysis`` (recommended_amount
fallback / confidence blend and the "시장 경쟁력" competitiveness surface).

The tuning literals below are declared as module-level named constants so the
behavior is auditable and stable. They are intentionally NOT Settings-backed:
these are internal heuristic weights, not deployment configuration. Changing
any value here changes live opportunity-analysis output, so
``tests/test_bid_recommendation_characterization.py`` pins the current results.
"""
from typing import Dict, Final

# --- get_bid_recommendation tuning ---
# Default operator average bid, as a share of the project budget, used when no
# historical average is supplied.
DEFAULT_AVERAGE_BID_BUDGET_RATIO: Final = 0.9
# Assumed win rate when the operator has no history.
DEFAULT_WIN_RATE: Final = 0.5
# Win-rate bands that steer the bid multiplier (strict comparisons).
HIGH_WIN_RATE_THRESHOLD: Final = 0.7
LOW_WIN_RATE_THRESHOLD: Final = 0.3
# Bid multiplier per win-rate band.
HIGH_WIN_RATE_BID_MULTIPLIER: Final = 1.05
LOW_WIN_RATE_BID_MULTIPLIER: Final = 0.95
NEUTRAL_BID_MULTIPLIER: Final = 1.0
# Starting point: 85% of budget before the win-rate multiplier is applied.
BASE_BUDGET_BID_RATIO: Final = 0.85
# Decimal places the recommended bid is rounded to.
RECOMMENDED_BID_ROUNDING_DECIMALS: Final = 2
# Confidence = base + win_rate * weight, clamped at the ceiling.
CONFIDENCE_BASE: Final = 0.8
CONFIDENCE_WIN_RATE_WEIGHT: Final = 0.2
CONFIDENCE_CEILING: Final = 0.95
# Above this win rate the market position reads "competitive", else "aggressive".
COMPETITIVE_POSITION_WIN_RATE_THRESHOLD: Final = 0.5
# Suggested timeline scales with budget but never below the floor.
TIMELINE_BUDGET_DIVISOR: Final = 50000
MIN_TIMELINE_DAYS: Final = 30

# --- calculate_competitiveness_score tuning ---
# Bid position relative to market average, and the score for each band.
COMPETITIVENESS_LOW_BAND_RATIO: Final = 0.8
COMPETITIVENESS_HIGH_BAND_RATIO: Final = 1.2
COMPETITIVENESS_VERY_COMPETITIVE_SCORE: Final = 0.95
COMPETITIVENESS_COMPETITIVE_SCORE: Final = 0.75
COMPETITIVENESS_MODERATE_SCORE: Final = 0.50
COMPETITIVENESS_NOT_COMPETITIVE_SCORE: Final = 0.25


def get_bid_recommendation(project_data: Dict, user_data: Dict) -> Dict:
    """
    Generate AI-powered bid recommendation based on project and user data.

    Args:
        project_data: Project information (budget, category, description)
        user_data: User historical data (win rate, avg bid, etc.)

    Returns:
        Dictionary with bid recommendation and reasoning
    """
    # TODO: Implement advanced ML model
    # For now, use heuristic approach

    project_budget = project_data.get("budget", 0)
    category = project_data.get("category", "")

    # Calculate recommended bid based on budget and user data
    user_avg_bid = user_data.get(
        "average_bid", project_budget * DEFAULT_AVERAGE_BID_BUDGET_RATIO
    )
    user_win_rate = user_data.get("win_rate", DEFAULT_WIN_RATE)

    # Adjust bid based on historical win rate
    if user_win_rate > HIGH_WIN_RATE_THRESHOLD:
        # User wins frequently, slightly increase bid
        bid_multiplier = HIGH_WIN_RATE_BID_MULTIPLIER
    elif user_win_rate < LOW_WIN_RATE_THRESHOLD:
        # User rarely wins, decrease bid to be more competitive
        bid_multiplier = LOW_WIN_RATE_BID_MULTIPLIER
    else:
        bid_multiplier = NEUTRAL_BID_MULTIPLIER

    recommended_bid = (
        project_budget * bid_multiplier * BASE_BUDGET_BID_RATIO
    )  # 85% of budget as starting point

    market_position = (
        "competitive"
        if user_win_rate > COMPETITIVE_POSITION_WIN_RATE_THRESHOLD
        else "aggressive"
    )

    return {
        "recommended_bid": round(recommended_bid, RECOMMENDED_BID_ROUNDING_DECIMALS),
        "confidence_score": min(
            CONFIDENCE_BASE + (user_win_rate * CONFIDENCE_WIN_RATE_WEIGHT),
            CONFIDENCE_CEILING,
        ),
        "reasoning": f"Recommended bid of {recommended_bid:.2f} based on project budget, category ({category}), and your win history (win rate: {user_win_rate*100:.1f}%)",
        "market_analysis": {
            "market_position": market_position,
            "suggested_timeline_days": max(
                int(project_budget / TIMELINE_BUDGET_DIVISOR), MIN_TIMELINE_DAYS
            ),
        },
    }


def calculate_competitiveness_score(
    bid_amount: float, project_data: Dict, market_data: Dict
) -> float:
    """Calculate how competitive a bid is"""
    market_avg = market_data.get("average_bid", project_data.get("budget", bid_amount))

    if bid_amount <= market_avg * COMPETITIVENESS_LOW_BAND_RATIO:
        return COMPETITIVENESS_VERY_COMPETITIVE_SCORE  # Very competitive
    elif bid_amount <= market_avg:
        return COMPETITIVENESS_COMPETITIVE_SCORE  # Competitive
    elif bid_amount <= market_avg * COMPETITIVENESS_HIGH_BAND_RATIO:
        return COMPETITIVENESS_MODERATE_SCORE  # Moderate
    else:
        return COMPETITIVENESS_NOT_COMPETITIVE_SCORE  # Not competitive
