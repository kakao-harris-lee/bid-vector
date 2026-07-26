"""Declarative score tables for opportunity analysis.

Single home for the band ladders and frozen composite-score weight maps consumed
by the scoring and workload mixins. Kept as ordered, immutable data so the
weights/thresholds are declared once (and a sum-to-1.0 invariant can be asserted
in tests/test_opportunity_weight_tables.py). Values are moved verbatim from the
original ``opportunity_analysis`` module — no thresholds or weights change.
"""

from __future__ import annotations

from types import MappingProxyType

# --- Execution-complexity band ladders (see _estimate_execution_complexity_score) ---
# Declarative rungs resolved by app.core.bands.resolve_band. The budget ladder is
# a descending >= cascade; the deadline ladder an ascending <= cascade. Each ends
# in a sentinel fallback rung so a signal is always produced. Values mirror the
# previous inline if/elif thresholds exactly.
_BUDGET_COMPLEXITY_BANDS: tuple[tuple[float, float], ...] = (
    (500_000_000, 0.92),
    (200_000_000, 0.78),
    (100_000_000, 0.62),
    (float("-inf"), 0.38),
)
_DEADLINE_COMPLEXITY_BANDS: tuple[tuple[float, float], ...] = (
    (6, 1.0),
    (24, 0.78),
    (72, 0.52),
    (float("inf"), 0.24),
)
# Signal used when the notice has no deadline (handled before the <= ladder).
_DEADLINE_MISSING_COMPLEXITY_SIGNAL = 0.3

# --- Frozen composite-score weight tables (each sums to 1.0) ---
# Read-only weight maps for the two weighted-sum scores below. Kept as ordered,
# immutable tables so the weights are declared once and a sum-to-1.0 invariant can
# be asserted (see tests/test_opportunity_weight_tables.py). The arithmetic that
# consumes them preserves the original left-associative order.
_WORKLOAD_COMPOSITE_WEIGHTS = MappingProxyType(
    {
        "active_load_ratio": 0.5,
        "average_priority": 0.25,
        "urgent_ratio": 0.15,
        "review_ratio": 0.1,
    }
)
_EXPECTED_MARGIN_COMPOSITE_WEIGHTS = MappingProxyType(
    {
        "recommended_rate": 0.35,
        "floor_headroom": 0.2,
        "prediction_alignment": 0.2,
        "price_confidence": 0.15,
        "normalized_capacity": 0.1,
    }
)
_EXECUTION_COMPLEXITY_COMPOSITE_WEIGHTS = MappingProxyType(
    {
        "budget_signal": 0.3,
        "keyword_signal": 0.25,
        "deadline_signal": 0.15,
        "active_load_ratio": 0.1,
        "match_friction": 0.1,
        "capacity_friction": 0.1,
    }
)
