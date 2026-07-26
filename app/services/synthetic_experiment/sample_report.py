"""G-1 repeatable-reporting readiness report over persisted run payloads.

Keeps price-close estimates separate from actual awards and exposes a
synthetic-only gate so canonical operator leakage blocks reporting readiness.
"""

from __future__ import annotations

from typing import Any

from .breakdown import _ReportAccumulator, _empty_breakdown
from .constants import (
    BUDGET_BAND_BOUNDARIES,
    BUDGET_BAND_TOP_KEY,
    REPORT_STATUS_DATA_MIXED,
    REPORT_STATUS_READY,
    SAMPLE_STATUS_INSUFFICIENT,
    SAMPLE_STATUS_SUFFICIENT,
    SYNTHETIC_OPERATOR_SAMPLE_TARGET,
    SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET,
    SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
)


def _is_non_synthetic_result(item: dict[str, Any]) -> bool:
    """Detect explicit canonical/operator leakage in a synthetic run result.

    Engine-backed synthetic rows carry ``username=synthetic-*``. Stubbed tests and
    legacy rows often have only a slug, so absence of a username is treated as
    unknown rather than as leakage. Explicit canonical flags or non-synthetic
    usernames fail the report readiness gate.
    """
    if item.get("is_canonical") is True:
        return True
    username = str(item.get("username") or "")
    return bool(username) and not username.startswith("synthetic-")


def _metric_win_count(item: dict[str, Any]) -> int:
    value = item.get("would_have_won_count")
    if value is None:
        value = item.get("would_have_won_price_only_count")
    return int(value or 0)


def _add_group_row(
    groups: dict[str, _ReportAccumulator],
    *,
    key: str,
    settled_count: int,
    would_have_won_count: int,
    avg_abs_bid_rate_error: Any,
) -> None:
    acc = groups.get(key)
    if acc is None:
        acc = _ReportAccumulator()
        groups[key] = acc
    acc.add(
        settled_count=settled_count,
        would_have_won_count=would_have_won_count,
        avg_abs_bid_rate_error=avg_abs_bid_rate_error,
    )


def _report_rows(
    groups: dict[str, _ReportAccumulator],
    *,
    dimension: str,
    order: list[str] | None = None,
) -> list[dict[str, Any]]:
    if order is None:
        ordered_keys = sorted(groups)
    else:
        ordered_keys = [key for key in order if key in groups]
        ordered_keys.extend(sorted(key for key in groups if key not in set(order)))
    return [
        groups[key].row(dimension=dimension, key=key)
        for key in ordered_keys
    ]


