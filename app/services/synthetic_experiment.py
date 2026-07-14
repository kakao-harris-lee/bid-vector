"""Synthetic experiment persistence and run-lifecycle orchestration.

Owns the "가상 회사 낙찰 실험실" (Experiment Lab) domain: saving experiment
definitions, triggering asynchronous runs, and persisting per-operator results.
ML/predictor logic is untouched here -- the underlying backtest engine is invoked
only through :class:`SyntheticBacktestService`, whose ``win_rate_on_settled`` is a
price-only estimate (NOT actual award) and is passed through unchanged.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import shlex
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import (
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
    User,
)
from app.services.synthetic_backtest import (
    SYNTHETIC_USERNAME_PREFIX,
    SyntheticBacktestService,
)

logger = logging.getLogger(__name__)

RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
SYNTHETIC_OPERATOR_SAMPLE_TARGET = 30
SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET = 100
SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET = 30
SAMPLE_STATUS_SUFFICIENT = "sufficient"
SAMPLE_STATUS_INSUFFICIENT = "insufficient_sample"
REPORT_STATUS_READY = "ready_for_reporting"
REPORT_STATUS_DATA_MIXED = "canonical_synthetic_mixed"
SAMPLE_GAP_WARNING_MIXED_DATA = "canonical_synthetic_mixed"
SAMPLE_GAP_WARNING_LEGACY_SUMMARY = "legacy_summary_without_sample_report"
SAMPLE_GAP_DIMENSION_ORDER = {
    "preset": 0,
    "category": 1,
    "business_type": 2,
    "budget_band": 3,
}
SYNTHETIC_SETTLE_ACTIONS = ("bid_now", "review", "skip")

_G1_PRESET_WINDOW = {
    "start_at": "2025-01-01T00:00:00+00:00",
    "end_at": "2025-12-31T23:59:59+00:00",
    "limit": 200,
    "scenario": "base",
    "settle_actions": False,
}

SYNTHETIC_EXPERIMENT_PRESETS: dict[str, dict[str, Any]] = {
    "g1-construction-base-12m": {
        "description": "G-1 공사 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "construction"},
        "operator_slugs": [
            "cn-small-gangwon",
            "cn-mid-gyeonggi",
            "cn-electric-telecom-national",
        ],
    },
    "g1-service-base-12m": {
        "description": "G-1 일반/기술 용역 2025년 12개월 base preset",
        # Mixes general-service and technical-service operators. Leave category
        # unset so each operator's focus categories scope its award pool.
        "params": {**_G1_PRESET_WINDOW, "category": None},
        "operator_slugs": [
            "eng-supervision-busan",
            "eng-design-daejeon",
            "gs-cleaning-metro",
            "gs-security-national",
        ],
    },
    "g1-goods-base-12m": {
        "description": "G-1 물품 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "goods"},
        "operator_slugs": [
            "gd-office-sme",
            "gd-it-equipment-midcap",
        ],
    },
    "g1-software-base-12m": {
        "description": "G-1 소프트웨어 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "software"},
        "operator_slugs": [
            "sw-small-seoul",
            "sw-mid-metro",
            "sw-large-national",
        ],
    },
}

# Budget-band boundaries (KRW). A settlement is placed into the first band whose
# upper bound it is *strictly* below; the final band catches everything at or
# above the last boundary. Mirrors common 나라장터 budget tiers (1억/5억/10억/50억).
BUDGET_BAND_BOUNDARIES: tuple[tuple[str, float], ...] = (
    ("lt_1eok", 100_000_000.0),
    ("1eok_5eok", 500_000_000.0),
    ("5eok_10eok", 1_000_000_000.0),
    ("10eok_50eok", 5_000_000_000.0),
)
BUDGET_BAND_TOP_KEY = "gte_50eok"

# A settlement counts as a (price-only estimated) "win" when its price-only
# verdict is "plausible" -- IDENTICAL to ``would_have_won_price_only_count`` in
# the engine summary. This is a price-based ESTIMATE, not an actual award.
_WIN_VERDICTS = {"plausible"}

# Eligibility-gate (PR3) final verdict that counts as an estimated favorable win:
# the bid cleared the 낙찰하한가 AND landed at/under the realized winning amount.
# ``unknown`` (no 예가/하한 data) is EXCLUDED from the eligibility-rate denominator
# so the rate stays honest -- it is computed only over settlements we could judge.
_ELIGIBLE_FAVORABLE_VERDICT = "eligible_favorable"
_ELIGIBILITY_UNKNOWN_VERDICT = "unknown"

# CSV export columns (Phase 4). ``operator_slug`` is prepended to the CLI
# comparison columns (see ``scripts.backtest_synthetic_operators._write_comparison_csv``)
# so each row is self-identifying. ``win_rate_on_settled`` /
# ``win_rate_on_candidates`` remain PRICE-ONLY estimates (NOT actual awards); the
# column name is preserved unchanged so downstream readers keep that meaning.
EXPORT_CSV_COLUMNS: tuple[str, ...] = (
    "operator_slug",
    "business_type",
    "candidate_count",
    "paper_bid_count",
    "settled_count",
    "skipped_by_strategy_count",
    "would_have_won_price_only_count",
    "win_rate_on_settled",
    "win_rate_on_candidates",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
    "average_absolute_amount_error_rate",
)

# Per-operator metric keys surfaced in the A/B compare response. ``win_rate_on_settled``
# is a price-only estimate (NOT an actual award) and is carried through unchanged.
_COMPARE_METRIC_KEYS: tuple[str, ...] = (
    "win_rate_on_settled",
    "settled_count",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
)

# Subset of compare metrics for which a signed delta (b - a) is computed. A delta
# is ``None`` when either side is missing/None (e.g. no settled rows -> win_rate None).
_COMPARE_DELTA_KEYS: tuple[str, ...] = (
    "win_rate_on_settled",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
)


class _ReportAccumulator:
    def __init__(self) -> None:
        self.settled_count = 0
        self.would_have_won_count = 0
        self.error_weighted_sum = 0.0
        self.error_weight = 0

    def add(
        self,
        *,
        settled_count: int,
        would_have_won_count: int,
        avg_abs_bid_rate_error: Any,
    ) -> None:
        settled = max(0, int(settled_count or 0))
        self.settled_count += settled
        self.would_have_won_count += max(0, int(would_have_won_count or 0))
        if avg_abs_bid_rate_error is not None and settled > 0:
            self.error_weighted_sum += float(avg_abs_bid_rate_error) * settled
            self.error_weight += settled

    def row(self, *, dimension: str, key: str, label: str | None = None) -> dict[str, Any]:
        missing = max(0, SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET - self.settled_count)
        return {
            "dimension": dimension,
            "key": key,
            "label": label or key,
            "settled_count": self.settled_count,
            "sample_target": SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET,
            "missing_settled_count": missing,
            "sample_status": (
                SAMPLE_STATUS_SUFFICIENT
                if missing == 0
                else SAMPLE_STATUS_INSUFFICIENT
            ),
            "would_have_won_count": self.would_have_won_count,
            "est_price_close_rate": (
                round(self.would_have_won_count / self.settled_count, 6)
                if self.settled_count > 0
                else None
            ),
            "avg_abs_bid_rate_error": (
                round(self.error_weighted_sum / self.error_weight, 6)
                if self.error_weight > 0
                else None
            ),
        }


def _budget_band_key(budget: float) -> str:
    for key, upper in BUDGET_BAND_BOUNDARIES:
        if budget < upper:
            return key
    return BUDGET_BAND_TOP_KEY


def _is_price_only_win(item: dict[str, Any]) -> bool:
    """Whether a settlement item is a price-only estimated win.

    Accepts both the rich engine settlement (``would_have_won_price_only`` string
    verdict) and the sliced dashboard item (``would_have_won`` bool) so the
    breakdown is correct regardless of which shape is fed in.
    """
    verdict = item.get("would_have_won_price_only")
    if verdict is not None:
        return str(verdict) in _WIN_VERDICTS
    return bool(item.get("would_have_won"))


def _empty_breakdown() -> dict[str, list[dict[str, Any]]]:
    return {"by_category": [], "by_budget_band": []}


def _latest_result_time(items: list[dict[str, Any]]) -> str | None:
    """Most recent award time (안내일/개찰일) in the group, ISO string or None.

    Surfaces per-group data freshness so a stale category is visible alongside
    its (possibly thin) sample. Items without a ``result_time`` are ignored.
    """
    times = [
        str(entry["result_time"])
        for entry in items
        if entry.get("result_time") not in (None, "")
    ]
    return max(times) if times else None


def _aggregate_group(items: list[dict[str, Any]]) -> dict[str, Any]:
    settled_count = len(items)
    would_have_won_count = sum(1 for entry in items if _is_price_only_win(entry))
    errors = [
        float(entry["absolute_bid_rate_error"])
        for entry in items
        if entry.get("absolute_bid_rate_error") is not None
    ]
    avg_error = round(sum(errors) / len(errors), 6) if errors else None
    # Price-only "close" rate. ``win_rate`` is kept (unchanged) for existing
    # consumers; ``est_price_close_rate`` is the SAME value under an honest name
    # ("가격 근접 추정율", NOT an actual award).
    est_price_close_rate = (
        round(would_have_won_count / settled_count, 6) if settled_count else None
    )
    # Eligibility-gate (PR3) estimate: favorable count over the JUDGEABLE
    # denominator (settled minus ``unknown``). ``unknown`` settlements (no 예가/
    # 낙찰하한 data) are excluded so the rate is not diluted by un-scoreable rows.
    eligible_favorable_count = sum(
        1
        for entry in items
        if str(entry.get("would_have_won_final")) == _ELIGIBLE_FAVORABLE_VERDICT
    )
    eligibility_unknown_count = sum(
        1
        for entry in items
        if str(entry.get("would_have_won_final")) == _ELIGIBILITY_UNKNOWN_VERDICT
    )
    eligibility_judged_count = settled_count - eligibility_unknown_count
    eligible_favorable_rate = (
        round(eligible_favorable_count / eligibility_judged_count, 6)
        if eligibility_judged_count > 0
        else None
    )
    return {
        "settled_count": settled_count,
        "would_have_won_count": would_have_won_count,
        # Price-only estimate (legacy key kept for frontend lockstep).
        "win_rate": est_price_close_rate,
        # Honest-named alias of the same price-only estimate.
        "est_price_close_rate": est_price_close_rate,
        # Eligibility-gate estimate (unknown excluded from denominator).
        "eligible_favorable_count": eligible_favorable_count,
        "eligibility_unknown_count": eligibility_unknown_count,
        "eligibility_judged_count": eligibility_judged_count,
        "eligible_favorable_rate": eligible_favorable_rate,
        "avg_abs_bid_rate_error": avg_error,
        # Per-group health: latest award time (freshness). ``settled_count`` above
        # already doubles as the sample-size health signal.
        "latest_result_time": _latest_result_time(items),
    }


def compute_breakdown(
    settlement_items: Optional[list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Group per-operator settlements into category / budget-band breakdowns.

    Each group carries TWO honest, separately-named rates:

    * ``win_rate`` / ``est_price_close_rate`` -- the SAME value:
      ``would_have_won_count / settled_count`` where a "win" is the price-only
      estimate (``would_have_won_price_only == "plausible"``). NOT an actual
      award; ``None`` when the group has no settled items. ``win_rate`` is the
      legacy key kept for frontend lockstep; ``est_price_close_rate`` is its
      honest-named alias.
    * ``eligible_favorable_rate`` -- the PR3 eligibility-gate estimate:
      ``eligible_favorable_count / eligibility_judged_count`` where the
      denominator EXCLUDES ``unknown`` settlements (no 예가/낙찰하한 data), so the
      rate is computed only over judgeable rows. ``None`` when nothing is
      judgeable.

    Each group also carries health fields: ``settled_count`` (sample size) and
    ``latest_result_time`` (freshness of the newest award in the group).
    """
    if not settlement_items:
        return _empty_breakdown()

    by_category: dict[str, list[dict[str, Any]]] = {}
    by_band: dict[str, list[dict[str, Any]]] = {}
    for item in settlement_items:
        category = item.get("category")
        category_key = str(category) if category not in (None, "") else "unknown"
        by_category.setdefault(category_key, []).append(item)

        budget = float(item.get("budget_estimate") or 0.0)
        by_band.setdefault(_budget_band_key(budget), []).append(item)

    category_rows = [
        {"category": key, **_aggregate_group(items)}
        for key, items in sorted(by_category.items())
    ]
    # Preserve the canonical band ordering (ascending budget).
    band_order = [key for key, _ in BUDGET_BAND_BOUNDARIES] + [BUDGET_BAND_TOP_KEY]
    band_rows = [
        {"budget_band": key, **_aggregate_group(by_band[key])}
        for key in band_order
        if key in by_band
    ]
    return {"by_category": category_rows, "by_budget_band": band_rows}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _json_loads(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        logger.warning("Failed to decode stored synthetic-experiment JSON payload")
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


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


def _safe_positive_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value if value is not None else default))
    except (TypeError, ValueError):
        return default


