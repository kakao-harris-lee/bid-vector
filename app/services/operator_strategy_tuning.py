"""Helpers for operator strategy tuning values produced by experiments."""

from __future__ import annotations

import json
from typing import Any


DEFAULT_AUTO_WORKLOAD_PENALTY_MULTIPLIER = 1.0
MIN_AUTO_WORKLOAD_PENALTY_MULTIPLIER = 0.0
MAX_AUTO_WORKLOAD_PENALTY_MULTIPLIER = 2.0
MIN_CATEGORY_PRIORITY_OVERRIDE = -0.2
MAX_CATEGORY_PRIORITY_OVERRIDE = 0.2


def clamp_auto_workload_penalty_multiplier(value: Any) -> float:
    """Normalize workload penalty multipliers into a bounded strategy setting."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = DEFAULT_AUTO_WORKLOAD_PENALTY_MULTIPLIER
    return round(
        max(
            MIN_AUTO_WORKLOAD_PENALTY_MULTIPLIER,
            min(MAX_AUTO_WORKLOAD_PENALTY_MULTIPLIER, numeric_value),
        ),
        4,
    )


def clamp_category_priority_override(value: Any) -> float:
    """Normalize per-category score offsets into a conservative range."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = 0.0
    return round(
        max(
            MIN_CATEGORY_PRIORITY_OVERRIDE,
            min(MAX_CATEGORY_PRIORITY_OVERRIDE, numeric_value),
        ),
        4,
    )


def parse_category_priority_overrides(raw_value: Any) -> dict[str, float]:
    """Parse the persisted category override JSON into a clean mapping."""
    if raw_value in (None, ""):
        return {}

    if isinstance(raw_value, str):
        try:
            parsed_value = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
    else:
        parsed_value = raw_value

    if not isinstance(parsed_value, dict):
        return {}

    overrides: dict[str, float] = {}
    for key, value in parsed_value.items():
        category = str(key or "").strip()
        if not category:
            continue
        overrides[category] = clamp_category_priority_override(value)
    return overrides


def dump_category_priority_overrides(overrides: Any) -> str:
    """Serialize category overrides into stable JSON for the strategy row."""
    return json.dumps(
        parse_category_priority_overrides(overrides),
        ensure_ascii=False,
        sort_keys=True,
    )


def get_strategy_auto_workload_penalty_multiplier(strategy: Any) -> float:
    """Read a normalized workload penalty multiplier from an operator strategy."""
    return clamp_auto_workload_penalty_multiplier(
        getattr(strategy, "auto_workload_penalty_multiplier", DEFAULT_AUTO_WORKLOAD_PENALTY_MULTIPLIER)
    )


def get_strategy_category_priority_overrides(strategy: Any) -> dict[str, float]:
    """Read normalized category priority overrides from an operator strategy."""
    return parse_category_priority_overrides(getattr(strategy, "category_priority_overrides", None))


def resolve_category_priority_override(strategy: Any, category: str | None) -> float:
    """Return the configured priority offset for a category, matching case-insensitively."""
    normalized_category = str(category or "").strip().lower()
    if not normalized_category:
        return 0.0

    for configured_category, override in get_strategy_category_priority_overrides(strategy).items():
        if configured_category.strip().lower() == normalized_category:
            return float(override)
    return 0.0
