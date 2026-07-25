#!/usr/bin/env python3
"""Backfill Project.award_floor_rate 및 eligibility_raw for pre-collected notices.

This is ``eligibility_raw``'s **single writer** (the collection feed no longer
emits it — the list/target response carries no eligibility detail, only flags;
실측 2026-07-19). Per open notice it targeted-queries the BidPublicInfoService
list operation (``getBidPblancListInfoServc`` / ...``Cnstwk`` / ...) once and,
**only when that notice flags an 업종제한** (``indstrytyLmtYn == "Y"``), makes a
second license-limit sub-call (``getBidPblancListInfoLicenseLimit``) to pull the
자격 상세. It fills two columns that older rows are missing:

- ``award_floor_rate`` (fraction, e.g. ``0.88``; added #201) — the notice's
  낙찰하한율 from ``sucsfbidLwltRate`` on the targeted query.
- ``eligibility_raw`` (JSON) — ``{"flags": {...}, "license_limits": [{...}]}``:
  the participation flags from the targeted query plus, when applicable, the
  면허제한 rows (``lcnsLmtNm``/``permsnIndstrytyList`` 등) from the sub-call. The
  source for later 라벨 추출 (PR-B); persisting only, no runtime consumer today.

Targeting rule: ``eligibility_raw IS NULL AND status='open' AND deadline >= now``.
Targets are ordered **operator candidates first**: tier 1 = notices that pass the
operator's cheap watch-rule gate (the same filters the strategy monitor uses, no
ML), tier 2 = everything else, each tier deadline-ascending. Tier 2 is still
collected (it feeds the label / ML dataset) but must not eat the daily quota
ahead of notices the operator could actually bid on — plain deadline order spent
most of a run on soon-to-expire, out-of-scope notices (실측 2026-07-20: 자격 원문
760건 중 610건이 이미 마감, 운영자 게이트 매칭 열린 공고 170건 중 보유 5건).
``--no-operator-priority`` restores plain deadline order, as does the absence of a
usable strategy gate. Keying the resume on ``eligibility_raw`` (not
``award_floor_rate``) lets one pass fill both columns and drops each row from the
re-query set once its eligibility raw is saved. A notice that carries flags is
completed even with a flags-only dict (no re-query); only a fetch failure leaves
the row ``eligibility_raw IS NULL`` for the next run. ``--include-past-days N``
widens the deadline window into the recent past; ``--limit`` caps the run (by
notice count — a Y-flagged notice costs two calls); ``--dry-run`` counts the
target set (and prints a sample) without any external call.

Per-column write rule: ``award_floor_rate`` is written **only when the row's
current value is NULL** (an already-set floor is never overwritten, mirroring the
collector's persistence guard); ``eligibility_raw`` is written when the notice
yields a non-empty synthesized dict.

Call discipline (§4.5.7): serial, throttled (``--delay`` seconds between **all**
API calls — targeted query and sub-call alike), never a concurrent burst. A
per-notice HTTP/parse failure (either call) is counted and the run continues with
that row's eligibility left NULL for retry; ``--max-consecutive-errors``
consecutive failures abort the run (rate-limit / outage guard). A partial commit
lands every ``--chunk-size`` updates.

Decision logic (response items -> latest 차수 -> fraction / flags / license
rows) is isolated as pure functions (``parse_floor_rate`` and the
``openapi.extract_eligibility_flags`` / ``build_eligibility_raw`` helpers are
reused, never re-implemented); both fetches are injected callables (default =
real API), so tests exercise the whole run with fakes and no network. Secrets
(service key) are never logged.

Usage (runs inside the api container):
    docker exec bid_vector_api python scripts/backfill_award_floor_rate.py --dry-run
    docker exec bid_vector_api python scripts/backfill_award_floor_rate.py
    docker exec bid_vector_api python scripts/backfill_award_floor_rate.py \
        --include-past-days 3 --delay 1.5 --limit 500
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.constants import OPEN_PROJECT_STATUS  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.single_user import get_operator_strategy  # noqa: E402
from app.core.time import kst_now, utc_now  # noqa: E402
from app.models.models import OperatorStrategy, Project  # noqa: E402
from app.services.koneps import http_client, openapi, parsing  # noqa: E402
from app.services.opportunity_monitoring import (  # noqa: E402
    has_watch_rules,
    matches_strategy_watch_rules,
)

# BidPublicInfoService protocol constants for a single-notice targeted query.
# (Tunable knobs — window/limit/delay/rows — are argparse defaults below.)
_RESPONSE_TYPE = "json"
_PAGE_NO = 1
_INQUIRY_DIV_NOTICE_NUMBER = "2"  # inqryDiv=2 => filter by bidNtceNo

# argparse defaults (magic values declared here, never inline in a function).
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_CHUNK_SIZE = 50
DEFAULT_MAX_CONSECUTIVE_ERRORS = 10
DEFAULT_INCLUDE_PAST_DAYS = 0
DEFAULT_NUM_OF_ROWS = 10
DEFAULT_PROGRESS_EVERY = 200
DEFAULT_SAMPLE_SIZE = 10

# Operator-priority tiering. The target set is ordered tier 1 (open notices that
# pass the operator's watch-rule gate) then tier 2 (everything else), deadline
# ascending inside each tier. Tier 2 is kept — it still feeds the label/ML
# dataset — but must not eat the daily quota ahead of notices the operator can
# actually bid on.
# Bound on how many candidate rows are pulled into Python for the tier decision.
# Rows past the cap keep plain SQL deadline order (tier 2 semantics): the gate is
# a cheap in-memory predicate, but the scan set must stay bounded regardless of
# how large the open-notice backlog grows.
OPERATOR_PRIORITY_SCAN_MULTIPLIER = 20
OPERATOR_PRIORITY_SCAN_FLOOR = 2_000
OPERATOR_PRIORITY_SCAN_CEILING = 20_000

# Row shape returned to the run loop: (id, notice_number, category, floor rate).
TargetRow = tuple[int, str, str | None, float | None]

# A callable that maps (notice_number, category) -> decoded OpenAPI payload dict
# (the BidPublicInfoService list targeted query — floor rate + eligibility flags).
FetchFn = Callable[[str, str | None], dict[str, Any]]
# A callable that maps (notice_number, bid_notice_ord) -> decoded payload dict
# (the getBidPblancListInfoLicenseLimit sub-call — 자격 상세 rows). Category is
# not a parameter of this single operation, so it is not passed.
# The 차수 argument is the raw zero-padded string ("000") — KONEPS silently
# returns totalCount=0 for an int-coerced ord (live-measured 2026-07-19).
LicenseLimitFetchFn = Callable[[str, str], dict[str, Any]]


@dataclass
class BackfillStats:
    """Aggregate counts for one backfill run."""

    dry_run: bool = False
    target_count: int = 0
    tier1_selected: int = 0
    tier2_selected: int = 0
    operator_priority: bool = False
    processed: int = 0
    updated: int = 0
    no_value: int = 0
    eligibility_saved: int = 0
    license_limit_calls: int = 0
    skipped_blank: int = 0
    errors: int = 0
    aborted: bool = False
    elapsed_seconds: float = 0.0
    sample: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "target_count": self.target_count,
            "tier1_selected": self.tier1_selected,
            "tier2_selected": self.tier2_selected,
            "operator_priority": self.operator_priority,
            "processed": self.processed,
            "updated": self.updated,
            "no_value": self.no_value,
            "eligibility_saved": self.eligibility_saved,
            "license_limit_calls": self.license_limit_calls,
            "skipped_blank": self.skipped_blank,
            "errors": self.errors,
            "aborted": self.aborted,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "sample": self.sample or [],
        }


# --- Pure decision logic (no IO) ---------------------------------------------


def latest_order_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the item for the latest 차수 (max ``bidNtceOrd``).

    When a notice was re-issued the list carries several rows; the newest order
    holds the currently-effective floor rate. Rows without a parseable order sort
    to the bottom so a numbered order always wins.
    """
    if not items:
        return None

    def order_key(item: dict[str, Any]) -> int:
        order = parsing.coerce_int_value(item.get("bidNtceOrd"))
        return order if order is not None else -1

    return max(items, key=order_key)


