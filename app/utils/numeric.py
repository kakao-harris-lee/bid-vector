"""Canonical scalar coercion helpers for untyped values (stdlib-only).

These utilities centralize the previously duplicated ``optional_float`` /
``_safe_optional_int`` / ``_coerce_amount`` / ``_coerce_float`` / ``_as_float`` /
``amount_float`` logic that lived in ``app/ai/guardrail_core.py``,
``app/services/synthetic_experiment/sample_gap.py``,
``app/ai/predictors/legal_floor_spec.py``, ``app/services/award_verification.py``,
``app/services/decision_samples.py`` and
``scripts/backtest_latest_award_holdouts.py``. The behavior is identical to the
original implementations.

Scope boundary: value → number coercion with **no domain knowledge**. Unit,
금액 basis (예정가/기초금액) and rounding policy belong to their domain owners
(``app/domain/``, ``app/ai/``) and must not move here.

This module must not import any other application module (typing only) to avoid
import cycles — ``app/ai/guardrail_core.py`` is one of its importers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def coerce_or_none(value: Any, cast: Callable[[Any], T]) -> T | None:
    """Apply ``cast`` to ``value``, returning ``None`` when it cannot be cast.

    ``None`` passes through without calling ``cast``. Empty and blank strings
    also become ``None`` because the numeric casts reject them.
    """
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def optional_float(value: Any) -> float | None:
    """Coerce ``value`` to ``float``, or ``None`` when it is not numeric."""
    return coerce_or_none(value, float)


def optional_int(value: Any) -> int | None:
    """Coerce ``value`` to ``int``, or ``None`` when it is not an integer."""
    return coerce_or_none(value, int)
