"""Market insight helpers derived from historical bid data.

Split out of the former single-file ``price_prediction`` module (§4.5 size
decomposition). ``get_price_insights`` is relocated verbatim — the statistics
are unchanged.
"""

from __future__ import annotations

from typing import Dict

import numpy as np


def get_price_insights(historical_bids: list) -> Dict:
    """Get market insights from historical bid data."""
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
