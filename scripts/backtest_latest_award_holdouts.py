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
from collections.abc import Collection
from typing import Any, Callable

from sqlalchemy.orm import joinedload

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ai.business_group import resolve_business_group
from app.ai.holdout_grouping import (
    AGENCY_UNKNOWN_KEY,
    DEFAULT_MIN_AGENCY_SAMPLES,
    normalize_agency_key,
    resolve_agency_group,
    resolve_agency_match_keys,
)
from app.ai.holdout_quality import assess_row_quality
from app.ai.holdout_reporting import (
    build_agency_axis_report,
    build_floor_applicability_report,
    build_quality_flag_report,
)
from app.ai.price_prediction import predict_price
from app.ai.predictors.historical import resolve_procurement_rate_band
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.domain.aggregates import error_rate
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.models.models import HistoricalData, Project, TenderResult
from app.services.base_amount_basis import (
    ALL_BASES,
    BASIS_CLEAN,
    classify_base_basis,
)
from app.services.bid_base import (
    build_prediction_text,
    resolve_notice_legal_floor_bid_rate,
    resolve_notice_legal_floor_inputs,
)
from app.services.prediction_dataset import PredictionDatasetService
from app.services.query_predicates import settled_any_signal
from app.utils.sequence_coercion import coerce_numeric_list

DEFAULT_GROUPS = ("construction", "service", "goods")
DEFAULT_THRESHOLDS = (0.001, 0.003, 0.005, 0.01)
SERVICE_LIKE_CATEGORIES = {"technical-service", "general-service", "software"}

# 대상 선택 축(split axis). 예측 입력(business_group)은 어느 축이든 그대로 유지되고,
# 이 값은 "무엇을 기준으로 최신 N건을 고를지"만 바꾼다.
GROUP_BY_BUSINESS_GROUP = "business_group"
GROUP_BY_AGENCY = "agency"
GROUP_BY_CHOICES = (GROUP_BY_BUSINESS_GROUP, GROUP_BY_AGENCY)