def latest_item_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the latest-차수 item row from an OpenAPI list payload (or ``None``).

    The run loop reads the floor rate, the eligibility flags, and the 차수
    (``bidNtceOrd``, needed for the license-limit sub-call) off this one row so
    all three come from a single consistent notice revision.
    """
    body = openapi.openapi_body(payload)
    items = openapi.openapi_item_list(body)
    return latest_order_item(items)


def floor_rate_from_item(item: dict[str, Any] | None) -> float | None:
    """Floor-rate fraction from one item row.

    Returns ``None`` when the row is absent or ``sucsfbidLwltRate`` is missing /
    zero / non-numeric (a legitimate "no value", distinct from a fetch error).
    """
    if item is None:
        return None
    return parsing.normalize_bid_rate_value(item.get("sucsfbidLwltRate"))


def parse_floor_rate(payload: dict[str, Any]) -> float | None:
    """Extract the latest-차수 floor rate from an OpenAPI payload as a fraction."""
    return floor_rate_from_item(latest_item_from_payload(payload))


def _needs_license_limit(
    flags: dict[str, Any] | None,
    bid_notice_ord: str | None,
) -> bool:
    """Whether to fetch the license-limit sub-operation for this notice.

    Only when the notice's flags mark an 업종제한 (``indstrytyLmtYn == "Y"``) and a
    차수 (``bidNtceOrd`` — a required sub-call parameter) is known. Notices with no
    participation limit return nothing useful from the sub-operation, so they are
    not called (saves half the KONEPS quota).
    """
    if not flags or not str(bid_notice_ord or "").strip():
        return False
    return str(flags.get("indstrytyLmtYn") or "").strip().upper() == "Y"


def raise_for_result_code(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` when the OpenAPI header carries a non-OK resultCode.

    KONEPS / data.go.kr signals quota-exceeded and key-throttle as **HTTP 200
    with an error ``resultCode``** in the envelope header, not a 4xx status. If
    those slipped through, they would parse to zero items and be miscounted as
    ``no_value``, resetting the consecutive-error counter so the abort guard
    never fires against a rate limit. Surfacing them as a per-notice error feeds
    the guard. An empty resultCode is treated as OK — some payload shapes omit
    the header.

    Thin wrapper over the shared ``http_client.check_result_code`` (single source
    for the ``00``/``03`` OK set, mirrored by the collector's notice-list /
    reserve-detail validation). The ``BidPublicInfoService`` subject preserves
    this script's original message verbatim; the raise-only return is kept for
    callers that ignore the extracted code/message pair.
    """
    http_client.check_result_code(payload, source="BidPublicInfoService")


