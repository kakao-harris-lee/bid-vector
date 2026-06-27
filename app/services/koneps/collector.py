"""KONEPS collector service skeleton."""

import json
from datetime import timedelta
from math import ceil
from time import sleep
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CrawlJob, HistoricalData, Project, TenderResult
from app.core.time import kst_now, utc_now
from app.schemas.schemas import CrawlNoticeItem, CrawlRequest
from app.services.koneps import html_parsing, matching, openapi, parsing
from app.services.project_similarity import ProjectSimilarityService
from app.services.realtime import realtime_event_manager


class KonepsLiveCollectionError(RuntimeError):
    """Wrap live crawl failures with retry attempts and crawl-stage metadata."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        attempts: list[dict[str, Any]] | None = None,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.attempts = attempts or []
        self.original_error = original_error


def format_crawl_error_message(metadata: dict[str, Any]) -> str | None:
    """Return a compact crawl-job error message with live failure category context."""
    if not isinstance(metadata, dict):
        return None

    reason = metadata.get("fallback_reason")
    if not reason:
        return None

    live_failure = (
        metadata.get("live_failure")
        if isinstance(metadata.get("live_failure"), dict)
        else {}
    )
    stage = metadata.get("fallback_failure_stage") or live_failure.get("stage")
    category = metadata.get("fallback_failure_category") or live_failure.get("category")

    if stage or category:
        failure_label = "/".join(str(value) for value in (stage, category) if value)
        return f"[{failure_label}] {reason}"
    return str(reason)


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
    LIVE_FAILURE_RETRYABLE_CATEGORIES = {"network", "timeout", "unknown"}

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
                failure_payload = self._live_failure_payload(
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
        """Create a crawl job record before execution starts.

        ``celery_task_id`` is stamped at INSERT time so the row is immediately
        recoverable by a redelivered task (closes the orphan window where a
        SIGKILL between create and stamp would leave an unrecoverable row).
        """
        crawl_job = CrawlJob(
            source=request.source,
            target_date=request.target_date,
            status="running",
            result_count=0,
            celery_task_id=str(celery_task_id) if celery_task_id else None,
        )
        db.add(crawl_job)
        db.commit()
        db.refresh(crawl_job)
        realtime_event_manager.publish_event(
            "crawl.completed" if crawl_job.status == "completed" else "crawl.fallback",
            {
                "crawl_job_id": int(crawl_job.id),
                "project_id": (
                    int(crawl_job.project_id)
                    if crawl_job.project_id is not None
                    else None
                ),
                "status": crawl_job.status,
                "source": crawl_job.source,
                "target_date": crawl_job.target_date,
                "result_count": int(crawl_job.result_count or 0),
                "error_message": crawl_job.error_message,
            },
        )
        return crawl_job

    def persist_crawl_results(
        self,
        db: Session,
        crawl_job: CrawlJob,
        request: CrawlRequest,
        response: dict[str, Any],
        *,
        defer_embeddings: bool = False,
    ) -> CrawlJob:
        """Persist crawl history and any usable opening-result data.

        ``defer_embeddings`` is decided by the *caller*, not this method: only the
        Celery collection task (the single path with a hard time limit) defers
        embeddings to an async backfill. Synchronous callers (``POST
        /operations/crawl``, the scsbid backfill script) leave it at the default
        ``False`` so projects are embedded inline -- otherwise their newly created
        projects would never be embedded (no inline, no enqueued backfill) and
        silently drop out of pgvector search/recommendation.
        """
        items = response.get("items", [])
        metadata = response.get("metadata", {})

        crawl_job.status = response.get("job_status", "completed")
        crawl_job.result_count = response.get("collected_count", len(items))
        crawl_job.error_message = format_crawl_error_message(metadata)
        crawl_job.completed_at = utc_now()

        project_similarity = ProjectSimilarityService()
        linked_project_ids: set[int] = set()
        # When deferred (Celery collection task for high-volume scsbid sweeps),
        # per-item embedding is skipped and the touched project ids are surfaced
        # for a single async backfill; CPU model inference per item would
        # otherwise exceed the Celery hard time limit.
        deferred_embedding_project_ids: set[int] = set()

        for item in items:
            item_metadata = item.get("metadata", {})
            historical_record = (
                db.query(HistoricalData)
                .filter(HistoricalData.notice_number == item.get("notice_number"))
                .first()
            )
            if historical_record is None:
                historical_record = HistoricalData(
                    notice_number=item.get("notice_number")
                )
                db.add(historical_record)

            project, embedding_deferred = self._resolve_project_for_item(
                db,
                item=item,
                request=request,
                historical_record=historical_record,
                project_similarity=project_similarity,
                defer_embeddings=defer_embeddings,
            )
            if project is not None:
                historical_record.project_id = project.id
                linked_project_ids.add(int(project.id))
                if embedding_deferred:
                    deferred_embedding_project_ids.add(int(project.id))

            historical_record.agency_name = (
                item_metadata.get("opening_demand_agency")
                or item_metadata.get("demand_agency")
                or item_metadata.get("issuing_agency")
                or ""
            )
            historical_record.category = matching.resolve_project_category(
                item, request
            )
            historical_record.base_amount = item.get("base_amount") or 0.0
            historical_record.predicted_price = (
                item.get("estimated_amount") or item.get("base_amount") or 0.0
            )
            historical_record.bid_rate = (
                parsing.normalize_bid_rate_value(
                    item_metadata.get("bid_rate") or item_metadata.get("winning_rate")
                )
                or 0.0
            )
            # Reserve price / selected numbers are settled, immutable values. An
            # incoming empty list means "not (re)fetched this run" (e.g. the
            # reserve-detail HTTP was skipped because it was already persisted),
            # so it must never clobber a previously stored non-empty value.
            incoming_reserve_prices = item_metadata.get("reserve_prices") or []
            if incoming_reserve_prices or not self._has_persisted_reserve_prices(
                historical_record
            ):
                historical_record.reserve_prices = json.dumps(
                    incoming_reserve_prices,
                    ensure_ascii=False,
                )
            incoming_selected_numbers = item_metadata.get("selected_numbers") or []
            if incoming_selected_numbers or not self._has_persisted_reserve_prices(
                historical_record
            ):
                historical_record.selected_numbers = json.dumps(
                    incoming_selected_numbers,
                    ensure_ascii=False,
                )
            historical_record.opened_at = parsing.coerce_datetime(
                item_metadata.get("opening_announced_at")
                or item_metadata.get("opening_scheduled_at")
            )

            has_tender_result = any(
                item_metadata.get(key)
                for key in (
                    "opening_status",
                    "winning_company",
                    "winning_amount",
                    "winning_rate",
                    "opening_announced_at",
                )
            )
            if has_tender_result:
                tender_result = self._resolve_tender_result(
                    db,
                    project_id=(
                        project.id
                        if project is not None
                        else historical_record.project_id
                    ),
                    item_metadata=item_metadata,
                    crawl_job_status=crawl_job.status,
                )

                if (
                    tender_result.project_id is None
                    and historical_record.project_id is not None
                ):
                    tender_result.project_id = historical_record.project_id

        if len(linked_project_ids) == 1:
            crawl_job.project_id = next(iter(linked_project_ids))

        # Surface deferred-embedding project ids so the task layer can enqueue a
        # single async backfill. The service layer never imports the task module
        # (avoids a circular import); the orchestration lives in the task.
        if deferred_embedding_project_ids:
            response.setdefault("metadata", {})[
                "deferred_embedding_project_ids"
            ] = sorted(deferred_embedding_project_ids)

        db.add(crawl_job)
        db.commit()
        db.refresh(crawl_job)
        realtime_event_manager.publish_event(
            "crawl.failed",
            {
                "crawl_job_id": int(crawl_job.id),
                "project_id": (
                    int(crawl_job.project_id)
                    if crawl_job.project_id is not None
                    else None
                ),
                "status": crawl_job.status,
                "source": crawl_job.source,
                "target_date": crawl_job.target_date,
                "result_count": int(crawl_job.result_count or 0),
                "error_message": crawl_job.error_message,
            },
        )
        return crawl_job

    def mark_crawl_job_failed(
        self, db: Session, crawl_job: CrawlJob, error_message: str
    ) -> CrawlJob:
        """Update an existing crawl job when execution fails unexpectedly."""
        crawl_job.status = "failed"
        crawl_job.error_message = error_message
        crawl_job.completed_at = utc_now()
        db.add(crawl_job)
        db.commit()
        db.refresh(crawl_job)
        return crawl_job

    def _live_collection_error(
        self,
        *,
        stage: str,
        attempts: list[dict[str, Any]],
        original_error: Exception,
    ) -> KonepsLiveCollectionError:
        """Build a live crawl exception with retry context attached."""
        attempt_count = len(attempts)
        detail = str(original_error) or type(original_error).__name__
        message = f"KONEPS {stage} failed after {attempt_count} attempt(s): {detail}"
        return KonepsLiveCollectionError(
            message,
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
        """Build one retry-attempt payload for operations diagnostics."""
        failure_payload = self._live_failure_payload(exc, stage=stage)
        next_delay_seconds = (
            None if final_attempt else self._retry_delay_seconds(attempt_index)
        )
        return {
            "attempt": attempt_index + 1,
            "stage": stage,
            "category": failure_payload["category"],
            "retryable": failure_payload["retryable"],
            "exception_type": failure_payload["exception_type"],
            "error_message": failure_payload["error_message"],
            "next_retry_delay_seconds": next_delay_seconds,
            "final_attempt": final_attempt,
        }

    def _live_failure_payload(self, exc: Exception, *, stage: str) -> dict[str, Any]:
        """Classify a live crawl exception into a stable operations payload."""
        original_error = getattr(exc, "original_error", None) or exc
        resolved_stage = str(getattr(exc, "stage", stage) or stage)
        attempts = getattr(exc, "attempts", None)
        category = self._classify_live_failure(original_error)
        retryable = category in self.LIVE_FAILURE_RETRYABLE_CATEGORIES
        detail = str(exc) or str(original_error) or type(original_error).__name__

        payload: dict[str, Any] = {
            "stage": resolved_stage,
            "category": category,
            "retryable": retryable,
            "detail": detail,
            "error_message": str(original_error) or detail,
            "exception_type": type(original_error).__name__,
        }
        if attempts:
            payload["attempt_count"] = len(attempts)
            payload["attempts"] = attempts
        return payload

    def _classify_live_failure(self, exc: Exception) -> str:
        """Map browser/network/parser errors to operator-actionable categories."""
        lowered_message = str(exc or "").lower()
        exception_name = type(exc).__name__.lower()

        if any(
            marker in lowered_message
            for marker in ("captcha", "access denied", "forbidden", "403", "blocked")
        ):
            return "access_denied"
        if any(
            marker in lowered_message
            for marker in (
                "browser not available",
                "executable doesn't exist",
                "playwright install",
                "failed to launch",
                "target page, context or browser has been closed",
            )
        ):
            return "browser_runtime"
        if any(
            marker in lowered_message
            for marker in (
                "err_name_not_resolved",
                "err_connection",
                "econn",
                "net::",
                "network",
                "connection reset",
                "connection refused",
            )
        ):
            return "network"
        if any(
            marker in lowered_message
            for marker in ("no notice items", "no result", "empty result")
        ):
            return "no_data"
        if any(
            marker in lowered_message
            for marker in (
                "could not be located",
                "selector",
                "locator",
                "strict mode violation",
                "waiting for",
            )
        ):
            return "selector_drift"
        if "timeout" in lowered_message or "timeout" in exception_name:
            return "timeout"
        return "unknown"

    def _retry_delay_seconds(self, attempt_index: int) -> float:
        """Return linear backoff delay for the next retry attempt."""
        return (settings.KONEPS_RETRY_BACKOFF_MS * (attempt_index + 1)) / 1000

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

        response, key_variant = self._request_openapi_with_key_variants(
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
        payload = self._load_openapi_json(response)
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
        page_size = self._scsbid_page_size(request)
        max_pages = self._scsbid_max_pages(request)
        collect_reserve_detail = bool(request.collect_reserve_detail)
        delay_seconds = self._scsbid_request_delay_seconds()

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

        parsed_items: list[dict[str, Any]] = []
        seen_notice_numbers: set[str] = set()
        # Deferred reserve-detail notices: {(notice_number, category)} to dedupe,
        # surfaced as a list of dicts in metadata for the async backfill enqueue.
        deferred_reserve_detail: list[dict[str, str]] = []
        deferred_reserve_seen: set[tuple[str, str]] = set()
        reserve_detail_count = 0
        reserve_detail_error_count = 0
        reserve_detail_reused_count = 0
        reserve_detail_deferred_count = 0
        api_call_count = 0
        key_variant = ""
        last_result_code = ""
        last_result_message = ""
        category_metadata: list[dict[str, Any]] = []

        for category in categories:
            operation = openapi.scsbid_operation_for_category(category)
            url = f"{settings.KONEPS_OPENAPI_SCSBID_INFO_URL.rstrip('/')}/{operation}"
            category_total_count: int | None = None
            category_pages = 0

            for page_no in range(1, max_pages + 1):
                params = {
                    "type": "json",
                    "numOfRows": page_size,
                    "pageNo": page_no,
                    "inqryDiv": "1",
                    "inqryBgnDt": begin_token,
                    "inqryEndDt": end_token,
                }
                if api_call_count > 0 and delay_seconds > 0:
                    sleep(delay_seconds)
                response, key_variant = self._request_openapi_with_key_variants(
                    url,
                    params=params,
                    service_key=service_key,
                    operation=operation,
                )
                api_call_count += 1
                if response.status_code >= 400:
                    raise ValueError(
                        f"KONEPS ScsbidInfoService HTTP {response.status_code} for "
                        f"{operation}: {response.text[:300]} "
                        f"Tried service key variants: {key_variant}."
                    )
                payload = self._load_openapi_json(response)
                header = openapi.openapi_header(payload)
                result_code = str(header.get("resultCode") or "").strip()
                result_message = str(header.get("resultMsg") or "").strip()
                if result_code and result_code not in {"00", "03"}:
                    raise ValueError(
                        f"KONEPS ScsbidInfoService returned resultCode={result_code}: "
                        f"{result_message or 'unknown error'}"
                    )
                last_result_code = result_code or last_result_code
                last_result_message = result_message or last_result_message

                body = openapi.openapi_body(payload)
                if category_total_count is None:
                    category_total_count = parsing.safe_int(body.get("totalCount"))
                raw_items = openapi.openapi_item_list(body)
                category_pages += 1

                for raw_item in raw_items:
                    detail: dict[str, Any] = {}
                    notice_number = str(raw_item.get("bidNtceNo") or "").strip()
                    if not notice_number:
                        continue
                    if notice_number in seen_notice_numbers:
                        continue
                    if collect_reserve_detail:
                        if notice_number in already_have_reserve:
                            # Reserve price already settled & persisted; leave
                            # ``detail`` empty so persist preserves the stored
                            # value instead of re-fetching it over HTTP.
                            reserve_detail_reused_count += 1
                        elif defer_reserve_detail:
                            # Time-limited Celery collection path: skip the inline
                            # per-notice fetch (and its throttle sleep) and queue
                            # the notice for a bounded async backfill instead.
                            # ``detail`` stays empty so persist preserves any
                            # already-stored reserve price.
                            dedupe_key = (notice_number, str(category or ""))
                            if dedupe_key not in deferred_reserve_seen:
                                deferred_reserve_seen.add(dedupe_key)
                                deferred_reserve_detail.append(
                                    {
                                        "notice_number": notice_number,
                                        "category": str(category or ""),
                                    }
                                )
                                reserve_detail_deferred_count += 1
                        else:
                            try:
                                if delay_seconds > 0:
                                    sleep(delay_seconds)
                                detail = self._fetch_scsbid_reserve_detail(
                                    raw_item,
                                    category=category,
                                    service_key=service_key,
                                )
                                api_call_count += 1
                                if detail.get("reserve_prices"):
                                    reserve_detail_count += 1
                            except Exception as exc:
                                reserve_detail_error_count += 1
                                detail = {"reserve_detail_error": str(exc)}

                    parsed_item = self._build_scsbid_award_item(
                        raw_item,
                        detail=detail,
                        request=request,
                        operation=operation,
                        category=category,
                    )
                    if parsed_item is None:
                        continue
                    notice_number = str(parsed_item["notice_number"])
                    if notice_number in seen_notice_numbers:
                        continue
                    seen_notice_numbers.add(notice_number)
                    parsed_items.append(parsed_item)

                # Stop conditions: empty/short page or totalCount window reached.
                if not raw_items:
                    break
                if len(raw_items) < page_size:
                    break
                if (
                    category_total_count is not None
                    and page_no * page_size >= category_total_count
                ):
                    break

            category_metadata.append(
                {
                    "category": category,
                    "operation": operation,
                    "total_count": category_total_count,
                    "pages_fetched": category_pages,
                }
            )

        return {
            "items": parsed_items,
            "metadata": {
                "resolved_mode": "scsbid_openapi",
                "openapi_service": "ScsbidInfoService",
                "openapi_operation": (
                    category_metadata[0]["operation"] if category_metadata else None
                ),
                "openapi_endpoint": settings.KONEPS_OPENAPI_SCSBID_INFO_URL,
                "openapi_service_key_variant": key_variant,
                "openapi_result_code": last_result_code or "00",
                "openapi_result_message": last_result_message,
                "openapi_total_count": sum(
                    int(entry["total_count"] or 0) for entry in category_metadata
                ),
                "scsbid_categories": [entry["category"] for entry in category_metadata],
                "scsbid_category_breakdown": category_metadata,
                "scsbid_api_call_count": api_call_count,
                "scsbid_collected_count": len(parsed_items),
                "reserve_detail_enabled": collect_reserve_detail,
                "reserve_detail_collected_count": reserve_detail_count,
                "reserve_detail_error_count": reserve_detail_error_count,
                "reserve_detail_reused_count": reserve_detail_reused_count,
                "reserve_detail_deferred_count": reserve_detail_deferred_count,
                "deferred_reserve_detail_notices": deferred_reserve_detail,
                "query_date_begin": begin_token,
                "query_date_end": end_token,
                "query_type": "award_registration_datetime",
            },
        }

    def _notice_numbers_with_persisted_reserve(self, db: Session) -> set[str]:
        """Notice numbers that already carry a non-empty persisted reserve price.

        One indexed scan over ``historical_data.notice_number`` (no per-notice
        round trip): a reserve price is JSON-encoded into ``reserve_prices`` as
        ``"[]"`` when absent, so we drop NULL/empty/``"[]"`` rows. Used to skip
        the per-notice reserve-detail HTTP fetch for already-settled awards.
        """
        rows = (
            db.query(HistoricalData.notice_number)
            .filter(
                HistoricalData.notice_number.isnot(None),
                HistoricalData.reserve_prices.isnot(None),
                HistoricalData.reserve_prices != "",
                HistoricalData.reserve_prices != "[]",
            )
            .all()
        )
        return {str(notice_number) for (notice_number,) in rows if notice_number}

    @staticmethod
    def _has_persisted_reserve_prices(historical_record: HistoricalData) -> bool:
        """Whether a HistoricalData row already stores a non-empty reserve price."""
        stored = historical_record.reserve_prices
        if not stored:
            return False
        try:
            return bool(json.loads(stored))
        except (TypeError, ValueError):
            return bool(str(stored).strip() not in {"", "[]"})

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
        """Resolve (inqryBgnDt, inqryEndDt) tokens for the scsbid date window.

        Priority: explicit ``start_date``/``end_date`` > ``lookback_days``
        (end=today) > ``target_date`` single-day (legacy). Returns
        ``YYYYMMDDHHMM`` tokens.
        """
        if request.start_date and request.end_date:
            begin = openapi.openapi_date_token(request.start_date)
            end = openapi.openapi_date_token(request.end_date)
        elif request.lookback_days is not None:
            # KONEPS opening dates are KST — anchor the rolling window on the
            # Korean calendar day so the latest ~9h of openings are not missed
            # while UTC is still on the previous date (KST 00:00-09:00).
            today = kst_now().date()
            start_day = today - timedelta(days=max(0, int(request.lookback_days)))
            begin = start_day.strftime("%Y%m%d")
            end = today.strftime("%Y%m%d")
        else:
            token = openapi.openapi_date_token(request.target_date)
            begin = token
            end = token
        return f"{begin}0000", f"{end}2359"

    def _scsbid_page_size(self, request: CrawlRequest) -> int:
        """Resolve numOfRows per page for a scsbid sweep (default 100, <=999)."""
        configured = request.page_size or settings.KONEPS_SCSBID_COLLECTION_PAGE_SIZE
        return max(1, min(int(configured or 100), 999))

    def _scsbid_max_pages(self, request: CrawlRequest) -> int:
        """Resolve the per-category page ceiling for a scsbid sweep (default 30)."""
        configured = request.max_pages or settings.KONEPS_SCSBID_COLLECTION_MAX_PAGES
        return max(1, int(configured or 30))

    def _scsbid_request_delay_seconds(self) -> float:
        """Return the inter-call throttle delay (seconds, never negative)."""
        return max(
            0.0,
            float(settings.KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS or 0.0),
        )

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
        response, key_variant = self._request_openapi_with_key_variants(
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

        payload = self._load_openapi_json(response)
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

    def _build_scsbid_award_item(
        self,
        raw_item: dict[str, Any],
        *,
        detail: dict[str, Any],
        request: CrawlRequest,
        operation: str,
        category: str | None = None,
    ) -> dict[str, Any] | None:
        """Convert one ScsbidInfoService row into the existing crawl payload."""
        notice_number = str(raw_item.get("bidNtceNo") or "").strip()
        if not notice_number:
            return None
        # Tag the item with the swept category so persist-time category
        # resolution honours the per-category operation, not request.category.
        resolved_category = (
            str(category).strip().lower()
            if category is not None
            else (request.category or None)
        )

        title = str(raw_item.get("bidNtceNm") or notice_number).strip()
        winning_amount = parsing.coerce_amount(raw_item.get("sucsfbidAmt"))
        success_rate = parsing.normalize_bid_rate_value(raw_item.get("sucsfbidRate"))
        base_amount = (
            detail.get("base_amount")
            or detail.get("planned_price")
            or (
                winning_amount / success_rate
                if winning_amount is not None and success_rate
                else None
            )
            or winning_amount
            or 0.0
        )
        planned_price = detail.get("planned_price") or (
            winning_amount / success_rate
            if winning_amount is not None and success_rate
            else None
        )
        bid_rate = (
            winning_amount / base_amount
            if winning_amount is not None and float(base_amount or 0.0) > 0
            else success_rate
        )
        opened_at = (
            raw_item.get("rlOpengDt")
            or raw_item.get("fnlSucsfDate")
            or raw_item.get("rgstDt")
        )
        demand_agency = str(raw_item.get("dminsttNm") or "").strip()

        return {
            "notice_number": notice_number,
            "title": title,
            "base_amount": float(base_amount or 0.0),
            "estimated_amount": float(planned_price or base_amount or 0.0),
            "closing_at": parsing.coerce_datetime(opened_at),
            "business_type": resolved_category or request.category,
            "region": parsing.extract_region([demand_agency, title]),
            "license_codes": [],
            "source_url": None,
            "metadata": {
                "mode": "scsbid_openapi",
                "openapi_service": "ScsbidInfoService",
                "openapi_operation": operation,
                "bid_notice_order": raw_item.get("bidNtceOrd"),
                "bid_classification_no": raw_item.get("bidClsfcNo"),
                "rebid_no": raw_item.get("rbidNo"),
                "opening_status": "낙찰",
                "opening_demand_agency": demand_agency,
                "demand_agency": demand_agency,
                "opening_scheduled_at": opened_at,
                "opening_announced_at": opened_at,
                "participant_count": parsing.safe_int(raw_item.get("prtcptCnum")),
                "winning_company": raw_item.get("bidwinnrNm"),
                "winning_business_no": raw_item.get("bidwinnrBizno"),
                "winning_amount": winning_amount,
                "winning_rate": success_rate,
                "bid_rate": parsing.normalize_bid_rate_value(bid_rate),
                "final_success_date": raw_item.get("fnlSucsfDate"),
                "reserve_prices": detail.get("reserve_prices") or [],
                "selected_numbers": detail.get("selected_numbers") or [],
                "planned_price": detail.get("planned_price"),
                "reserve_detail_error": detail.get("reserve_detail_error"),
                "raw_openapi_item": raw_item,
                "raw_reserve_detail_items": detail.get("raw_reserve_detail_items")
                or [],
            },
        }

    def _request_openapi_with_key_variants(
        self,
        url: str,
        *,
        params: dict[str, Any],
        service_key: str,
        operation: str,
    ) -> tuple[requests.Response, str]:
        """Call OpenAPI with raw and URL-encoded key forms used by data.go.kr."""
        timeout = max(1, int(settings.KONEPS_OPENAPI_TIMEOUT_SECONDS))
        variants = openapi.openapi_service_key_variants(service_key)
        last_response: requests.Response | None = None

        for variant_name, variant_value, value_is_preencoded in variants:
            if value_is_preencoded:
                query_string = openapi.openapi_query_string(
                    params={**params, "ServiceKey": variant_value},
                    preencoded_keys={"ServiceKey"},
                )
                response = requests.get(f"{url}?{query_string}", timeout=timeout)
            else:
                response = requests.get(
                    url,
                    params={**params, "ServiceKey": variant_value},
                    timeout=timeout,
                )
            if response.status_code != 401:
                return response, variant_name
            last_response = response

        if last_response is None:
            raise ValueError(
                f"KONEPS OpenAPI request was not attempted for {operation}."
            )
        return last_response, ",".join(name for name, _, _ in variants)

    def _load_openapi_json(self, response: requests.Response) -> dict[str, Any]:
        """Decode one OpenAPI response, surfacing non-JSON error bodies clearly."""
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(
                f"KONEPS OpenAPI response was not JSON: {response.text[:500]}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError("KONEPS OpenAPI response did not contain a JSON object.")
        return payload

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
            failure_payload = self._live_failure_payload(exc, stage="opening_result")
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
                        sleep(self._retry_delay_seconds(attempt))

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
                        sleep(self._retry_delay_seconds(attempt))

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

        Performs a simple HTTP GET on ``source_url``, parses the HTML with the
        same ``html_parsing.parse_detail_html`` helper used during live
        collection, and returns only the two business-type keys.

        Best-effort: any exception raised by the HTTP call is propagated so
        callers (e.g. backfill scripts) can record per-row failures.
        """
        timeout = max(1, int(getattr(settings, "KONEPS_OPENAPI_TIMEOUT_SECONDS", 30)))
        response = requests.get(source_url, timeout=timeout)
        response.raise_for_status()
        detail = html_parsing.parse_detail_html(response.text)
        return {
            "business_type_code": detail.get("business_type_code"),
            "business_type_label": detail.get("business_type_label"),
        }

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
        """Find or create a project row for a crawled notice and keep it enriched with crawl metadata.

        Returns ``(project, embedding_deferred)``. When ``defer_embeddings`` is
        True the synchronous embedding refresh is skipped (so high-volume scsbid
        award collection does not exceed the Celery time limit); the caller is
        expected to enqueue an async backfill for the touched project ids.
        """
        project: Project | None = None
        if historical_record.project_id is not None:
            project = (
                db.query(Project)
                .filter(Project.id == historical_record.project_id)
                .first()
            )

        if project is None:
            project = self._find_matching_project(db, item=item, request=request)

        is_new_project = project is None
        if project is None:
            project = Project(
                title=item.get("title") or item.get("notice_number") or "KONEPS notice",
                description="",
                requirements="",
                budget_estimate=0.0,
                category=matching.resolve_project_category(item, request),
            )
            db.add(project)
            db.flush()

        self._update_project_from_item(project, item=item, request=request)
        if defer_embeddings:
            # Persist a project row now; embedding is rebuilt asynchronously.
            db.flush()
            return project, True

        project_similarity.refresh_project_embedding(db, project, force=is_new_project)
        return project, False

    def _find_matching_project(
        self,
        db: Session,
        *,
        item: dict[str, Any],
        request: CrawlRequest,
    ) -> Project | None:
        """Heuristically link a crawled notice to an existing project using explicit keys first.

        Performance + correctness note (perf/scsbid-find-matching-project-index)
        ----------------------------------------------------------------------
        ``notice_number`` is KONEPS's authoritative unique key: two notices with
        different numbers are *different* tenders. The previous implementation
        loaded **every** project in the category (thousands of rows) per item and
        scanned them in Python, capping scsbid award persistence at ~1-2 items/s.

        This version exploits ``ix_projects_notice_number``:

        1. **Index fast path** -- when the item carries a notice number, query the
           index directly (``notice_number.in_(...)``) for matching
           ``Project.notice_number`` values. The column is persisted in canonical
           (normalized) form -- ``_update_project_from_item`` writes
           ``_normalize_notice_number(...)`` and the ``20260612_*`` data migration
           back-filled legacy rows -- so the indexed equality probe is exact and
           the overwhelming majority of scsbid open-bid items resolve here with a
           handful of indexed rows loaded instead of the whole category.
        2. **Column-NULL notice fallback** -- a small set of legacy projects keep
           their notice number only inside free-form ``description``/``requirements``
           text (``Project.notice_number IS NULL``). Those few rows are loaded and
           compared with the existing extraction logic.
        3. **source_url / title fuzzy** -- restricted to ``notice_number IS NULL``
           candidates. Projects that *do* carry a notice number are resolved
           authoritatively in steps 1-2; we deliberately no longer fuzzy-merge an
           item into a project whose notice number differs, which previously could
           collapse two distinct tenders on title overlap.

        Invariant (why limiting step 3 to notice-less rows loses nothing):
        ``_normalize_source_url`` keeps only the ``bidNtceNo``/``bidNtceOrd``
        (and ``bidPbancNo``/``bidPbancOrd``) query keys, so a KONEPS detail URL
        *encodes the notice number*. Therefore ``source_url`` equality implies
        ``notice_number`` equality, and the step-1 notice probe already subsumes
        any source_url match a notice-bearing project could have offered. Limiting
        the fuzzy step to notice-less candidates never drops a valid notice-bearing
        match -- it only avoids re-introducing a full category scan.

        Behavioural change: an item with a notice number is no longer matched to an
        existing project that has a *different* notice number via title overlap.
        This is intentional -- it prevents merging distinct tenders -- and the
        fuzzy heuristics now apply only to notice-less candidates.

        4. **Notice-less items** (rare; some KONEPS payloads) retain the original
           full category load + source_url/title fuzzy matching to avoid any
           regression for that path.
        """
        target_title = parsing.normalize_title(item.get("title"))
        target_notice_number = parsing.normalize_notice_number(
            item.get("notice_number")
        )
        target_source_url = matching.normalize_source_url(item.get("source_url"))
        target_agencies = matching.extract_item_agency_keys(item)
        target_category = matching.resolve_project_category(item, request)
        target_budget = matching.resolve_budget_estimate(item)
        target_deadline = parsing.coerce_datetime(item.get("closing_at"))

        if target_notice_number:
            # 1. Index fast path: match on the indexed notice_number column.
            raw_notice = str(item.get("notice_number") or "").strip()
            notice_variants = {
                variant for variant in (raw_notice, target_notice_number) if variant
            }
            for candidate in (
                db.query(Project)
                .filter(Project.notice_number.in_(notice_variants))
                .all()
            ):
                if (
                    parsing.normalize_notice_number(candidate.notice_number)
                    == target_notice_number
                ):
                    return candidate

            # 2. Notice number stored only in free-form text (column is NULL).
            #    These are a small minority, so scanning them is cheap.
            null_notice_query = db.query(Project).filter(
                Project.notice_number.is_(None)
            )
            if target_category:
                null_notice_query = null_notice_query.filter(
                    Project.category == target_category
                )
            null_notice_candidates = null_notice_query.all()
            for candidate in null_notice_candidates:
                candidate_notice_number = parsing.normalize_notice_number(
                    matching.extract_project_notice_number(candidate)
                )
                if (
                    candidate_notice_number
                    and candidate_notice_number == target_notice_number
                ):
                    return candidate

            # 3. source_url / title fuzzy, restricted to notice-less candidates.
            #    Projects carrying a (different) notice number are authoritatively
            #    distinct tenders and must not be fuzzy-merged here.
            return matching.match_by_url_or_title(
                null_notice_candidates,
                target_source_url=target_source_url,
                target_title=target_title,
                target_agencies=target_agencies,
                target_budget=target_budget,
                target_deadline=target_deadline,
            )

        # 4. Item has no notice number (rare). Preserve the original behaviour:
        #    full category load + source_url/title fuzzy matching.
        query = db.query(Project)
        if target_category:
            query = query.filter(Project.category == target_category)
        candidates = query.all()
        return matching.match_by_url_or_title(
            candidates,
            target_source_url=target_source_url,
            target_title=target_title,
            target_agencies=target_agencies,
            target_budget=target_budget,
            target_deadline=target_deadline,
        )

    def _update_project_from_item(
        self, project: Project, *, item: dict[str, Any], request: CrawlRequest
    ) -> None:
        """Apply crawled notice details onto a project without discarding user-entered context."""
        item_metadata = item.get("metadata", {})
        resolved_category = matching.resolve_project_category(item, request)
        budget_estimate = matching.resolve_budget_estimate(item)
        budget_values = [
            float(amount)
            for amount in (
                item.get("base_amount"),
                item.get("estimated_amount"),
                budget_estimate,
            )
            if amount not in (None, "", 0, 0.0)
        ]
        description_lines = [
            (
                f"공고번호: {item.get('notice_number')}"
                if item.get("notice_number")
                else None
            ),
            (
                f"공고기관: {item_metadata.get('issuing_agency')}"
                if item_metadata.get("issuing_agency")
                else None
            ),
            (
                f"수요기관: {item_metadata.get('opening_demand_agency') or item_metadata.get('demand_agency')}"
                if item_metadata.get("opening_demand_agency")
                or item_metadata.get("demand_agency")
                else None
            ),
            f"공고원문: {item.get('source_url')}" if item.get("source_url") else None,
            (
                f"업무구분: {item.get('business_type')}"
                if item.get("business_type")
                else None
            ),
            (
                f"개찰상태: {item_metadata.get('opening_status')}"
                if item_metadata.get("opening_status")
                else None
            ),
        ]
        requirement_lines = [
            f"지역요건: {item.get('region')}" if item.get("region") else None,
            (
                f"면허요건: {' '.join(item.get('license_codes') or [])}"
                if item.get("license_codes")
                else None
            ),
            (
                f"기초금액: {float(item.get('base_amount')):.0f}"
                if item.get("base_amount")
                else None
            ),
            (
                f"추정금액: {float(item.get('estimated_amount')):.0f}"
                if item.get("estimated_amount")
                else None
            ),
            (
                f"계약방법: {item_metadata.get('contract_method')}"
                if item_metadata.get("contract_method")
                else None
            ),
        ]

        if item.get("title") and parsing.should_replace_project_title(
            project.title, item.get("title")
        ):
            project.title = str(item.get("title")).strip()
        notice_number = item.get("notice_number")
        # Persist notice_number in canonical (normalized) form so the indexed
        # ``notice_number.in_(...)`` fast path in ``_find_matching_project`` can
        # rely on equality. Storing a non-canonical value (lower case / inner
        # whitespace) would make the index probe miss and create duplicates.
        normalized_notice_number = parsing.normalize_notice_number(notice_number)
        if normalized_notice_number and (
            not project.notice_number
            or parsing.normalize_notice_number(project.notice_number)
            == normalized_notice_number
        ):
            project.notice_number = normalized_notice_number
        source_url = item.get("source_url")
        if source_url and (
            not project.source_url
            or matching.normalize_source_url(project.source_url)
            == matching.normalize_source_url(source_url)
        ):
            project.source_url = str(source_url).strip()
        issuing_agency = item_metadata.get("issuing_agency")
        if issuing_agency and (
            not project.issuing_agency
            or parsing.normalize_agency_name(project.issuing_agency)
            == parsing.normalize_agency_name(issuing_agency)
        ):
            project.issuing_agency = str(issuing_agency).strip()
        demand_agency = item_metadata.get("opening_demand_agency") or item_metadata.get(
            "demand_agency"
        )
        if demand_agency and (
            not project.demand_agency
            or parsing.normalize_agency_name(project.demand_agency)
            == parsing.normalize_agency_name(demand_agency)
        ):
            project.demand_agency = str(demand_agency).strip()
        project.description = parsing.merge_text_lines(
            project.description, description_lines
        )
        project.requirements = parsing.merge_text_lines(
            project.requirements, requirement_lines
        )
        project.category = resolved_category or project.category
        project.budget_estimate = budget_estimate or float(
            project.budget_estimate or 0.0
        )
        project.budget_min = min(budget_values) if budget_values else project.budget_min
        project.budget_max = max(budget_values) if budget_values else project.budget_max

        closing_at = parsing.coerce_datetime(item.get("closing_at"))
        if closing_at is not None:
            project.deadline = closing_at

        resolved_status = matching.resolve_project_status(item)
        if resolved_status:
            project.status = resolved_status

        if item.get("business_type_code") is not None:
            project.business_type_code = item.get("business_type_code")
        if item.get("business_type_label") is not None:
            project.business_type_label = item.get("business_type_label")

        db_title = project.title or item.get("notice_number") or "KONEPS notice"
        project.title = db_title.strip()

    def _resolve_tender_result(
        self,
        db: Session,
        *,
        project_id: int | None,
        item_metadata: dict[str, Any],
        crawl_job_status: str,
    ) -> TenderResult:
        """Upsert a tender result snapshot so repeated crawls do not duplicate the same award record."""
        announced_at = parsing.coerce_datetime(
            item_metadata.get("opening_announced_at")
        )
        winning_company = item_metadata.get("winning_company") or ""
        winning_amount = item_metadata.get("winning_amount") or 0.0
        winning_rate = item_metadata.get("winning_rate") or 0.0
        result_status = item_metadata.get("opening_status") or crawl_job_status

        tender_result: TenderResult | None = None
        if project_id is not None:
            candidates = (
                db.query(TenderResult)
                .filter(TenderResult.project_id == project_id)
                .order_by(TenderResult.id.desc())
                .all()
            )
            for candidate in candidates:
                if announced_at is not None and candidate.announced_at == announced_at:
                    tender_result = candidate
                    break
                if (
                    candidate.winning_company == winning_company
                    and float(candidate.winning_amount or 0.0)
                    == float(winning_amount or 0.0)
                    and float(candidate.winning_rate or 0.0)
                    == float(winning_rate or 0.0)
                ):
                    tender_result = candidate
                    break

        if tender_result is None:
            tender_result = TenderResult(project_id=project_id)
            db.add(tender_result)

        tender_result.project_id = project_id
        tender_result.winning_company = winning_company
        tender_result.winning_amount = winning_amount
        tender_result.winning_rate = winning_rate
        tender_result.result_status = result_status
        tender_result.announced_at = announced_at
        return tender_result

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
