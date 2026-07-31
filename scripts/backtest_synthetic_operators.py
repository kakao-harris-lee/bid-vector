#!/usr/bin/env python3
"""Run paper-bidding backtest for every synthetic operator and compare win rates.

Loads the synthetic operators seeded by ``scripts/seed_synthetic_operators.py``,
runs :func:`PaperBiddingBacktestService.run_historical_backtest` for each one,
and writes both a per-operator JSON breakdown and a compact CSV-style summary.

Two honest rate estimates are emitted (NEITHER is an actual award):

* ``est_price_close_rate_on_settled`` = ``would_have_won_price_only_count /
  settled_count`` -- a PRICE-ONLY "close" estimate mirroring what the
  paper-bidding service records in ``PaperBidSettlement``.
* ``eligible_favorable_rate`` = ``eligible_favorable_count /
  eligibility_judged_count`` -- the PR3 낙찰하한/적격 gate estimate, with
  ``unknown`` (no 예가/하한 data) settlements EXCLUDED from the denominator.

Field-level breakdowns (by category / budget band) are written to sibling CSVs
(``comparison_by_category.csv`` / ``comparison_by_budget_band.csv``) with
per-group sample size + freshness so thin/stale fields are visible.

Usage::

    python scripts/backtest_synthetic_operators.py \\
        --start-date 2025-01-01 --end-date 2025-12-31 --limit 200

    # Persist runs into paper_bid_runs / paper_bids / paper_bid_settlements
    python scripts/backtest_synthetic_operators.py --persist

    # Only test specific archetype slugs
    python scripts/backtest_synthetic_operators.py \\
        --operators sw-small-seoul,sw-mid-metro,cn-mid-gyeonggi
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.constants import PRICE_SCENARIOS
from app.core.database import Base, SessionLocal, engine
from app.models.models import CompanyProfile, OperatorStrategy, User
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.synthetic_experiment import compute_breakdown
from scripts._common import parse_datetime
from scripts.seed_synthetic_operators import (  # noqa: E402  (import after sys.path tweak)
    SYNTHETIC_USERNAME_PREFIX,
)


def parse_actions(value: str) -> tuple[str, ...]:
    actions = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    return actions or ("bid_now",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--category", default="", help="Optional category filter applied to every operator.")
    parser.add_argument("--start-date", help="Award window start date/datetime.")
    parser.add_argument("--end-date", help="Award window end date/datetime.")
    parser.add_argument("--limit", type=int, default=150, help="Maximum awarded projects to replay per operator.")
    parser.add_argument(
        "--scenario",
        choices=list(PRICE_SCENARIOS),
        default="base",
    )
    parser.add_argument("--history-limit", type=int, default=80)
    parser.add_argument("--cutoff-hours-before-deadline", type=int, default=2)
    parser.add_argument("--strategy-version", default="synthetic-backtest")
    parser.add_argument("--model-version", default="current")
    parser.add_argument(
        "--settle-actions",
        default="bid_now",
        help="Comma-separated actions counted as virtual submissions. Default: bid_now.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist paper_bid_* rows for each operator run.",
    )
    parser.add_argument(
        "--operators",
        default="",
        help="Optional comma-separated archetype slugs to limit the run to (e.g. sw-small-seoul,cn-mid-gyeonggi).",
    )
    parser.add_argument(
        "--out-dir",
        default="models/reports/synthetic-operators",
        help="Directory for per-operator JSON artifacts and the comparison summary.",
    )
    return parser


def _load_synthetic_operators(db, only_slugs: set[str] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    users = (
        db.query(User)
        .filter(User.username.like(f"{SYNTHETIC_USERNAME_PREFIX}%"))
        .order_by(User.id.asc())
        .all()
    )
    for user in users:
        slug = user.username[len(SYNTHETIC_USERNAME_PREFIX):]
        if only_slugs and slug not in only_slugs:
            continue
        profile = (
            db.query(CompanyProfile)
            .filter(CompanyProfile.user_id == user.id)
            .first()
        )
        strategy = (
            db.query(OperatorStrategy)
            .filter(OperatorStrategy.user_id == user.id)
            .first()
        )
        if profile is None or strategy is None:
            # Skip half-seeded rows so the backtest never silently uses defaults.
            continue
        rows.append(
            {
                "slug": slug,
                "user_id": int(user.id),
                "username": user.username,
                "company": user.company,
                "business_type": profile.business_type,
                "region_codes": profile.region_codes,
                "focus_categories": strategy.focus_categories,
                "min_budget_estimate": float(strategy.min_budget_estimate or 0.0),
                "max_budget_estimate": float(strategy.max_budget_estimate or 0.0),
            }
        )
    return rows


def _safe_ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    try:
        n = float(numerator or 0.0)
        d = float(denominator or 0.0)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return round(n / d, 6)


def _condense_summary(summary: dict[str, Any]) -> dict[str, Any]:
    settled = int(summary.get("settled_count") or 0)
    candidate = int(summary.get("candidate_count") or 0)
    paper_bids = int(summary.get("paper_bid_count") or 0)
    win_plausible = int(summary.get("would_have_won_price_only_count") or 0)
    # Eligibility-gate (PR3) tallies. ``unknown`` (no 예가/낙찰하한 data) is excluded
    # from the eligibility-rate denominator so the rate is computed only over
    # judgeable settlements -- the honest counterpart to the price-only estimate.
    eligible_favorable = int(
        summary.get("would_have_won_final_eligible_favorable_count") or 0
    )
    eligibility_unknown = int(
        summary.get("would_have_won_final_unknown_count") or 0
    )
    eligibility_judged = settled - eligibility_unknown
    return {
        "candidate_count": candidate,
        "paper_bid_count": paper_bids,
        "settled_count": settled,
        "skipped_by_strategy_count": int(summary.get("skipped_by_strategy_count") or 0),
        "would_have_won_price_only_count": win_plausible,
        # Honest naming: these are PRICE-ONLY "close" estimates, NOT actual awards.
        # The legacy ``win_rate_*`` names are dropped here (CLI artifacts have no
        # frontend consumer) in favour of explicit ``est_price_close_*`` names.
        "est_price_close_rate_on_settled": _safe_ratio(win_plausible, settled),
        "est_price_close_rate_on_candidates": _safe_ratio(win_plausible, candidate),
        # Eligibility-gate estimate: favorable over judgeable (unknown excluded).
        "would_have_won_final_eligible_favorable_count": eligible_favorable,
        "would_have_won_final_unknown_count": eligibility_unknown,
        "eligibility_judged_count": eligibility_judged,
        "eligible_favorable_rate": _safe_ratio(eligible_favorable, eligibility_judged),
        "bid_submission_rate": _safe_ratio(paper_bids, candidate),
        "average_absolute_bid_rate_error": summary.get("average_absolute_bid_rate_error"),
        "average_absolute_amount_error_rate": summary.get("average_absolute_amount_error_rate"),
        "within_0_1pct_count": int(summary.get("within_0_1pct_count") or 0),
        "within_0_3pct_count": int(summary.get("within_0_3pct_count") or 0),
        "within_1pct_count": int(summary.get("within_1pct_count") or 0),
        "price_close_count": int(summary.get("price_close_count") or 0),
        "price_competitive_count": int(summary.get("price_competitive_count") or 0),
        "action_counts": summary.get("action_counts") or {},
    }


def _write_comparison_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    # Column names keep the price-only/estimate qualifier explicit
    # (``est_price_close_rate_*``) and surface the eligibility-gate estimate
    # (``eligible_favorable_rate``, unknown excluded from its denominator).
    headers = [
        "slug",
        "business_type",
        "candidate_count",
        "paper_bid_count",
        "settled_count",
        "skipped_by_strategy_count",
        "would_have_won_price_only_count",
        "est_price_close_rate_on_settled",
        "est_price_close_rate_on_candidates",
        "eligible_favorable_count",
        "eligibility_judged_count",
        "eligible_favorable_rate",
        "bid_submission_rate",
        "average_absolute_bid_rate_error",
        "average_absolute_amount_error_rate",
    ]
    lines = [",".join(headers)]
    for row in rows:
        condensed = row["summary"]
        values = [
            row["slug"],
            row["operator"].get("business_type", ""),
            condensed.get("candidate_count", 0),
            condensed.get("paper_bid_count", 0),
            condensed.get("settled_count", 0),
            condensed.get("skipped_by_strategy_count", 0),
            condensed.get("would_have_won_price_only_count", 0),
            condensed.get("est_price_close_rate_on_settled", ""),
            condensed.get("est_price_close_rate_on_candidates", ""),
            condensed.get("would_have_won_final_eligible_favorable_count", 0),
            condensed.get("eligibility_judged_count", 0),
            condensed.get("eligible_favorable_rate", ""),
            condensed.get("bid_submission_rate", ""),
            condensed.get("average_absolute_bid_rate_error", ""),
            condensed.get("average_absolute_amount_error_rate", ""),
        ]
        lines.append(",".join("" if value is None else str(value) for value in values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Long-format breakdown CSV headers. ``est_price_close_rate`` is the price-only
# estimate; ``eligible_favorable_rate`` is the eligibility-gate estimate (unknown
# excluded). ``latest_result_time`` is the per-group freshness signal.
_BREAKDOWN_CSV_COMMON_TAIL = [
    "settled_count",
    "would_have_won_count",
    "est_price_close_rate",
    "eligible_favorable_count",
    "eligibility_judged_count",
    "eligible_favorable_rate",
    "avg_abs_bid_rate_error",
    "latest_result_time",
]


def _breakdown_group_values(group: dict[str, Any]) -> list[Any]:
    return [
        group.get("settled_count", 0),
        group.get("would_have_won_count", 0),
        # Prefer the honest alias; fall back to legacy ``win_rate`` for safety.
        group.get("est_price_close_rate", group.get("win_rate", "")),
        group.get("eligible_favorable_count", 0),
        group.get("eligibility_judged_count", 0),
        group.get("eligible_favorable_rate", ""),
        group.get("avg_abs_bid_rate_error", ""),
        group.get("latest_result_time", ""),
    ]


def _write_breakdown_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    breakdown_key: str,
    group_label: str,
) -> None:
    """Write a long-format per-operator breakdown CSV (one row per group).

    ``breakdown_key`` is ``"by_category"`` or ``"by_budget_band"``;
    ``group_label`` is the leading column name (``category`` / ``budget_band``).
    Each operator contributes one row per populated group, carrying sample size
    (``settled_count``) and freshness (``latest_result_time``).
    """
    headers = ["slug", "business_type", group_label, *_BREAKDOWN_CSV_COMMON_TAIL]
    lines = [",".join(headers)]
    for row in rows:
        breakdown = row.get("breakdown") or {}
        groups = breakdown.get(breakdown_key) or []
        business_type = row["operator"].get("business_type", "")
        for group in groups:
            values = [
                row["slug"],
                business_type,
                group.get(group_label, ""),
                *_breakdown_group_values(group),
            ]
            lines.append(
                ",".join("" if value is None else str(value) for value in values)
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) / started_at
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.persist:
        Base.metadata.create_all(bind=engine)

    only_slugs: set[str] | None = None
    if args.operators.strip():
        only_slugs = {value.strip() for value in args.operators.split(",") if value.strip()}

    service = PaperBiddingBacktestService()
    comparison_rows: list[dict[str, Any]] = []

    db = SessionLocal()
    try:
        operators = _load_synthetic_operators(db, only_slugs)
        if not operators:
            sys.stderr.write(
                "No synthetic operators found. Run 'python scripts/seed_synthetic_operators.py' first.\n"
            )
            return 2

        for operator in operators:
            slug = operator["slug"]
            try:
                result = service.run_historical_backtest(
                    db,
                    operator_id=operator["user_id"],
                    category=args.category.strip() or None,
                    start_at=parse_datetime(args.start_date),
                    end_at=parse_datetime(args.end_date, end_of_day=True),
                    limit=args.limit,
                    scenario=args.scenario,
                    strategy_version=f"{args.strategy_version}:{slug}",
                    model_version=args.model_version,
                    cutoff_hours_before_deadline=args.cutoff_hours_before_deadline,
                    history_limit=args.history_limit,
                    settle_actions=parse_actions(args.settle_actions),
                    persist=bool(args.persist),
                )
            except Exception as exc:  # backtest of one operator must not abort the rest
                error_payload = {
                    "slug": slug,
                    "operator": operator,
                    "error": str(exc),
                }
                (out_dir / f"{slug}.error.json").write_text(
                    json.dumps(error_payload, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
                comparison_rows.append(
                    {
                        "slug": slug,
                        "operator": operator,
                        "summary": {
                            "candidate_count": 0,
                            "paper_bid_count": 0,
                            "settled_count": 0,
                            "skipped_by_strategy_count": 0,
                            "would_have_won_price_only_count": 0,
                            "est_price_close_rate_on_settled": None,
                            "est_price_close_rate_on_candidates": None,
                            "would_have_won_final_eligible_favorable_count": 0,
                            "would_have_won_final_unknown_count": 0,
                            "eligibility_judged_count": 0,
                            "eligible_favorable_rate": None,
                            "bid_submission_rate": None,
                            "average_absolute_bid_rate_error": None,
                            "average_absolute_amount_error_rate": None,
                            "within_0_1pct_count": 0,
                            "within_0_3pct_count": 0,
                            "within_1pct_count": 0,
                            "price_close_count": 0,
                            "price_competitive_count": 0,
                            "action_counts": {},
                        },
                        "breakdown": {"by_category": [], "by_budget_band": []},
                        "error": str(exc),
                    }
                )
                continue

            summary = _condense_summary(result.get("summary", {}))
            # Field-level breakdown (category / budget band) reusing the same
            # honest aggregation the web Lab uses -- propagates the per-field view
            # all the way to the CLI artifacts (sibling CSVs below).
            breakdown = compute_breakdown(result.get("settlements") or [])
            comparison_rows.append(
                {
                    "slug": slug,
                    "operator": operator,
                    "run_id": result.get("run_id"),
                    "summary": summary,
                    "breakdown": breakdown,
                }
            )

            (out_dir / f"{slug}.json").write_text(
                json.dumps(
                    {
                        "operator": operator,
                        "request": result.get("request"),
                        "run_id": result.get("run_id"),
                        "summary": result.get("summary"),
                        "breakdown": breakdown,
                        "settlement_count": len(result.get("settlements") or []),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )
    finally:
        db.close()

    # Sort by est_price_close_rate_on_settled descending (None last) for a quick
    # visual scan. This is a PRICE-ONLY estimate, NOT an actual award.
    def _sort_key(row: dict[str, Any]) -> tuple[int, float]:
        rate = row["summary"].get("est_price_close_rate_on_settled")
        if rate is None:
            return (1, 0.0)
        return (0, -float(rate))

    comparison_rows.sort(key=_sort_key)

    comparison_path = out_dir / "comparison.json"
    comparison_path.write_text(
        json.dumps(
            {
                "started_at": started_at,
                "request": {
                    "category": args.category or None,
                    "start_date": args.start_date,
                    "end_date": args.end_date,
                    "limit": args.limit,
                    "scenario": args.scenario,
                    "settle_actions": parse_actions(args.settle_actions),
                    "persist": bool(args.persist),
                    "filter_slugs": sorted(only_slugs) if only_slugs else None,
                },
                "rows": comparison_rows,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    csv_path = out_dir / "comparison.csv"
    _write_comparison_csv(csv_path, comparison_rows)

    # Sibling long-format CSVs carry the field-level breakdown (one row per
    # operator x group) with sample size + freshness, so thin/stale fields are
    # visible without opening the JSON.
    category_csv_path = out_dir / "comparison_by_category.csv"
    _write_breakdown_csv(
        category_csv_path,
        comparison_rows,
        breakdown_key="by_category",
        group_label="category",
    )
    budget_band_csv_path = out_dir / "comparison_by_budget_band.csv"
    _write_breakdown_csv(
        budget_band_csv_path,
        comparison_rows,
        breakdown_key="by_budget_band",
        group_label="budget_band",
    )

    print(
        json.dumps(
            {
                "status": "completed",
                "operator_count": len(comparison_rows),
                "out_dir": str(out_dir),
                "comparison_json": str(comparison_path),
                "comparison_csv": str(csv_path),
                "comparison_by_category_csv": str(category_csv_path),
                "comparison_by_budget_band_csv": str(budget_band_csv_path),
                "top": [
                    {
                        "slug": row["slug"],
                        # PRICE-ONLY estimate (NOT an actual award).
                        "est_price_close_rate_on_settled": row["summary"].get(
                            "est_price_close_rate_on_settled"
                        ),
                        # Eligibility-gate estimate (unknown excluded).
                        "eligible_favorable_rate": row["summary"].get(
                            "eligible_favorable_rate"
                        ),
                        "settled_count": row["summary"].get("settled_count"),
                        "candidate_count": row["summary"].get("candidate_count"),
                    }
                    for row in comparison_rows[:5]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