# --- IO seam: default real-API fetch -----------------------------------------


def make_openapi_fetch(
    *,
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    url: str | None = None,
    service_key: str | None = None,
) -> FetchFn:
    """Build the default fetch that targeted-queries BidPublicInfoService.

    The returned callable performs one keyed HTTP GET (raw + URL-encoded key
    variants) and returns the decoded JSON payload; it raises on a non-2xx
    status so the run loop records a per-notice error. The service key is passed
    through ``http_client`` and never logged.
    """
    resolved_url = str(url or settings.KONEPS_OPENAPI_BID_PUBLIC_INFO_URL).rstrip("/")
    resolved_key = str(
        service_key if service_key is not None else settings.KONEPS_OPENAPI_SERVICE_KEY
    ).strip()
    if not resolved_key:
        raise ValueError(
            "KONEPS_OPENAPI_SERVICE_KEY is required to fetch award floor rates."
        )

    def fetch(notice_number: str, category: str | None) -> dict[str, Any]:
        operation = openapi.openapi_operation_for_category(category)
        endpoint = f"{resolved_url}/{operation}"
        params = {
            "type": _RESPONSE_TYPE,
            "numOfRows": num_of_rows,
            "pageNo": _PAGE_NO,
            "inqryDiv": _INQUIRY_DIV_NOTICE_NUMBER,
            "bidNtceNo": notice_number,
        }
        response, key_variant = http_client.request_openapi_with_key_variants(
            endpoint,
            params=params,
            service_key=resolved_key,
            operation=operation,
        )
        if response.status_code >= 400:
            raise ValueError(
                f"KONEPS BidPublicInfoService HTTP {response.status_code} for "
                f"{operation}: {response.text[:200]} "
                f"Tried service key variants: {key_variant}."
            )
        return http_client.load_openapi_json(response)

    return fetch


