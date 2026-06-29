"""KONEPS collector service skeleton."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import ceil
from time import sleep
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CrawlJob, HistoricalData, Project
from app.core.time import kst_now, utc_now
from app.schemas.schemas import CrawlNoticeItem, CrawlRequest
from app.services.koneps import (
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

    HOME_SEARCH_KEYWORD_ID = "mf_wfm_container_wq_uuid_925_wq_uuid_934_searchKeyword"
    HOME_SEARCH_BUTTON_ID = (
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_btnBidPbancDtlSrch"
    )
    HOME_SEARCH_TYPE_RADIO_ID = (
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_rbxSrchType_input_0"
    )
    HOME_SEARCH_START_DATE_ID = "wq_uuid_1239_ibxStrDay"
    HOME_SEARCH_END_DATE_ID = "wq_uuid_1239_ibxEndDay"
    HOME_SEARCH_PAGER_ID_PREFIX = "mf_wfm_container_pglList_page_"
    HOME_SEARCH_DEFAULT_PAGE_SIZE = 10
    HOME_SEARCH_CATEGORY_IDS = {
        "goods": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_1",
        "물품": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_1",
        "general-service": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_2",
        "service": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_2",
        "일반용역": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_2",
        "technical-service": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_3",
        "기술용역": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_3",
        "construction": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_4",
        "공사": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_4",
        "other": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_5",
        "기타": "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_5",
    }
    HOME_SEARCH_CATEGORY_GROUP_IDS = [
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_0",
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_1",
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_2",
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_3",
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_4",
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_sbxUntyBsneSe_input_5",
    ]
    OPENING_RESULT_MENU_ID = (
        "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_genMenu2_4_genMenu3_0_btnMenu3"
    )
    OPENING_RESULT_BID_NO_ID = "mf_wfm_container_ibxBidPbancNo"
    OPENING_RESULT_TITLE_ID = "mf_wfm_container_wq_uuid_4242"
    OPENING_RESULT_START_DATE_ID = "wq_uuid_4247_ibxStrDay"
    OPENING_RESULT_END_DATE_ID = "wq_uuid_4247_ibxEndDay"
    OPENING_RESULT_SEARCH_BUTTON_ID = "mf_wfm_container_btnS0001"
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

        if openapi.is_openapi_source(normalized_request.source):
            live_result = self._collect_openapi_items(normalized_request)
            items = live_result["items"]
            response_metadata.update(live_result["metadata"])
            job_status = "completed"
        elif openapi.is_scsbid_openapi_source(normalized_request.source):
            live_result = self._collect_scsbid_openapi_items(
                normalized_request, db=db, defer_reserve_detail=defer_reserve_detail
            )
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
                items = self._build_mock_items(
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
            items = self._build_mock_items(normalized_request)
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

    def _close_browser_context(self, context: Any) -> None:
        """Best-effort browser context cleanup without masking crawl failures."""
        try:
            context.close()
        except Exception:
            return

    def _normalize_request(self, request: CrawlRequest) -> CrawlRequest:
        """Normalize optional request fields for downstream collection logic."""
        normalized_source = (request.source or "koneps").strip().lower()
        normalized_category = (
            request.category.strip().lower() if request.category else "general"
        )
        normalized_keyword = request.keyword.strip() if request.keyword else "AI"
        # KONEPS dates are KST: default "today" must be the Korean calendar day,
        # not the UTC one (which lags KST by 9h — wrong day for KST 00:00-09:00).
        normalized_target_date = request.target_date or kst_now().date().isoformat()
        normalized_mode = request.execution_mode.strip().lower()
        configured_max_items = (
            settings.KONEPS_OPENAPI_MAX_ITEMS
            if openapi.is_openapi_source(normalized_source)
            or openapi.is_scsbid_openapi_source(normalized_source)
            else settings.KONEPS_MAX_ITEMS
        )
        normalized_max_items = min(request.max_items, configured_max_items)

        return request.model_copy(
            update={
                "source": normalized_source,
                "category": normalized_category,
                "keyword": normalized_keyword,
                "target_date": normalized_target_date,
                "execution_mode": normalized_mode,
                "max_items": normalized_max_items,
            }
        )

    def _is_scsbid_openapi_source(self, source: str | None) -> bool:
        """Thin delegator kept for external callers (``app/tasks/jobs.py``).

        The implementation now lives in
        :func:`app.services.koneps.openapi.is_scsbid_openapi_source`.
        """
        return openapi.is_scsbid_openapi_source(source)

    def _collect_openapi_items(self, request: CrawlRequest) -> dict[str, Any]:
        """Collect notice rows from the public KONEPS BidPublicInfoService OpenAPI."""
        service_key = str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip()
        if not service_key:
            raise ValueError(
                "KONEPS_OPENAPI_SERVICE_KEY is required for source=koneps-openapi"
            )

        operation = openapi.openapi_operation_for_category(request.category)
        date_token = openapi.openapi_date_token(request.target_date)
        page_size = max(1, min(int(request.max_items or 1), 999))
        url = f"{settings.KONEPS_OPENAPI_BID_PUBLIC_INFO_URL.rstrip('/')}/{operation}"
        params = {
            "type": "json",
            "numOfRows": page_size,
            "pageNo": 1,
            "inqryDiv": "1",
            "inqryBgnDt": f"{date_token}0000",
            "inqryEndDt": f"{date_token}2359",
        }

        response, key_variant = http_client.request_openapi_with_key_variants(
            url,
            params=params,
            service_key=service_key,
            operation=operation,
        )
        if response.status_code >= 400:
            raise ValueError(
                f"KONEPS OpenAPI HTTP {response.status_code} for {operation}: "
                f"{response.text[:300]} Tried service key variants: {key_variant}."
            )
        payload = http_client.load_openapi_json(response)
        header = openapi.openapi_header(payload)
        result_code = str(header.get("resultCode") or "").strip()
        result_message = str(header.get("resultMsg") or "").strip()
        if result_code and result_code not in {"00", "03"}:
            raise ValueError(
                f"KONEPS OpenAPI returned resultCode={result_code}: {result_message or 'unknown error'}"
            )

        body = openapi.openapi_body(payload)
        raw_items = openapi.openapi_item_list(body)
        parsed_items: list[dict[str, Any]] = []
        seen_notice_numbers: set[str] = set()
        for raw_item in raw_items:
            parsed_item = openapi.build_openapi_notice_item(
                raw_item,
                request=request,
                operation=operation,
            )
            if parsed_item is None:
                continue
            notice_number = str(parsed_item["notice_number"])
            if notice_number in seen_notice_numbers:
                continue
            seen_notice_numbers.add(notice_number)
            parsed_items.append(parsed_item)
            if len(parsed_items) >= request.max_items:
                break

        return {
            "items": parsed_items,
            "metadata": {
                "resolved_mode": "openapi",
                "openapi_service": "BidPublicInfoService",
                "openapi_operation": operation,
                "openapi_endpoint": settings.KONEPS_OPENAPI_BID_PUBLIC_INFO_URL,
                "openapi_service_key_variant": key_variant,
                "openapi_result_code": result_code or "00",
                "openapi_result_message": result_message,
                "openapi_total_count": parsing.safe_int(body.get("totalCount")),
                "openapi_page_no": parsing.safe_int(body.get("pageNo")),
                "openapi_num_of_rows": parsing.safe_int(body.get("numOfRows")),
                "query_date": date_token,
                "query_type": "registration_datetime",
            },
        }

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
        header = openapi.openapi_header(payload)
        result_code = str(header.get("resultCode") or "").strip()
        result_message = str(header.get("resultMsg") or "").strip()
        if result_code and result_code not in {"00", "03"}:
            raise ValueError(
                f"KONEPS ScsbidInfoService returned resultCode={result_code}: "
                f"{result_message or 'unknown error'}"
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
                if (
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
            "numOfRows": 100,
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
        header = openapi.openapi_header(payload)
        result_code = str(header.get("resultCode") or "").strip()
        result_message = str(header.get("resultMsg") or "").strip()
        if result_code and result_code not in {"00", "03"}:
            raise ValueError(
                f"KONEPS ScsbidInfoService returned resultCode={result_code}: "
                f"{result_message or 'unknown error'}"
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
        """Collect live KONEPS items via the public homepage search flow."""
        page_snapshots = self._gather_live_page_snapshots(request)
        parsed_items: list[dict[str, Any]] = []
        seen_notice_numbers: set[str] = set()

        for snapshot in page_snapshots:
            snapshot_items = self._parse_live_html(
                html=snapshot["html"],
                request=request,
                page_url=snapshot["url"],
                page_number=snapshot["page_number"],
                detail_pages=snapshot.get("detail_pages"),
            )
            for item in snapshot_items:
                if item.notice_number in seen_notice_numbers:
                    continue
                seen_notice_numbers.add(item.notice_number)
                parsed_items.append(item.model_dump(mode="json"))
                if len(parsed_items) >= request.max_items:
                    break
            if len(parsed_items) >= request.max_items:
                break

        if not parsed_items:
            raise ValueError(
                "No notice items could be parsed from the live KONEPS page"
            )

        opening_result_metadata = {
            "opening_result_grid_id": html_parsing.OPENING_RESULT_GRID_ID,
            "opening_result_row_count": 0,
            "opening_result_enriched_count": 0,
        }
        try:
            opening_rows = self._collect_opening_result_rows(request)
            (
                parsed_items,
                opening_result_metadata,
            ) = html_parsing.merge_opening_result_rows(parsed_items, opening_rows)
        except Exception as exc:
            failure_payload = live_failure.live_failure_payload(
                exc, stage="opening_result"
            )
            opening_result_metadata.update(
                {
                    "opening_result_error": failure_payload["detail"],
                    "opening_result_failure_category": failure_payload["category"],
                    "opening_result_failure_stage": failure_payload["stage"],
                    "opening_result_retryable": failure_payload["retryable"],
                    "opening_result_failure": failure_payload,
                    "opening_result_retry_attempts": failure_payload.get(
                        "attempts", []
                    ),
                }
            )

        return {
            "items": parsed_items[: request.max_items],
            "metadata": {
                "resolved_mode": "live",
                "page_count": len(page_snapshots),
                "search_entry_url": settings.KONEPS_HOME_URL,
                "result_table_id": html_parsing.HOME_SEARCH_RESULT_TABLE_ID,
                "pager_id_prefix": self.HOME_SEARCH_PAGER_ID_PREFIX,
                **opening_result_metadata,
            },
        }

    def _collect_opening_result_rows(
        self, request: CrawlRequest
    ) -> list[dict[str, Any]]:
        """Collect opening-result rows from 개찰결과분류조회 using the live SPA page."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.KONEPS_HEADLESS)
            try:
                last_error: Exception | None = None
                attempts: list[dict[str, Any]] = []
                for attempt in range(settings.KONEPS_RETRY_COUNT + 1):
                    context = browser.new_context(
                        user_agent=settings.KONEPS_USER_AGENT,
                        locale="ko-KR",
                    )
                    page = context.new_page()
                    try:
                        page.set_default_timeout(settings.KONEPS_TIMEOUT_MS)
                        self._open_opening_result_page(page, request)
                        rows = self._read_opening_result_rows(page)
                        self._close_browser_context(context)
                        return rows
                    except (
                        Exception
                    ) as exc:  # pragma: no cover - exercised by live browser only
                        last_error = exc
                        self._close_browser_context(context)
                        final_attempt = attempt >= settings.KONEPS_RETRY_COUNT
                        attempts.append(
                            self._build_live_retry_attempt(
                                stage="opening_result",
                                attempt_index=attempt,
                                exc=exc,
                                final_attempt=final_attempt,
                            )
                        )
                        if final_attempt:
                            raise self._live_collection_error(
                                stage="opening_result",
                                attempts=attempts,
                                original_error=exc,
                            ) from exc
                        sleep(live_failure.retry_delay_seconds(attempt))

                if last_error:
                    raise self._live_collection_error(
                        stage="opening_result",
                        attempts=attempts,
                        original_error=last_error,
                    ) from last_error
                raise RuntimeError("Failed to collect KONEPS opening-result rows")
            finally:
                browser.close()

    def _open_opening_result_page(self, page: Any, request: CrawlRequest) -> None:
        """Navigate to 개찰결과분류조회 and execute a live search."""
        page.goto(settings.KONEPS_HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(settings.KONEPS_REQUEST_DELAY_MS)

        menu_clicked = page.evaluate(
            """
            (menuId) => {
                const menu = document.getElementById(menuId);
                if (!menu) {
                    return false;
                }
                menu.click();
                return true;
            }
            """,
            self.OPENING_RESULT_MENU_ID,
        )
        if not menu_clicked:
            raise ValueError("KONEPS opening-result menu could not be located")

        page.wait_for_selector(f"#{self.OPENING_RESULT_BID_NO_ID}")
        page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)

        target_date = request.target_date.replace("-", "/")
        self._set_input_value(page, self.OPENING_RESULT_START_DATE_ID, target_date)
        self._set_input_value(page, self.OPENING_RESULT_END_DATE_ID, target_date)
        if request.keyword:
            self._set_input_value(page, self.OPENING_RESULT_TITLE_ID, request.keyword)

        search_button = page.locator(f"#{self.OPENING_RESULT_SEARCH_BUTTON_ID}")
        if search_button.count() == 0:
            raise ValueError("KONEPS opening-result search button could not be located")

        search_button.click()
        page.wait_for_selector(f"#{html_parsing.OPENING_RESULT_GRID_ID}")
        page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)

    def _read_opening_result_rows(self, page: Any) -> list[dict[str, Any]]:
        """Read opening-result rows from the page's WebSquare data list."""
        rows = page.evaluate(
            """
            (dataListKey) => {
                const dataList = window[dataListKey];
                if (!dataList || typeof dataList.getAllJSON !== 'function') {
                    return [];
                }
                return dataList.getAllJSON();
            }
            """,
            html_parsing.OPENING_RESULT_DATA_LIST_KEY,
        )
        return [
            normalized_row
            for normalized_row in (
                html_parsing.normalize_opening_result_row(row) for row in rows or []
            )
            if normalized_row.get("notice_number")
        ]

    def _gather_live_page_snapshots(
        self, request: CrawlRequest
    ) -> list[dict[str, Any]]:
        """Search the public KONEPS homepage and gather result page snapshots."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.KONEPS_HEADLESS)
            try:
                last_error: Exception | None = None
                attempts: list[dict[str, Any]] = []
                for attempt in range(settings.KONEPS_RETRY_COUNT + 1):
                    context = browser.new_context(
                        user_agent=settings.KONEPS_USER_AGENT,
                        locale="ko-KR",
                    )
                    page = context.new_page()
                    try:
                        page.set_default_timeout(settings.KONEPS_TIMEOUT_MS)
                        self._open_live_search_results(page, request)
                        snapshots = self._collect_result_page_snapshots(page, request)
                        self._close_browser_context(context)
                        return snapshots
                    except (
                        Exception
                    ) as exc:  # pragma: no cover - exercised indirectly in fallback path
                        last_error = exc
                        self._close_browser_context(context)
                        final_attempt = attempt >= settings.KONEPS_RETRY_COUNT
                        attempts.append(
                            self._build_live_retry_attempt(
                                stage="notice_search",
                                attempt_index=attempt,
                                exc=exc,
                                final_attempt=final_attempt,
                            )
                        )
                        if final_attempt:
                            raise self._live_collection_error(
                                stage="notice_search",
                                attempts=attempts,
                                original_error=exc,
                            ) from exc
                        sleep(live_failure.retry_delay_seconds(attempt))

                if last_error:
                    raise self._live_collection_error(
                        stage="notice_search",
                        attempts=attempts,
                        original_error=last_error,
                    ) from last_error
                raise RuntimeError("Failed to gather KONEPS live page snapshots")
            finally:
                browser.close()

    def _open_live_search_results(self, page: Any, request: CrawlRequest) -> None:
        """Navigate to the public search form and execute an 입찰공고 search."""
        page.goto(settings.KONEPS_HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(settings.KONEPS_REQUEST_DELAY_MS)

        self._set_input_value(page, self.HOME_SEARCH_KEYWORD_ID, request.keyword)
        self._set_checked_state(page, self.HOME_SEARCH_TYPE_RADIO_ID, True)
        self._apply_business_type_filter(page, request.category)
        self._set_input_value(
            page, self.HOME_SEARCH_START_DATE_ID, request.target_date.replace("-", "/")
        )
        self._set_input_value(
            page, self.HOME_SEARCH_END_DATE_ID, request.target_date.replace("-", "/")
        )

        search_button = page.locator(f"#{self.HOME_SEARCH_BUTTON_ID}")
        if search_button.count() == 0:
            raise ValueError("KONEPS public search button could not be located")

        search_button.click()
        page.wait_for_selector(f"#{html_parsing.HOME_SEARCH_RESULT_TABLE_ID}")
        page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)

    def _collect_result_page_snapshots(
        self, page: Any, request: CrawlRequest
    ) -> list[dict[str, Any]]:
        """Collect HTML snapshots for the required number of result pages."""
        expected_pages = max(
            1, ceil(request.max_items / self.HOME_SEARCH_DEFAULT_PAGE_SIZE)
        )
        snapshots: list[dict[str, Any]] = []

        for page_number in range(1, expected_pages + 1):
            page.wait_for_selector(f"#{html_parsing.HOME_SEARCH_RESULT_TABLE_ID}")
            page.wait_for_timeout(settings.KONEPS_REQUEST_DELAY_MS)
            snapshots.append(
                {
                    "page_number": page_number,
                    "url": page.url,
                    "html": page.content(),
                    "detail_pages": self._collect_detail_page_snapshots(page),
                }
            )

            next_page_number = page_number + 1
            if next_page_number > expected_pages:
                break
            if not self._go_to_result_page(page, next_page_number):
                break

        return snapshots

    def _collect_detail_page_snapshots(self, page: Any) -> dict[str, dict[str, str]]:
        """Open each visible detail link in a new tab and capture its URL and HTML."""
        detail_links = page.locator(
            f"#{html_parsing.HOME_SEARCH_RESULT_TABLE_ID} a[id$='btnOpenKonepsInfo']"
        )
        detail_page_snapshots: dict[str, dict[str, str]] = {}

        for index in range(detail_links.count()):
            detail_link = detail_links.nth(index)
            detail_action_id = detail_link.get_attribute("id")
            if not detail_action_id:
                continue

            with page.context.expect_page() as detail_page_info:
                detail_link.click()

            detail_page = detail_page_info.value
            detail_page.wait_for_load_state("domcontentloaded")
            detail_page.wait_for_timeout(settings.KONEPS_REQUEST_DELAY_MS)
            detail_page_snapshots[detail_action_id] = {
                "url": detail_page.url,
                "html": detail_page.content(),
            }
            detail_page.close()

        return detail_page_snapshots

    def _go_to_result_page(self, page: Any, page_number: int) -> bool:
        """Move to a numbered KONEPS result page when available."""
        pager = page.locator(f"#{self.HOME_SEARCH_PAGER_ID_PREFIX}{page_number}")
        if pager.count() == 0:
            return False

        pager.click()
        page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)
        page.wait_for_selector(f"#{html_parsing.HOME_SEARCH_RESULT_TABLE_ID}")
        return True

    def _set_input_value(self, page: Any, input_id: str, value: str) -> bool:
        """Set a text input value by DOM id using JS events compatible with the KONEPS page."""
        return bool(
            page.evaluate(
                """
                ([fieldId, fieldValue]) => {
                    const input = document.getElementById(fieldId);
                    if (!input) {
                        return false;
                    }
                    input.focus();
                    input.value = fieldValue;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                """,
                [input_id, value],
            )
        )

    def _set_checked_state(self, page: Any, input_id: str, checked: bool) -> bool:
        """Set a checkbox or radio input state by DOM id."""
        return bool(
            page.evaluate(
                """
                ([fieldId, checkedState]) => {
                    const input = document.getElementById(fieldId);
                    if (!input) {
                        return false;
                    }
                    input.checked = checkedState;
                    input.dispatchEvent(new Event('click', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                """,
                [input_id, checked],
            )
        )

    @staticmethod
    def _split_business_type_cell(raw: str | None) -> tuple[str | None, str | None]:
        """Thin delegator to ``html_parsing.split_business_type_cell``.

        Kept for backward compatibility with external callers/tests that invoke
        ``service._split_business_type_cell(...)``. The implementation lives in
        ``html_parsing`` as a pure helper.
        """
        return html_parsing.split_business_type_cell(raw)

    def _apply_business_type_filter(self, page: Any, category: str) -> None:
        """Apply a known 업무구분 filter when the incoming category maps cleanly to KONEPS options."""
        filter_id = self.HOME_SEARCH_CATEGORY_IDS.get(category)
        if not filter_id:
            return

        for category_id in self.HOME_SEARCH_CATEGORY_GROUP_IDS:
            self._set_checked_state(page, category_id, False)
        self._set_checked_state(page, filter_id, True)

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

    def _build_mock_items(
        self,
        request: CrawlRequest,
        mode: str = "mock",
        fallback_reason: str | None = None,
        fallback_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build deterministic mock notice data while live crawling is under construction."""
        closing_at = utc_now() + timedelta(days=3)
        target_stamp = request.target_date.replace("-", "")
        source_root = settings.KONEPS_BASE_URL.rstrip("/")
        fallback_metadata = fallback_metadata or {}

        mock_items = [
            CrawlNoticeItem(
                notice_number=f"KONEPS-{target_stamp}-001",
                title=f"{request.keyword} {request.category} 유지관리 용역",
                base_amount=125000000.0,
                estimated_amount=121500000.0,
                closing_at=closing_at,
                business_type=request.category,
                region="전국",
                license_codes=["SW001", "IT002"],
                source_url=f"{source_root}/notice/{target_stamp}/001",
                metadata={
                    "mode": mode,
                    "target_date": request.target_date,
                    "request_delay_ms": settings.KONEPS_REQUEST_DELAY_MS,
                    "fallback_reason": fallback_reason,
                    "search_entry_url": settings.KONEPS_HOME_URL,
                    **fallback_metadata,
                },
            ),
            CrawlNoticeItem(
                notice_number=f"KONEPS-{target_stamp}-002",
                title=f"{request.keyword} 데이터 분석 플랫폼 구축",
                base_amount=98000000.0,
                estimated_amount=95060000.0,
                closing_at=closing_at + timedelta(hours=6),
                business_type=request.category,
                region="서울",
                license_codes=["DATA001"],
                source_url=f"{source_root}/notice/{target_stamp}/002",
                metadata={
                    "mode": mode,
                    "target_date": request.target_date,
                    "request_delay_ms": settings.KONEPS_REQUEST_DELAY_MS,
                    "fallback_reason": fallback_reason,
                    "search_entry_url": settings.KONEPS_HOME_URL,
                    **fallback_metadata,
                },
            ),
        ]

        return [
            item.model_dump(mode="json") for item in mock_items[: request.max_items]
        ]