def _safe_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_settle_actions(value: Any) -> list[str]:
    if isinstance(value, bool):
        return ["bid_now", "review"] if value else ["bid_now"]
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return ["bid_now", "review"]
        if lowered in {"false", "0", "no", "n", ""}:
            return ["bid_now"]
        raw_actions = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_actions = value
    else:
        return ["bid_now"]

    actions: list[str] = []
    for item in raw_actions:
        action = str(item).strip()
        if action in SYNTHETIC_SETTLE_ACTIONS and action not in actions:
            actions.append(action)
    return actions or ["bid_now"]


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _sample_gap_run_context(
    run: SyntheticExperimentRun,
    *,
    summary: dict[str, Any],
    sample_report: dict[str, Any],
) -> dict[str, Any]:
    params = _json_loads(run.experiment.params_json) if run.experiment else {}
    params = params if isinstance(params, dict) else {}
    operator_slugs = (
        _json_loads(run.experiment.operator_slugs_json) if run.experiment else []
    )
    operator_slugs = operator_slugs if isinstance(operator_slugs, list) else []
    preset_name = _first_present(
        sample_report.get("preset_name"),
        run.experiment.name if run.experiment else None,
    )
    context_params = {
        "start_at": _first_present(summary.get("start_at"), params.get("start_at")),
        "end_at": _first_present(summary.get("end_at"), params.get("end_at")),
        "category": _first_present(summary.get("category"), params.get("category")),
        "limit": _safe_positive_int(
            _first_present(summary.get("limit"), params.get("limit")),
            default=100,
        ),
        "scenario": _first_present(summary.get("scenario"), params.get("scenario"))
        or "base",
        "settle_actions": _normalize_settle_actions(params.get("settle_actions")),
    }
    return {
        "run_id": run.id,
        "experiment_id": run.experiment_id,
        "preset_name": preset_name,
        "status": run.status,
        "finished_at": run.finished_at,
        **context_params,
        "params": context_params,
        "operator_slugs": [str(slug) for slug in operator_slugs],
        "synthetic_only": sample_report.get("synthetic_only") is not False,
        "report_status": sample_report.get("report_status"),
        "warnings": [],
    }


