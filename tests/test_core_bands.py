"""Unit tests for the shared ``resolve_band`` ladder resolver.

The critical property is boundary exactness: a value AT a threshold must resolve
the same way the original open-coded ``>=`` / ``<=`` cascade did (the most common
off-by-one trap when data-driving a threshold ladder).
"""

from __future__ import annotations

import operator

import pytest

from app.core.bands import resolve_band


# Descending ``>=`` ladder shaped like the budget-complexity rungs.
GE_BANDS: tuple[tuple[float, float], ...] = (
    (500_000_000, 0.92),
    (200_000_000, 0.78),
    (100_000_000, 0.62),
    (float("-inf"), 0.38),  # fallback floor — always matches
)

# Ascending ``<=`` ladder shaped like the deadline-complexity rungs.
LE_BANDS: tuple[tuple[float, float], ...] = (
    (6, 1.0),
    (24, 0.78),
    (72, 0.52),
    (float("inf"), 0.24),  # fallback ceiling — always matches
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (900_000_000, 0.92),
        (500_000_000, 0.92),  # AT threshold -> matches (>=)
        (499_999_999, 0.78),  # just below -> next rung
        (200_000_000, 0.78),
        (199_999_999, 0.62),
        (100_000_000, 0.62),
        (99_999_999, 0.38),  # falls through to fallback floor
        (0, 0.38),
    ],
)
def test_ge_ladder_boundaries(value: float, expected: float) -> None:
    assert resolve_band(value, GE_BANDS) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 1.0),
        (6, 1.0),  # AT threshold -> matches (<=)
        (7, 0.78),  # just above -> next rung
        (24, 0.78),
        (25, 0.52),
        (72, 0.52),
        (73, 0.24),  # falls through to fallback ceiling
        (100, 0.24),
    ],
)
def test_le_ladder_boundaries(value: float, expected: float) -> None:
    assert resolve_band(value, LE_BANDS, compare=operator.le) == expected


def test_default_compare_is_greater_equal() -> None:
    # A ge-ladder value exactly on the threshold resolves to that rung by default.
    assert resolve_band(200_000_000, GE_BANDS) == 0.78


def test_strict_operators_are_supported() -> None:
    # The resolver is comparison-agnostic; a strict ``>`` shifts the boundary.
    assert resolve_band(200_000_000, GE_BANDS, compare=operator.gt) == 0.62
    assert resolve_band(200_000_001, GE_BANDS, compare=operator.gt) == 0.78


def test_payloads_are_generic() -> None:
    labels: tuple[tuple[float, str], ...] = (
        (10, "high"),
        (5, "mid"),
        (float("-inf"), "low"),
    )
    assert resolve_band(10, labels) == "high"
    assert resolve_band(7, labels) == "mid"
    assert resolve_band(1, labels) == "low"


def test_fallback_only_ladder_always_returns() -> None:
    only = ((float("-inf"), "always"),)
    assert resolve_band(-1_000, only) == "always"
    assert resolve_band(1_000, only) == "always"