def build_sample_report(
    *,
    preset_name: str | None,
    operator_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the G-1 repeatable-reporting readiness stream for a completed run.

    The report is intentionally derived from run payloads already persisted in
    ``SyntheticExperimentResult``. It flags sample gaps by preset, category,
    operator business group, and budget band while keeping price-close estimates
    separate from actual awards. It also exposes an explicit synthetic-only gate
    so canonical operator leakage blocks repeatable-reporting readiness.
    """
    (
        by_preset,
        category_rows,
        business_rows,
        budget_rows,
        non_synthetic_slugs,
    ) = _build_sample_report_rows(
        preset_name=preset_name,
        operator_results=operator_results,
    )
    lacking = _sample_report_lacking_groups(
        by_preset=by_preset,
        category_rows=category_rows,
        business_rows=business_rows,
        budget_rows=budget_rows,
    )
    synthetic_only = len(non_synthetic_slugs) == 0
    ready = synthetic_only and not lacking and bool(operator_results)
    return {
        "preset_name": preset_name,
        "group_sample_target": SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET,
        "operator_sample_target": SYNTHETIC_OPERATOR_SAMPLE_TARGET,
        "run_total_sample_target": SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
        "synthetic_only": synthetic_only,
        "non_synthetic_operator_slugs": sorted(set(non_synthetic_slugs)),
        "ready_for_repeatable_reporting": ready,
        "report_status": _sample_report_status(ready, synthetic_only),
        "by_preset": by_preset,
        "by_category": category_rows,
        "by_business_type": business_rows,
        "by_budget_band": budget_rows,
        "lacking_groups": lacking,
    }


def _build_sample_report_rows(
    *,
    preset_name: str | None,
    operator_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    category_groups: dict[str, _ReportAccumulator] = {}
    business_groups: dict[str, _ReportAccumulator] = {}
    budget_groups: dict[str, _ReportAccumulator] = {}
    preset_acc = _ReportAccumulator()

    non_synthetic_slugs: list[str] = []
    for item in operator_results:
        slug = str(item.get("slug") or item.get("operator_slug") or "unknown")
        settled_count = int(item.get("settled_count") or 0)
        win_count = _metric_win_count(item)
        avg_error = item.get("average_absolute_bid_rate_error")

        preset_acc.add(
            settled_count=settled_count,
            would_have_won_count=win_count,
            avg_abs_bid_rate_error=avg_error,
        )
        if _is_non_synthetic_result(item):
            non_synthetic_slugs.append(slug)

        business_type = str(item.get("business_type") or "unknown")
        _add_group_row(
            business_groups,
            key=business_type,
            settled_count=settled_count,
            would_have_won_count=win_count,
            avg_abs_bid_rate_error=avg_error,
        )

        breakdown = item.get("breakdown") or _empty_breakdown()
        for row in breakdown.get("by_category", []) or []:
            key = str(row.get("category") or "unknown")
            _add_group_row(
                category_groups,
                key=key,
                settled_count=int(row.get("settled_count") or 0),
                would_have_won_count=int(row.get("would_have_won_count") or 0),
                avg_abs_bid_rate_error=row.get("avg_abs_bid_rate_error"),
            )
        for row in breakdown.get("by_budget_band", []) or []:
            key = str(row.get("budget_band") or "unknown")
            _add_group_row(
                budget_groups,
                key=key,
                settled_count=int(row.get("settled_count") or 0),
                would_have_won_count=int(row.get("would_have_won_count") or 0),
                avg_abs_bid_rate_error=row.get("avg_abs_bid_rate_error"),
            )

    budget_order = [key for key, _ in BUDGET_BAND_BOUNDARIES] + [BUDGET_BAND_TOP_KEY]
    by_preset = [
        {
            **preset_acc.row(
                dimension="preset",
                key=preset_name or "custom",
                label=preset_name or "custom",
            ),
            "sample_target": SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
            "missing_settled_count": max(
                0, SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET - preset_acc.settled_count
            ),
        }
    ]
    by_preset[0]["sample_status"] = (
        SAMPLE_STATUS_SUFFICIENT
        if by_preset[0]["missing_settled_count"] == 0
        else SAMPLE_STATUS_INSUFFICIENT
    )
    category_rows = _report_rows(category_groups, dimension="category")
    business_rows = _report_rows(business_groups, dimension="business_type")
    budget_rows = _report_rows(
        budget_groups, dimension="budget_band", order=budget_order
    )
    return by_preset, category_rows, business_rows, budget_rows, non_synthetic_slugs


def _sample_report_lacking_groups(
    *,
    by_preset: list[dict[str, Any]],
    category_rows: list[dict[str, Any]],
    business_rows: list[dict[str, Any]],
    budget_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    all_rows = by_preset + category_rows + business_rows + budget_rows
    lacking = [
        {
            "dimension": row["dimension"],
            "key": row["key"],
            "settled_count": row["settled_count"],
            "sample_target": row["sample_target"],
            "missing_settled_count": row["missing_settled_count"],
        }
        for row in all_rows
        if row["sample_status"] != SAMPLE_STATUS_SUFFICIENT
    ]
    for dimension, rows in (
        ("category", category_rows),
        ("business_type", business_rows),
        ("budget_band", budget_rows),
    ):
        if not rows:
            lacking.append(
                {
                    "dimension": dimension,
                    "key": "missing",
                    "settled_count": 0,
                    "sample_target": SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET,
                    "missing_settled_count": SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET,
                }
            )
    return lacking


def _sample_report_status(ready: bool, synthetic_only: bool) -> str:
    if ready:
        return REPORT_STATUS_READY
    if not synthetic_only:
        return REPORT_STATUS_DATA_MIXED
    return SAMPLE_STATUS_INSUFFICIENT