def _sample_gap_run_warnings(
    run: SyntheticExperimentRun,
    sample_report: dict[str, Any],
) -> list[dict[str, Any]]:
    mixed_data = (
        sample_report.get("synthetic_only") is False
        or sample_report.get("report_status") == REPORT_STATUS_DATA_MIXED
    )
    if not mixed_data:
        return []
    operator_slugs = sample_report.get("non_synthetic_operator_slugs") or []
    if not isinstance(operator_slugs, list):
        operator_slugs = []
    return [
        {
            "code": SAMPLE_GAP_WARNING_MIXED_DATA,
            "message": (
                "Run includes canonical/operator data mixed into a synthetic report; "
                "rerun with synthetic-only operators before using it for reporting."
            ),
            "run_ids": [run.id],
            "operator_slugs": sorted(str(slug) for slug in operator_slugs),
        }
    ]


def _dedupe_sample_gap_warnings(
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for warning in warnings:
        key = (str(warning.get("code") or "unknown"), str(warning.get("message") or ""))
        current = merged.setdefault(
            key,
            {
                "code": key[0],
                "message": key[1],
                "run_ids": [],
                "operator_slugs": [],
            },
        )
        current["run_ids"].extend(
            _safe_positive_int(run_id)
            for run_id in warning.get("run_ids", []) or []
            if _safe_positive_int(run_id) > 0
        )
        current["operator_slugs"].extend(
            str(slug)
            for slug in warning.get("operator_slugs", []) or []
            if str(slug)
        )
    return [
        {
            **warning,
            "run_ids": sorted(set(warning["run_ids"])),
            "operator_slugs": sorted(set(warning["operator_slugs"])),
        }
        for warning in merged.values()
    ]


def _sample_gap_action(
    code: str,
    label: str,
    detail: str,
) -> dict[str, str]:
    return {"code": code, "label": label, "detail": detail}


def _sample_gap_candidate_name(
    *, preset_name: Optional[str], dimension: str, key: str, action_code: str
) -> str:
    if preset_name and action_code == "rerun_related_preset":
        return str(preset_name)[:200]
    if preset_name:
        return f"{preset_name}-{action_code}"[:200]
    clean_key = key.replace("/", "-").strip() or "unknown"
    return f"sample-gap-{dimension}-{clean_key}"[:200]


def _sample_gap_candidate_description(
    *, dimension: str, key: str, action_label: str
) -> str:
    return (
        f"Sample-gap follow-up candidate for {dimension}:{key}. "
        f"Recommended action: {action_label}."
    )


def _normalized_experiment_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    normalized["limit"] = _safe_positive_int(normalized.get("limit"), default=100)
    normalized["scenario"] = str(normalized.get("scenario") or "base")
    normalized["settle_actions"] = _normalize_settle_actions(
        normalized.get("settle_actions")
    )
    return normalized


def _candidate_params_for_action(
    params: dict[str, Any],
    *,
    action_code: str,
    missing_settled_count: int,
) -> dict[str, Any]:
    candidate = _normalized_experiment_params(params)
    if action_code == "increase_limit":
        current_limit = _safe_positive_int(candidate.get("limit"), default=100)
        suggested_limit = max(
            current_limit + max(1, missing_settled_count),
            int(current_limit * 1.5),
        )
        candidate["limit"] = min(1000, suggested_limit)
    return candidate


def _sample_gap_params_match(
    experiment: SyntheticExperiment,
    params: dict[str, Any],
) -> bool:
    saved = _json_loads(experiment.params_json) or {}
    if not isinstance(saved, dict):
        return False
    return _sample_gap_core_params(saved) == _sample_gap_core_params(params)


def _sample_gap_core_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_experiment_params(params)
    return {
        "start_at": normalized.get("start_at"),
        "end_at": normalized.get("end_at"),
        "category": normalized.get("category"),
        "limit": normalized.get("limit"),
        "scenario": normalized.get("scenario"),
        "settle_actions": normalized.get("settle_actions"),
    }


def _sample_gap_operator_slugs_match(
    experiment: SyntheticExperiment,
    operator_slugs: list[str],
) -> bool:
    saved = _json_loads(experiment.operator_slugs_json) or []
    if not isinstance(saved, list):
        return False
    return [str(slug) for slug in saved] == [str(slug) for slug in operator_slugs]


def _sample_gap_matches_fixed_preset(
    *,
    definition: dict[str, Any],
    params: dict[str, Any],
    operator_slugs: list[str],
) -> bool:
    return (
        _sample_gap_core_params(definition.get("params") or {})
        == _sample_gap_core_params(params)
        and [str(slug) for slug in definition.get("operator_slugs") or []]
        == [str(slug) for slug in operator_slugs]
    )


def _sample_gap_source_context(
    *,
    gap: dict[str, Any],
    action_code: str,
    action_label: str,
    preset_name: Optional[str],
    params: dict[str, Any],
    operator_slugs: list[str],
    operator_targets: list[dict[str, Any]],
    operator_id_scope_ready: bool,
    run_allowed: bool,
    blocked_by_warnings: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "source": "sample_gap_candidate",
        "dimension": str(gap["dimension"]),
        "key": str(gap["key"]),
        "action_code": action_code,
        "action_label": action_label,
        "preset_name": preset_name,
        "related_run_ids": [
            _safe_positive_int(run_id)
            for run_id in gap.get("related_run_ids", []) or []
            if _safe_positive_int(run_id) > 0
        ],
        "missing_settled_count": _safe_positive_int(
            gap.get("missing_settled_count")
        ),
        "sample_target": _safe_positive_int(gap.get("sample_target")),
        "source_run_count": _safe_positive_int(gap.get("source_run_count")),
        "params": params,
        "operator_slugs": operator_slugs,
        "operator_targets": operator_targets,
        "operator_id_scope_ready": operator_id_scope_ready,
        "run_allowed": run_allowed,
        "blocked_by_warnings": blocked_by_warnings,
        "warnings": warnings,
    }


def _sample_gap_candidate_warning_context(
    gap: dict[str, Any],
) -> tuple[list[str], list[str], bool]:
    warnings = [str(code) for code in gap.get("warnings", []) or [] if str(code)]
    blocked_by_warnings = sorted(
        {
            SAMPLE_GAP_WARNING_MIXED_DATA
            for code in warnings
            if code == SAMPLE_GAP_WARNING_MIXED_DATA
        }
    )
    return warnings, blocked_by_warnings, not blocked_by_warnings


def _sample_gap_cli_command(
    *,
    preset_name: Optional[str],
    dimension: str,
    key: str,
    action_code: str,
    write: bool,
) -> str:
    parts = ["python", "scripts/run_g2_synthetic_evidence.py"]
    parts.append("--write" if write else "--dry-run")
    if preset_name:
        parts.extend(["--preset", preset_name])
    else:
        parts.extend(["--dimension", dimension, "--key", key])
    if action_code:
        parts.extend(["--action-code", action_code])
    return " ".join(shlex.quote(part) for part in parts)


class _SampleGapAccumulator:
    def __init__(self, *, dimension: str, key: str) -> None:
        self.dimension = dimension
        self.key = key
        self.settled_count = 0
        self.sample_target = 0
        self.missing_settled_count = 0
        self.total_missing_settled_count = 0
        self.related_runs: dict[int, dict[str, Any]] = {}
        self.preset_counts: Counter[str] = Counter()
        self.warning_codes: set[str] = set()

    def add(
        self,
        *,
        row: dict[str, Any],
        run_context: dict[str, Any],
        warning_codes: list[str],
    ) -> None:
        missing = _safe_positive_int(row.get("missing_settled_count"))
        settled = _safe_positive_int(row.get("settled_count"))
        target = _safe_positive_int(
            row.get("sample_target"), default=SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET
        )
        if missing <= 0:
            return
        self.total_missing_settled_count += missing
        if (
            missing > self.missing_settled_count
            or (
                missing == self.missing_settled_count
                and (self.settled_count == 0 or settled < self.settled_count)
            )
        ):
            self.missing_settled_count = missing
            self.settled_count = settled
            self.sample_target = target

        run_id = _safe_positive_int(run_context.get("run_id"))
        if run_id > 0:
            self.related_runs[run_id] = {
                **run_context,
                "warnings": sorted(set(warning_codes)),
            }
        preset_name = run_context.get("preset_name")
        if preset_name:
            self.preset_counts[str(preset_name)] += 1
        self.warning_codes.update(warning_codes)

    @property
    def source_run_count(self) -> int:
        return len(self.related_runs)

    @property
    def related_preset_names(self) -> list[str]:
        return sorted(self.preset_counts)

    def primary_run_context(self) -> dict[str, Any]:
        if not self.related_runs:
            return {}
        if self.preset_counts:
            preset_name = self.preset_counts.most_common(1)[0][0]
            for run in self.related_runs.values():
                if run.get("preset_name") == preset_name:
                    return run
        return next(iter(self.related_runs.values()))

    def recommendation(self) -> dict[str, Any]:
        run_context = self.primary_run_context()
        params = dict(run_context.get("params") or {})
        if self.dimension == "category" and self.key != "missing":
            params["category"] = self.key
        elif "category" not in params and run_context.get("category") is not None:
            params["category"] = run_context.get("category")

        current_limit = _safe_positive_int(params.get("limit"), default=100)
        if current_limit:
            params["limit"] = current_limit
        actions = [
            _sample_gap_action(
                "rerun_related_preset",
                "Rerun related preset",
                "Repeat the related synthetic experiment preset and keep the same result window first.",
            )
        ]
        if self.dimension == "category":
            actions.append(
                _sample_gap_action(
                    "expand_category_window",
                    "Expand category window",
                    "If the rerun is still thin, widen the date window for this category before changing operators.",
                )
            )
        if self.dimension in {"preset", "budget_band"}:
            actions.append(
                _sample_gap_action(
                    "increase_limit",
                    "Increase limit",
                    "Raise the backtest limit when the current preset cannot collect enough settled samples.",
                )
            )
        if self.dimension == "business_type":
            actions.append(
                _sample_gap_action(
                    "review_operator_mix",
                    "Review operator mix",
                    "Rerun the preset with operators that cover this business type before widening all categories.",
                )
            )
        if SAMPLE_GAP_WARNING_MIXED_DATA in self.warning_codes:
            actions.append(
                _sample_gap_action(
                    "rerun_synthetic_only",
                    "Rerun synthetic-only",
                    "Exclude canonical operators before using this sample set for repeatable reporting.",
                )
            )

        deduped_actions: dict[str, dict[str, str]] = {}
        for action in actions:
            deduped_actions.setdefault(action["code"], action)

        return {
            "preset_name": run_context.get("preset_name"),
            "params": params,
            "actions": list(deduped_actions.values()),
        }

    def to_item(self, *, priority: int) -> dict[str, Any]:
        related_runs = sorted(
            self.related_runs.values(),
            key=lambda item: int(item.get("run_id") or 0),
            reverse=True,
        )
        return {
            "priority": priority,
            "dimension": self.dimension,
            "key": self.key,
            "settled_count": self.settled_count,
            "sample_target": self.sample_target,
            "missing_settled_count": self.missing_settled_count,
            "total_missing_settled_count": self.total_missing_settled_count,
            "source_run_count": self.source_run_count,
            "related_preset_names": self.related_preset_names,
            "related_run_ids": [run["run_id"] for run in related_runs],
            "related_runs": related_runs,
            "recommendation": self.recommendation(),
            "warnings": sorted(self.warning_codes),
        }


class SyntheticExperimentService:
    """CRUD + run lifecycle for synthetic experiments."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- experiment CRUD -------------------------------------------------------

    def create_experiment(
        self,
        *,
        name: str,
        description: Optional[str],
        params: dict[str, Any],
        operator_slugs: Optional[list[str]],
    ) -> SyntheticExperiment:
        experiment = SyntheticExperiment(
            name=name,
            description=description,
            params_json=_json_dumps(params),
            operator_slugs_json=_json_dumps(operator_slugs or []),
        )
        self.db.add(experiment)
        self.db.commit()
        self.db.refresh(experiment)
        return experiment

    def list_experiments(self) -> list[SyntheticExperiment]:
        return (
            self.db.query(SyntheticExperiment)
            .order_by(
                SyntheticExperiment.created_at.desc(), SyntheticExperiment.id.desc()
            )
            .all()
        )

    def list_presets(self) -> list[dict[str, Any]]:
        """Return fixed G-1 presets with their saved experiment/run state."""
        return [
            self._serialize_preset(name, definition)
            for name, definition in SYNTHETIC_EXPERIMENT_PRESETS.items()
        ]

    def ensure_preset(self, name: str) -> Optional[SyntheticExperiment]:
        """Create or update the saved experiment for a fixed G-1 preset."""
        definition = SYNTHETIC_EXPERIMENT_PRESETS.get(name)
        if definition is None:
            return None
        experiment = (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name == name)
            .first()
        )
        if experiment is None:
            experiment = SyntheticExperiment(name=name)
            self.db.add(experiment)
        experiment.description = str(definition["description"])
        experiment.params_json = _json_dumps(definition["params"])
        experiment.operator_slugs_json = _json_dumps(definition["operator_slugs"])
        self.db.commit()
        self.db.refresh(experiment)
        return experiment

    def _serialize_preset(
        self, name: str, definition: dict[str, Any]
    ) -> dict[str, Any]:
        experiment = (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name == name)
            .first()
        )
        latest_run = experiment.runs[0] if experiment and experiment.runs else None
        return {
            "name": name,
            "description": str(definition["description"]),
            "params": definition["params"],
            "operator_slugs": list(definition["operator_slugs"]),
            "experiment_id": experiment.id if experiment else None,
            "latest_run_id": latest_run.id if latest_run else None,
            "latest_run_status": latest_run.status if latest_run else None,
        }

    def get_experiment(self, experiment_id: int) -> Optional[SyntheticExperiment]:
        return (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.id == experiment_id)
            .first()
        )

    # --- run lifecycle ---------------------------------------------------------

    def create_run(
        self,
        experiment: SyntheticExperiment,
        *,
        source_sample_gap_candidate: Optional[dict[str, Any]] = None,
    ) -> SyntheticExperimentRun:
        """Create a queued run and enqueue the Celery backtest task.

        The Celery payload carries ``experiment_id`` + ``run_id`` so the task can
        persist the run/result lifecycle, plus the saved params and slug subset.
        """
        from app.tasks.jobs import enqueue_synthetic_operator_backtest

        run = SyntheticExperimentRun(
            experiment_id=experiment.id,
            status=RUN_STATUS_QUEUED,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        params = _json_loads(experiment.params_json) or {}
        operator_slugs = _json_loads(experiment.operator_slugs_json) or []
        payload: dict[str, Any] = dict(params)
        payload["experiment_id"] = experiment.id
        payload["run_id"] = run.id
        payload["slugs"] = operator_slugs or None
        if isinstance(source_sample_gap_candidate, dict):
            payload["source_sample_gap_candidate"] = source_sample_gap_candidate

        async_result = enqueue_synthetic_operator_backtest(payload=payload)
        run.task_id = getattr(async_result, "id", None)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(
        self, experiment_id: int, run_id: int
    ) -> Optional[SyntheticExperimentRun]:
        run = (
            self.db.query(SyntheticExperimentRun)
            .filter(
                SyntheticExperimentRun.id == run_id,
                SyntheticExperimentRun.experiment_id == experiment_id,
            )
            .first()
        )
        if run is None:
            return None
        if run.status in (RUN_STATUS_QUEUED, RUN_STATUS_RUNNING) and run.task_id:
            self._sync_run_with_task(run)
        return run

    def build_sample_gap_plan(self, *, max_runs: int = 20) -> dict[str, Any]:
        """Aggregate recent completed sample-report gaps into a backfill plan.

        This is read-only planning over persisted run summaries. It deliberately
        does not trigger DB backfills or new experiment runs; the response gives
        operators the preset/category/window/limit hints needed to decide the
        next synthetic backtest.
        """
        bounded_max_runs = min(100, max(1, int(max_runs or 20)))
        runs = (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.status == RUN_STATUS_COMPLETED)
            .order_by(
                SyntheticExperimentRun.finished_at.is_(None).asc(),
                SyntheticExperimentRun.finished_at.desc(),
                SyntheticExperimentRun.created_at.desc(),
                SyntheticExperimentRun.id.desc(),
            )
            .limit(bounded_max_runs)
            .all()
        )

        gap_groups: dict[tuple[str, str], _SampleGapAccumulator] = {}
        warnings: list[dict[str, Any]] = []
        legacy_run_ids: list[int] = []
        source_run_count = 0

        for run in runs:
            summary = _json_loads(run.summary_json)
            if not isinstance(summary, dict):
                legacy_run_ids.append(run.id)
                continue
            sample_report = summary.get("sample_report")
            if not isinstance(sample_report, dict):
                legacy_run_ids.append(run.id)
                continue

            source_run_count += 1
            run_context = _sample_gap_run_context(
                run, summary=summary, sample_report=sample_report
            )
            run_warnings = _sample_gap_run_warnings(run, sample_report)
            warning_codes = [str(warning["code"]) for warning in run_warnings]
            warnings.extend(run_warnings)

            lacking_groups = sample_report.get("lacking_groups") or []
            if not isinstance(lacking_groups, list):
                continue
            for row in lacking_groups:
                if not isinstance(row, dict):
                    continue
                dimension = str(row.get("dimension") or "")
                if dimension not in SAMPLE_GAP_DIMENSION_ORDER:
                    continue
                key = str(row.get("key") or "unknown")
                missing = _safe_positive_int(row.get("missing_settled_count"))
                if missing <= 0:
                    continue
                group_key = (dimension, key)
                accumulator = gap_groups.get(group_key)
                if accumulator is None:
                    accumulator = _SampleGapAccumulator(
                        dimension=dimension,
                        key=key,
                    )
                    gap_groups[group_key] = accumulator
                accumulator.add(
                    row=row,
                    run_context=run_context,
                    warning_codes=warning_codes,
                )

        if legacy_run_ids:
            warnings.append(
                {
                    "code": SAMPLE_GAP_WARNING_LEGACY_SUMMARY,
                    "message": (
                        "Completed runs without summary.sample_report were skipped; "
                        "rerun them to include G-1 sample-gap metadata."
                    ),
                    "run_ids": sorted(legacy_run_ids),
                    "operator_slugs": [],
                }
            )

        ordered_gaps = sorted(
            gap_groups.values(),
            key=lambda item: (
                -item.total_missing_settled_count,
                -item.missing_settled_count,
                -item.source_run_count,
                SAMPLE_GAP_DIMENSION_ORDER[item.dimension],
                item.key,
            ),
        )
        gaps = [
            accumulator.to_item(priority=index + 1)
            for index, accumulator in enumerate(ordered_gaps)
        ]
        return {
            "generated_at": datetime.now(timezone.utc),
            "max_runs": bounded_max_runs,
            "scanned_completed_run_count": len(runs),
            "source_run_count": source_run_count,
            "legacy_summary_run_count": len(legacy_run_ids),
            "gap_count": len(gaps),
            "warnings": _dedupe_sample_gap_warnings(warnings),
            "gaps": gaps,
        }

    def build_sample_gap_run_candidate(
        self,
        *,
        dimension: str,
        key: str,
        max_runs: int = 20,
        action_code: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Build a read-only runnable candidate from one sample-gap item.

        This intentionally does not save an experiment or enqueue a run. It turns
        the recommendation already exposed in ``sample-gaps`` into an explicit
        experiment payload plus the next UI step.
        """
        plan = self.build_sample_gap_plan(max_runs=max_runs)
        gap = next(
            (
                item
                for item in plan["gaps"]
                if item["dimension"] == dimension and item["key"] == key
            ),
            None,
        )
        if gap is None:
            return None

        recommendation, action = self._resolve_sample_gap_candidate_action(
            gap,
            action_code=action_code,
        )
        preset_name = recommendation.get("preset_name")
        preset_name = str(preset_name) if preset_name else None
        definition = (
            SYNTHETIC_EXPERIMENT_PRESETS.get(preset_name) if preset_name else None
        )
        experiment = self._sample_gap_candidate_experiment(
            preset_name=preset_name,
            gap=gap,
        )
        base_params = dict(definition.get("params") or {}) if definition else {}
        base_params.update(recommendation.get("params") or {})
        params = _candidate_params_for_action(
            base_params,
            action_code=str(action["code"]),
            missing_settled_count=_safe_positive_int(
                gap.get("missing_settled_count")
            ),
        )
        operator_slugs = self._sample_gap_candidate_operator_slugs(
            definition=definition,
            experiment=experiment,
            gap=gap,
        )
        operator_targets = self._sample_gap_candidate_operator_targets(operator_slugs)
        operator_id_scope_ready = bool(operator_targets) and all(
            bool(target.get("operator_id_scope_ready"))
            for target in operator_targets
        )
        warnings, blocked_by_warnings, run_allowed = (
            _sample_gap_candidate_warning_context(gap)
        )
        latest_run = self._latest_experiment_run(experiment)
        action_label = str(action.get("label") or action["code"])
        experiment_payload = {
            "name": _sample_gap_candidate_name(
                preset_name=preset_name,
                dimension=str(gap["dimension"]),
                key=str(gap["key"]),
                action_code=str(action["code"]),
            ),
            "description": _sample_gap_candidate_description(
                dimension=str(gap["dimension"]),
                key=str(gap["key"]),
                action_label=action_label,
            ),
            "params": params,
            "operator_slugs": operator_slugs,
        }
        next_step = self._sample_gap_candidate_next_step(
            run_allowed=run_allowed,
            experiment=experiment,
            preset_name=preset_name,
            definition=definition,
            params=params,
            operator_slugs=operator_slugs,
        )
        source_context = _sample_gap_source_context(
            gap=gap,
            action_code=str(action["code"]),
            action_label=action_label,
            preset_name=preset_name,
            params=params,
            operator_slugs=operator_slugs,
            operator_targets=operator_targets,
            operator_id_scope_ready=operator_id_scope_ready,
            run_allowed=run_allowed,
            blocked_by_warnings=blocked_by_warnings,
            warnings=warnings,
        )
        execution_plan = self._sample_gap_candidate_execution_plan(
            next_step=next_step,
            preset_name=preset_name,
            experiment_id=experiment.id if experiment else None,
            experiment_payload=experiment_payload,
            source_context=source_context,
        )
        return self._sample_gap_candidate_response(
            gap=gap,
            action=action,
            action_label=action_label,
            preset_name=preset_name,
            params=params,
            operator_slugs=operator_slugs,
            operator_targets=operator_targets,
            operator_id_scope_ready=operator_id_scope_ready,
            experiment_payload=experiment_payload,
            experiment=experiment,
            latest_run=latest_run,
            next_step=next_step,
            execution_plan=execution_plan,
            run_allowed=run_allowed,
            blocked_by_warnings=blocked_by_warnings,
            warnings=warnings,
        )

    def _sample_gap_candidate_response(
        self,
        *,
        gap: dict[str, Any],
        action: dict[str, Any],
        action_label: str,
        preset_name: Optional[str],
        params: dict[str, Any],
        operator_slugs: list[str],
        operator_targets: list[dict[str, Any]],
        operator_id_scope_ready: bool,
        experiment_payload: dict[str, Any],
        experiment: Optional[SyntheticExperiment],
        latest_run: Optional[SyntheticExperimentRun],
        next_step: str,
        execution_plan: dict[str, Any],
        run_allowed: bool,
        blocked_by_warnings: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc),
            "gap": gap,
            "action_code": str(action["code"]),
            "action_label": action_label,
            "preset_name": preset_name,
            "params": params,
            "operator_slugs": operator_slugs,
            "operator_targets": operator_targets,
            "operator_id_scope_ready": operator_id_scope_ready,
            "experiment_payload": experiment_payload,
            "experiment_id": experiment.id if experiment else None,
            "latest_run_id": latest_run.id if latest_run else None,
            "latest_run_status": latest_run.status if latest_run else None,
            "next_step": next_step,
            "execution_plan": execution_plan,
            "run_allowed": run_allowed,
            "blocked_by_warnings": blocked_by_warnings,
            "warnings": warnings,
            "message": self._sample_gap_candidate_message(
                next_step=next_step,
                preset_name=preset_name,
                blocked_by_warnings=blocked_by_warnings,
            ),
        }

    def _resolve_sample_gap_candidate_action(
        self,
        gap: dict[str, Any],
        *,
        action_code: Optional[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        recommendation = gap.get("recommendation") or {}
        actions = recommendation.get("actions") or []
        action_lookup = {
            str(action.get("code")): action
            for action in actions
            if isinstance(action, dict) and action.get("code")
        }
        selected_action_code = action_code or self._default_sample_gap_action_code(
            gap,
            action_lookup,
        )
        action = action_lookup.get(str(selected_action_code))
        if action is None:
            available = ", ".join(sorted(action_lookup)) or "none"
            raise ValueError(
                f"Unsupported sample-gap action '{selected_action_code}'. "
                f"Available actions: {available}."
            )
        return recommendation, action

    def materialize_sample_gap_candidate_run(
        self,
        *,
        dimension: str,
        key: str,
        max_runs: int = 20,
        action_code: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Persist the selected candidate and enqueue its async evidence run.

        This is intentionally not called by the candidate API endpoint. It is
        used by explicit operator write paths (CLI or a separate approval flow)
        after the read-only candidate has been inspected.
        """
        candidate = self.build_sample_gap_run_candidate(
            dimension=dimension,
            key=key,
            max_runs=max_runs,
            action_code=action_code,
        )
        if candidate is None:
            return None
        if not candidate.get("run_allowed"):
            return {
                "status": "blocked",
                "candidate": candidate,
                "experiment": None,
                "run": None,
            }

        next_step = str(candidate.get("next_step") or "")
        experiment: Optional[SyntheticExperiment] = None
        if next_step == "run_existing_experiment":
            experiment_id = _safe_positive_int(candidate.get("experiment_id"))
            if experiment_id > 0:
                experiment = self.get_experiment(experiment_id)
        elif next_step == "save_preset":
            preset_name = candidate.get("preset_name")
            if preset_name:
                experiment = self.ensure_preset(str(preset_name))
        else:
            payload = candidate.get("experiment_payload") or {}
            experiment = self.create_experiment(
                name=str(payload.get("name") or "sample-gap-candidate"),
                description=payload.get("description"),
                params=dict(payload.get("params") or {}),
                operator_slugs=list(payload.get("operator_slugs") or []),
            )

        if experiment is None:
            raise ValueError("Could not materialize sample-gap candidate experiment.")

        source_context = (
            (candidate.get("execution_plan") or {}).get("source_context") or {}
        )
        run = self.create_run(
            experiment,
            source_sample_gap_candidate=(
                source_context if isinstance(source_context, dict) else None
            ),
        )
        return {
            "status": "queued",
            "candidate": candidate,
            "experiment": self.serialize_experiment(experiment),
            "run": self.serialize_run_detail(run),
        }

    def _default_sample_gap_action_code(
        self,
        gap: dict[str, Any],
        action_lookup: dict[str, dict[str, Any]],
    ) -> str:
        warnings = set(gap.get("warnings", []) or [])
        if (
            SAMPLE_GAP_WARNING_MIXED_DATA in warnings
            and "rerun_synthetic_only" in action_lookup
        ):
            return "rerun_synthetic_only"
        if action_lookup:
            return next(iter(action_lookup))
        raise ValueError("Sample gap has no recommended actions.")

    def _sample_gap_candidate_experiment(
        self,
        *,
        preset_name: Optional[str],
        gap: dict[str, Any],
    ) -> Optional[SyntheticExperiment]:
        if preset_name:
            experiment = (
                self.db.query(SyntheticExperiment)
                .filter(SyntheticExperiment.name == preset_name)
                .first()
            )
            if experiment is not None:
                return experiment

        related_runs = gap.get("related_runs") or []
        source_experiment_id = 0
        if related_runs and isinstance(related_runs[0], dict):
            source_experiment_id = _safe_positive_int(
                related_runs[0].get("experiment_id")
            )
        if source_experiment_id <= 0:
            return None
        return self.get_experiment(source_experiment_id)

    def _sample_gap_candidate_operator_slugs(
        self,
        *,
        definition: Optional[dict[str, Any]],
        experiment: Optional[SyntheticExperiment],
        gap: dict[str, Any],
    ) -> list[str]:
        if definition is not None:
            return [str(slug) for slug in definition.get("operator_slugs") or []]
        if experiment is not None:
            operator_slugs = _json_loads(experiment.operator_slugs_json) or []
            if isinstance(operator_slugs, list):
                return [str(slug) for slug in operator_slugs]
        related_runs = gap.get("related_runs") or []
        if related_runs and isinstance(related_runs[0], dict):
            operator_slugs = related_runs[0].get("operator_slugs") or []
            if isinstance(operator_slugs, list):
                return [str(slug) for slug in operator_slugs]
        return []

    def _sample_gap_candidate_operator_targets(
        self,
        operator_slugs: list[str],
    ) -> list[dict[str, Any]]:
        pairs = [
            (
                str(slug),
                (
                    str(slug)
                    if str(slug).startswith(SYNTHETIC_USERNAME_PREFIX)
                    else f"{SYNTHETIC_USERNAME_PREFIX}{slug}"
                ),
            )
            for slug in operator_slugs
        ]
        usernames = [username for _, username in pairs]
        users_by_username: dict[str, User] = {}
        if usernames:
            users = (
                self.db.query(User)
                .filter(User.username.in_(usernames))
                .filter(User.is_active.is_(True))
                .all()
            )
            users_by_username = {str(user.username): user for user in users}

        targets: list[dict[str, Any]] = []
        for slug, username in pairs:
            user = users_by_username.get(username)
            user_id = int(user.id) if user is not None else None
            resolved = user_id is not None
            targets.append(
                {
                    "slug": slug,
                    "username": username,
                    "operator_id": user_id,
                    "user_id": user_id,
                    "resolved": resolved,
                    "operator_id_scope_ready": resolved,
                }
            )
        return targets

    def _latest_experiment_run(
        self, experiment: Optional[SyntheticExperiment]
    ) -> Optional[SyntheticExperimentRun]:
        if experiment is None or not experiment.runs:
            return None
        return max(experiment.runs, key=lambda run: run.id)

    def _sample_gap_candidate_next_step(
        self,
        *,
        run_allowed: bool,
        experiment: Optional[SyntheticExperiment],
        preset_name: Optional[str],
        definition: Optional[dict[str, Any]],
        params: dict[str, Any],
        operator_slugs: list[str],
    ) -> str:
        if not run_allowed:
            return "resolve_mixed_data"
        if (
            experiment is not None
            and _sample_gap_params_match(experiment, params)
            and _sample_gap_operator_slugs_match(experiment, operator_slugs)
        ):
            return "run_existing_experiment"
        if (
            preset_name
            and definition is not None
            and _sample_gap_matches_fixed_preset(
                definition=definition,
                params=params,
                operator_slugs=operator_slugs,
            )
        ):
            return "save_preset"
        return "create_experiment"

    def _sample_gap_candidate_execution_plan(
        self,
        *,
        next_step: str,
        preset_name: Optional[str],
        experiment_id: Optional[int],
        experiment_payload: dict[str, Any],
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        dimension = str(source_context.get("dimension") or "")
        key = str(source_context.get("key") or "")
        action_code = str(source_context.get("action_code") or "")
        dry_run_command = _sample_gap_cli_command(
            preset_name=preset_name,
            dimension=dimension,
            key=key,
            action_code=action_code,
            write=False,
        )
        write_command = _sample_gap_cli_command(
            preset_name=preset_name,
            dimension=dimension,
            key=key,
            action_code=action_code,
            write=True,
        )
        base = {
            "approval_required": True,
            "dry_run_default": True,
            "source_context": source_context,
            "cli_command": dry_run_command,
            "write_cli_command": None,
        }
        run_body = {"source_sample_gap_candidate": source_context}
        if next_step == "resolve_mixed_data":
            return {
                **base,
                "mode": "blocked",
                "preset_request": None,
                "experiment_request": None,
                "run_request": None,
                "instructions": [
                    "Do not enqueue a run from this candidate.",
                    "Resolve mixed canonical/synthetic data and rerun synthetic-only first.",
                ],
            }
        if next_step == "run_existing_experiment" and experiment_id is not None:
            return {
                **base,
                "mode": "run_existing_experiment",
                "preset_request": None,
                "experiment_request": None,
                "run_request": {
                    "method": "POST",
                    "path": f"/api/v1/synthetic/experiments/{experiment_id}/runs",
                    "body": run_body,
                },
                "write_cli_command": write_command,
                "instructions": [
                    "Dry-run this plan first; --write enqueues the asynchronous evidence run.",
                    "The API request only queues the run and does not run the backtest inline.",
                ],
            }
        if next_step == "save_preset" and preset_name:
            return {
                **base,
                "mode": "save_preset_then_run",
                "preset_request": {
                    "method": "POST",
                    "path": f"/api/v1/synthetic/experiments/presets/{preset_name}",
                    "body": {},
                },
                "experiment_request": None,
                "run_request": {
                    "method": "POST",
                    "path": "/api/v1/synthetic/experiments/{experiment_id}/runs",
                    "body": run_body,
                },
                "write_cli_command": write_command,
                "instructions": [
                    "Save the preset, then enqueue a run using the returned experiment id.",
                    "--write performs both DB write steps and requires operator approval.",
                ],
            }
        return {
            **base,
            "mode": "create_experiment_then_run",
            "preset_request": None,
            "experiment_request": {
                "method": "POST",
                "path": "/api/v1/synthetic/experiments",
                "body": experiment_payload,
            },
            "run_request": {
                "method": "POST",
                "path": "/api/v1/synthetic/experiments/{experiment_id}/runs",
                "body": run_body,
            },
            "write_cli_command": write_command,
            "instructions": [
                "Create the candidate experiment, then enqueue a run using the returned experiment id.",
                "--write performs both DB write steps and requires operator approval.",
            ],
        }

    def _sample_gap_candidate_message(
        self,
        *,
        next_step: str,
        preset_name: Optional[str],
        blocked_by_warnings: list[str],
    ) -> str:
        if SAMPLE_GAP_WARNING_MIXED_DATA in blocked_by_warnings:
            return (
                "Related runs include canonical/operator data mixed with synthetic "
                "results. Review the source run and create a synthetic-only rerun "
                "candidate before treating it as reporting-ready."
            )
        if next_step == "run_existing_experiment":
            return "Existing experiment is ready to select and run asynchronously."
        if next_step == "save_preset":
            return f"Save preset {preset_name} before starting a run."
        return "Create this experiment candidate before starting a run."

    def _sync_run_with_task(self, run: SyntheticExperimentRun) -> None:
        """Reconcile a still-pending run with the Celery task state.

        The task itself persists completion/failure to the DB; this only covers a
        task that failed without the lifecycle hook recording it (defensive).
        """
        from app.tasks.jobs import get_synthetic_backtest_task_status

        try:
            status_payload = get_synthetic_backtest_task_status(run.task_id)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Could not read task status for run %s", run.id)
            return
        if (status_payload.get("status") == "failed") and run.status not in (
            RUN_STATUS_COMPLETED,
            RUN_STATUS_FAILED,
        ):
            self.mark_failed(run.id, status_payload.get("error") or "Task failed")
            self.db.refresh(run)

    # --- lifecycle hooks invoked from inside the Celery task -------------------

    def mark_running(self, run_id: int) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        run.status = RUN_STATUS_RUNNING
        run.started_at = utc_now()
        self.db.commit()

    def mark_completed(
        self,
        run_id: int,
        result: dict[str, Any],
        *,
        source_sample_gap_candidate: Optional[dict[str, Any]] = None,
    ) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        operator_results = result.get("results", []) or []
        normalized_results: list[dict[str, Any]] = []
        for item in operator_results:
            settlement_sample = item.get("settlement_items")
            # Prefer the engine-supplied breakdown (computed over the full,
            # non-truncated settlement set). Fall back to computing from the
            # sampled ``settlement_items`` for stubbed/legacy payloads.
            breakdown = item.get("breakdown")
            if breakdown is None:
                breakdown = compute_breakdown(settlement_sample)
            normalized_results.append({**item, "breakdown": breakdown})

        preset_name = run.experiment.name if run.experiment else None
        summary = {
            "operator_count": result.get("operator_count", len(normalized_results)),
            "scenario": result.get("scenario"),
            "category": result.get("category"),
            "start_at": result.get("start_at"),
            "end_at": result.get("end_at"),
            "limit": result.get("limit"),
            **aggregate_sample_status(normalized_results),
            "sample_report": build_sample_report(
                preset_name=preset_name,
                operator_results=normalized_results,
            ),
        }
        if isinstance(source_sample_gap_candidate, dict):
            summary["source_sample_gap_candidate"] = source_sample_gap_candidate
        run.status = RUN_STATUS_COMPLETED
        run.finished_at = utc_now()
        run.error = None
        run.summary_json = _json_dumps(summary)

        excluded_metric_keys = {"settlement_items", "breakdown"}
        for item in normalized_results:
            metrics = {
                key: value
                for key, value in item.items()
                if key not in excluded_metric_keys
            }
            # Bind the per-operator result to ``operator_id`` so the G-2 evidence
            # ledger (analytics_reporting._build_g2_synthetic_experiment_summary)
            # can attribute it to a specific operator. The upstream backtest item
            # carries ``user_id`` (synthetic_backtest.list_operators) but not
            # ``operator_id``; mirror it here. ``user_id`` is preserved alongside
            # so existing consumers are unaffected.
            operator_id_value = item.get("operator_id")
            if operator_id_value is None:
                operator_id_value = item.get("user_id")
            if operator_id_value is not None:
                metrics["operator_id"] = operator_id_value
            metrics.update(
                sample_status_for_settled_count(int(item.get("settled_count") or 0))
            )
            settlement_sample = item.get("settlement_items")
            breakdown = item.get("breakdown")
            self.db.add(
                SyntheticExperimentResult(
                    run_id=run.id,
                    operator_slug=str(item.get("slug") or "unknown"),
                    metrics_json=_json_dumps(metrics),
                    settlement_sample_json=(
                        _json_dumps(settlement_sample)
                        if settlement_sample is not None
                        else None
                    ),
                    breakdown_json=_json_dumps(breakdown),
                )
            )
        self.db.commit()

    def mark_failed(self, run_id: int, error: str) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        run.status = RUN_STATUS_FAILED
        run.finished_at = utc_now()
        run.error = error
        self.db.commit()

    def _fetch_run(self, run_id: int) -> Optional[SyntheticExperimentRun]:
        return (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.id == run_id)
            .first()
        )

    # --- serialization (ORM -> response dicts) --------------------------------

    def serialize_experiment(self, experiment: SyntheticExperiment) -> dict[str, Any]:
        return {
            "id": experiment.id,
            "name": experiment.name,
            "description": experiment.description,
            "params": _json_loads(experiment.params_json) or {},
            "operator_slugs": _json_loads(experiment.operator_slugs_json) or [],
            "created_at": experiment.created_at,
            "updated_at": experiment.updated_at,
            "runs": [self.serialize_run_summary(run) for run in experiment.runs],
        }

    def serialize_run_summary(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "experiment_id": run.experiment_id,
            "status": run.status,
            "task_id": run.task_id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": run.error,
            "summary": _json_loads(run.summary_json),
            "created_at": run.created_at,
        }

    def serialize_run_detail(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        payload = self.serialize_run_summary(run)
        payload["results"] = [
            {
                **sample_status_for_settled_count(
                    int((_json_loads(item.metrics_json) or {}).get("settled_count") or 0)
                ),
                "operator_slug": item.operator_slug,
                "metrics": _json_loads(item.metrics_json) or {},
                "settlement_sample": _json_loads(item.settlement_sample_json),
                "breakdown": _json_loads(item.breakdown_json) or _empty_breakdown(),
            }
            for item in run.results
        ]
        return payload

    # --- Phase 4: CSV export ---------------------------------------------------

    def export_run_csv(self, run: SyntheticExperimentRun) -> str:
        """Render a run's per-operator metrics as a CSV document (string).

        Columns mirror the CLI comparison CSV (``EXPORT_CSV_COLUMNS``) with
        ``operator_slug`` prepended. Missing metric keys serialize to an empty
        cell. A run with no results yields a header-only CSV (still HTTP 200), so
        not-yet-completed runs export cleanly rather than erroring. ``win_rate_*``
        columns stay price-only estimates -- the engine values pass through
        unchanged.
        """
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=list(EXPORT_CSV_COLUMNS), extrasaction="ignore"
        )
        writer.writeheader()
        for item in run.results:
            metrics = _json_loads(item.metrics_json) or {}
            row: dict[str, Any] = {}
            for column in EXPORT_CSV_COLUMNS:
                if column == "operator_slug":
                    value: Any = item.operator_slug
                else:
                    value = metrics.get(column)
                row[column] = "" if value is None else value
            writer.writerow(row)
        return buffer.getvalue()

    # --- Phase 4: A/B run comparison -------------------------------------------

    def _fetch_run_by_id(self, run_id: int) -> Optional[SyntheticExperimentRun]:
        """Fetch a run by id alone (cross-experiment; for A/B comparison)."""
        return (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.id == run_id)
            .first()
        )

    def _run_compact_header(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        """Minimal run header (id + experiment_id + summary) for the compare payload."""
        return {
            "id": run.id,
            "experiment_id": run.experiment_id,
            "summary": _json_loads(run.summary_json),
        }

    @staticmethod
    def _compare_side_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        """Extract the compare metric subset from a stored per-operator metrics dict."""
        return {key: metrics.get(key) for key in _COMPARE_METRIC_KEYS}

    @staticmethod
    def _compare_delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        """Signed (b - a) deltas; ``None`` when either operand is missing/None."""
        delta: dict[str, Any] = {}
        for key in _COMPARE_DELTA_KEYS:
            a_value = a.get(key)
            b_value = b.get(key)
            if a_value is None or b_value is None:
                delta[key] = None
            else:
                delta[key] = round(float(b_value) - float(a_value), 6)
        return delta

    def compare_runs(self, run_a_id: int, run_b_id: int) -> Optional[dict[str, Any]]:
        """Join two runs' per-operator metrics by ``operator_slug`` and diff them.

        Returns ``None`` when either run is missing (router maps to 404). The two
        runs may belong to different experiments -- the join is purely on the
        operator-slug intersection. ``delta`` is ``b - a`` (positive => B higher);
        ``win_rate_*`` deltas are ``None`` when either side has no settled rows.
        """
        run_a = self._fetch_run_by_id(run_a_id)
        run_b = self._fetch_run_by_id(run_b_id)
        if run_a is None or run_b is None:
            return None

        metrics_a = {
            item.operator_slug: (_json_loads(item.metrics_json) or {})
            for item in run_a.results
        }
        metrics_b = {
            item.operator_slug: (_json_loads(item.metrics_json) or {})
            for item in run_b.results
        }

        shared = sorted(set(metrics_a) & set(metrics_b))
        operators = []
        for slug in shared:
            side_a = self._compare_side_metrics(metrics_a[slug])
            side_b = self._compare_side_metrics(metrics_b[slug])
            operators.append(
                {
                    "operator_slug": slug,
                    "a": side_a,
                    "b": side_b,
                    "delta": self._compare_delta(side_a, side_b),
                }
            )

        only_in_a = sorted(set(metrics_a) - set(metrics_b))
        only_in_b = sorted(set(metrics_b) - set(metrics_a))
        return {
            "run_a": self._run_compact_header(run_a),
            "run_b": self._run_compact_header(run_b),
            "operators": operators,
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
        }


def run_experiment_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute an experiment-scoped backtest inside a fresh DB session.

    Invoked by the Celery task. Opens its own ``SessionLocal`` so it is safe in
    both eager (memory://) and worker execution. Persists the run/result
    lifecycle when ``run_id`` is present in the payload.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    run_id = payload.get("run_id")
    service = SyntheticExperimentService(db)
    try:
        if run_id is not None:
            service.mark_running(run_id)

        backtest = SyntheticBacktestService()
        result = backtest.run_for_all(
            db,
            start_at=_parse_dt(payload.get("start_at")),
            end_at=_parse_dt(payload.get("end_at")),
            category=payload.get("category"),
            limit=int(payload.get("limit") or 100),
            scenario=str(payload.get("scenario") or "base"),
            slugs=payload.get("slugs"),
            cutoff_hours_before_deadline=_safe_optional_int(
                _first_present(
                    payload.get("cutoff_hours_before_deadline"),
                    payload.get("cutoff_hours"),
                )
            ),
            history_limit=_safe_optional_int(payload.get("history_limit")),
            settle_actions=_normalize_settle_actions(payload.get("settle_actions")),
        )
        if run_id is not None:
            source_context = payload.get("source_sample_gap_candidate")
            service.mark_completed(
                run_id,
                result,
                source_sample_gap_candidate=(
                    source_context if isinstance(source_context, dict) else None
                ),
            )
        return result
    except Exception as exc:
        if run_id is not None:
            service.mark_failed(run_id, str(exc))
        raise
    finally:
        db.close()
