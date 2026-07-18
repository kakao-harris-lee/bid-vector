#!/usr/bin/env python3
"""Backfill Project.award_floor_rate for notices collected before #201.

``Project.award_floor_rate`` (fraction, e.g. ``0.88``) was added in #201 and is
populated going forward by the collector from KONEPS ``sucsfbidLwltRate``. Every
notice collected before that release stored ``NULL``. This one-off script fills
those gaps by targeted-querying the BidPublicInfoService list operation
(``getBidPblancListInfoServc`` / ...``Cnstwk`` / ...) for each open notice and
persisting the normalized floor rate.

Targeting rule: ``award_floor_rate IS NULL AND status='open' AND deadline >=
now`` (imminent notices first). ``--include-past-days N`` widens the deadline
window into the recent past; ``--limit`` caps the run; ``--dry-run`` counts the
target set (and prints a sample) without any external call.

Call discipline (§4.5.7): serial, throttled (``--delay`` seconds between calls),
never a concurrent burst. A per-notice HTTP/parse failure is counted and the run
continues; ``--max-consecutive-errors`` consecutive failures abort the run
(rate-limit / outage guard). The ``award_floor_rate IS NULL`` filter is itself
the resume point — a re-run only revisits rows still missing a value, and a
notice that legitimately has no floor rate stays ``NULL`` (re-tryable, not
marked). A partial commit lands every ``--chunk-size`` updates.

Decision logic (response items -> latest 차수 -> fraction) is isolated as pure
functions; the fetch is an injected callable (default = real API), so tests
exercise the whole run with a fake fetch and no network. Secrets (service key)
are never logged.

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
from app.core.database import SessionLocal  # noqa: E402
from app.core.time import kst_now, utc_now  # noqa: E402
from app.models.models import Project  # noqa: E402
from app.services.koneps import http_client, openapi, parsing  # noqa: E402

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

# A callable that maps (notice_number, category) -> decoded OpenAPI payload dict.
FetchFn = Callable[[str, str | None], dict[str, Any]]


@dataclass
class BackfillStats:
    """Aggregate counts for one backfill run."""

    dry_run: bool = False
    target_count: int = 0
    processed: int = 0
    updated: int = 0
    no_value: int = 0
    errors: int = 0
    aborted: bool = False
    elapsed_seconds: float = 0.0
    sample: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "target_count": self.target_count,
            "processed": self.processed,
            "updated": self.updated,
            "no_value": self.no_value,
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


def parse_floor_rate(payload: dict[str, Any]) -> float | None:
    """Extract the latest-차수 floor rate from an OpenAPI payload as a fraction.

    Returns ``None`` when the response has no items or the ``sucsfbidLwltRate``
    field is missing / zero / non-numeric (a legitimate "no value", distinct
    from a fetch error).
    """
    body = openapi.openapi_body(payload)
    items = openapi.openapi_item_list(body)
    item = latest_order_item(items)
    if item is None:
        return None
    return parsing.normalize_bid_rate_value(item.get("sucsfbidLwltRate"))


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


# --- Target selection (DB read) ----------------------------------------------


def load_targets(
    db: Session,
    *,
    include_past_days: int = DEFAULT_INCLUDE_PAST_DAYS,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[tuple[int, str, str | None]]:
    """Return (id, notice_number, category) for open notices missing a floor rate.

    Filter: ``award_floor_rate IS NULL AND status='open' AND deadline >= cutoff``
    where ``cutoff = now - include_past_days``. Ordered by ``deadline`` ascending
    (imminent first) so a capped/interrupted run helps the soonest notices.
    """
    cutoff = (now or utc_now()) - timedelta(days=max(0, include_past_days))
    query = (
        db.query(Project.id, Project.notice_number, Project.category)
        .filter(Project.award_floor_rate.is_(None))
        .filter(Project.status == "open")
        .filter(Project.deadline >= cutoff)
        .order_by(Project.deadline.asc(), Project.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    return [(row[0], row[1], row[2]) for row in query.all()]


# --- Run loop -----------------------------------------------------------------


def run_backfill(
    db: Session,
    targets: list[tuple[int, str, str | None]],
    *,
    fetch: FetchFn,
    dry_run: bool = False,
    delay: float = DEFAULT_DELAY_SECONDS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> BackfillStats:
    """Fill ``award_floor_rate`` for ``targets`` (serial, throttled, resumable).

    On ``dry_run`` the fetch is never called: only the target count and a small
    notice-number sample are reported. Otherwise each notice is fetched with a
    ``delay``-second throttle between calls, the floor rate is persisted when
    present, and a commit lands every ``chunk_size`` updates.
    """
    started = time.monotonic()
    stats = BackfillStats(dry_run=dry_run, target_count=len(targets))

    if dry_run:
        stats.sample = [notice for _, notice, _ in targets[:sample_size]]
        stats.elapsed_seconds = time.monotonic() - started
        return stats

    consecutive_errors = 0
    pending = 0
    for index, (project_id, notice_number, category) in enumerate(targets):
        if index > 0 and delay > 0:
            sleep(delay)

        stats.processed += 1
        normalized = parsing.normalize_notice_number(notice_number)
        if not normalized:
            # No notice number to query with — leave NULL (re-tryable), not an
            # API failure, so do not trip the consecutive-error guard.
            stats.no_value += 1
            continue

        try:
            payload = fetch(normalized, category)
        except Exception as exc:  # noqa: BLE001 - record and continue
            stats.errors += 1
            consecutive_errors += 1
            log(
                f"[floor-backfill] error notice={normalized} "
                f"({type(exc).__name__}: {str(exc)[:120]})"
            )
            if consecutive_errors >= max_consecutive_errors:
                stats.aborted = True
                log(
                    "[floor-backfill] aborting: "
                    f"{consecutive_errors} consecutive errors."
                )
                break
            continue
        consecutive_errors = 0

        rate = parse_floor_rate(payload)
        if rate is None:
            stats.no_value += 1
        else:
            db.query(Project).filter(Project.id == project_id).update(
                {Project.award_floor_rate: rate},
                synchronize_session=False,
            )
            stats.updated += 1
            pending += 1
            if pending >= chunk_size:
                db.commit()
                pending = 0

        if progress_every and stats.processed % progress_every == 0:
            log(
                f"[floor-backfill] {_kst_stamp()} processed={stats.processed}/"
                f"{stats.target_count} updated={stats.updated} "
                f"no_value={stats.no_value} errors={stats.errors}"
            )

    if pending:
        db.commit()
    stats.elapsed_seconds = time.monotonic() - started
    return stats


def _kst_stamp() -> str:
    """Current KST timestamp for progress/summary lines."""
    return kst_now().strftime("%Y-%m-%d %H:%M:%S KST")


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db = SessionLocal()
    try:
        targets = load_targets(
            db,
            include_past_days=args.include_past_days,
            limit=args.limit,
        )
        fetch: FetchFn | None = None
        if not args.dry_run:
            fetch = make_openapi_fetch(num_of_rows=max(1, args.num_of_rows))

        stats = run_backfill(
            db,
            targets,
            # A dry run never touches fetch; a harmless placeholder keeps the
            # signature satisfied without building the real (key-requiring) one.
            fetch=fetch or (lambda notice, category: {}),
            dry_run=args.dry_run,
            delay=max(0.0, args.delay),
            chunk_size=max(1, args.chunk_size),
            max_consecutive_errors=max(1, args.max_consecutive_errors),
            progress_every=max(0, args.progress_every),
            sample_size=max(0, args.sample_size),
        )
        if args.dry_run:
            db.rollback()
    finally:
        db.close()

    summary = stats.as_dict()
    mode = "DRY-RUN (no API, no write)" if args.dry_run else "APPLIED"
    print(f"[floor-backfill] {_kst_stamp()} {mode}")
    print(
        f"[floor-backfill] target={summary['target_count']} "
        f"processed={summary['processed']} updated={summary['updated']} "
        f"no_value={summary['no_value']} errors={summary['errors']} "
        f"aborted={summary['aborted']} elapsed={summary['elapsed_seconds']}s"
    )
    if args.dry_run and summary["sample"]:
        print(f"[floor-backfill] sample={summary['sample']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
