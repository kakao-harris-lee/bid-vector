#!/usr/bin/env python3
# ruff: noqa: E402
"""Backtest latest awarded notices per business group as leakage-free holdouts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ai.business_group import resolve_business_group
from app.ai.price_prediction import predict_price
from app.ai.predictors.historical import resolve_procurement_rate_band
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models.models import HistoricalData, Project, TenderResult
from app.services.prediction_dataset import PredictionDatasetService

DEFAULT_GROUPS = ("construction", "service", "goods")
DEFAULT_THRESHOLDS = (0.001, 0.003, 0.005, 0.01)
SERVICE_LIKE_CATEGORIES = {"technical-service", "general-service", "software"}


@dataclass(frozen=True)
class HoldoutTarget:
    """One latest award target selected for a business group."""

    group: str
    group_source: str
    result: TenderResult
    project: Project
    historical: HistoricalData
    event_at: datetime
    available_at: datetime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Select the latest awarded notice per business group, hide its award values, "
            "and compare the current predictor output against the actual winning amount."
        )
    )
    parser.add_argument(
        "--groups",
        default=",".join(DEFAULT_GROUPS),
        help="Comma-separated business groups to evaluate. Default: construction,service,goods.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=1000,
        help="Maximum group-scoped settled history rows supplied to each prediction.",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=10000,
        help="Maximum recent TenderResult rows scanned while selecting latest group targets.",
    )
    parser.add_argument(
        "--targets-per-group",
        type=int,
        default=1,
        help="Number of latest awarded targets selected per group when --notice-numbers is omitted.",
    )
    parser.add_argument(
        "--max-targets",
        type=int,
        default=0,
        help="Optional cap on total selected targets after sorting. 0 means no extra cap.",
    )
    parser.add_argument(
        "--worst-limit",
        type=int,
        default=10,
        help="Number of worst recommended-error targets included in the report summary.",
    )
    parser.add_argument(
        "--print-target-limit",
        type=int,
        default=20,
        help="Number of short target rows printed to stdout. Full rows are always written to --out.",
    )
    parser.add_argument(
        "--notice-numbers",
        default="",
        help=(
            "Optional comma-separated notice numbers to replay as fixed holdouts. "
            "When omitted, the script selects the latest awarded notice per group."
        ),
    )
    parser.add_argument(
        "--timestamp-grace-hours",
        type=float,
        default=9.0,
        help=(
            "Grace window for selecting source timestamps that appear ahead of utc_now. "
            "The default handles KST/UTC mixed source timestamps in local DB snapshots."
        ),
    )
    parser.add_argument(
        "--thresholds",
        default=",".join(str(value) for value in DEFAULT_THRESHOLDS),
        help="Comma-separated absolute amount-error thresholds as ratios, e.g. 0.001,0.003.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional JSON output path. Defaults to models/reports/latest-award-holdout-<timestamp>.json.",
    )
    return parser


def parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def parse_thresholds(value: str) -> tuple[float, ...]:
    thresholds: list[float] = []
    for item in parse_csv(value):
        try:
            threshold = float(item)
        except ValueError:
            continue
        if threshold > 0:
            thresholds.append(threshold)
    return tuple(thresholds) or DEFAULT_THRESHOLDS


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def display_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def amount_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_rate(value: Any) -> float | None:
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    if rate > 1.5:
        rate /= 100.0
    return rate


def normalize_category(service: PredictionDatasetService, value: str | None) -> str | None:
    if not value:
        return None
    return service._normalize_category(value)


def derive_group(
    service: PredictionDatasetService,
    project: Project | None,
    historical: HistoricalData | None,
    groups: set[str],
) -> tuple[str | None, str]:
    business_type_code = getattr(project, "business_type_code", None) if project else None
    code_group = resolve_business_group(business_type_code)
    if code_group in groups:
        return code_group, f"business_type_code:{business_type_code}"

    project_category = normalize_category(service, getattr(project, "category", None) if project else None)
    if project_category in groups:
        return project_category, "project.category"

    historical_category = normalize_category(service, getattr(historical, "category", None) if historical else None)
    if historical_category in groups:
        return historical_category, "historical.category"

    if "service" in groups and (project_category in SERVICE_LIKE_CATEGORIES or historical_category in SERVICE_LIKE_CATEGORIES):
        return "service", "service-like category"

    return None, "unmapped"


def base_amount(project: Project, historical: HistoricalData) -> float | None:
    return (
        amount_float(historical.base_amount)
        or amount_float(project.budget_amount)
        or amount_float(project.estimated_price)
    )


def event_time(result: TenderResult, historical: HistoricalData | None) -> datetime | None:
    for dt in (
        aware(result.announced_at),
        aware(historical.opened_at if historical else None),
        aware(result.created_at),
    ):
        if dt is not None:
            return dt
    return None


def available_time(result: TenderResult, historical: HistoricalData | None) -> datetime | None:
    return aware(result.created_at) or event_time(result, historical)


def latest_history_by_project(db, project_ids: list[int]) -> dict[int, HistoricalData]:
    histories: dict[int, HistoricalData] = {}
    if not project_ids:
        return histories

    rows = (
        db.query(HistoricalData)
        .filter(HistoricalData.project_id.in_(project_ids))
        .order_by(HistoricalData.project_id.asc(), HistoricalData.id.desc())
        .all()
    )
    for row in rows:
        histories.setdefault(row.project_id, row)
    return histories


def amount_bucket(amount: float | None) -> str:
    """Bucket base amounts for report breakdowns."""
    if amount is None or amount <= 0:
        return "unknown"
    if amount < 10_000_000:
        return "<10m"
    if amount < 30_000_000:
        return "10m-30m"
    if amount < 100_000_000:
        return "30m-100m"
    if amount < 300_000_000:
        return "100m-300m"
    if amount < 1_000_000_000:
        return "300m-1b"
    return "1b+"


def select_latest_targets(
    db,
    *,
    service: PredictionDatasetService,
    groups: tuple[str, ...],
    now: datetime,
    timestamp_grace_hours: float,
    candidate_limit: int,
    targets_per_group: int,
    max_targets: int,
) -> list[HoldoutTarget]:
    group_set = set(groups)
    group_target_limit = max(1, int(targets_per_group or 1))
    selection_cutoff = now + timedelta(hours=max(0.0, float(timestamp_grace_hours or 0.0)))
    results = (
        db.query(TenderResult)
        .options(joinedload(TenderResult.project))
        .filter(
            TenderResult.project_id.isnot(None),
            or_(TenderResult.winning_amount > 0, TenderResult.winning_rate > 0),
        )
        .order_by(
            TenderResult.announced_at.desc().nullslast(),
            TenderResult.created_at.desc().nullslast(),
            TenderResult.id.desc(),
        )
        .limit(max(1, int(candidate_limit or 1)))
        .all()
    )
    histories = latest_history_by_project(db, [row.project_id for row in results if row.project_id])

    candidates: list[HoldoutTarget] = []
    seen_project_ids: set[int] = set()
    for result in results:
        project = result.project
        historical = histories.get(result.project_id)
        if project is None or historical is None:
            continue
        if result.project_id in seen_project_ids:
            continue
        seen_project_ids.add(result.project_id)
        budget = base_amount(project, historical)
        actual_amount = amount_float(result.winning_amount)
        if not budget or budget <= 0 or not actual_amount or actual_amount <= 0:
            continue

        group, group_source = derive_group(service, project, historical, group_set)
        if group not in group_set:
            continue

        event_at = event_time(result, historical)
        available_at = available_time(result, historical)
        if event_at is None or available_at is None:
            continue
        if event_at > selection_cutoff or available_at > selection_cutoff:
            continue

        candidates.append(
            HoldoutTarget(
                group=group,
                group_source=group_source,
                result=result,
                project=project,
                historical=historical,
                event_at=event_at,
                available_at=available_at,
            )
        )

    candidates.sort(key=lambda item: (item.event_at, item.available_at, item.result.id or 0), reverse=True)
    selected: dict[str, list[HoldoutTarget]] = {group: [] for group in groups}
    for candidate in candidates:
        group_targets = selected.setdefault(candidate.group, [])
        if len(group_targets) < group_target_limit:
            group_targets.append(candidate)

    ordered_targets = [
        target
        for group in groups
        for target in selected.get(group, [])
    ]
    if max_targets and max_targets > 0:
        return ordered_targets[:max_targets]
    return ordered_targets


def select_targets_by_notice(
    db,
    *,
    service: PredictionDatasetService,
    notice_numbers: tuple[str, ...],
    groups: tuple[str, ...],
    now: datetime,
    timestamp_grace_hours: float,
) -> list[HoldoutTarget]:
    group_set = set(groups)
    selection_cutoff = now + timedelta(hours=max(0.0, float(timestamp_grace_hours or 0.0)))
    histories_by_notice: dict[str, HistoricalData] = {}
    rows = (
        db.query(HistoricalData)
        .filter(HistoricalData.notice_number.in_(notice_numbers))
        .order_by(HistoricalData.notice_number.asc(), HistoricalData.id.desc())
        .all()
    )
    for row in rows:
        histories_by_notice.setdefault(row.notice_number, row)

    project_ids = [
        history.project_id
        for history in histories_by_notice.values()
        if history.project_id is not None
    ]
    projects = {
        project.id: project
        for project in db.query(Project).filter(Project.id.in_(project_ids)).all()
    }
    results_by_project: dict[int, TenderResult] = {}
    if project_ids:
        result_rows = (
            db.query(TenderResult)
            .filter(
                TenderResult.project_id.in_(project_ids),
                or_(TenderResult.winning_amount > 0, TenderResult.winning_rate > 0),
            )
            .order_by(TenderResult.project_id.asc(), TenderResult.created_at.desc().nullslast(), TenderResult.id.desc())
            .all()
        )
        for row in result_rows:
            results_by_project.setdefault(row.project_id, row)

    targets: list[HoldoutTarget] = []
    for notice_number in notice_numbers:
        historical = histories_by_notice.get(notice_number)
        if historical is None or historical.project_id is None:
            continue
        project = projects.get(historical.project_id)
        result = results_by_project.get(historical.project_id)
        if project is None or result is None:
            continue
        budget = base_amount(project, historical)
        actual_amount = amount_float(result.winning_amount)
        if not budget or budget <= 0 or not actual_amount or actual_amount <= 0:
            continue

        group, group_source = derive_group(service, project, historical, group_set)
        if group not in group_set:
            continue

        event_at = event_time(result, historical)
        available_at = available_time(result, historical)
        if event_at is None or available_at is None:
            continue
        if event_at > selection_cutoff or available_at > selection_cutoff:
            continue

        targets.append(
            HoldoutTarget(
                group=group,
                group_source=group_source,
                result=result,
                project=project,
                historical=historical,
                event_at=event_at,
                available_at=available_at,
            )
        )
    return targets


def candidate_rows(raw_candidates: Any) -> list[dict[str, Any]]:
    if isinstance(raw_candidates, dict):
        return [
            {"label": key, **value}
            for key, value in raw_candidates.items()
            if isinstance(value, dict)
        ]
    if isinstance(raw_candidates, list):
        return [item for item in raw_candidates if isinstance(item, dict)]
    return []


def scenario_metrics(
    *,
    label: str,
    price: float | None,
    rate: float | None,
    budget: float,
    actual_amount: float,
    actual_rate: float,
) -> dict[str, Any] | None:
    if price is None and rate is not None:
        price = budget * rate
    if rate is None and price is not None and budget > 0:
        rate = price / budget
    if price is None or rate is None:
        return None
    amount_error = price - actual_amount
    return {
        "scenario": label,
        "price": round(price, 2),
        "rate": round(rate, 6),
        "amount_error": round(amount_error, 2),
        "absolute_amount_error": round(abs(amount_error), 2),
        "absolute_amount_error_pct": round(abs(amount_error) / actual_amount, 6),
        "rate_error_bp": round((rate - actual_rate) * 10000, 2),
    }


def evaluate_target(
    db,
    *,
    service: PredictionDatasetService,
    target: HoldoutTarget,
    history_limit: int,
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    result = target.result
    project = target.project
    historical = target.historical
    budget = base_amount(project, historical)
    actual_amount = amount_float(result.winning_amount)
    if not budget or not actual_amount:
        raise ValueError(f"Target {historical.notice_number} has no usable budget or winning amount.")

    actual_rate = (
        normalize_rate(result.winning_rate)
        or normalize_rate(historical.bid_rate)
        or (actual_amount / budget)
    )
    amount_derived_rate = actual_amount / budget
    data_quality_flags = resolve_data_quality_flags(
        group=target.group,
        actual_rate=actual_rate,
        amount_derived_rate=amount_derived_rate,
    )
    # The target's award row is unavailable at inference time. Use the stricter of
    # source event and persisted availability to avoid leaking rows with mixed TZs.
    as_of = min(target.event_at, target.available_at) - timedelta(seconds=1)
    history = service.load_historical_series(
        db,
        category=target.group,
        agency_name=None,
        limit=max(1, int(history_limit or 1)),
        explicit_bid_rate_only=True,
        as_of=as_of,
    )
    filtered_history = [
        point
        for point in history
        if point.get("notice_number") != historical.notice_number
        and point.get("project_id") != historical.project_id
    ]
    description = "\n".join(
        part
        for part in (
            project.title or getattr(historical, "title", "") or "",
            project.description or getattr(historical, "description", "") or "",
        )
        if part
    )
    category = (
        normalize_category(service, project.category)
        or normalize_category(service, historical.category)
        or target.group
    )
    prediction = predict_price(
        budget=budget,
        category=category,
        description=description,
        historical_records=filtered_history,
        agency_name=project.issuing_agency or project.demand_agency,
        business_type_code=project.business_type_code,
        business_group=target.group,
    )

    recommended = scenario_metrics(
        label="recommended",
        price=amount_float(prediction.get("predicted_price")),
        rate=normalize_rate(prediction.get("predicted_bid_rate") or prediction.get("bid_rate")),
        budget=budget,
        actual_amount=actual_amount,
        actual_rate=actual_rate,
    )
    scenarios: list[dict[str, Any]] = [recommended] if recommended else []
    for candidate in candidate_rows(prediction.get("bid_rate_candidates")):
        scenario = scenario_metrics(
            label=str(candidate.get("label") or candidate.get("scenario") or "candidate"),
            price=amount_float(candidate.get("predicted_price") or candidate.get("price")),
            rate=normalize_rate(candidate.get("bid_rate") or candidate.get("predicted_bid_rate")),
            budget=budget,
            actual_amount=actual_amount,
            actual_rate=actual_rate,
        )
        if scenario is not None:
            scenarios.append(scenario)

    closest = (
        min(scenarios, key=lambda item: item["absolute_amount_error_pct"])
        if scenarios
        else None
    )
    return {
        "group": target.group,
        "group_source": target.group_source,
        "notice_number": historical.notice_number or project.notice_number,
        "project_id": historical.project_id,
        "title": project.title or getattr(historical, "title", None),
        "category": category,
        "amount_bucket": amount_bucket(budget),
        "procurement_rate_band": prediction.get("procurement_rate_band")
        or resolve_procurement_rate_band(category=category, description=description),
        "business_type_code": project.business_type_code,
        "event_at": display_dt(target.event_at),
        "available_at": display_dt(target.available_at),
        "training_cutoff_at": display_dt(as_of),
        "history_count": len(filtered_history),
        "budget": round(budget, 2),
        "actual": {
            "winning_amount": round(actual_amount, 2),
            "winning_rate": round(actual_rate, 6),
            "amount_derived_rate": round(amount_derived_rate, 6),
        },
        "data_quality_flags": data_quality_flags,
        "prediction_metadata": {
            "predictor_name": prediction.get("predictor_name"),
            "predictor_family": prediction.get("predictor_family"),
            "selector_name": prediction.get("selector_name"),
            "selection_reason": prediction.get("selection_reason"),
            "fallback_reason": prediction.get("fallback_reason"),
            "confidence_score": prediction.get("confidence_score"),
            "guardrail_applied": prediction.get("guardrail_applied"),
            "floor_bid_rate": prediction.get("floor_bid_rate"),
            "safe_floor_bid_rate": prediction.get("safe_floor_bid_rate"),
            "ceiling_bid_rate": prediction.get("ceiling_bid_rate"),
            "bid_price_granularity": prediction.get("bid_price_granularity"),
            "price_granularity_applied": prediction.get("price_granularity_applied"),
            "procurement_rate_band": prediction.get("procurement_rate_band"),
            "high_rate_tail_adjustment": prediction.get("high_rate_tail_adjustment"),
        },
        "recommended": recommended,
        "closest": closest,
        "scenarios": scenarios,
        "within_thresholds": {
            format_threshold(threshold): bool(
                recommended and recommended["absolute_amount_error_pct"] <= threshold
            )
            for threshold in thresholds
        },
        "closest_within_thresholds": {
            format_threshold(threshold): bool(
                closest and closest["absolute_amount_error_pct"] <= threshold
            )
            for threshold in thresholds
        },
    }


def format_threshold(threshold: float) -> str:
    return f"{threshold * 100:.1f}%"


def aggregate(rows: list[dict[str, Any]], thresholds: tuple[float, ...]) -> dict[str, Any]:
    recommended = [row["recommended"] for row in rows if row.get("recommended")]
    closest = [row["closest"] for row in rows if row.get("closest")]
    return {
        "target_count": len(rows),
        "recommended": aggregate_scenarios(recommended, thresholds),
        "closest": aggregate_scenarios(closest, thresholds),
    }


def aggregate_by_key(
    rows: list[dict[str, Any]],
    *,
    key: str,
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        bucket_key = str(row.get(key) or "unknown")
        buckets.setdefault(bucket_key, []).append(row)
    return {
        bucket_key: aggregate(bucket_rows, thresholds)
        for bucket_key, bucket_rows in sorted(
            buckets.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    }


def aggregate_by_flag(rows: list[dict[str, Any]], thresholds: tuple[float, ...]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        flags = row.get("data_quality_flags") or ["clean"]
        for flag in flags:
            buckets.setdefault(str(flag), []).append(row)
    return {
        bucket_key: aggregate(bucket_rows, thresholds)
        for bucket_key, bucket_rows in sorted(
            buckets.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    }


def resolve_data_quality_flags(*, group: str, actual_rate: float, amount_derived_rate: float) -> list[str]:
    """Flag rows where the stored amount/rate relation looks unsuitable for price backtests."""
    flags: list[str] = []
    if actual_rate < 0.75:
        flags.append("low_actual_rate")
    if str(group or "").lower() == "construction" and actual_rate < 0.85:
        flags.append("construction_low_rate_review")
    if amount_derived_rate <= 0:
        flags.append("missing_amount_derived_rate")
    elif abs(actual_rate - amount_derived_rate) > 0.02:
        flags.append("amount_rate_mismatch")
    return flags


def worst_targets(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ranked = sorted(
        (row for row in rows if row.get("recommended")),
        key=lambda row: row["recommended"]["absolute_amount_error_pct"],
        reverse=True,
    )
    return [report_short_target(row) for row in ranked[: max(0, int(limit or 0))]]


def aggregate_scenarios(rows: list[dict[str, Any]], thresholds: tuple[float, ...]) -> dict[str, Any]:
    if not rows:
        return {
            "sample_count": 0,
            "mean_absolute_amount_error_pct": None,
            "mean_absolute_bid_rate_error_bp": None,
            "within_counts": {format_threshold(threshold): 0 for threshold in thresholds},
        }
    return {
        "sample_count": len(rows),
        "mean_absolute_amount_error_pct": round(
            mean(row["absolute_amount_error_pct"] for row in rows),
            6,
        ),
        "mean_absolute_bid_rate_error_bp": round(
            mean(abs(row["rate_error_bp"]) for row in rows),
            2,
        ),
        "within_counts": {
            format_threshold(threshold): sum(
                1 for row in rows if row["absolute_amount_error_pct"] <= threshold
            )
            for threshold in thresholds
        },
    }


def report_short_target(row: dict[str, Any]) -> dict[str, Any]:
    recommended = row.get("recommended") or {}
    closest = row.get("closest") or {}
    actual = row.get("actual") or {}
    return {
        "group": row.get("group"),
        "amount_bucket": row.get("amount_bucket"),
        "procurement_rate_band": row.get("procurement_rate_band"),
        "data_quality_flags": row.get("data_quality_flags") or [],
        "notice_number": row.get("notice_number"),
        "event_at": row.get("event_at"),
        "history_count": row.get("history_count"),
        "actual_amount": actual.get("winning_amount"),
        "actual_rate": actual.get("winning_rate"),
        "recommended_price": recommended.get("price"),
        "recommended_rate": recommended.get("rate"),
        "recommended_error_pct": recommended.get("absolute_amount_error_pct"),
        "recommended_rate_error_bp": recommended.get("rate_error_bp"),
        "closest_scenario": closest.get("scenario"),
        "closest_error_pct": closest.get("absolute_amount_error_pct"),
    }


def main() -> int:
    args = build_parser().parse_args()
    groups = parse_csv(args.groups) or DEFAULT_GROUPS
    thresholds = parse_thresholds(args.thresholds)
    generated_at = datetime.now(UTC)
    output_path = Path(
        args.out
        or f"models/reports/latest-award-holdout-{generated_at.strftime('%Y%m%d-%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    service = PredictionDatasetService()
    now = utc_now()
    notice_numbers = parse_csv(args.notice_numbers)
    db = SessionLocal()
    try:
        if notice_numbers:
            targets = select_targets_by_notice(
                db,
                service=service,
                notice_numbers=notice_numbers,
                groups=groups,
                now=now,
                timestamp_grace_hours=args.timestamp_grace_hours,
            )
        else:
            targets = select_latest_targets(
                db,
                service=service,
                groups=groups,
                now=now,
                timestamp_grace_hours=args.timestamp_grace_hours,
                candidate_limit=args.candidate_limit,
                targets_per_group=args.targets_per_group,
                max_targets=args.max_targets,
            )
        rows = [
            evaluate_target(
                db,
                service=service,
                target=target,
                history_limit=args.history_limit,
                thresholds=thresholds,
            )
            for target in targets
        ]
    finally:
        db.close()

    report = {
        "generated_at": generated_at.isoformat(),
        "method": "latest_awarded_notices_per_business_group_holdout",
        "settings": {
            "groups": list(groups),
            "history_limit": args.history_limit,
            "candidate_limit": args.candidate_limit,
            "targets_per_group": args.targets_per_group,
            "max_targets": args.max_targets,
            "notice_numbers": list(notice_numbers),
            "timestamp_grace_hours": args.timestamp_grace_hours,
            "thresholds": list(thresholds),
            "utc_now": display_dt(now),
        },
        "selection_notes": [
            "Select the latest awarded TenderResult targets per business group using announced/opened event time.",
            "Target project and notice are excluded from prediction history.",
            "Training cutoff is one second before the stricter of source event time and result availability time.",
            "Only explicit settled bid-rate evidence is used for historical records.",
        ],
        "summary": aggregate(rows, thresholds),
        "breakdowns": {
            "by_group": aggregate_by_key(rows, key="group", thresholds=thresholds),
            "by_amount_bucket": aggregate_by_key(rows, key="amount_bucket", thresholds=thresholds),
            "by_procurement_rate_band": aggregate_by_key(rows, key="procurement_rate_band", thresholds=thresholds),
            "by_data_quality_flag": aggregate_by_flag(rows, thresholds),
        },
        "worst_recommended_targets": worst_targets(rows, limit=args.worst_limit),
        "targets": rows,
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "completed",
                "out": str(output_path),
                "summary": report["summary"],
                "breakdowns": report["breakdowns"],
                "worst_recommended_targets": report["worst_recommended_targets"],
                "printed_target_count": min(len(rows), max(0, int(args.print_target_limit or 0))),
                "target_count": len(rows),
                "targets": [
                    report_short_target(row)
                    for row in rows[: max(0, int(args.print_target_limit or 0))]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