def make_license_limit_fetch(
    *,
    num_of_rows: int = DEFAULT_NUM_OF_ROWS,
    url: str | None = None,
    service_key: str | None = None,
) -> LicenseLimitFetchFn:
    """Build the default fetch for the license-limit sub-operation.

    The returned callable performs one keyed HTTP GET against
    ``getBidPblancListInfoLicenseLimit`` with the measured required parameters
    (``inqryDiv=2`` + ``bidNtceNo`` + ``bidNtceOrd``) and returns the decoded
    JSON payload; it raises on a non-2xx status so the run loop records a
    per-notice error. The operation belongs to BidPublicInfoService, so it shares
    the list URL. The service key is passed through ``http_client`` and never
    logged.
    """
    resolved_url = str(url or settings.KONEPS_OPENAPI_BID_PUBLIC_INFO_URL).rstrip("/")
    resolved_key = str(
        service_key if service_key is not None else settings.KONEPS_OPENAPI_SERVICE_KEY
    ).strip()
    if not resolved_key:
        raise ValueError(
            "KONEPS_OPENAPI_SERVICE_KEY is required to fetch license limits."
        )
    operation = openapi.LICENSE_LIMIT_OPERATION
    endpoint = f"{resolved_url}/{operation}"

    def fetch(notice_number: str, bid_notice_ord: str) -> dict[str, Any]:
        params = {
            "type": _RESPONSE_TYPE,
            "numOfRows": num_of_rows,
            "pageNo": _PAGE_NO,
            "inqryDiv": _INQUIRY_DIV_NOTICE_NUMBER,
            "bidNtceNo": notice_number,
            "bidNtceOrd": bid_notice_ord,
        }
        response, key_variant = http_client.request_openapi_with_key_variants(
            endpoint,
            params=params,
            service_key=resolved_key,
            operation=operation,
        )
        if response.status_code >= 400:
            raise ValueError(
                f"KONEPS BidPublicInfoService HTTP {response.status_code} for "
                f"{operation}: {response.text[:200]} "
                f"Tried service key variants: {key_variant}."
            )
        return http_client.load_openapi_json(response)

    return fetch


# --- Target selection (DB read) ----------------------------------------------


@dataclass
class TargetSelection:
    """An ordered target list plus how it was tiered."""

    targets: list[TargetRow]
    tier1_count: int = 0
    tier2_count: int = 0
    operator_priority: bool = False


# Columns the run loop consumes, and the extra columns the watch-rule gate reads.
# Loaded explicitly (never whole ORM rows) so a large scan stays cheap.
_TARGET_COLUMNS = (
    Project.id,
    Project.notice_number,
    Project.category,
    Project.award_floor_rate,
)
_GATE_COLUMNS = (
    Project.title,
    Project.description,
    Project.requirements,
    Project.budget_estimate,
)


