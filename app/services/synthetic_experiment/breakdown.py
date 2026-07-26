"""Per-operator settlement breakdowns and price-only/eligibility aggregates.

``win_rate`` / ``est_price_close_rate`` are the SAME price-only estimate (NOT an
actual award); ``eligible_favorable_rate`` is the PR3 eligibility-gate estimate
with ``unknown`` settlements excluded from the denominator.
"""

from __future__ import annotations

from typing import Any, Optional

from app.domain.aggregates import average

from .constants import (
    BUDGET_BAND_BOUNDARIES,
    BUDGET_BAND_TOP_KEY,
    SAMPLE_STATUS_INSUFFICIENT,
    SAMPLE_STATUS_SUFFICIENT,
    SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET,
    _ELIGIBILITY_UNKNOWN_VERDICT,
    _ELIGIBLE_FAVORABLE_VERDICT,
    _WIN_VERDICTS,
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
    avg_error = average(errors, digits=6)
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
