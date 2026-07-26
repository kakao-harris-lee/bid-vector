"""Pure processing helpers for the deferred scsbid reserve-detail backfill.

Extracted verbatim from ``app.tasks.jobs`` (§4.5 size decomposition). These are
I/O-boundary helpers with no Celery-task references: the ``@task`` entry
``backfill_scsbid_reserve_detail`` stays in ``app.tasks.jobs`` (registration name
unchanged) and drives these functions. ``utc_now`` is imported locally inside
``_process_scsbid_reserve_detail_chunk`` exactly as before so the test seam that
patches ``app.core.time.utc_now`` keeps working.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.koneps.collector import KonepsCollectorService

logger = logging.getLogger(__name__)


def run_scsbid_reserve_detail_backfill_job(
    notices: list[dict[str, Any]],
    *,
    enqueue_continuation: Callable[[list[dict[str, Any]]], bool],
) -> dict:
    """Fetch deferred scsbid reserve-detail rows and persist them per notice.

    Body of the ``backfill_scsbid_reserve_detail`` Celery task (the ``@task``
    entry stays in ``app.tasks.jobs`` so its registration name is unchanged). The
    serial self-chain continuation enqueue references the task object and is
    injected as ``enqueue_continuation`` so this module stays task-free.

    For each ``{"notice_number", "category"}``:

    * Idempotency — if the notice's ``HistoricalData.reserve_prices`` is already a
      non-empty settled value it is skipped (a reserve price is immutable, so a
      re-run after a partial crash never re-pays the HTTP cost).
    * Otherwise the reserve detail is fetched (one throttled HTTP call) and the
      resulting ``reserve_prices`` / ``selected_numbers`` are written onto the
      matching ``HistoricalData`` row as JSON.

    Serial self-chaining processes only the first
    ``KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE`` notices, then enqueues a
    single continuation for the remainder, so the rate-limited API is hit strictly
    one-chunk-at-a-time. Partial progress is committed in small batches; on
    ``SoftTimeLimitExceeded`` the work done so far is committed and a graceful
    summary is returned (no continuation — the next 6h collect self-heals). A
    single notice failing is caught per-notice and counted.
    """
    all_cleaned = _normalize_deferred_reserve_notices(notices)
    chunk_size = max(1, int(settings.KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE))
    cleaned = all_cleaned[:chunk_size]
    rest = all_cleaned[chunk_size:]
    service = KonepsCollectorService()
    service_key = str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip()
    # Backfill-specific inter-call throttle, kept separate from the collection
    # page-pagination delay: the reserve-detail endpoint is rate-limited harder
    # (HTTP 429 persisted even at collection's ~serial pace), so the backfill runs
    # at its own, slacker pace without slowing collection. 0 disables the sleep.
    delay_seconds = max(
        0.0,
        float(settings.KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS or 0.0),
    )
    commit_every = 25

    requested = len(cleaned)

    db = SessionLocal()
    try:
        if not service_key:
            # No key -> nothing fetchable; surface as errors without HTTP. Do NOT
            # chain a continuation (the remainder would fail the same way); the
            # next 6h collect re-defers everything once a key is configured.
            return _missing_reserve_detail_service_key_result(
                requested=requested,
                remaining=len(rest),
            )

        stats = _process_scsbid_reserve_detail_chunk(
            db,
            service=service,
            cleaned=cleaned,
            service_key=service_key,
            delay_seconds=delay_seconds,
            commit_every=commit_every,
            requested=requested,
            remaining=len(rest),
        )
        if stats.get("soft_time_limit_exceeded"):
            return _reserve_detail_backfill_result(
                requested=requested,
                remaining=len(rest),
                continued=False,
                stats=stats,
            )

        db.commit()
        _log_reserve_detail_backfill_errors(requested=requested, stats=stats)
        continued = enqueue_continuation(rest)
        return _reserve_detail_backfill_result(
            requested=requested,
            remaining=len(rest),
            continued=continued,
            stats=stats,
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _normalize_deferred_reserve_notices(
    notices: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """De-duplicate and clean the deferred reserve-detail notice payloads.

    Each entry needs a non-empty ``notice_number``; ``category`` is optional and
    only selects the reserve-detail operation. Order-preserving dedupe on the
    (notice_number, category) pair keeps the backfill from re-fetching the same
    notice twice within one chunk set.
    """
    cleaned: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in notices or []:
        if not isinstance(entry, dict):
            continue
        notice_number = str(entry.get("notice_number") or "").strip()
        if not notice_number:
            continue
        category = str(entry.get("category") or "").strip()
        key = (notice_number, category)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"notice_number": notice_number, "category": category})
    return cleaned


def _missing_reserve_detail_service_key_result(
    *,
    requested: int,
    remaining: int,
) -> dict[str, Any]:
    return {
        "requested": requested,
        "processed": 0,
        "fetched": 0,
        "skipped_existing": 0,
        "errors": requested,
        "error": "missing_service_key",
        "remaining": remaining,
        "continued": False,
    }


def _process_scsbid_reserve_detail_chunk(
    db,
    *,
    service: "KonepsCollectorService",
    cleaned: list[dict[str, Any]],
    service_key: str,
    delay_seconds: float,
    commit_every: int,
    requested: int,
    remaining: int,
) -> dict[str, Any]:
    from time import sleep

    from app.core.time import utc_now
    from app.models.models import HistoricalData

    stats: dict[str, Any] = {
        "fetched": 0,
        "skipped_existing": 0,
        "not_settled": 0,
        "errors": 0,
        "error_types": {},
        "error_samples": [],
        "processed_since_commit": 0,
    }
    for index, entry in enumerate(cleaned):
        notice_number = entry["notice_number"]
        category = entry["category"] or None
        try:
            record = _load_reserve_detail_record(db, HistoricalData, notice_number)
            if record is not None and service._has_persisted_reserve_prices(record):
                stats["skipped_existing"] += 1
                continue
            if index > 0 and delay_seconds > 0:
                sleep(delay_seconds)
            detail = service._fetch_scsbid_reserve_detail(
                {"bidNtceNo": notice_number},
                category=category,
                service_key=service_key,
            )
            _persist_reserve_detail_result(
                db,
                HistoricalData,
                record=record,
                notice_number=notice_number,
                detail=detail,
                stats=stats,
                utc_now=utc_now,
            )
            _commit_reserve_detail_progress(db, stats=stats, commit_every=commit_every)
        except SoftTimeLimitExceeded:
            return _reserve_detail_soft_limit_stats(
                db,
                stats=stats,
                requested=requested,
                remaining=remaining,
            )
        except Exception as exc:  # noqa: BLE001 — one notice must not abort the chunk
            _record_reserve_detail_error(stats, exc)
    stats.pop("processed_since_commit", None)
    return stats


def _load_reserve_detail_record(db, historical_model, notice_number: str):
    return (
        db.query(historical_model)
        .filter(historical_model.notice_number == notice_number)
        .order_by(historical_model.id.asc())
        .first()
    )


def _persist_reserve_detail_result(
    db,
    historical_model,
    *,
    record,
    notice_number: str,
    detail: dict[str, Any],
    stats: dict[str, Any],
    utc_now,
) -> None:
    import json

    reserve_prices = detail.get("reserve_prices") or []
    selected_numbers = detail.get("selected_numbers") or []
    if record is None:
        record = historical_model(notice_number=notice_number)
        db.add(record)
    if not reserve_prices:
        record.reserve_detail_checked_at = utc_now()
        stats["not_settled"] += 1
    else:
        record.reserve_prices = json.dumps(reserve_prices, ensure_ascii=False)
        record.selected_numbers = json.dumps(selected_numbers, ensure_ascii=False)
        stats["fetched"] += 1
    stats["processed_since_commit"] += 1


def _commit_reserve_detail_progress(
    db,
    *,
    stats: dict[str, Any],
    commit_every: int,
) -> None:
    if int(stats["processed_since_commit"]) < commit_every:
        return
    db.commit()
    stats["processed_since_commit"] = 0


def _reserve_detail_soft_limit_stats(
    db,
    *,
    stats: dict[str, Any],
    requested: int,
    remaining: int,
) -> dict[str, Any]:
    db.commit()
    logger.warning(
        "backfill_scsbid_reserve_detail hit soft time limit "
        "(fetched=%s skipped=%s errors=%s of %s)",
        stats["fetched"],
        stats["skipped_existing"],
        stats["errors"],
        requested,
    )
    stats = dict(stats)
    stats.pop("processed_since_commit", None)
    stats["soft_time_limit_exceeded"] = True
    stats["remaining"] = remaining
    return stats


def _record_reserve_detail_error(stats: dict[str, Any], exc: Exception) -> None:
    stats["errors"] += 1
    error_type_counts = stats["error_types"]
    error_samples = stats["error_samples"]
    exc_type = type(exc).__name__
    error_type_counts[exc_type] = error_type_counts.get(exc_type, 0) + 1
    label = f"{exc_type}: {exc}"[:200]
    if label not in error_samples and len(error_samples) < 5:
        error_samples.append(label)


def _log_reserve_detail_backfill_errors(
    *,
    requested: int,
    stats: dict[str, Any],
) -> None:
    if not stats["errors"]:
        return
    logger.warning(
        "backfill_scsbid_reserve_detail chunk done requested=%s fetched=%s "
        "skipped=%s not_settled=%s errors=%s error_types=%s samples=%s",
        requested,
        stats["fetched"],
        stats["skipped_existing"],
        stats["not_settled"],
        stats["errors"],
        stats["error_types"],
        stats["error_samples"],
    )


def _reserve_detail_backfill_result(
    *,
    requested: int,
    remaining: int,
    continued: bool,
    stats: dict[str, Any],
) -> dict[str, Any]:
    return {
        "requested": requested,
        "processed": stats["fetched"]
        + stats["skipped_existing"]
        + stats.get("not_settled", 0)
        + stats["errors"],
        "fetched": stats["fetched"],
        "skipped_existing": stats["skipped_existing"],
        "not_settled": stats.get("not_settled", 0),
        "errors": stats["errors"],
        "error_types": stats.get("error_types", {}),
        "error_samples": stats.get("error_samples", []),
        "remaining": remaining,
        "continued": continued,
        **(
            {"soft_time_limit_exceeded": True}
            if stats.get("soft_time_limit_exceeded")
            else {}
        ),
    }