def operator_priority_scan_cap(limit: int | None) -> int:
    """How many candidate rows may be pulled into Python for the tier decision."""
    if limit is None:
        return OPERATOR_PRIORITY_SCAN_CEILING
    return min(
        OPERATOR_PRIORITY_SCAN_CEILING,
        max(OPERATOR_PRIORITY_SCAN_FLOOR, limit * OPERATOR_PRIORITY_SCAN_MULTIPLIER),
    )


def partition_by_tier(
    rows: list[Any],
    is_operator_candidate: Callable[[Any], bool],
) -> tuple[list[TargetRow], list[TargetRow]]:
    """Split deadline-ordered candidate rows into (tier 1, tier 2) target rows.

    Pure: order inside each tier is the input order (deadline ascending), so the
    concatenation is "operator candidates first, each tier still imminent-first".
    """
    tier1: list[TargetRow] = []
    tier2: list[TargetRow] = []
    for row in rows:
        bucket = tier1 if is_operator_candidate(row) else tier2
        bucket.append(_as_target_row(row))
    return tier1, tier2


def _as_target_row(row: Any) -> TargetRow:
    """Narrow a candidate query row to the tuple the run loop consumes."""
    return (row[0], row[1], row[2], row[3])


def _gate_project(row: Any) -> Project:
    """Build a transient Project carrying only the fields the gate reads.

    Not added to the session — the gate needs a Project-shaped object, and this
    keeps the scan query to the few columns it actually uses.
    """
    return Project(
        title=row.title,
        description=row.description,
        requirements=row.requirements,
        category=row.category,
        budget_estimate=row.budget_estimate,
    )


def _target_query(
    db: Session,
    cutoff: datetime,
    *,
    columns: tuple[Any, ...],
    limit: int | None = None,
    offset: int | None = None,
):
    """Base target query: eligibility NULL, open, deadline in window, imminent first."""
    query = (
        db.query(*columns)
        .filter(Project.eligibility_raw.is_(None))
        .filter(Project.status == OPEN_PROJECT_STATUS)
        .filter(Project.deadline >= cutoff)
        .order_by(Project.deadline.asc(), Project.id.asc())
    )
    if offset is not None:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query


def _resolve_watch_strategy(db: Session) -> OperatorStrategy | None:
    """The canonical operator's watch strategy, or ``None`` when it is no gate.

    Read-only on purpose (``get_operator_strategy``, not ``ensure_...``): target
    selection must never create an operator/strategy row as a side effect, and a
    ``--dry-run`` must stay write-free. A missing strategy, or one with every
    watch field empty, yields ``None`` so selection falls back to plain
    deadline order.
    """
    strategy = get_operator_strategy(db)
    return strategy if has_watch_rules(strategy) else None


def select_targets(
    db: Session,
    *,
    include_past_days: int = DEFAULT_INCLUDE_PAST_DAYS,
    limit: int | None = None,
    now: datetime | None = None,
    operator_priority: bool = True,
) -> TargetSelection:
    """Return the ordered target set for one run, with its tier breakdown.

    Filter: ``eligibility_raw IS NULL AND status='open' AND deadline >= cutoff``
    where ``cutoff = now - include_past_days``; the current ``award_floor_rate``
    rides along so the run loop can honour the "write floor only when currently
    NULL" guard without a second query.

    Order: with ``operator_priority`` (default) the set is tier 1 (passes the
    operator's watch-rule gate) then tier 2 (the rest), ``deadline`` ascending
    inside each tier — a capped or interrupted run then spends its quota on
    notices the operator could actually bid on instead of burning it on soon-to-
    expire, out-of-scope notices. Without a usable strategy gate, or with
    ``operator_priority=False``, the order is plain ``deadline`` ascending.
    """
    cutoff = (now or utc_now()) - timedelta(days=max(0, include_past_days))
    strategy = _resolve_watch_strategy(db) if operator_priority else None

    if strategy is None:
        rows = _target_query(db, cutoff, columns=_TARGET_COLUMNS, limit=limit).all()
        targets = [_as_target_row(row) for row in rows]
        return TargetSelection(
            targets=targets,
            tier1_count=0,
            tier2_count=len(targets),
            operator_priority=False,
        )

    scan_cap = operator_priority_scan_cap(limit)
    scanned = _target_query(
        db, cutoff, columns=_TARGET_COLUMNS + _GATE_COLUMNS, limit=scan_cap
    ).all()
    tier1, tier2 = partition_by_tier(
        scanned,
        lambda row: matches_strategy_watch_rules(_gate_project(row), strategy),
    )
    ordered = tier1 + tier2

    if len(scanned) >= scan_cap and (limit is None or limit > scan_cap):
        # The scan cap was reached and the run still wants more rows: append the
        # unscanned tail in SQL deadline order (tier 2 — never gate-checked).
        ordered.extend(
            _as_target_row(row)
            for row in _target_query(
                db, cutoff, columns=_TARGET_COLUMNS, limit=None, offset=scan_cap
            ).all()
        )

    if limit is not None:
        ordered = ordered[:limit]
    tier1_selected = min(len(tier1), len(ordered))
    return TargetSelection(
        targets=ordered,
        tier1_count=tier1_selected,
        tier2_count=len(ordered) - tier1_selected,
        operator_priority=True,
    )