# 기관 축 실행의 고정 리포트 경로(로드맵 6번: 개선 전후를 같은 경로로 비교).
# ``--out`` 이 없을 때만 쓰인다.
AGENCY_AXIS_FIXED_OUT = "models/reports/latest-award-holdout-agency.json"


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
    # 기관 축 분할/리포트 전용 필드. ``group`` 과 달리 예측 입력에는 쓰이지 않는다.
    # 기본값은 ``_unknown`` — 기관 정보 없이 조립된 타깃이 조용히 사라지지 않고
    # 명시적 미상 버킷으로 모인다(:func:`build_target` 은 항상 실제 값을 채운다).
    agency_group: str = AGENCY_UNKNOWN_KEY
    agency_display: str = AGENCY_UNKNOWN_KEY
    # 이력 제외(--exclude-agency-history)용 동일-기관 키 집합. 분할 키 하나가 아니라
    # 집합인 이유는 ``resolve_agency_match_keys`` docstring 참조(적재 규칙 비대칭).
    agency_match_keys: frozenset[str] = frozenset()


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
        "--group-by",
        choices=GROUP_BY_CHOICES,
        default=GROUP_BY_BUSINESS_GROUP,
        help=(
            "Split axis used to select the latest targets. 'business_group' keeps the "
            "existing 공사/용역/물품 split; 'agency' selects the latest targets per "
            "발주기관(없으면 수요기관). The agency breakdown is always reported."
        ),
    )
    parser.add_argument(
        "--min-agency-samples",
        type=int,
        default=DEFAULT_MIN_AGENCY_SAMPLES,
        help=(
            "Minimum targets an agency needs to keep its own report bucket. Agencies "
            "below it are folded into the explicit '_etc' bucket (never silently "
            "dropped — the folded agency/target counts are reported)."
        ),
    )
    parser.add_argument(
        "--exclude-agency-history",
        action="store_true",
        help=(
            "True group holdout: also drop the target agency's own rows from the "
            "prediction history, measuring generalization to an unseen agency. "
            "Default off (the existing time-based holdout is unchanged)."
        ),
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
        help=(
            "Number of latest awarded targets selected per split bucket (business "
            "group or agency, see --group-by) when --notice-numbers is omitted."
        ),
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
        help=(
            "Optional JSON output path. Defaults to "
            "models/reports/latest-award-holdout-<timestamp>.json, or the fixed "
            f"{AGENCY_AXIS_FIXED_OUT} when --group-by agency (before/after runs "
            "compare on one path)."
        ),
    )
    parser.add_argument(
        "--include-contaminated",
        action="store_true",
        help=(
            "Fold non-clean (derived/suspect base_amount) targets back into the "
            "summary/breakdown/worst aggregates. Default: clean-only aggregation "
            "(contaminated targets stay in the report but are excluded from stats)."
        ),
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
    """Raw pricing-base fallback: stored 기초금액, else the project budget field.

    Callers pick the estimate-aware base via ``resolve_pricing_base``; this stays
    the fallback tail used when neither a clean stored base nor a recovered
    estimate is available. ``Project``'s only base/budget field is ``budget_estimate``
    (추정가격). The old fallback referenced ``project.budget_amount`` AND
    ``project.estimated_price`` — NEITHER exists on ``Project`` (estimated_price lives
    on ``PaperBidSettlement``), so both raised ``AttributeError`` the moment
    ``historical.base_amount`` was falsy. That crash stayed latent because
    ``base_amount`` is truthy for almost every row, short-circuiting the fallback.
    """
    return amount_float(historical.base_amount) or amount_float(project.budget_estimate)


# Provenance labels for the base value fed into error measurement.
BASE_SOURCE_STORED = "base_amount"
BASE_SOURCE_ESTIMATED = "base_amount_estimated"
BASE_SOURCE_PROJECT = "project_budget"
BASE_SOURCE_NONE = "none"

# Map the shared primitive's provenance verdict onto this report's source labels.
# CLEAN_BASE and BASE_FALLBACK both return the stored ``base_amount`` (clean, or
# non-clean/unknown with no recovered estimate), so both surface as "base_amount".
_RELIABLE_SOURCE_LABELS = {
    ReliableBaseSource.CLEAN_BASE: BASE_SOURCE_STORED,
    ReliableBaseSource.RESERVE_ESTIMATE: BASE_SOURCE_ESTIMATED,
    ReliableBaseSource.BASE_FALLBACK: BASE_SOURCE_STORED,
}


def resolve_pricing_base(
    project: Project, historical: HistoricalData, basis: str
) -> tuple[float | None, str]:
    """Choose the pricing base used for error measurement + its provenance source.

    The basis RULE — a non-clean stored ``base_amount`` (derived 예정가-역산/VAT or
    suspect) is contaminated, so prefer the recovered ``base_amount_estimated``
    (복수예비가격 midpoint, a real 기초금액 추정) — is delegated to the shared
    ``get_reliable_base`` primitive (``app/domain/reliable_base.py``), the SAME rule
    the live-path ``resolve_notice_bid_base`` consumes. This holdout keeps a thin
    row-specific wrapper (not the DB-query live helper) because it must measure error
    against THIS target's award row and report the base's provenance, but the
    interpretation of the basis tag is now single-sourced (no reimplemented branch).

    ``base_amount_estimated`` is an 개찰-time reserve recovery of the 기초금액 that
    was polluted in storage — the same information the notice publishes at
    announcement, NOT future leakage, so it does not affect the ``as_of`` history
    boundary applied elsewhere. Clean rows keep the stored base; rows with neither a
    usable stored base nor an estimate fall back to project budget fields. Pure: no
    I/O, so the choice stays unit-testable as a value table.
    """
    reliable = get_reliable_base(
        base_amount=amount_float(historical.base_amount),
        basis=basis,
        base_amount_estimated=amount_float(
            getattr(historical, "base_amount_estimated", None)
        ),
    )
    if reliable.value is not None and reliable.value > 0:
        return float(reliable.value), _RELIABLE_SOURCE_LABELS[reliable.source]
    budget = base_amount(project, historical)
    return (budget, BASE_SOURCE_PROJECT) if budget else (None, BASE_SOURCE_NONE)


def resolve_base_basis(historical: HistoricalData, result: TenderResult) -> str:
    """Classify the stored base_amount provenance (reuses the canonical pure fn).

    Detects rows whose ``base_amount`` is derived from award values (예정가 역산 /
    VAT division) rather than a real 기초금액, so they can be excluded from the
    error aggregates. Uses the RAW stored ``historical.base_amount`` (not the
    budget fallback) with the row's normalized winning rate.
    """
    return classify_base_basis(
        getattr(historical, "base_amount", None),
        amount_float(result.winning_amount),
        normalize_rate(result.winning_rate),
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


def selection_key(target: HoldoutTarget, group_by: str) -> str:
    """Split-axis bucket key for target selection (NOT a prediction input)."""
    return target.agency_group if group_by == GROUP_BY_AGENCY else target.group


def build_target(
    *,
    group: str,
    group_source: str,
    result: TenderResult,
    project: Project,
    historical: HistoricalData,
    event_at: datetime,
    available_at: datetime,
) -> HoldoutTarget:
    """Assemble a target, deriving the agency axis from the notice's agencies.

    분할 키(``agency_group``)는 발주기관 우선 단일 값이지만, 이력 제외 키는 발주·수요에
    더해 이 공고의 ``historical.agency_name`` 까지 묶은 **집합**이다 — 이력 행의
    ``agency_name`` 은 수요기관 우선으로 적재돼 분할 키와 갈릴 수 있기 때문이다.
    """
    agency = resolve_agency_group(project.issuing_agency, project.demand_agency)
    return HoldoutTarget(
        group=group,
        group_source=group_source,
        result=result,
        project=project,
        historical=historical,
        event_at=event_at,
        available_at=available_at,
        agency_group=agency.key,
        agency_display=agency.display,
        agency_match_keys=resolve_agency_match_keys(
            project.issuing_agency,
            project.demand_agency,
            getattr(historical, "agency_name", None),
        ),
    )


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
    group_by: str = GROUP_BY_BUSINESS_GROUP,
) -> list[HoldoutTarget]:
    group_set = set(groups)
    group_target_limit = max(1, int(targets_per_group or 1))
    selection_cutoff = now + timedelta(hours=max(0.0, float(timestamp_grace_hours or 0.0)))
    results = (
        db.query(TenderResult)
        .options(joinedload(TenderResult.project))
        .filter(
            TenderResult.project_id.isnot(None),
            settled_any_signal(),
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
        basis = resolve_base_basis(historical, result)
        budget, _base_source = resolve_pricing_base(project, historical, basis)
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
            build_target(
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
    # Bucket order == report/selection order (dict preserves insertion). Pre-seeding
    # the business-group buckets keeps the historical --groups ordering byte-identical;
    # the agency axis has no fixed universe, so its buckets order by first appearance
    # (= most recent award first, since candidates are already sorted desc).
    selected: dict[str, list[HoldoutTarget]] = (
        {group: [] for group in groups} if group_by == GROUP_BY_BUSINESS_GROUP else {}
    )
    for candidate in candidates:
        group_targets = selected.setdefault(selection_key(candidate, group_by), [])
        if len(group_targets) < group_target_limit:
            group_targets.append(candidate)

    ordered_targets = [target for bucket in selected.values() for target in bucket]
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
                settled_any_signal(),
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
        basis = resolve_base_basis(historical, result)
        budget, _base_source = resolve_pricing_base(project, historical, basis)
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
            build_target(
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
        # actual_amount > 0 는 호출부(evaluate_target)에서 이미 보장 → error_rate None 분기 도달 불가.
        "absolute_amount_error_pct": round(error_rate(price, actual_amount) or 0.0, 6),
        "rate_error_bp": round((rate - actual_rate) * 10000, 2),
    }


def evaluate_target(
    db,
    *,
    service: PredictionDatasetService,
    target: HoldoutTarget,
    history_limit: int,
    thresholds: tuple[float, ...],
    exclude_agency_history: bool = False,
) -> dict[str, Any]:
    result = target.result
    project = target.project
    historical = target.historical
    basis = resolve_base_basis(historical, result)
    stored_base = amount_float(historical.base_amount)
    estimated_base = amount_float(getattr(historical, "base_amount_estimated", None))
    # For non-clean rows the stored base_amount is 예정가-역산/VAT contaminated, so
    # resolve_pricing_base swaps in the recovered 기초금액 estimate — this measures
    # error on a 기초금액-basis instead of the polluted 예정가-basis.
    budget, base_source = resolve_pricing_base(project, historical, basis)
    actual_amount = amount_float(result.winning_amount)
    if not budget or not actual_amount:
        raise ValueError(f"Target {historical.notice_number} has no usable budget or winning amount.")

    # 소스가 보고한 낙찰률과 금액-역산 낙찰률을 분리해 둔다. 보고값이 없어 역산으로
    # 대체된 행은 분모 정합/법정 하한 검사의 근거가 없으므로(basis 가 달라짐)
    # 품질 판정기가 그 상태를 알아야 오탐하지 않는다.
    reported_rate = normalize_rate(result.winning_rate) or normalize_rate(historical.bid_rate)
    actual_rate = reported_rate or (actual_amount / budget)
    amount_derived_rate = actual_amount / budget
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
    if exclude_agency_history:
        filtered_history = drop_agency_history(filtered_history, target.agency_match_keys)
    # Predictor input = title+description+requirements via the shared assembler, the
    # SAME text the live path feeds. The prior self-assembly dropped requirements and
    # joined with "\n"; unifying it removes the holdout-vs-live input asymmetry.
    description = build_prediction_text(project)
    category = (
        normalize_category(service, project.category)
        or normalize_category(service, historical.category)
        or target.group
    )
    # 공사 법정 낙찰하한 tier 입력(구간=추정가격, 기준일=공고 시점). #197 live 경로와
    # 동일 헬퍼로 공고 자신의 날짜를 넘겨, 홀드아웃 평가가 그 시점의 구/신율을
    # era-correct 하게 적용하도록 한다(2026-01-30 신율 소급 없음). estimation_amount는
    # 추정가격(budget_estimate)이라 pricing base(budget=기초금액)와 별개다.
    estimation_amount, reference_date = resolve_notice_legal_floor_inputs(project)
    published_floor_rate = resolve_notice_legal_floor_bid_rate(project)
    # 복수예비가격은 예정가를 독립적으로 재구성할 수 있는 유일한 저장 증거다. 품질
    # 판정기는 I/O 없이 돌아야 하므로 여기서 개수만 뽑아 주입한다(§4.7.3).
    reserve_price_count = len(coerce_numeric_list(historical.reserve_prices))
    # 분모/법정하한 품질 판정은 순수 모듈에 위임한다(읽기 전용 — 아래 predict_price
    # 입력에는 전혀 관여하지 않는다).
    quality = assess_row_quality(
        group=target.group,
        category=category,
        basis=basis,
        reported_rate=reported_rate,
        effective_rate=actual_rate,
        amount_derived_rate=amount_derived_rate,
        published_floor_rate=published_floor_rate,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
        # 하한 모델 적용 범위 판별 입력. 분할 키와 같은 원천(발주기관 우선, 부재 시
        # 수요기관)을 써서 리포트의 기관 표기와 판정 근거가 어긋나지 않게 한다.
        agency_name=target.agency_display,
        # 독립 예정가 증거의 유무. 예비가가 없으면 보고 낙찰률이 금액비 파생인지
        # 가릴 수 없어 하한 판정을 생략한다(순수 판정기는 개수만 받는다).
        reserve_price_count=reserve_price_count,
    )
    prediction = predict_price(
        budget=budget,
        category=category,
        description=description,
        historical_records=filtered_history,
        agency_name=project.issuing_agency or project.demand_agency,
        business_type_code=project.business_type_code,
        business_group=target.group,
        # 공고 자신의 published 낙찰하한율(award_floor_rate, #201). 공고 시점 공개값
        # (개찰 후 정보 아님)이라 leakage-safe 하며, guardrail_core 가 max() 로만
        # 폴드해 floor 를 올리기만 한다. 라이브가 강제하는 하한을 홀드아웃 정확도
        # 측정에도 태워, 재캘리브레이션 판단이 실 파이프라인과 같은 입력을 쓰게 한다.
        legal_floor_bid_rate=published_floor_rate,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
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
        "agency_group": target.agency_group,
        "agency_display": target.agency_display,
        "agency_history_excluded": bool(exclude_agency_history),
        "basis": basis,
        "base_source": base_source,
        "base_provenance": {
            "source": base_source,
            "stored_base_amount": round(stored_base, 2) if stored_base else None,
            "base_amount_estimated": round(estimated_base, 2) if estimated_base else None,
        },
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
            "reported_winning_rate": (
                round(reported_rate, 6) if reported_rate is not None else None
            ),
            "amount_derived_rate": round(amount_derived_rate, 6),
        },
        "data_quality_flags": list(quality.flags),
        "data_quality_details": quality.as_details(),
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


def partition_targets_by_basis(
    rows: list[dict[str, Any]], *, include_contaminated: bool
) -> tuple[list[dict[str, Any]], int]:
    """Split evaluated targets into the aggregation set + excluded-contaminated count.

    Summary / breakdown / worst aggregates use only ``basis == 'clean'`` targets by
    default so derived (예정가 역산 / VAT) or suspect base_amount rows cannot skew the
    error stats. The full target rows still appear in the report for transparency;
    pass ``include_contaminated`` to fold them back into the aggregates.
    """
    if include_contaminated:
        return list(rows), 0
    clean = [row for row in rows if row.get("basis") == BASIS_CLEAN]
    return clean, len(rows) - len(clean)


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


def bind_aggregate(thresholds: tuple[float, ...]) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
    """Bind the report's single-source ``aggregate`` for injection into section builders.

    ``app.ai.holdout_reporting`` owns the agency/flag section SHAPE but must not own
    the metric definitions — it receives this closure so both surfaces report the
    exact same sample_count / mean error / threshold counts (§4.7.3).
    """
    return lambda rows: aggregate(rows, thresholds)


def drop_agency_history(
    history: list[dict[str, Any]], agency_keys: Collection[str]
) -> list[dict[str, Any]]:
    """Remove the target agency's own rows from the prediction history (group holdout).

    Turns the time-based holdout into a true 기관 group holdout: the predictor never
    sees a past award attributed to ANY of the target notice's agency names, so the
    measured error reflects generalization to an unseen agency rather than
    agency-level memorization (로드맵 11번 고카디널리티 과적합 방지).

    WHY A KEY SET, NOT ONE KEY
    --------------------------
    History rows are filtered on ``HistoricalData.agency_name``, which collection
    persists as ``opening_demand_agency or demand_agency or issuing_agency``
    (``app/services/koneps/persistence.py``) — 수요기관 우선. The report's split key
    is 발주기관 우선. For a 발주≠수요 notice (조달청 경유 등) those disagree, so a
    single-key compare would let the target agency's own past awards through under
    the other name — silent leakage that makes "unseen agency" numbers optimistic.
    :func:`resolve_agency_match_keys` therefore builds the set from the notice's
    발주·수요 agencies AND its own stored ``agency_name``.

    RESIDUAL LIMIT (honest scope): a history row stored under an ``opening_demand_agency``
    that matches NONE of the target's three names still slips through. Closing that
    would require the history series to carry the row's own issuing/demand agencies,
    which lives in the shared live-path serializer — out of scope here.

    Rows whose agency is unknown are kept — they cannot be proven to belong to the
    target agency, and dropping them would silently shrink the training window.
    """
    keys = {key for key in agency_keys if key}
    if not keys:
        return list(history)
    return [
        point
        for point in history
        if normalize_agency_key(point.get("agency_name")) not in keys
    ]


def default_output_path(group_by: str, generated_at: datetime) -> str:
    """Report path used when ``--out`` is omitted.

    The agency axis writes to a FIXED path so a before/after comparison of the same
    change runs the same command twice and diffs one file (로드맵 6번). The business
    group axis keeps its historical timestamped path (existing runbooks pin it).
    """
    if group_by == GROUP_BY_AGENCY:
        return AGENCY_AXIS_FIXED_OUT
    return f"models/reports/latest-award-holdout-{generated_at.strftime('%Y%m%d-%H%M%S')}.json"


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
        "agency_group": row.get("agency_group"),
        "agency_display": row.get("agency_display"),
        "basis": row.get("basis"),
        "base_source": row.get("base_source"),
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
    output_path = Path(args.out or default_output_path(args.group_by, generated_at))
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
                group_by=args.group_by,
            )
        rows = [
            evaluate_target(
                db,
                service=service,
                target=target,
                history_limit=args.history_limit,
                thresholds=thresholds,
                exclude_agency_history=args.exclude_agency_history,
            )
            for target in targets
        ]
    finally:
        db.close()

    # Base-amount contamination guard: exclude derived/suspect base_amount targets
    # from the error aggregates by default so 예정가 역산/VAT rows cannot skew the
    # stats. The full target rows remain in the report for transparency.
    aggregation_rows, excluded_contaminated = partition_targets_by_basis(
        rows, include_contaminated=args.include_contaminated
    )
    summary = aggregate(aggregation_rows, thresholds)
    summary["aggregation_basis"] = (
        "all" if args.include_contaminated else "clean_only"
    )
    summary["excluded_contaminated"] = excluded_contaminated
    summary["basis_counts"] = {
        basis: sum(1 for row in rows if row.get("basis") == basis)
        for basis in ALL_BASES
    }

    aggregate_fn = bind_aggregate(thresholds)
    agency_axis = build_agency_axis_report(
        aggregation_rows,
        aggregate_fn=aggregate_fn,
        min_samples=args.min_agency_samples,
        worst_limit=args.worst_limit,
    )
    # 오차 지표는 clean-only 집계 스코프를 유지하되, 건수는 전체 평가 행도 함께
    # 넘겨 basis 오염이 "0건"으로 오독되지 않게 한다(집계 스코프는 clean 선필터라
    # base_basis_contaminated 가 구조적으로 항상 0).
    quality_report = build_quality_flag_report(
        aggregation_rows, aggregate_fn=aggregate_fn, evaluated_rows=rows
    )
    # 하한 판정이 생략된 규모(적용 범위 밖=비국가기관/판별 불가/별도 규정, 그리고
    # 보고율 basis 미검증). 이 건수가 없으면 "하회 0건"이 데이터 청결로 오독된다
    # (침묵 스킵 금지).
    floor_applicability = build_floor_applicability_report(
        aggregation_rows, evaluated_rows=rows
    )
    summary["agency_axis"] = agency_axis["summary"]
    summary.update(floor_applicability)
    summary["quality_flag_counts"] = quality_report["flag_counts"]
    summary["evaluated_quality_flag_counts"] = quality_report["evaluated_flag_counts"]
    summary["quality_flag_scope"] = quality_report["scope"]
    # 플래그 제외 전/후 오차 비교(로드맵 12번 clean/flag 분리 리포트). 겹치지 않는
    # 3분할이라 all == flag_free + flagged 로 검산 가능하다.
    summary["quality_flag_partition"] = quality_report["partition"]

    report = {
        "generated_at": generated_at.isoformat(),
        "method": f"latest_awarded_notices_per_{args.group_by}_holdout",
        "settings": {
            "groups": list(groups),
            "group_by": args.group_by,
            "min_agency_samples": args.min_agency_samples,
            "exclude_agency_history": args.exclude_agency_history,
            "history_limit": args.history_limit,
            "candidate_limit": args.candidate_limit,
            "targets_per_group": args.targets_per_group,
            "max_targets": args.max_targets,
            "notice_numbers": list(notice_numbers),
            "timestamp_grace_hours": args.timestamp_grace_hours,
            "thresholds": list(thresholds),
            "include_contaminated": args.include_contaminated,
            "utc_now": display_dt(now),
        },
        "selection_notes": [
            f"Select the latest awarded TenderResult targets per {args.group_by} using announced/opened event time.",
            "Target project and notice are excluded from prediction history.",
            "Training cutoff is one second before the stricter of source event time and result availability time.",
            "Only explicit settled bid-rate evidence is used for historical records.",
            "Summary/breakdown/worst aggregates exclude non-clean base_amount targets "
            "(derived 예정가 역산/VAT or suspect) by default; see summary.basis_counts and "
            "breakdowns.by_basis. Use --include-contaminated to fold them in.",
            "Agencies below --min-agency-samples are folded into the explicit '_etc' "
            "bucket (never silently dropped); see summary.agency_axis for the folded counts.",
            "below_legal_floor / amount_rate_mismatch are only evaluated when the source "
            "reported a winning_rate — the amount-derived rate is 기초금액-basis and would "
            "false-positive against the 예정가-basis legal floor.",
            "below_legal_floor is skipped when the floor model does not apply to the "
            "issuing agency (산학협력단/협동조합 등 non-state = not_applicable), the "
            "agency type cannot be told from its name (대학교 등 = uncertain), or the "
            "agency follows a separate 행정규칙 instead of 국가계약 적격심사 (산림청 "
            "계열 = separate_regime); see summary.floor_applicability_counts and "
            "targets[].data_quality_details.floor_applicability. A published "
            "award_floor_rate outside the plausible band is not used either "
            "(published_floor_implausible; live data holds 1.00000 rows) and falls "
            "back to the era tier.",
            "separate_regime records ONLY that the 국가계약 era tier does not apply. No "
            "substitute floor rate is asserted — the 산림청 산림사업 적격심사 세부기준 "
            "text is unverified, so its tiers are not encoded anywhere.",
            "below_legal_floor is also skipped when the reported winning_rate is not "
            "independent evidence: if it matches winning_amount/base_amount within "
            "data_quality_details.rate_basis_independence_tolerance and no 복수예비가격 "
            "were collected, the reported rate may just be that amount ratio and would "
            "read 사정률(~0.98) below the 예정가-basis floor. See "
            "summary.rate_basis_unverified_count and data_quality_details."
            "rate_basis_unverified / reserve_price_count.",
            "Known limit: shallow undercuts (0.9~2.7%p, 지방계약/공공기관/수의견적 등 "
            "다른 하한 체계 가능성) outside those gates are NOT resolved and still "
            "surface as below_legal_floor — the data alone cannot tell which tier applied.",
            "summary.quality_flag_counts is scoped to the (clean-only) aggregation set, so "
            "base_basis_contaminated is 0 there by construction; read "
            "summary.evaluated_quality_flag_counts for all evaluated targets.",
            "--exclude-agency-history drops history rows matching ANY of the target's "
            "발주/수요/stored agency names, because HistoricalData.agency_name is persisted "
            "수요기관-first while the report's split key is 발주기관-first.",
        ],
        "summary": summary,
        "breakdowns": {
            "by_group": aggregate_by_key(aggregation_rows, key="group", thresholds=thresholds),
            "by_agency": agency_axis["by_agency"],
            "by_amount_bucket": aggregate_by_key(aggregation_rows, key="amount_bucket", thresholds=thresholds),
            "by_procurement_rate_band": aggregate_by_key(aggregation_rows, key="procurement_rate_band", thresholds=thresholds),
            "by_data_quality_flag": quality_report["by_flag"],
            "by_basis": aggregate_by_key(rows, key="basis", thresholds=thresholds),
        },
        "agency_displays": agency_axis["agency_displays"],
        "worst_agency_groups": agency_axis["worst_agencies"],
        "worst_recommended_targets": worst_targets(aggregation_rows, limit=args.worst_limit),
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
                "worst_agency_groups": report["worst_agency_groups"],
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
