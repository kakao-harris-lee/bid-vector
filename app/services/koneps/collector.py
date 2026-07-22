"""KONEPS collector service skeleton."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import sleep
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CrawlJob, HistoricalData, Project
from app.core.time import kst_now
from app.schemas.schemas import CrawlNoticeItem, CrawlRequest
from app.services.koneps import (
    browser_crawl,
    collection,
    html_parsing,
    http_client,
    live_failure,
    openapi,
    parsing,
    persistence,
    scsbid,
)
from app.services.koneps.live_failure import KonepsLiveCollectionError
from app.services.project_similarity import ProjectSimilarityService


# ``format_crawl_error_message`` now lives in ``parsing`` (a pure, no-IO helper)
# so the extracted ``persistence`` write functions can call it without importing
# ``collector`` (which would create an import cycle). It is re-exported here under
# its original name because external callers still import it from this module
# (``app/api/operations.py``).
format_crawl_error_message = parsing.format_crawl_error_message


@dataclass(frozen=True)
class _ScsbidSweepConfig:
    """Immutable per-run configuration for a ScsbidInfoService award sweep.

    Bundles the values resolved once in ``_collect_scsbid_openapi_items``'s setup
    so the extracted sweep/page/item helpers receive an explicit, read-only
    config instead of closing over a dozen locals. Pure data; no behaviour.
    """

    service_key: str
    page_size: int
    max_pages: int
    delay_seconds: float
    begin_token: str
    end_token: str
    collect_reserve_detail: bool
    defer_reserve_detail: bool
    already_have_reserve: frozenset[str]
    reserve_detail_age_cutoff: datetime | None
    checked_recently: frozenset[str]


@dataclass
class _ScsbidSweepState:
    """Mutable accumulators shared across the categories of one award sweep.

    Holds exactly the running collections, counters, and last-seen header fields
    that ``_collect_scsbid_openapi_items`` previously kept as method locals. The
    extracted helpers mutate this in place so dedup sets, counters, and ordering
    are preserved bit-for-bit (no copying, no re-ordering).
    """

    parsed_items: list[dict[str, Any]] = field(default_factory=list)
    seen_notice_numbers: set[str] = field(default_factory=set)
    deferred_reserve_detail: list[dict[str, str]] = field(default_factory=list)
    deferred_reserve_seen: set[tuple[str, str]] = field(default_factory=set)
    reserve_detail_count: int = 0
    reserve_detail_error_count: int = 0
    reserve_detail_reused_count: int = 0
    reserve_detail_deferred_count: int = 0
    reserve_detail_backoff_skipped_count: int = 0
    reserve_detail_recheck_skipped_count: int = 0
    api_call_count: int = 0
    key_variant: str = ""
    last_result_code: str = ""
    last_result_message: str = ""
    category_metadata: list[dict[str, Any]] = field(default_factory=list)


class KonepsCollectorService:
    """Collect KONEPS notices/opening data."""

    # OpenAPI/category mapping constants now live in ``openapi`` (single
    # source). This class-level alias is kept only for backward compatibility
    # with external callers that read ``KonepsCollectorService
    # .SCSBID_OPENAPI_SOURCE_ALIASES``; it references the module constant
    # rather than duplicating its value.
    SCSBID_OPENAPI_SOURCE_ALIASES = openapi.SCSBID_OPENAPI_SOURCE_ALIASES

    # The live-crawl form/selector constants (``HOME_SEARCH_*`` /
    # ``OPENING_RESULT_*``) and the Playwright homepage-scraping cluster now live
    # in ``browser_crawl`` (single source); the collector keeps only thin
    # delegator methods (``_collect_live_items`` / ``_gather_live_page_snapshots``
    # / ``_collect_opening_result_rows``) so tests that monkeypatch those names on
    # the service surface keep working.
    # ``OPENING_RESULT_GRID_ID`` / ``OPENING_RESULT_DATA_LIST_KEY`` /
    # ``HOME_SEARCH_RESULT_TABLE_ID`` now live in ``html_parsing`` (single
    # source); collector references them as ``html_parsing.<CONST>``.
    # ``LIVE_FAILURE_RETRYABLE_CATEGORIES`` and the live-failure classification
    # helpers now live in ``live_failure`` (single source); collector calls
    # them as ``live_failure.<name>`` (and keeps thin delegator methods below
    # for external instance-method callers).

    def collect_notices(
        self,
        request: CrawlRequest,
        db: Session | None = None,
        *,
        defer_reserve_detail: bool = False,
    ) -> dict[str, Any]:
        """Collect KONEPS notices with live mode support and safe fallback.

        ``db`` is optional: when supplied (Celery collection task, sync crawl
        endpoint, backfill script) the scsbid sweep can pre-load which awards
        already have a persisted reserve price and skip the per-notice
        reserve-detail HTTP fetch for them. Callers without a session (smoke
        test) simply re-fetch as before.

        ``defer_reserve_detail`` is decided by the *caller* (only the
        time-limited Celery collection task sets it True): when True the scsbid
        sweep skips the inline per-notice reserve-detail HTTP fetch and surfaces
        the not-yet-settled notices in
        ``metadata['deferred_reserve_detail_notices']`` so the task layer can
        enqueue a bounded ``backfill_scsbid_reserve_detail`` sweep. Synchronous
        callers leave the default False and fetch inline as before.
        """
        normalized_request = self._normalize_request(request)
        job_status = "mock"
        response_metadata = {
            "requested_mode": normalized_request.execution_mode,
            "target_date": normalized_request.target_date,
            "keyword": normalized_request.keyword,
            "max_items": normalized_request.max_items,
        }

        # First-match source dispatch for the two OpenAPI ingestion paths. Both
        # share the identical "call handler → items + metadata → completed" shape,
        # so they collapse into an ordered (predicate, handler) list resolved by
        # source; first match wins, preserving the openapi-before-scsbid order.
        # The live/auto crawl branch below is NOT part of this table — it carries
        # its own try/except fallback-to-mock control flow, so it stays open-coded
        # (동등성 우선 — 무리한 통일 금지).
        openapi_source_handlers = (
            (
                openapi.is_openapi_source,
                lambda: collection.collect_openapi_items(normalized_request),
            ),
            (
                openapi.is_scsbid_openapi_source,
                lambda: self._collect_scsbid_openapi_items(
                    normalized_request, db=db, defer_reserve_detail=defer_reserve_detail
                ),
            ),
        )
        matched_handler = next(
            (
                handler
                for predicate, handler in openapi_source_handlers
                if predicate(normalized_request.source)
            ),
            None,
        )

        if matched_handler is not None:
            live_result = matched_handler()
            items = live_result["items"]
            response_metadata.update(live_result["metadata"])
            job_status = "completed"
        elif normalized_request.execution_mode in {"live", "auto"}:
            try:
                live_result = self._collect_live_items(normalized_request)
                items = live_result["items"]
                response_metadata.update(live_result["metadata"])
                job_status = "completed"
            except (
                Exception
            ) as exc:  # pragma: no cover - fallback path is covered via monkeypatch test
                failure_payload = live_failure.live_failure_payload(
                    exc, stage="live_collection"
                )
                fallback_item_metadata = {
                    "fallback_failure_category": failure_payload["category"],
                    "fallback_failure_stage": failure_payload["stage"],
                    "fallback_retryable": failure_payload["retryable"],
                }
                items = collection.build_mock_items(
                    normalized_request,
                    mode="fallback_mock",
                    fallback_reason=failure_payload["detail"],
                    fallback_metadata=fallback_item_metadata,
                )
                response_metadata.update(
                    {
                        "resolved_mode": "fallback_mock",
                        "fallback_reason": failure_payload["detail"],
                        "fallback_failure_category": failure_payload["category"],
                        "fallback_failure_stage": failure_payload["stage"],
                        "fallback_retryable": failure_payload["retryable"],
                        "live_failure": failure_payload,
                        "live_retry_attempts": failure_payload.get("attempts", []),
                        "search_entry_url": settings.KONEPS_HOME_URL,
                    }
                )
                job_status = "fallback_mock"
        else:
            items = collection.build_mock_items(normalized_request)
            response_metadata.update(
                {
                    "resolved_mode": "mock",
                    "search_entry_url": settings.KONEPS_HOME_URL,
                }
            )

        return {
            "job_status": job_status,
            "source": normalized_request.source,
            "collected_count": len(items),
            "items": items,
            "metadata": response_metadata,
        }

    def create_crawl_job(
        self,
        db: Session,
        request: CrawlRequest,
        *,
        celery_task_id: str | None = None,
    ) -> CrawlJob:
        """Thin delegator to ``persistence.create_crawl_job``.

        The implementation now lives in ``app.services.koneps.persistence``
        (DB write/persist layer). Retained as an instance method because
        external callers (``app/api/operations.py``, ``app/tasks/jobs.py``)
        and tests invoke it through the service surface; the single ``commit``
        and the post-commit realtime event stay byte-identical.
        """
        return persistence.create_crawl_job(db, request, celery_task_id=celery_task_id)

    def persist_crawl_results(
        self,
        db: Session,
        crawl_job: CrawlJob,
        request: CrawlRequest,
        response: dict[str, Any],
        *,
        defer_embeddings: bool = False,
    ) -> CrawlJob:
        """Thin delegator to ``persistence.persist_crawl_results``.

        The implementation now lives in ``app.services.koneps.persistence``
        (DB write/persist layer). Retained as an instance method because
        external callers (``app/api/operations.py``, ``app/tasks/jobs.py``,
        ``scripts/backfill_scsbid_awards.py``) and tests invoke it through the
        service surface. The transaction boundary is preserved exactly: every
        item is staged in the loop and the single ``db.commit`` runs once at the
        end, followed by the post-commit realtime event.
        """
        return persistence.persist_crawl_results(
            db,
            crawl_job,
            request,
            response,
            defer_embeddings=defer_embeddings,
        )

    def mark_crawl_job_failed(
        self, db: Session, crawl_job: CrawlJob, error_message: str
    ) -> CrawlJob:
        """Thin delegator to ``persistence.mark_crawl_job_failed``.

        The implementation now lives in ``app.services.koneps.persistence``
        (DB write/persist layer). Retained as an instance method because
        external callers (``app/tasks/jobs.py``) and tests invoke it through the
        service surface; the single ``db.commit`` stays byte-identical.
        """
        return persistence.mark_crawl_job_failed(db, crawl_job, error_message)

    def _live_collection_error(
        self,
        *,
        stage: str,
        attempts: list[dict[str, Any]],
        original_error: Exception,
    ) -> KonepsLiveCollectionError:
        """Build a live crawl exception with retry context attached.

        Thin delegator kept for external callers that invoke this as an
        instance method; the implementation now lives in ``live_failure``.
        """
        return live_failure.live_collection_error(
            stage=stage,
            attempts=attempts,
            original_error=original_error,
        )

    def _build_live_retry_attempt(
        self,
        *,
        stage: str,
        attempt_index: int,
        exc: Exception,
        final_attempt: bool,
    ) -> dict[str, Any]:
        """Build one retry-attempt payload for operations diagnostics.

        Thin delegator kept for external callers that invoke this as an
        instance method; the implementation now lives in ``live_failure``.
        """
        return live_failure.build_live_retry_attempt(
            stage=stage,
            attempt_index=attempt_index,
            exc=exc,
            final_attempt=final_attempt,
        )

    def _normalize_request(self, request: CrawlRequest) -> CrawlRequest:
        """Thin delegator to ``collection.normalize_request``.

        The implementation now lives in ``app.services.koneps.collection``.
        Retained as an instance method because many tests (scsbid reserve-detail
        defer/reuse, forward-coverage, kst-time) invoke it through the service
        surface as ``service._normalize_request(...)``. The KST default-day
        logic and ``model_copy`` shape stay byte-identical; note the default
        ``target_date`` now resolves ``collection.kst_now`` (not the collector's),
        so the kst-time test patches that target.
        """
        return collection.normalize_request(request)

    def _is_scsbid_openapi_source(self, source: str | None) -> bool:
        """Thin delegator kept for external callers (``app/tasks/jobs.py``).

        The implementation now lives in
        :func:`app.services.koneps.openapi.is_scsbid_openapi_source`.
        """
        return openapi.is_scsbid_openapi_source(source)

    def _collect_scsbid_openapi_items(
        self,
        request: CrawlRequest,
        *,
        db: Session | None = None,
        defer_reserve_detail: bool = False,
    ) -> dict[str, Any]:
        """Collect awarded/opening rows from the KONEPS ScsbidInfoService OpenAPI.

        Sweeps every requested category over a resolved date window with safe
        pagination, deduping notices across the whole run. The legacy single-day,
        single-category behaviour is preserved when the new optional request
        fields are absent.

        When ``db`` is supplied and reserve-detail collection is on, awards whose
        reserve price is already persisted (a settled, immutable value) are not
        re-fetched: a single up-front query loads the set of notice numbers that
        already carry a non-empty ``reserve_prices`` row, and the per-notice HTTP
        fetch is skipped for those (their persisted reserve price is preserved by
        ``persist_crawl_results``). This is what keeps the rolling-window
        scheduled sweep from re-paying the per-notice HTTP cost every run.

        ``defer_reserve_detail`` (set only by the time-limited Celery collection
        task) makes the sweep skip the inline per-notice reserve-detail HTTP
        fetch (and its throttle sleep) entirely: the award list pagination still
        runs inline (cheap, <=90 calls) but each non-settled notice is recorded
        as ``{"notice_number": ..., "category": ...}`` in
        ``metadata['deferred_reserve_detail_notices']`` so the task layer can
        enqueue a bounded ``backfill_scsbid_reserve_detail`` sweep instead. The
        award item is built with an empty ``detail`` so ``persist_crawl_results``
        preserves any already-stored reserve price. When False the inline fetch
        runs exactly as before.
        """
        service_key = str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip()
        if not service_key:
            raise ValueError(
                "KONEPS_OPENAPI_SERVICE_KEY is required for source=koneps-scsbid"
            )

        categories = self._scsbid_categories_for_request(request)
        begin_token, end_token = self._scsbid_date_window(request)
        collect_reserve_detail = bool(request.collect_reserve_detail)

        # One query, not N: load the notice numbers that already have a settled
        # reserve price so we can skip their per-notice reserve-detail HTTP fetch.
        reuse_persisted_reserve = (
            collect_reserve_detail
            and db is not None
            and bool(settings.KONEPS_SCSBID_REUSE_PERSISTED_RESERVE_DETAIL)
        )
        already_have_reserve: set[str] = (
            self._notice_numbers_with_persisted_reserve(db)
            if reuse_persisted_reserve
            else set()
        )

        # Reserve-detail backoff age-gate (only when deferring): a just-opened
        # notice usually has no settled reserve yet, so deferring it now means every
        # 6h sweep re-fetches an empty reserve ("not_settled") and burns
        # ScsbidInfoService rate limit (HTTP 429). Defer a notice only once its
        # opening datetime is at least MIN_SETTLE_AGE_HOURS old. Comparison is by
        # instant: ``coerce_datetime`` returns UTC-aware values and ``kst_now`` is
        # KST-aware (KONEPS frame, per the KST policy), so the cutoff is exact.
        min_settle_age_hours = max(
            0, int(settings.KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS)
        )
        reserve_detail_age_cutoff = (
            kst_now() - timedelta(hours=min_settle_age_hours)
            if defer_reserve_detail and min_settle_age_hours > 0
            else None
        )

        # Reserve-detail recheck backoff (only when deferring): a notice the
        # backfill already fetched and found empty ("not_settled") is stamped with
        # ``reserve_detail_checked_at``. Skip re-deferring it within the recheck
        # window so a permanently-empty notice is re-checked at most once per
        # window instead of every 6h sweep (rate-limit backoff). One query, not N.
        recheck_hours = max(
            0, int(settings.KONEPS_SCSBID_RESERVE_DETAIL_RECHECK_HOURS)
        )
        checked_recently: set[str] = (
            self._notice_numbers_checked_recently(db, recheck_hours)
            if defer_reserve_detail and db is not None and recheck_hours > 0
            else set()
        )

        config = _ScsbidSweepConfig(
            service_key=service_key,
            page_size=scsbid.page_size(request),
            max_pages=scsbid.max_pages(request),
            delay_seconds=scsbid.request_delay_seconds(),
            begin_token=begin_token,
            end_token=end_token,
            collect_reserve_detail=collect_reserve_detail,
            defer_reserve_detail=defer_reserve_detail,
            already_have_reserve=frozenset(already_have_reserve),
            reserve_detail_age_cutoff=reserve_detail_age_cutoff,
            checked_recently=frozenset(checked_recently),
        )
        state = _ScsbidSweepState()

        for category in categories:
            self._sweep_scsbid_category(
                category, state=state, config=config, request=request
            )

        return self._build_scsbid_result(state, config)

    def _build_scsbid_result(
        self, state: "_ScsbidSweepState", config: "_ScsbidSweepConfig"
    ) -> dict[str, Any]:
        """Assemble the final collect result dict from the accumulated sweep state.

        Pure read-only projection of ``state``/``config`` into the exact ``items``
        + ``metadata`` shape ``_collect_scsbid_openapi_items`` returned before the
        decomposition (every counter key/value preserved).
        """
        category_metadata = state.category_metadata
        return {
            "items": state.parsed_items,
            "metadata": {
                "resolved_mode": "scsbid_openapi",
                "openapi_service": "ScsbidInfoService",
                "openapi_operation": (
                    category_metadata[0]["operation"] if category_metadata else None
                ),
                "openapi_endpoint": settings.KONEPS_OPENAPI_SCSBID_INFO_URL,
                "openapi_service_key_variant": state.key_variant,
                "openapi_result_code": state.last_result_code or "00",
                "openapi_result_message": state.last_result_message,
                "openapi_total_count": sum(
                    int(entry["total_count"] or 0) for entry in category_metadata
                ),
                "scsbid_categories": [entry["category"] for entry in category_metadata],
                "scsbid_category_breakdown": category_metadata,
                "scsbid_api_call_count": state.api_call_count,
                "scsbid_collected_count": len(state.parsed_items),
                "reserve_detail_enabled": config.collect_reserve_detail,
                "reserve_detail_collected_count": state.reserve_detail_count,
                "reserve_detail_error_count": state.reserve_detail_error_count,
                "reserve_detail_reused_count": state.reserve_detail_reused_count,
                "reserve_detail_deferred_count": state.reserve_detail_deferred_count,
                "reserve_detail_backoff_skipped_count": (
                    state.reserve_detail_backoff_skipped_count
                ),
                "reserve_detail_recheck_skipped_count": (
                    state.reserve_detail_recheck_skipped_count
                ),
                "deferred_reserve_detail_notices": state.deferred_reserve_detail,
                "query_date_begin": config.begin_token,
                "query_date_end": config.end_token,
                "query_type": "award_registration_datetime",
            },
        }

    def _sweep_scsbid_category(
        self,
        category: str,
        *,
        state: "_ScsbidSweepState",
        config: "_ScsbidSweepConfig",
        request: CrawlRequest,
    ) -> None:
        """Paginate one ScsbidInfoService category, mutating ``state`` in place.

        Runs the page loop (HTTP fetch via ``_fetch_scsbid_page``, per-item
        processing via ``_process_scsbid_raw_item``, then the empty/short-page and
        totalCount stop conditions) and appends this category's breakdown entry to
        ``state.category_metadata``. The page-fetch throttle, stop-condition order,
        and counter updates are unchanged from the original inline loop.
        """
        operation = openapi.scsbid_operation_for_category(category)
        url = f"{settings.KONEPS_OPENAPI_SCSBID_INFO_URL.rstrip('/')}/{operation}"
        category_total_count: int | None = None
        category_pages = 0

        for page_no in range(1, config.max_pages + 1):
            raw_items, body = self._fetch_scsbid_page(
                url,
                page_no=page_no,
                operation=operation,
                config=config,
                state=state,
            )
            if category_total_count is None:
                category_total_count = parsing.safe_int(body.get("totalCount"))
            category_pages += 1

            for raw_item in raw_items:
                self._process_scsbid_raw_item(
                    raw_item,
                    state=state,
                    config=config,
                    category=category,
                    operation=operation,
                    request=request,
                )

            # Stop conditions: empty/short page or totalCount window reached.
            if not raw_items:
                break
            if len(raw_items) < config.page_size:
                break
            if (
                category_total_count is not None
                and page_no * config.page_size >= category_total_count
            ):
                break

        state.category_metadata.append(
            {
                "category": category,
                "operation": operation,
                "total_count": category_total_count,
                "pages_fetched": category_pages,
            }
        )

    def _fetch_scsbid_page(
        self,
        url: str,
        *,
        page_no: int,
        operation: str,
        config: "_ScsbidSweepConfig",
        state: "_ScsbidSweepState",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Fetch and validate one award-list page, returning ``(raw_items, body)``.

        Applies the inter-call throttle (skipped on the very first API call),
        performs the keyed HTTP GET, validates the HTTP status and OpenAPI
        ``resultCode``, and updates ``state.api_call_count`` / ``key_variant`` /
        ``last_result_*``. Error handling (the two ``raise ValueError`` paths) is
        identical to the original inline page loop.
        """
        params = {
            "type": "json",
            "numOfRows": config.page_size,
            "pageNo": page_no,
            "inqryDiv": "1",
            "inqryBgnDt": config.begin_token,
            "inqryEndDt": config.end_token,
        }
        if state.api_call_count > 0 and config.delay_seconds > 0:
            sleep(config.delay_seconds)
        response, state.key_variant = http_client.request_openapi_with_key_variants(
            url,
            params=params,
            service_key=config.service_key,
            operation=operation,
        )
        state.api_call_count += 1
        if response.status_code >= 400:
            raise ValueError(
                f"KONEPS ScsbidInfoService HTTP {response.status_code} for "
                f"{operation}: {response.text[:300]} "
                f"Tried service key variants: {state.key_variant}."
            )
        payload = http_client.load_openapi_json(response)
        result_code, result_message = http_client.check_result_code(
            payload, source="ScsbidInfoService returned"
        )
        state.last_result_code = result_code or state.last_result_code
        state.last_result_message = result_message or state.last_result_message

        body = openapi.openapi_body(payload)
        raw_items = openapi.openapi_item_list(body)
        return raw_items, body

    def _process_scsbid_raw_item(
        self,
        raw_item: dict[str, Any],
        *,
        state: "_ScsbidSweepState",
        config: "_ScsbidSweepConfig",
        category: str,
        operation: str,
        request: CrawlRequest,
    ) -> None:
        """Process one raw award row, mutating ``state`` in place.

        Preserves the exact original ordering and conditions: notice presence /
        pre-build dedup, then the reserve-detail branch (reused -> deferred with
        age-gate -> inline fetch), then ``build_scsbid_award_item`` plus the
        post-build dedup and append. Counter increments, set adds, the throttle
        sleep before the inline fetch, and the ``api_call_count`` bump on a
        successful inline fetch all happen at the same points as before.
        """
        detail: dict[str, Any] = {}
        notice_number = str(raw_item.get("bidNtceNo") or "").strip()
        if not notice_number:
            return
        if notice_number in state.seen_notice_numbers:
            return
        if config.collect_reserve_detail:
            if notice_number in config.already_have_reserve:
                # Reserve price already settled & persisted; leave
                # ``detail`` empty so persist preserves the stored
                # value instead of re-fetching it over HTTP.
                state.reserve_detail_reused_count += 1
            elif config.defer_reserve_detail:
                # Time-limited Celery collection path: skip the inline
                # per-notice fetch (and its throttle sleep) and queue
                # the notice for a bounded async backfill instead.
                # ``detail`` stays empty so persist preserves any
                # already-stored reserve price.
                #
                # Age-gate (rate-limit backoff): only defer once the
                # notice's opening datetime is old enough to have a
                # settled reserve. A just-opened/future notice is skipped
                # this sweep (counted separately) and re-checked next
                # sweep — within the 3-day lookback it ages in and is
                # fetched exactly once. Unknown opening => defer (the gate
                # cannot apply, so we try). Uses the SAME opening fields
                # as ``_build_scsbid_award_item``'s closing_at.
                opened_at = parsing.coerce_datetime(
                    raw_item.get("rlOpengDt")
                    or raw_item.get("fnlSucsfDate")
                    or raw_item.get("rgstDt")
                )
                if notice_number in config.checked_recently:
                    # Recheck-gate (rate-limit backoff): the backfill already
                    # fetched this notice and found no settled reserve within the
                    # recheck window. Skip it this sweep so a permanently-empty
                    # notice is re-checked at most once per window, not every 6h.
                    # Complementary to the age-gate (which defers before settling;
                    # this backs off after a confirmed-empty fetch).
                    state.reserve_detail_recheck_skipped_count += 1
                elif (
                    config.reserve_detail_age_cutoff is not None
                    and opened_at is not None
                    and opened_at > config.reserve_detail_age_cutoff
                ):
                    state.reserve_detail_backoff_skipped_count += 1
                else:
                    dedupe_key = (notice_number, str(category or ""))
                    if dedupe_key not in state.deferred_reserve_seen:
                        state.deferred_reserve_seen.add(dedupe_key)
                        state.deferred_reserve_detail.append(
                            {
                                "notice_number": notice_number,
                                "category": str(category or ""),
                            }
                        )
                        state.reserve_detail_deferred_count += 1
            else:
                try:
                    if config.delay_seconds > 0:
                        sleep(config.delay_seconds)
                    detail = self._fetch_scsbid_reserve_detail(
                        raw_item,
                        category=category,
                        service_key=config.service_key,
                    )
                    state.api_call_count += 1
                    if detail.get("reserve_prices"):
                        state.reserve_detail_count += 1
                except Exception as exc:
                    state.reserve_detail_error_count += 1
                    detail = {"reserve_detail_error": str(exc)}

        parsed_item = scsbid.build_scsbid_award_item(
            raw_item,
            detail=detail,
            request=request,
            operation=operation,
            category=category,
        )
        if parsed_item is None:
            return
        notice_number = str(parsed_item["notice_number"])
        if notice_number in state.seen_notice_numbers:
            return
        state.seen_notice_numbers.add(notice_number)
        state.parsed_items.append(parsed_item)

    def _notice_numbers_with_persisted_reserve(self, db: Session) -> set[str]:
        """Thin delegator to ``persistence.notice_numbers_with_persisted_reserve``.

        The implementation now lives in ``app.services.koneps.persistence``.
        Retained as an instance method (called via ``self.`` internally) because
        ``test_scsbid_reserve_detail_reuse`` monkeypatches it on the class to
        assert the persisted-reserve set is loaded with a single query.
        """
        return persistence.notice_numbers_with_persisted_reserve(db)

    def _notice_numbers_checked_recently(
        self, db: Session, within_hours: int
    ) -> set[str]:
        """Thin delegator to ``persistence.notice_numbers_checked_recently``.

        Notice numbers whose deferred reserve-detail fetch was stamped
        ``reserve_detail_checked_at`` within ``within_hours``. Used by the defer
        path to back off permanently-empty notices. Kept as an instance method
        (called via ``self.``) so tests can monkeypatch it on the class.
        """
        return persistence.notice_numbers_checked_recently(db, within_hours)

    @staticmethod
    def _has_persisted_reserve_prices(historical_record: HistoricalData) -> bool:
        """Thin delegator to ``scsbid.has_persisted_reserve_prices``.

        Kept as an instance method because external callers
        (``app/tasks/jobs.py``) and internal persist paths invoke it via the
        service. The implementation now lives in ``scsbid``.
        """
        return scsbid.has_persisted_reserve_prices(historical_record)

    def _scsbid_categories_for_request(self, request: CrawlRequest) -> list[str]:
        """Resolve the ordered, de-duplicated category list for a scsbid sweep.

        Priority: ``request.categories`` > ``[request.category]`` > legacy default
        (empty category, which maps to the 용역 operation). The legacy single
        category path is preserved when ``categories`` is absent.
        """
        if request.categories:
            raw_categories = [str(value) for value in request.categories]
        elif request.category:
            raw_categories = [str(request.category)]
        else:
            raw_categories = [str(request.category or "")]

        resolved: list[str] = []
        seen: set[str] = set()
        for value in raw_categories:
            normalized = value.strip().lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(normalized)
        return resolved or [""]

    def _scsbid_date_window(self, request: CrawlRequest) -> tuple[str, str]:
        """Thin delegator to ``scsbid.date_window``.

        Kept as an instance method because external callers (timezone /
        forward-coverage tests) invoke it via the service. The KST-anchored
        implementation now lives in ``scsbid``.
        """
        return scsbid.date_window(request)

    def _fetch_scsbid_reserve_detail(
        self,
        raw_item: dict[str, Any],
        *,
        category: str | None,
        service_key: str,
    ) -> dict[str, Any]:
        """Fetch and summarize reserve-price detail rows for one awarded notice."""
        operation = openapi.scsbid_reserve_detail_operation_for_category(category)
        notice_number = str(raw_item.get("bidNtceNo") or "").strip()
        if not notice_number:
            return {}

        url = f"{settings.KONEPS_OPENAPI_SCSBID_INFO_URL.rstrip('/')}/{operation}"
        params = {
            "type": "json",
            "numOfRows": settings.KONEPS_SCSBID_DETAIL_PAGE_SIZE,
            "pageNo": 1,
            "inqryDiv": "2",
            "bidNtceNo": notice_number,
        }
        response, key_variant = http_client.request_openapi_with_key_variants(
            url,
            params=params,
            service_key=service_key,
            operation=operation,
        )
        if response.status_code >= 400:
            raise ValueError(
                f"KONEPS ScsbidInfoService HTTP {response.status_code} for {operation}: "
                f"{response.text[:300]} Tried service key variants: {key_variant}."
            )

        payload = http_client.load_openapi_json(response)
        result_code, result_message = http_client.check_result_code(
            payload, source="ScsbidInfoService returned"
        )

        body = openapi.openapi_body(payload)
        rows = openapi.openapi_item_list(body)
        detail = openapi.summarize_scsbid_reserve_detail(rows)
        detail.update(
            {
                "reserve_detail_operation": operation,
                "reserve_detail_result_code": result_code or "00",
                "reserve_detail_result_message": result_message,
                "reserve_detail_total_count": parsing.safe_int(body.get("totalCount")),
            }
        )
        return detail

    def _collect_live_items(self, request: CrawlRequest) -> dict[str, Any]:
        """Thin delegator to ``browser_crawl.collect_live_items``.

        The Playwright homepage-scraping implementation now lives in
        ``app.services.koneps.browser_crawl``. Retained as an instance method
        (and still entered from ``collect_notices`` via ``self.``) because
        ``test_operations`` monkeypatches it on the service surface to drive the
        live-collection fallback path. ``collect_live_items`` receives ``self``
        so it dispatches the two inner steps (``_gather_live_page_snapshots`` /
        ``_collect_opening_result_rows``) back through the service, keeping those
        monkeypatch hooks honored.
        """
        return browser_crawl.collect_live_items(self, request)

    def _gather_live_page_snapshots(
        self, request: CrawlRequest
    ) -> list[dict[str, Any]]:
        """Thin delegator to ``browser_crawl.gather_live_page_snapshots``.

        The implementation now lives in ``app.services.koneps.browser_crawl``.
        Retained as an instance method because ``test_operations`` monkeypatches
        it on the service (and class) surface to substitute live page snapshots
        in the live-collection and fallback paths.
        """
        return browser_crawl.gather_live_page_snapshots(request)

    def _collect_opening_result_rows(
        self, request: CrawlRequest
    ) -> list[dict[str, Any]]:
        """Thin delegator to ``browser_crawl.collect_opening_result_rows``.

        The implementation now lives in ``app.services.koneps.browser_crawl``.
        Retained as an instance method because ``test_operations`` monkeypatches
        it on the service surface to substitute opening-result rows (and assert
        opening-result enrichment / failure classification).
        """
        return browser_crawl.collect_opening_result_rows(request)

    @staticmethod
    def _split_business_type_cell(raw: str | None) -> tuple[str | None, str | None]:
        """Thin delegator to ``html_parsing.split_business_type_cell``.

        Kept for backward compatibility with external callers/tests that invoke
        ``service._split_business_type_cell(...)``. The implementation lives in
        ``html_parsing`` as a pure helper.
        """
        return html_parsing.split_business_type_cell(raw)

    def _parse_live_html(
        self,
        html: str,
        request: CrawlRequest,
        page_url: str | None = None,
        page_number: int = 1,
        detail_pages: dict[str, dict[str, str]] | None = None,
    ) -> list[CrawlNoticeItem]:
        """Thin delegator to ``html_parsing.parse_live_html``.

        Kept for backward compatibility with external callers/tests that invoke
        ``service._parse_live_html(...)``. The implementation lives in
        ``html_parsing`` as a pure helper.
        """
        return html_parsing.parse_live_html(
            html,
            request,
            page_url=page_url,
            page_number=page_number,
            detail_pages=detail_pages,
        )

    def fetch_detail_html_payload(self, source_url: str) -> dict[str, str | None]:
        """Fetch + parse a single KONEPS detail page, returning the business-type fields.

        Thin delegator to ``http_client.fetch_detail_html_payload``; kept as an
        instance method so external callers can use it as a bound callable
        (``business_type_enrichment``) or invoke it on a service instance
        (``scripts/backfill_business_type.py``).
        """
        return http_client.fetch_detail_html_payload(source_url)

    def _resolve_project_for_item(
        self,
        db: Session,
        *,
        item: dict[str, Any],
        request: CrawlRequest,
        historical_record: HistoricalData,
        project_similarity: ProjectSimilarityService,
        defer_embeddings: bool = False,
    ) -> tuple[Project | None, bool]:
        """Thin delegator to ``persistence.resolve_project_for_item``.

        The implementation now lives in ``app.services.koneps.persistence``.
        Retained as an instance method because existing tests exercise it
        through the service surface (``service._resolve_project_for_item``).
        """
        return persistence.resolve_project_for_item(
            db,
            item=item,
            request=request,
            historical_record=historical_record,
            project_similarity=project_similarity,
            defer_embeddings=defer_embeddings,
        )

    def _find_matching_project(
        self,
        db: Session,
        *,
        item: dict[str, Any],
        request: CrawlRequest,
    ) -> Project | None:
        """Thin delegator to ``persistence.find_matching_project``.

        The implementation now lives in ``app.services.koneps.persistence``.
        Retained as an instance method because existing regression/perf tests
        exercise it through the service surface
        (``service._find_matching_project``).
        """
        return persistence.find_matching_project(db, item=item, request=request)

    def _update_project_from_item(
        self, project: Project, *, item: dict[str, Any], request: CrawlRequest
    ) -> None:
        """Thin delegator to ``persistence.update_project_from_item``.

        The implementation now lives in ``app.services.koneps.persistence``.
        Retained as an instance method because existing tests exercise it
        through the service surface (``service._update_project_from_item``).
        """
        persistence.update_project_from_item(project, item=item, request=request)

    def _normalize_notice_number(self, value: Any) -> str:
        """Delegate notice-number normalization to the pure parsing module.

        Retained as a thin instance method because existing tests exercise it
        through the service surface (``service._normalize_notice_number``).
        """
        return parsing.normalize_notice_number(value)
