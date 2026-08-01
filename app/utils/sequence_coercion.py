"""Canonical coercion helpers for JSON-list values from ORM rows/dicts.

These utilities centralize the previously duplicated ``coerce_sequence`` /
``coerce_numeric_list`` / ``coerce_integer_list`` logic that lived in
``app/services/prediction_dataset.py``, ``app/services/backtest_cutoff.py`` and
``app/ai/predictors/historical.py``, plus the ``as_str_list`` copies from
``app/schemas/opportunity.py`` and ``app/services/bid_summary.py``. The behavior
is byte-for-byte identical to the original implementations.

This module must not import any other application module (json/typing only) to
avoid import cycles.
"""

from __future__ import annotations

import json
from typing import Any


def coerce_sequence(raw_value: Any) -> list[Any]:
    """Parse list-like values coming from ORM rows or dictionaries."""
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def coerce_numeric_list(raw_value: Any) -> list[float]:
    """Coerce a JSON string or list of numbers into floats."""
    parsed = coerce_sequence(raw_value)
    numbers: list[float] = []
    for item in parsed:
        try:
            numbers.append(float(item))
        except (TypeError, ValueError):
            continue
    return numbers


def coerce_integer_list(raw_value: Any) -> list[int]:
    """Coerce a JSON string or list of numbers into integers."""
    parsed = coerce_sequence(raw_value)
    numbers: list[int] = []
    for item in parsed:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return numbers


def as_str_list(raw_value: Any) -> list[str]:
    """Keep the non-empty strings of a list value; anything else yields ``[]``.

    Deliberately *not* built on ``coerce_sequence``: callers pass a member of an
    already-decoded blob, where a bare string is data rather than nested JSON, so
    a string input must stay an empty result. Non-string items are dropped, not
    cast — only empty strings are filtered out (``"0"`` survives).
    """
    if not isinstance(raw_value, list):
        return []
    return [str(item) for item in raw_value if isinstance(item, str) and item]
