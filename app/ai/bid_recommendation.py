"""Bid recommendation engine"""
from typing import Dict


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
    user_avg_bid = user_data.get("average_bid", project_budget * 0.9)
    user_win_rate = user_data.get("win_rate", 0.5)

    # Adjust bid based on historical win rate
    if user_win_rate > 0.7:
        # User wins frequently, slightly increase bid
        bid_multiplier = 1.05
    elif user_win_rate < 0.3:
        # User rarely wins, decrease bid to be more competitive
        bid_multiplier = 0.95
    else:
        bid_multiplier = 1.0

    recommended_bid = project_budget * bid_multiplier * 0.85  # 85% of budget as starting point

    return {
        "recommended_bid": round(recommended_bid, 2),
        "confidence_score": min(0.8 + (user_win_rate * 0.2), 0.95),
        "reasoning": f"Recommended bid of {recommended_bid:.2f} based on project budget, category ({category}), and your win history (win rate: {user_win_rate*100:.1f}%)",
        "market_analysis": {
            "market_position": "competitive" if user_win_rate > 0.5 else "aggressive",
            "suggested_timeline_days": max(int(project_budget / 50000), 30),
        }
    }


def calculate_competitiveness_score(bid_amount: float, project_data: Dict, market_data: Dict) -> float:
    """Calculate how competitive a bid is"""
    market_avg = market_data.get("average_bid", project_data.get("budget", bid_amount))

    if bid_amount <= market_avg * 0.8:
        return 0.95  # Very competitive
    elif bid_amount <= market_avg:
        return 0.75  # Competitive
    elif bid_amount <= market_avg * 1.2:
        return 0.50  # Moderate
    else:
        return 0.25  # Not competitive
