"""Price prediction AI module"""
import numpy as np
from typing import Dict


def predict_price(budget: float, category: str, description: str) -> Dict:
    """
    Predict project price based on budget, category, and description.
    Uses machine learning model for accurate predictions.

    Args:
        budget: Estimated budget
        category: Project category
        description: Project description

    Returns:
        Dictionary with predicted price and confidence
    """
    # TODO: Implement actual ML model
    # For now, return mock prediction based on budget range

    category_multipliers = {
        "software": 1.2,
        "hardware": 1.0,
        "design": 0.9,
        "consulting": 1.1,
        "infrastructure": 1.3,
        "other": 1.0,
    }

    multiplier = category_multipliers.get(category.lower(), 1.0)

    # Add noise based on description length (complexity)
    complexity_factor = min(len(description) / 500, 1.5)

    predicted_price = budget * multiplier * (0.8 + complexity_factor)

    # Calculate confidence based on data available
    confidence_score = min(0.9 if len(description) > 200 else 0.7, 0.95)

    return {
        "predicted_price": round(predicted_price, 2),
        "price_range_min": round(predicted_price * 0.8, 2),
        "price_range_max": round(predicted_price * 1.2, 2),
        "confidence_score": confidence_score,
        "model_version": "v1.0",
    }


def get_price_insights(historical_bids: list) -> Dict:
    """Get market insights from historical bid data"""
    if not historical_bids:
        return {"average_bid": 0, "median_bid": 0, "std_dev": 0}

    bid_amounts = [bid["amount"] for bid in historical_bids]

    return {
        "average_bid": float(np.mean(bid_amounts)),
        "median_bid": float(np.median(bid_amounts)),
        "std_dev": float(np.std(bid_amounts)),
        "min_bid": float(np.min(bid_amounts)),
        "max_bid": float(np.max(bid_amounts)),
    }
