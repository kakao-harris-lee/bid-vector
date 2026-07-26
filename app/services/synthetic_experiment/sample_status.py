"""Fixed G-1 per-operator and run-level sample-health payloads."""

from __future__ import annotations

from typing import Any

from .constants import (
    SAMPLE_STATUS_INSUFFICIENT,
    SAMPLE_STATUS_SUFFICIENT,
    SYNTHETIC_OPERATOR_SAMPLE_TARGET,
    SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
)


def sample_status_for_settled_count(settled_count: int) -> dict[str, Any]:
    """Return the fixed G-1 per-operator sample health payload."""
    count = max(0, int(settled_count or 0))
    missing = max(0, SYNTHETIC_OPERATOR_SAMPLE_TARGET - count)
    return {
        "sample_status": (
            SAMPLE_STATUS_SUFFICIENT
            if missing == 0
            else SAMPLE_STATUS_INSUFFICIENT
        ),
        "sample_target": SYNTHETIC_OPERATOR_SAMPLE_TARGET,
        "settled_count": count,
        "missing_settled_count": missing,
    }


def aggregate_sample_status(operator_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize G-1 run-level sample health across operator result rows."""
    total_settled = sum(int(item.get("settled_count") or 0) for item in operator_results)
    insufficient = [
        {
            "operator_slug": str(item.get("slug") or item.get("operator_slug") or "unknown"),
            **sample_status_for_settled_count(int(item.get("settled_count") or 0)),
        }
        for item in operator_results
        if int(item.get("settled_count") or 0) < SYNTHETIC_OPERATOR_SAMPLE_TARGET
    ]
    missing_total = max(0, SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET - total_settled)
    return {
        "sample_status": (
            SAMPLE_STATUS_SUFFICIENT
            if missing_total == 0 and not insufficient
            else SAMPLE_STATUS_INSUFFICIENT
        ),
        "operator_sample_target": SYNTHETIC_OPERATOR_SAMPLE_TARGET,
        "run_total_sample_target": SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
        "total_settled_count": total_settled,
        "missing_total_settled_count": missing_total,
        "insufficient_operators": insufficient,
    }