def load_targets(
    db: Session,
    *,
    include_past_days: int = DEFAULT_INCLUDE_PAST_DAYS,
    limit: int | None = None,
    now: datetime | None = None,
    operator_priority: bool = True,
) -> list[TargetRow]:
    """Ordered ``(id, notice_number, category, award_floor_rate)`` targets.

    Thin wrapper over :func:`select_targets` for callers that do not need the
    tier breakdown.
    """
    return select_targets(
        db,
        include_past_days=include_past_days,
        limit=limit,
        now=now,
        operator_priority=operator_priority,
    ).targets


# --- Run loop -----------------------------------------------------------------


def run_backfill(
    db: Session,
    targets: list[tuple[int, str, str | None, float | None]],
    *,
    fetch: FetchFn,
    fetch_license_limit: LicenseLimitFetchFn,
    dry_run: bool = False,
    delay: float = DEFAULT_DELAY_SECONDS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> BackfillStats:
    """Fill ``award_floor_rate``/``eligibility_raw`` for ``targets``.

    Serial, throttled, resumable. On ``dry_run`` neither fetch is called: only
    the target count and a small notice-number sample are reported. Otherwise
    each notice is targeted-queried once (floor + eligibility flags) and, when it
    flags an 업종제한 (``indstrytyLmtYn == "Y"``), a second license-limit sub-call
    pulls the 자격 상세; the ``delay``-second throttle applies between **all** API
    calls. The floor rate is written **only when the row's current floor is
    NULL**; ``eligibility_raw`` is the synthesized ``{"flags", "license_limits"}``
    dict. Both columns are staged into one per-notice UPDATE, committed every
    ``chunk_size`` written rows. A fetch failure (either call) leaves the row's
    eligibility NULL for the next run.
    """
    started = time.monotonic()
    stats = BackfillStats(dry_run=dry_run, target_count=len(targets))

    if dry_run:
        stats.sample = [notice for _, notice, _, _ in targets[:sample_size]]
        stats.elapsed_seconds = time.monotonic() - started
        return stats

    consecutive_errors = 0
    pending = 0
    api_calls_made = 0

    def throttle() -> None:
        # Throttle *between* real API calls — the targeted query and the
        # license-limit sub-call alike — so a burst never trips the KONEPS rate
        # limit. The very first call of the run is not delayed, and blank-notice
        # skips make no call so they never sleep.
        nonlocal api_calls_made
        if api_calls_made > 0 and delay > 0:
            sleep(delay)
        api_calls_made += 1

    def record_error(notice: str, exc: Exception) -> bool:
        """Count a per-notice fetch error; return True when the run must abort."""
        nonlocal consecutive_errors
        stats.errors += 1
        consecutive_errors += 1
        log(
            f"[floor-backfill] error notice={notice} "
            f"({type(exc).__name__}: {str(exc)[:120]})"
        )
        if consecutive_errors >= max_consecutive_errors:
            stats.aborted = True
            log(f"[floor-backfill] aborting: {consecutive_errors} consecutive errors.")
            return True
        return False

    for project_id, notice_number, category, current_floor in targets:
        stats.processed += 1
        normalized = parsing.normalize_notice_number(notice_number)
        if not normalized:
            # No notice number to query with — leave NULL (re-tryable). No API
            # call happens, so skip the throttle and the error guard entirely.
            stats.skipped_blank += 1
            continue

        # 1. Targeted list query — floor rate + participation flags + 차수.
        try:
            throttle()
            payload = fetch(normalized, category)
            # data.go.kr returns quota/throttle errors as HTTP 200 + error
            # resultCode; treat that as a per-notice failure so the abort guard
            # actually fires against a rate limit (not silent no_value).
            raise_for_result_code(payload)
        except Exception as exc:  # noqa: BLE001 - record and continue
            if record_error(normalized, exc):
                break
            continue

        item = latest_item_from_payload(payload)
        update_values: dict[Any, Any] = {}

        # Floor rate: fill only when the row's current value is NULL — never
        # overwrite an already-set floor (mirrors the persistence guard). A
        # notice whose current floor is already set is not counted as no_value.
        if current_floor is None:
            rate = floor_rate_from_item(item)
            if rate is None:
                stats.no_value += 1
            else:
                update_values[Project.award_floor_rate] = rate
                stats.updated += 1

        flags = openapi.extract_eligibility_flags(item) if item else None
        # Keep the 차수 as the raw zero-padded string ("000") — int-coercing it
        # makes the sub-call silently return totalCount=0 (live-measured
        # 2026-07-19: ord=0 → 0 rows, ord="000" → real rows).
        bid_notice_ord = (
            str(item.get("bidNtceOrd") or "").strip() if item else None
        )

        # 2. License-limit sub-call, only for 업종제한 notices (required param
        #    bidNtceOrd known). A sub-call failure is a per-notice error: the
        #    floor still lands, but eligibility stays NULL so the row retries.
        license_limit_items: list[dict[str, Any]] = []
        sub_call_failed = False
        if _needs_license_limit(flags, bid_notice_ord):
            try:
                throttle()
                sub_payload = fetch_license_limit(normalized, bid_notice_ord)
                raise_for_result_code(sub_payload)
            except Exception as exc:  # noqa: BLE001 - record and continue
                # Mark failed so eligibility is not written (row stays NULL for
                # retry); record_error flips stats.aborted when the guard trips
                # and the ``if stats.aborted: break`` below stops the run after
                # the floor for this row still lands.
                record_error(normalized, exc)
                sub_call_failed = True
            else:
                stats.license_limit_calls += 1
                license_limit_items = openapi.openapi_item_list(
                    openapi.openapi_body(sub_payload)
                )

        if not sub_call_failed:
            eligibility = openapi.build_eligibility_raw(flags, license_limit_items)
            if eligibility:
                update_values[Project.eligibility_raw] = eligibility
                stats.eligibility_saved += 1
            # A fully successful notice clears the consecutive-error streak.
            consecutive_errors = 0

        if update_values:
            db.query(Project).filter(Project.id == project_id).update(
                update_values,
                synchronize_session=False,
            )
            pending += 1
            if pending >= chunk_size:
                db.commit()
                pending = 0

        if stats.aborted:
            break

        if progress_every and stats.processed % progress_every == 0:
            log(
                f"[floor-backfill] {_kst_stamp()} processed={stats.processed}/"
                f"{stats.target_count} updated={stats.updated} "
                f"no_value={stats.no_value} eligibility={stats.eligibility_saved} "
                f"license_limit_calls={stats.license_limit_calls} "
                f"errors={stats.errors}"
            )

    if pending:
        db.commit()
    stats.elapsed_seconds = time.monotonic() - started
    return stats


def _kst_stamp() -> str:
    """Current KST timestamp for progress/summary lines."""
    return kst_now().strftime("%Y-%m-%d %H:%M:%S KST")


def _priority_line(selection: TargetSelection) -> str:
    """One KST-stamped line describing how this run's target set was tiered."""
    if not selection.operator_priority:
        headline = "운영자 후보 우선 미적용 (전략 게이트 없음 또는 --no-operator-priority)"
    else:
        headline = f"운영자 후보 우선 {selection.tier1_count}건 포함"
    return (
        f"{_kst_stamp()} {headline} "
        f"tier1_selected={selection.tier1_count} "
        f"tier2_selected={selection.tier2_count}"
    )


# --- CLI ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count the target set and print a sample; no external call, no write.",
    )
    parser.add_argument(
        "--include-past-days",
        type=int,
        default=DEFAULT_INCLUDE_PAST_DAYS,
        help="Also include open notices whose deadline is within the last N days.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N target notices (default: no cap).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to sleep between successive API calls (throttle).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Commit after this many updates (resume checkpoint).",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_ERRORS,
        help="Abort after this many consecutive fetch failures.",
    )
    parser.add_argument(
        "--num-of-rows",
        type=int,
        default=DEFAULT_NUM_OF_ROWS,
        help="numOfRows page size for the single-notice targeted query.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Emit one progress line every N processed notices.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="How many notice numbers to show in the --dry-run sample.",
    )
    parser.add_argument(
        "--no-operator-priority",
        action="store_true",
        help=(
            "Disable operator-candidate-first tiering and process targets in "
            "plain deadline order (legacy behaviour)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = SessionLocal()
    try:
        selection = select_targets(
            db,
            include_past_days=args.include_past_days,
            limit=args.limit,
            operator_priority=not args.no_operator_priority,
        )
        targets = selection.targets
        fetch: FetchFn | None = None
        fetch_license_limit: LicenseLimitFetchFn | None = None
        if not args.dry_run:
            fetch = make_openapi_fetch(num_of_rows=max(1, args.num_of_rows))
            fetch_license_limit = make_license_limit_fetch(
                num_of_rows=max(1, args.num_of_rows)
            )

        stats = run_backfill(
            db,
            targets,
            # A dry run never touches either fetch; harmless placeholders keep the
            # signature satisfied without building the real (key-requiring) ones.
            fetch=fetch or (lambda notice, category: {}),
            fetch_license_limit=fetch_license_limit or (lambda notice, ord: {}),
            dry_run=args.dry_run,
            delay=max(0.0, args.delay),
            chunk_size=max(1, args.chunk_size),
            max_consecutive_errors=max(1, args.max_consecutive_errors),
            progress_every=max(0, args.progress_every),
            sample_size=max(0, args.sample_size),
        )
        stats.tier1_selected = selection.tier1_count
        stats.tier2_selected = selection.tier2_count
        stats.operator_priority = selection.operator_priority
        if args.dry_run:
            db.rollback()
    finally:
        db.close()

    summary = stats.as_dict()
    mode = "DRY-RUN (no API, no write)" if args.dry_run else "APPLIED"
    print(f"[floor-backfill] {_kst_stamp()} {mode}")
    print(f"[floor-backfill] {_priority_line(selection)}")
    print(
        f"[floor-backfill] target={summary['target_count']} "
        f"processed={summary['processed']} updated={summary['updated']} "
        f"no_value={summary['no_value']} "
        f"eligibility_saved={summary['eligibility_saved']} "
        f"license_limit_calls={summary['license_limit_calls']} "
        f"skipped_blank={summary['skipped_blank']} "
        f"errors={summary['errors']} aborted={summary['aborted']} "
        f"elapsed={summary['elapsed_seconds']}s"
    )
    if args.dry_run and summary["sample"]:
        print(f"[floor-backfill] sample={summary['sample']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
