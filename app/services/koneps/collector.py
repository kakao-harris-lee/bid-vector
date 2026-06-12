"""KONEPS collector service skeleton."""

import json
import re
from datetime import datetime, timedelta
from html import unescape
from math import ceil
from time import sleep
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CrawlJob, HistoricalData, Project, TenderResult
from app.core.time import ensure_utc, utc_now
from app.schemas.schemas import CrawlNoticeItem, CrawlRequest
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

    OPENAPI_SOURCE_ALIASES = {
        "koneps-openapi",
        "koneps_api",
        "koneps-api",
        "koneps-public-api",
        "bid-public-info",
    }
    SCSBID_OPENAPI_SOURCE_ALIASES = {
        "koneps-scsbid",
        "koneps-award-openapi",
        "koneps-awards",
        "scsbid",
        "scsbid-openapi",
    }
    OPENAPI_CATEGORY_OPERATIONS = {
        "construction": "getBidPblancListInfoCnstwk",
        "공사": "getBidPblancListInfoCnstwk",
        "service": "getBidPblancListInfoServc",
        "general-service": "getBidPblancListInfoServc",
        "technical-service": "getBidPblancListInfoServc",
        "software": "getBidPblancListInfoServc",
        "용역": "getBidPblancListInfoServc",
        "goods": "getBidPblancListInfoThng",
        "물품": "getBidPblancListInfoThng",
        "foreign": "getBidPblancListInfoFrgcpt",
        "frgcpt": "getBidPblancListInfoFrgcpt",
        "외자": "getBidPblancListInfoFrgcpt",
    }
    SCSBID_CATEGORY_OPERATIONS = {
        "construction": "getScsbidListSttusCnstwk",
        "공사": "getScsbidListSttusCnstwk",
        "service": "getScsbidListSttusServc",
        "general-service": "getScsbidListSttusServc",
        "technical-service": "getScsbidListSttusServc",
        "software": "getScsbidListSttusServc",
        "용역": "getScsbidListSttusServc",
        "goods": "getScsbidListSttusThng",
        "물품": "getScsbidListSttusThng",
        "foreign": "getScsbidListSttusFrgcpt",
        "frgcpt": "getScsbidListSttusFrgcpt",
        "외자": "getScsbidListSttusFrgcpt",
    }
    SCSBID_RESERVE_DETAIL_OPERATIONS = {
        "construction": "getOpengResultListInfoCnstwkPreparPcDetail",
        "공사": "getOpengResultListInfoCnstwkPreparPcDetail",
        "service": "getOpengResultListInfoServcPreparPcDetail",
        "general-service": "getOpengResultListInfoServcPreparPcDetail",
        "technical-service": "getOpengResultListInfoServcPreparPcDetail",
        "software": "getOpengResultListInfoServcPreparPcDetail",
        "용역": "getOpengResultListInfoServcPreparPcDetail",
        "goods": "getOpengResultListInfoThngPreparPcDetail",
        "물품": "getOpengResultListInfoThngPreparPcDetail",
        "foreign": "getOpengResultListInfoFrgcptPreparPcDetail",
        "frgcpt": "getOpengResultListInfoFrgcptPreparPcDetail",
        "외자": "getOpengResultListInfoFrgcptPreparPcDetail",
    }

    HOME_SEARCH_KEYWORD_ID = "mf_wfm_container_wq_uuid_925_wq_uuid_934_searchKeyword"
    HOME_SEARCH_BUTTON_ID = (
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_btnBidPbancDtlSrch"
    )
    HOME_SEARCH_TYPE_RADIO_ID = (
        "mf_wfm_container_wq_uuid_925_wq_uuid_934_rbxSrchType_input_0"
    )
    HOME_SEARCH_START_DATE_ID = "wq_uuid_1239_ibxStrDay"
    HOME_SEARCH_END_DATE_ID = "wq_uuid_1239_ibxEndDay"
    HOME_SEARCH_RESULT_TABLE_ID = "mf_wfm_container_testTable"
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
    OPENING_RESULT_GRID_ID = "mf_wfm_container_onbsRsltClsfInqyGrd"
    OPENING_RESULT_DATA_LIST_KEY = "mf_wfm_container_dlOnbsRsltClsfListOutL"
    LIVE_FAILURE_RETRYABLE_CATEGORIES = {"network", "timeout", "unknown"}

    def collect_notices(self, request: CrawlRequest) -> dict[str, Any]:
        """Collect KONEPS notices with live mode support and safe fallback."""
        normalized_request = self._normalize_request(request)
        job_status = "mock"
        response_metadata = {
            "requested_mode": normalized_request.execution_mode,
            "target_date": normalized_request.target_date,
            "keyword": normalized_request.keyword,
            "max_items": normalized_request.max_items,
        }

        if self._is_openapi_source(normalized_request.source):
            live_result = self._collect_openapi_items(normalized_request)
            items = live_result["items"]
            response_metadata.update(live_result["metadata"])
            job_status = "completed"
        elif self._is_scsbid_openapi_source(normalized_request.source):
            live_result = self._collect_scsbid_openapi_items(normalized_request)
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
    ) -> CrawlJob:
        """Persist crawl history and any usable opening-result data."""
        items = response.get("items", [])
        metadata = response.get("metadata", {})

        crawl_job.status = response.get("job_status", "completed")
        crawl_job.result_count = response.get("collected_count", len(items))
        crawl_job.error_message = format_crawl_error_message(metadata)
        crawl_job.completed_at = utc_now()

        project_similarity = ProjectSimilarityService()
        linked_project_ids: set[int] = set()
        # scsbid (open-bid result) collection processes thousands of award rows
        # per run. Embedding each item synchronously (CPU model inference) blows
        # past the Celery hard time limit, so we defer embeddings for that source
        # and hand the touched project ids to the task layer for an async
        # backfill. KONEPS notice collection keeps inline embeddings (no
        # regression for bid-eligible notices).
        defer_embeddings = self._is_scsbid_openapi_source(request.source)
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
            historical_record.category = self._resolve_project_category(item, request)
            historical_record.base_amount = item.get("base_amount") or 0.0
            historical_record.predicted_price = (
                item.get("estimated_amount") or item.get("base_amount") or 0.0
            )
            historical_record.bid_rate = (
                self._normalize_bid_rate_value(
                    item_metadata.get("bid_rate") or item_metadata.get("winning_rate")
                )
                or 0.0
            )
            historical_record.reserve_prices = json.dumps(
                item_metadata.get("reserve_prices", []),
                ensure_ascii=False,
            )
            historical_record.selected_numbers = json.dumps(
                item_metadata.get("selected_numbers", []),
                ensure_ascii=False,
            )
            historical_record.opened_at = self._coerce_datetime(
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
            response.setdefault("metadata", {})["deferred_embedding_project_ids"] = sorted(
                deferred_embedding_project_ids
            )

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
        normalized_target_date = request.target_date or utc_now().date().isoformat()
        normalized_mode = request.execution_mode.strip().lower()
        configured_max_items = (
            settings.KONEPS_OPENAPI_MAX_ITEMS
            if self._is_openapi_source(normalized_source)
            or self._is_scsbid_openapi_source(normalized_source)
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

    def _is_openapi_source(self, source: str | None) -> bool:
        """Return whether the crawl request should use the KONEPS OpenAPI path."""
        return str(source or "").strip().lower() in self.OPENAPI_SOURCE_ALIASES

    def _is_scsbid_openapi_source(self, source: str | None) -> bool:
        """Return whether the crawl request should use the KONEPS award OpenAPI path."""
        return (
            str(source or "").strip().lower() in self.SCSBID_OPENAPI_SOURCE_ALIASES
        )

    def _collect_openapi_items(self, request: CrawlRequest) -> dict[str, Any]:
        """Collect notice rows from the public KONEPS BidPublicInfoService OpenAPI."""
        service_key = str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip()
        if not service_key:
            raise ValueError(
                "KONEPS_OPENAPI_SERVICE_KEY is required for source=koneps-openapi"
            )

        operation = self._openapi_operation_for_category(request.category)
        date_token = self._openapi_date_token(request.target_date)
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
        header = self._openapi_header(payload)
        result_code = str(header.get("resultCode") or "").strip()
        result_message = str(header.get("resultMsg") or "").strip()
        if result_code and result_code not in {"00", "03"}:
            raise ValueError(
                f"KONEPS OpenAPI returned resultCode={result_code}: {result_message or 'unknown error'}"
            )

        body = self._openapi_body(payload)
        raw_items = self._openapi_item_list(body)
        parsed_items: list[dict[str, Any]] = []
        seen_notice_numbers: set[str] = set()
        for raw_item in raw_items:
            parsed_item = self._build_openapi_notice_item(
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
                "openapi_total_count": self._safe_int(body.get("totalCount")),
                "openapi_page_no": self._safe_int(body.get("pageNo")),
                "openapi_num_of_rows": self._safe_int(body.get("numOfRows")),
                "query_date": date_token,
                "query_type": "registration_datetime",
            },
        }

    def _collect_scsbid_openapi_items(self, request: CrawlRequest) -> dict[str, Any]:
        """Collect awarded/opening rows from the KONEPS ScsbidInfoService OpenAPI.

        Sweeps every requested category over a resolved date window with safe
        pagination, deduping notices across the whole run. The legacy single-day,
        single-category behaviour is preserved when the new optional request
        fields are absent.
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

        parsed_items: list[dict[str, Any]] = []
        seen_notice_numbers: set[str] = set()
        reserve_detail_count = 0
        reserve_detail_error_count = 0
        api_call_count = 0
        key_variant = ""
        last_result_code = ""
        last_result_message = ""
        category_metadata: list[dict[str, Any]] = []

        for category in categories:
            operation = self._scsbid_operation_for_category(category)
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
                header = self._openapi_header(payload)
                result_code = str(header.get("resultCode") or "").strip()
                result_message = str(header.get("resultMsg") or "").strip()
                if result_code and result_code not in {"00", "03"}:
                    raise ValueError(
                        f"KONEPS ScsbidInfoService returned resultCode={result_code}: "
                        f"{result_message or 'unknown error'}"
                    )
                last_result_code = result_code or last_result_code
                last_result_message = result_message or last_result_message

                body = self._openapi_body(payload)
                if category_total_count is None:
                    category_total_count = self._safe_int(body.get("totalCount"))
                raw_items = self._openapi_item_list(body)
                category_pages += 1

                for raw_item in raw_items:
                    detail: dict[str, Any] = {}
                    notice_number = str(raw_item.get("bidNtceNo") or "").strip()
                    if not notice_number:
                        continue
                    if notice_number in seen_notice_numbers:
                        continue
                    if collect_reserve_detail:
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
                "query_date_begin": begin_token,
                "query_date_end": end_token,
                "query_type": "award_registration_datetime",
            },
        }

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
            begin = self._openapi_date_token(request.start_date)
            end = self._openapi_date_token(request.end_date)
        elif request.lookback_days is not None:
            today = utc_now().date()
            start_day = today - timedelta(days=max(0, int(request.lookback_days)))
            begin = start_day.strftime("%Y%m%d")
            end = today.strftime("%Y%m%d")
        else:
            token = self._openapi_date_token(request.target_date)
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
        operation = self._scsbid_reserve_detail_operation_for_category(category)
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
        header = self._openapi_header(payload)
        result_code = str(header.get("resultCode") or "").strip()
        result_message = str(header.get("resultMsg") or "").strip()
        if result_code and result_code not in {"00", "03"}:
            raise ValueError(
                f"KONEPS ScsbidInfoService returned resultCode={result_code}: "
                f"{result_message or 'unknown error'}"
            )

        body = self._openapi_body(payload)
        rows = self._openapi_item_list(body)
        detail = self._summarize_scsbid_reserve_detail(rows)
        detail.update(
            {
                "reserve_detail_operation": operation,
                "reserve_detail_result_code": result_code or "00",
                "reserve_detail_result_message": result_message,
                "reserve_detail_total_count": self._safe_int(body.get("totalCount")),
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
        winning_amount = self._coerce_amount(raw_item.get("sucsfbidAmt"))
        success_rate = self._normalize_bid_rate_value(raw_item.get("sucsfbidRate"))
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
            "closing_at": self._coerce_datetime(opened_at),
            "business_type": resolved_category or request.category,
            "region": self._extract_region([demand_agency, title]),
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
                "participant_count": self._safe_int(raw_item.get("prtcptCnum")),
                "winning_company": raw_item.get("bidwinnrNm"),
                "winning_business_no": raw_item.get("bidwinnrBizno"),
                "winning_amount": winning_amount,
                "winning_rate": success_rate,
                "bid_rate": self._normalize_bid_rate_value(bid_rate),
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

    def _scsbid_operation_for_category(self, category: str | None) -> str:
        """Choose the ScsbidInfoService award operation for an internal category."""
        normalized_category = str(category or "").strip().lower()
        return self.SCSBID_CATEGORY_OPERATIONS.get(
            normalized_category,
            "getScsbidListSttusServc",
        )

    def _scsbid_reserve_detail_operation_for_category(
        self, category: str | None
    ) -> str:
        """Choose the ScsbidInfoService reserve-detail operation for a category."""
        normalized_category = str(category or "").strip().lower()
        return self.SCSBID_RESERVE_DETAIL_OPERATIONS.get(
            normalized_category,
            "getOpengResultListInfoServcPreparPcDetail",
        )

    def _summarize_scsbid_reserve_detail(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Summarize reserve-detail rows into prices, selected numbers, and prices."""
        reserve_rows: list[tuple[int, float]] = []
        selected_numbers: list[int] = []
        planned_price: float | None = None
        base_amount: float | None = None

        for row in rows:
            if planned_price is None:
                planned_price = self._coerce_amount(row.get("plnprc"))
            if base_amount is None:
                base_amount = self._coerce_amount(row.get("bssamt"))

            sequence = self._coerce_int_value(row.get("compnoRsrvtnPrceSno"))
            reserve_price = self._coerce_amount(row.get("bsisPlnprc"))
            if sequence is not None and reserve_price is not None:
                reserve_rows.append((sequence, reserve_price))

            drawn = str(row.get("drwtYn") or "").strip().upper()
            if sequence is not None and drawn in {"Y", "1", "TRUE", "예", "추첨"}:
                selected_numbers.append(sequence)

        reserve_rows.sort(key=lambda item: item[0])
        return {
            "reserve_prices": [price for _, price in reserve_rows],
            "selected_numbers": sorted(set(selected_numbers)),
            "planned_price": planned_price,
            "base_amount": base_amount,
            "raw_reserve_detail_items": rows,
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
        variants = self._openapi_service_key_variants(service_key)
        last_response: requests.Response | None = None

        for variant_name, variant_value, value_is_preencoded in variants:
            if value_is_preencoded:
                query_string = self._openapi_query_string(
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

    def _openapi_service_key_variants(
        self, service_key: str
    ) -> list[tuple[str, str, bool]]:
        """Return distinct service key variants without logging the key value."""
        raw_key = str(service_key or "").strip()
        encoded_key = quote_plus(raw_key, safe="")
        configured_encoded_key = str(
            settings.KONEPS_OPENAPI_ENCODED_SERVICE_KEY or ""
        ).strip()
        variants: list[tuple[str, str, bool]] = [("configured", raw_key, False)]
        if encoded_key != raw_key:
            variants.append(("url_encoded", encoded_key, True))
        if configured_encoded_key and configured_encoded_key not in {
            raw_key,
            encoded_key,
        }:
            variants.append(("configured_encoded", configured_encoded_key, True))
        return variants

    def _openapi_query_string(
        self,
        *,
        params: dict[str, Any],
        preencoded_keys: set[str],
    ) -> str:
        """Build a query string while preserving already-encoded key values."""
        query_parts: list[str] = []
        for key, value in params.items():
            encoded_key = quote_plus(str(key), safe="")
            encoded_value = (
                str(value)
                if key in preencoded_keys
                else quote_plus(str(value), safe="")
            )
            query_parts.append(f"{encoded_key}={encoded_value}")
        return "&".join(query_parts)

    def _openapi_operation_for_category(self, category: str | None) -> str:
        """Choose the BidPublicInfoService operation matching the internal category."""
        normalized_category = str(category or "").strip().lower()
        return self.OPENAPI_CATEGORY_OPERATIONS.get(
            normalized_category,
            "getBidPblancListInfoServc",
        )

    def _openapi_date_token(self, target_date: str | None) -> str:
        """Return YYYYMMDD for OpenAPI date-time query parameters."""
        raw_value = str(target_date or utc_now().date().isoformat()).strip()
        compact = re.sub(r"\D", "", raw_value)
        if len(compact) >= 8:
            return compact[:8]
        parsed = self._coerce_datetime(raw_value)
        if parsed is None:
            raise ValueError(f"target_date must be parseable as a date: {raw_value}")
        return parsed.strftime("%Y%m%d")

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

    def _openapi_header(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extract the normalized OpenAPI response header."""
        response = (
            payload.get("response") if isinstance(payload.get("response"), dict) else {}
        )
        header = (
            response.get("header") if isinstance(response.get("header"), dict) else {}
        )
        return dict(header)

    def _openapi_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Extract the normalized OpenAPI response body."""
        response = (
            payload.get("response") if isinstance(payload.get("response"), dict) else {}
        )
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        return dict(body)

    def _openapi_item_list(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        """Return a list of item dictionaries across supported JSON response shapes."""
        items_container = body.get("items")
        if isinstance(items_container, dict):
            raw_items = items_container.get("item", [])
        else:
            raw_items = items_container or []
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            return []
        return [dict(item) for item in raw_items if isinstance(item, dict)]

    def _build_openapi_notice_item(
        self,
        raw_item: dict[str, Any],
        *,
        request: CrawlRequest,
        operation: str,
    ) -> dict[str, Any] | None:
        """Convert one OpenAPI row into the existing crawl notice payload."""
        notice_number = str(
            raw_item.get("bidNtceNo")
            or raw_item.get("bidPbancNo")
            or raw_item.get("bfSpecRgstNo")
            or ""
        ).strip()
        if not notice_number:
            return None

        title = str(
            raw_item.get("bidNtceNm") or raw_item.get("ntceNm") or notice_number
        ).strip()
        base_amount = self._first_openapi_amount(
            raw_item,
            [
                "asignBdgtAmt",
                "bdgtAmt",
                "presmptPrce",
                "presmptAmt",
                "bssAmt",
                "bssamt",
                "bssAmtPurcnstcst",
            ],
        )
        estimated_amount = self._first_openapi_amount(
            raw_item,
            ["presmptPrce", "presmptAmt", "asignBdgtAmt", "bdgtAmt"],
        )
        business_type = str(
            raw_item.get("bsnsDivNm")
            or raw_item.get("prcmBsneSeCd")
            or request.category
            or ""
        ).strip()
        demand_agency = str(raw_item.get("dminsttNm") or "").strip()
        issuing_agency = str(raw_item.get("ntceInsttNm") or "").strip()
        opening_at = (
            raw_item.get("opengDt")
            or raw_item.get("opengDate")
            or raw_item.get("bidOpenDt")
        )
        closing_at = (
            self._coerce_datetime(raw_item.get("bidClseDt"))
            or self._coerce_datetime(opening_at)
            or self._coerce_datetime(raw_item.get("bidNtceDt"))
        )
        source_url = (
            str(
                raw_item.get("bidNtceDtlUrl") or raw_item.get("ntceSpecDocUrl1") or ""
            ).strip()
            or None
        )
        license_text = " ".join(
            str(raw_item.get(key) or "")
            for key in ("indstrytyCd", "indstrytyNm", "lcnsLmtNm", "prtcptLmtRgnNm")
        )

        return {
            "notice_number": notice_number,
            "title": title,
            "base_amount": float(base_amount or 0.0),
            "estimated_amount": float(estimated_amount or base_amount or 0.0),
            "closing_at": closing_at,
            "business_type": business_type or request.category,
            "region": str(raw_item.get("prtcptLmtRgnNm") or "").strip() or None,
            "license_codes": self._extract_license_codes(license_text),
            "source_url": source_url,
            "metadata": {
                "mode": "openapi",
                "openapi_service": "BidPublicInfoService",
                "openapi_operation": operation,
                "bid_notice_order": raw_item.get("bidNtceOrd"),
                "notice_kind": raw_item.get("ntceKindNm"),
                "registration_type": raw_item.get("rgstTyNm"),
                "bid_method": raw_item.get("bidMethdNm"),
                "contract_method": raw_item.get("cntrctCnclsMthdNm"),
                "business_type": business_type,
                "demand_agency": demand_agency,
                "opening_demand_agency": demand_agency,
                "issuing_agency": issuing_agency,
                "opening_status": raw_item.get("ntceKindNm"),
                "opening_scheduled_at": opening_at,
                "bid_notice_datetime": raw_item.get("bidNtceDt"),
                "bid_begin_at": raw_item.get("bidBeginDt"),
                "bid_close_at": raw_item.get("bidClseDt"),
                "reference_number": raw_item.get("refNo"),
                "raw_openapi_item": raw_item,
            },
        }

    def _first_openapi_amount(
        self,
        raw_item: dict[str, Any],
        candidate_keys: list[str],
    ) -> float | None:
        """Return the first parseable amount from one OpenAPI row."""
        for key in candidate_keys:
            value = self._coerce_amount(raw_item.get(key))
            if value is not None:
                return value
        return None

    def _safe_int(self, value: Any) -> int | None:
        """Convert optional OpenAPI count fields into integers."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
            "opening_result_grid_id": self.OPENING_RESULT_GRID_ID,
            "opening_result_row_count": 0,
            "opening_result_enriched_count": 0,
        }
        try:
            opening_rows = self._collect_opening_result_rows(request)
            parsed_items, opening_result_metadata = self._merge_opening_result_rows(
                parsed_items, opening_rows
            )
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
                "result_table_id": self.HOME_SEARCH_RESULT_TABLE_ID,
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
        page.wait_for_selector(f"#{self.OPENING_RESULT_GRID_ID}")
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
            self.OPENING_RESULT_DATA_LIST_KEY,
        )
        return [
            normalized_row
            for normalized_row in (
                self._normalize_opening_result_row(row) for row in rows or []
            )
            if normalized_row.get("notice_number")
        ]

    def _merge_opening_result_rows(
        self,
        items: list[dict[str, Any]],
        opening_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Merge opening-result data into collected notice items by notice number."""
        opening_index: dict[str, dict[str, Any]] = {}
        normalized_rows = [
            row if row.get("notice_number") else self._normalize_opening_result_row(row)
            for row in opening_rows
        ]
        for row in normalized_rows:
            if row.get("notice_number"):
                opening_index[row["notice_number"]] = row
            if row.get("notice_full_number"):
                opening_index[row["notice_full_number"]] = row

        enriched_count = 0
        for item in items:
            notice_number = item.get("notice_number", "")
            opening_row = opening_index.get(notice_number)
            if opening_row is None and "-" in notice_number:
                opening_row = opening_index.get(notice_number.split("-", maxsplit=1)[0])
            if opening_row is None:
                continue

            item_metadata = dict(item.get("metadata", {}))
            item_metadata.update(
                {
                    "opening_notice_full_number": opening_row.get("notice_full_number"),
                    "opening_status": opening_row.get("status"),
                    "opening_scheduled_at": (
                        opening_row.get("scheduled_at").isoformat()
                        if opening_row.get("scheduled_at")
                        else None
                    ),
                    "opening_bid_classification": opening_row.get("bid_classification"),
                    "opening_bid_progress_order": opening_row.get("bid_progress_order"),
                    "opening_demand_agency": opening_row.get("demand_agency"),
                    "opening_business_type": opening_row.get("business_type"),
                    "opening_amount": opening_row.get("opening_amount"),
                    "opening_detail_collected": bool(
                        opening_row.get("reserve_prices")
                        or opening_row.get("selected_numbers")
                        or opening_row.get("winning_company")
                    ),
                }
            )

            if opening_row.get("reserve_prices"):
                item_metadata["reserve_prices"] = opening_row["reserve_prices"]
            if opening_row.get("selected_numbers"):
                item_metadata["selected_numbers"] = opening_row["selected_numbers"]
            if opening_row.get("winning_company"):
                item_metadata["winning_company"] = opening_row["winning_company"]
            if opening_row.get("winning_amount") is not None:
                item_metadata["winning_amount"] = opening_row["winning_amount"]
            if opening_row.get("winning_rate") is not None:
                item_metadata["winning_rate"] = opening_row["winning_rate"]
            if opening_row.get("announced_at") is not None:
                item_metadata["opening_announced_at"] = opening_row[
                    "announced_at"
                ].isoformat()

            item["metadata"] = item_metadata
            if not item.get("business_type") and opening_row.get("business_type"):
                item["business_type"] = opening_row["business_type"]
            if not item.get("region") and opening_row.get("demand_agency"):
                item["region"] = self._extract_region([opening_row["demand_agency"]])
            enriched_count += 1

        return items, {
            "opening_result_grid_id": self.OPENING_RESULT_GRID_ID,
            "opening_result_row_count": len(normalized_rows),
            "opening_result_enriched_count": enriched_count,
        }

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
        page.wait_for_selector(f"#{self.HOME_SEARCH_RESULT_TABLE_ID}")
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
            page.wait_for_selector(f"#{self.HOME_SEARCH_RESULT_TABLE_ID}")
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
            f"#{self.HOME_SEARCH_RESULT_TABLE_ID} a[id$='btnOpenKonepsInfo']"
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
        page.wait_for_selector(f"#{self.HOME_SEARCH_RESULT_TABLE_ID}")
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
        """Split 'NNNN 라벨' KONEPS 업종 셀로부터 코드/라벨을 분리.

        - 'NNNN 라벨' → ('NNNN', '라벨')
        - '라벨' 단독 → (None, '라벨')
        - 빈 문자열/None → (None, None)
        """
        if not raw:
            return None, None
        text = str(raw).strip()
        if not text:
            return None, None
        parts = text.split(maxsplit=1)
        if parts and parts[0].isdigit() and 3 <= len(parts[0]) <= 8:
            code = parts[0]
            label = parts[1].strip() if len(parts) > 1 else None
            return code, (label or None)
        return None, text

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
        """Parse a live KONEPS list page into crawl notice items."""
        soup = BeautifulSoup(html, "html.parser")
        result_table = soup.select_one(f"#{self.HOME_SEARCH_RESULT_TABLE_ID}")
        if result_table:
            return self._parse_koneps_result_table(
                result_table,
                request,
                page_url=page_url,
                page_number=page_number,
                detail_pages=detail_pages,
            )

        rows = soup.select("table tbody tr, table tr")
        parsed_items: list[CrawlNoticeItem] = []

        for index, row in enumerate(rows):
            cells = [cell.get_text(" ", strip=True) for cell in row.select("td, th")]
            link = row.select_one("a[href]")
            item = self._build_notice_from_cells(
                cells=cells,
                link=link,
                request=request,
                row_index=index,
                page_url=page_url,
                page_number=page_number,
            )
            if item:
                parsed_items.append(item)
            if len(parsed_items) >= request.max_items:
                break

        return parsed_items

    def _parse_koneps_result_table(
        self,
        table: Any,
        request: CrawlRequest,
        page_url: str | None,
        page_number: int,
        detail_pages: dict[str, dict[str, str]] | None = None,
    ) -> list[CrawlNoticeItem]:
        """Parse the real KONEPS 입찰공고 검색결과 테이블."""
        parsed_items: list[CrawlNoticeItem] = []

        for row_index, row in enumerate(table.select("tr")):
            cells = row.select("td")
            if len(cells) < 10:
                continue
            item = self._build_notice_from_result_row(
                cells=cells,
                request=request,
                row_index=row_index,
                page_url=page_url,
                page_number=page_number,
                detail_pages=detail_pages,
            )
            if item:
                parsed_items.append(item)
            if len(parsed_items) >= request.max_items:
                break

        return parsed_items

    def _build_notice_from_result_row(
        self,
        cells: list[Any],
        request: CrawlRequest,
        row_index: int,
        page_url: str | None,
        page_number: int,
        detail_pages: dict[str, dict[str, str]] | None = None,
    ) -> CrawlNoticeItem | None:
        """Build a notice item from the observed KONEPS search result row format."""
        if len(cells) < 15:
            return None

        notice_number = cells[2].get_text(" ", strip=True)
        title_cell = cells[3]
        title = self._extract_koneps_title(title_cell)
        business_type = cells[1].get_text(" ", strip=True)
        business_type_code, business_type_label = self._split_business_type_cell(business_type)
        status = cells[5].get_text(" ", strip=True)
        procurement_scope = cells[6].get_text(" ", strip=True)
        posted_at_text = cells[7].get_text(" ", strip=True)
        opening_at_text = cells[8].get_text(" ", strip=True)
        closing_at_text = cells[9].get_text(" ", strip=True)
        issuing_agency = cells[10].get_text(" ", strip=True)
        demand_agency = cells[11].get_text(" ", strip=True)
        contract_method = cells[12].get_text(" ", strip=True)
        detail_link = cells[4].select_one("a")
        detail_action_id = detail_link.get("id") if detail_link else None
        detail_snapshot = (
            detail_pages.get(detail_action_id)
            if detail_pages and detail_action_id
            else None
        )
        detail_data = (
            self._parse_detail_html(detail_snapshot["html"]) if detail_snapshot else {}
        )
        row_text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
        region = detail_data.get("region") or self._extract_region(
            [title, issuing_agency, demand_agency, row_text]
        )
        source_url = (
            detail_snapshot["url"]
            if detail_snapshot
            else (page_url or settings.KONEPS_HOME_URL)
        )
        base_amount = detail_data.get("base_amount") or 0.0
        estimated_amount = detail_data.get("estimated_amount")
        closing_at = (
            detail_data.get("closing_at")
            or self._extract_datetime(closing_at_text)
            or self._extract_datetime(opening_at_text)
        )
        license_codes = detail_data.get("license_codes") or self._extract_license_codes(
            row_text
        )

        return CrawlNoticeItem(
            notice_number=notice_number
            or f"LIVE-{request.target_date.replace('-', '')}-{row_index + 1:03d}",
            title=detail_data.get("title") or title,
            base_amount=base_amount,
            estimated_amount=estimated_amount,
            closing_at=closing_at,
            business_type=detail_data.get("business_type") or business_type,
            business_type_code=detail_data.get("business_type_code") or business_type_code,
            business_type_label=detail_data.get("business_type_label") or business_type_label,
            region=region,
            license_codes=license_codes,
            source_url=source_url,
            metadata={
                "mode": "live",
                "page_number": page_number,
                "row_index": row_index,
                "amount_source": (
                    "detail_page" if detail_snapshot else "missing_in_list"
                ),
                "posted_at": posted_at_text,
                "opening_at": detail_data.get("opening_at_text") or opening_at_text,
                "status": status,
                "procurement_scope": procurement_scope,
                "issuing_agency": issuing_agency,
                "demand_agency": demand_agency,
                "contract_method": contract_method,
                "detail_action_id": detail_action_id,
                "detail_collected": bool(detail_snapshot),
                "detail_notice_number": detail_data.get("notice_number"),
                "target_date": request.target_date,
            },
        )

    def _parse_detail_html(self, html: str) -> dict[str, Any]:
        """Parse a detail popup page into normalized notice fields."""
        soup = BeautifulSoup(html, "html.parser")
        field_map: dict[str, str] = {}

        for row in soup.select("tr"):
            cells = row.select("th, td")
            texts = [
                cell.get_text(" ", strip=True)
                for cell in cells
                if cell.get_text(" ", strip=True)
            ]
            if len(texts) < 2:
                continue
            for index in range(0, len(texts) - 1, 2):
                key = texts[index]
                value = texts[index + 1]
                if key and value and key not in field_map:
                    field_map[key] = value

        base_amounts = self._extract_amounts(field_map.get("기초금액", ""))
        estimated_amounts = self._extract_amounts(field_map.get("추정가격", ""))
        license_codes = self._extract_license_codes(field_map.get("면허제한", ""))
        region = self._extract_region([field_map.get("제한지역", "")])
        business_type_raw = field_map.get("입찰유형") or field_map.get("업무구분")
        business_type_code, business_type_label = self._split_business_type_cell(
            business_type_raw
        )

        return {
            "notice_number": field_map.get("입찰공고번호"),
            "title": field_map.get("입찰공고명"),
            "business_type": business_type_raw,
            "business_type_code": business_type_code,
            "business_type_label": business_type_label,
            "base_amount": base_amounts[0] if base_amounts else None,
            "estimated_amount": estimated_amounts[0] if estimated_amounts else None,
            "closing_at": self._extract_datetime(field_map.get("입찰마감일시", "")),
            "opening_at_text": field_map.get("개찰일시"),
            "license_codes": license_codes,
            "region": region,
        }

    def fetch_detail_html_payload(self, source_url: str) -> dict[str, str | None]:
        """Fetch + parse a single KONEPS detail page, returning the business-type fields.

        Performs a simple HTTP GET on ``source_url``, parses the HTML with the
        same ``_parse_detail_html`` helper used during live collection, and
        returns only the two business-type keys.

        Best-effort: any exception raised by the HTTP call is propagated so
        callers (e.g. backfill scripts) can record per-row failures.
        """
        timeout = max(1, int(getattr(settings, "KONEPS_OPENAPI_TIMEOUT_SECONDS", 30)))
        response = requests.get(source_url, timeout=timeout)
        response.raise_for_status()
        detail = self._parse_detail_html(response.text)
        return {
            "business_type_code": detail.get("business_type_code"),
            "business_type_label": detail.get("business_type_label"),
        }

    def _normalize_opening_result_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Normalize an opening-result row or mocked detail payload into a stable structure."""
        detail_data = {}
        detail_html = row.get("detail_html")
        if isinstance(detail_html, str) and detail_html.strip():
            detail_data.update(self._parse_opening_detail_html(detail_html))
        if isinstance(row.get("detail"), dict):
            detail_data.update(
                {
                    key: value
                    for key, value in row["detail"].items()
                    if value not in (None, "", [], {})
                }
            )

        notice_number = str(
            row.get("notice_number")
            or row.get("bidPbancNo")
            or detail_data.get("notice_number")
            or ""
        ).strip()
        notice_order = str(
            row.get("notice_order")
            or row.get("bidPbancOrd")
            or detail_data.get("notice_order")
            or ""
        ).strip()
        notice_full_number = str(
            row.get("notice_full_number")
            or row.get("bidPbancNoPbancOrd")
            or f"{notice_number}-{notice_order}".strip("-")
        ).strip()
        opening_amount = self._coerce_amount(
            row.get("opening_amount") or row.get("bizAmt")
        )

        return {
            "notice_number": notice_number,
            "notice_order": notice_order,
            "notice_full_number": notice_full_number,
            "title": unescape(
                str(
                    row.get("title")
                    or row.get("bidPbancNm")
                    or detail_data.get("title")
                    or ""
                )
            ).strip(),
            "bid_classification": str(
                row.get("bid_classification") or row.get("bidClsfNo") or ""
            ).strip(),
            "bid_progress_order": str(
                row.get("bid_progress_order") or row.get("bidPrgrsOrd") or ""
            ).strip(),
            "demand_agency": str(
                row.get("demand_agency") or row.get("dmstGrpNm") or ""
            ).strip(),
            "status": str(
                row.get("status")
                or row.get("bidPgstCd")
                or detail_data.get("result_status")
                or ""
            ).strip(),
            "scheduled_at": self._coerce_datetime(
                row.get("scheduled_at")
                or row.get("onbsPrnmntDt")
                or detail_data.get("announced_at")
            ),
            "business_type": row.get("business_type")
            or self._map_opening_business_type(row.get("prcmBsneSeCd")),
            "opening_amount": opening_amount,
            "reserve_prices": detail_data.get("reserve_prices")
            or row.get("reserve_prices")
            or [],
            "selected_numbers": detail_data.get("selected_numbers")
            or row.get("selected_numbers")
            or [],
            "winning_company": detail_data.get("winning_company")
            or row.get("winning_company"),
            "winning_amount": (
                detail_data.get("winning_amount")
                if detail_data.get("winning_amount") is not None
                else row.get("winning_amount")
            ),
            "winning_rate": (
                detail_data.get("winning_rate")
                if detail_data.get("winning_rate") is not None
                else row.get("winning_rate")
            ),
            "announced_at": self._coerce_datetime(
                detail_data.get("announced_at") or row.get("announced_at")
            ),
            "raw": row,
        }

    def _parse_opening_detail_html(self, html: str) -> dict[str, Any]:
        """Parse opening-result detail HTML into reserve prices, picked numbers, and winner info."""
        soup = BeautifulSoup(html, "html.parser")
        field_map: dict[str, str] = {}

        for row in soup.select("tr"):
            cells = row.select("th, td")
            texts = [
                cell.get_text(" ", strip=True)
                for cell in cells
                if cell.get_text(" ", strip=True)
            ]
            if len(texts) < 2:
                continue
            for index in range(0, len(texts) - 1, 2):
                key = texts[index]
                value = texts[index + 1]
                if key and value and key not in field_map:
                    field_map[key] = value

        all_text = soup.get_text(" ", strip=True)
        reserve_text = self._find_field_value(
            field_map, ["복수예비가격", "예비가격", "추첨예비가격"]
        )
        if not reserve_text:
            reserve_match = re.search(
                r"복수예비가격\s*(.*?)(?:선택번호|추첨번호|낙찰자|낙찰업체|낙찰금액|낙찰률|개찰일시|$)",
                all_text,
            )
            reserve_text = reserve_match.group(1) if reserve_match else ""

        selected_text = self._find_field_value(
            field_map, ["선택번호", "추첨번호", "선정번호"]
        )
        if not selected_text:
            selected_match = re.search(
                r"(?:선택번호|추첨번호|선정번호)\s*(.*?)(?:낙찰자|낙찰업체|낙찰금액|낙찰률|개찰일시|$)",
                all_text,
            )
            selected_text = selected_match.group(1) if selected_match else ""

        winning_company = self._find_field_value(
            field_map, ["낙찰업체", "낙찰자", "낙찰자명", "계약상대자"]
        )
        winning_amount = self._coerce_amount(
            self._find_field_value(field_map, ["낙찰금액", "낙찰가격", "투찰금액"])
        )
        winning_rate = self._extract_percentage(
            self._find_field_value(field_map, ["낙찰률", "투찰률", "낙찰하한율"])
        )
        announced_at = self._coerce_datetime(
            self._find_field_value(field_map, ["개찰일시", "개찰완료일시", "낙찰일시"])
        )
        status = self._find_field_value(field_map, ["진행상태", "개찰상태", "낙찰상태"])

        return {
            "reserve_prices": self._extract_amounts(reserve_text)[:15],
            "selected_numbers": self._extract_integer_tokens(
                selected_text, max_items=4
            ),
            "winning_company": winning_company or None,
            "winning_amount": winning_amount,
            "winning_rate": winning_rate,
            "announced_at": announced_at,
            "result_status": status or None,
        }

    def _build_notice_from_cells(
        self,
        cells: list[str],
        link: Any,
        request: CrawlRequest,
        row_index: int,
        page_url: str | None,
        page_number: int,
    ) -> CrawlNoticeItem | None:
        """Convert parsed table cells into a normalized crawl notice item."""
        cleaned_cells = [cell for cell in cells if cell]
        if not cleaned_cells:
            return None

        combined_text = " ".join(cleaned_cells)
        amounts = self._extract_amounts(combined_text)
        notice_number = (
            self._extract_notice_number(combined_text)
            or f"LIVE-{request.target_date.replace('-', '')}-{row_index + 1:03d}"
        )
        title = self._extract_title(
            cleaned_cells, notice_number, link.get_text(strip=True) if link else None
        )

        if not title:
            return None

        region = self._extract_region(cleaned_cells)
        closing_at = self._extract_datetime(combined_text)
        href = link.get("href") if link else None
        source_url = (
            urljoin(page_url or settings.KONEPS_BASE_URL, href)
            if href
            else (page_url or settings.KONEPS_HOME_URL)
        )

        return CrawlNoticeItem(
            notice_number=notice_number,
            title=title,
            base_amount=amounts[0] if amounts else 0.0,
            estimated_amount=amounts[1] if len(amounts) > 1 else None,
            closing_at=closing_at,
            business_type=request.category,
            region=region,
            license_codes=self._extract_license_codes(combined_text),
            source_url=source_url,
            metadata={
                "mode": "live",
                "cell_count": len(cleaned_cells),
                "page_number": page_number,
                "row_index": row_index,
                "target_date": request.target_date,
            },
        )

    def _extract_koneps_title(self, title_cell: Any) -> str:
        """Extract the visible KONEPS title from a result cell with optional state badges."""
        linked_title = title_cell.select_one(".link_txt")
        if linked_title:
            return linked_title.get_text(" ", strip=True)

        title_attr = title_cell.get("title")
        if title_attr:
            return title_attr.strip()

        return title_cell.get_text(" ", strip=True)

    def _extract_notice_number(self, text: str) -> str | None:
        """Extract a plausible notice number from freeform row text."""
        match = re.search(r"\b(?:\d{4,}-\d{2,}|\d{8,}|[A-Z]{2,}\d{2,})\b", text)
        return match.group(0) if match else None

    def _extract_amounts(self, text: str) -> list[float]:
        """Extract monetary values from text."""
        matches = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text)
        amounts = []
        for value in matches:
            try:
                amounts.append(float(value.replace(",", "")))
            except ValueError:
                continue
        return amounts

    def _extract_datetime(self, text: str) -> datetime | None:
        """Extract a datetime value from text when possible."""
        patterns = [
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y.%m.%d",
            "%Y/%m/%d",
        ]
        match = re.search(
            r"\d{4}[-./]\d{2}[-./]\d{2}(?:[T\s]+\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?",
            text,
        )
        if not match:
            return None

        raw = match.group(0)
        for pattern in patterns:
            try:
                return ensure_utc(datetime.strptime(raw, pattern))
            except ValueError:
                continue

        return None

    def _extract_title(
        self, cells: list[str], notice_number: str, link_text: str | None
    ) -> str | None:
        """Choose the most likely title cell."""
        generic_link_texts = {"상세", "상세보기", "보기", "조회", "바로가기"}
        if link_text and link_text.strip() not in generic_link_texts:
            return link_text.strip()

        for cell in cells:
            if cell == notice_number:
                continue
            if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", cell):
                continue
            if re.fullmatch(r"\d{4}[-./]\d{2}[-./]\d{2}(?:\s+\d{2}:\d{2})?", cell):
                continue
            return cell.strip()

        return None

    def _extract_region(self, cells: list[str]) -> str | None:
        """Extract a likely Korean region value."""
        region_keywords = [
            "서울",
            "부산",
            "대구",
            "인천",
            "광주",
            "대전",
            "울산",
            "세종",
            "경기",
            "강원",
            "충북",
            "충남",
            "전북",
            "전남",
            "경북",
            "경남",
            "제주",
            "전국",
        ]
        for cell in cells:
            for keyword in region_keywords:
                if keyword in cell:
                    return keyword
        return None

    def _extract_license_codes(self, text: str) -> list[str]:
        """Extract structured license-like codes from row text."""
        return sorted(set(re.findall(r"\b[A-Z]{2,}\d{2,}\b", text)))

    def _extract_percentage(self, text: str) -> float | None:
        """Extract a percentage value from text."""
        if not text:
            return None
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
        if match:
            return float(match.group(1))
        try:
            return float(str(text).strip())
        except ValueError:
            return None

    def _normalize_bid_rate_value(self, value: Any) -> float | None:
        """Normalize percentage-like bid rates into predictor-friendly ratios."""
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            numeric = float(value)
        else:
            percentage = self._extract_percentage(str(value))
            if percentage is not None:
                numeric = percentage
            else:
                try:
                    numeric = float(str(value).replace(",", "").strip())
                except ValueError:
                    return None
        if numeric <= 0:
            return None
        if numeric > 1.5:
            numeric = numeric / 100.0
        return round(float(numeric), 6)

    def _coerce_int_value(self, value: Any) -> int | None:
        """Convert numeric text into an integer when possible."""
        if value in (None, ""):
            return None
        try:
            return int(float(str(value).replace(",", "").strip()))
        except ValueError:
            return None

    def _extract_integer_tokens(
        self, text: str, max_items: int | None = None
    ) -> list[int]:
        """Extract integer tokens from text, optionally limiting the result length."""
        numbers = [int(token) for token in re.findall(r"\b\d{1,2}\b", text)]
        if max_items is None:
            return numbers
        return numbers[:max_items]

    def _find_field_value(self, field_map: dict[str, str], labels: list[str]) -> str:
        """Find the first matching field value using partial Korean label matching."""
        for label in labels:
            for key, value in field_map.items():
                if label in key:
                    return value
        return ""

    def _map_opening_business_type(self, code: str | None) -> str | None:
        """Map observed 개찰결과 업무코드 values to readable business types."""
        code_map = {
            "01": "물품",
            "03": "일반용역",
            "05": "기술용역",
            "07": "공사",
        }
        if not code:
            return None
        return code_map.get(str(code).strip(), str(code).strip())

    def _coerce_amount(self, value: Any) -> float | None:
        """Convert arbitrary numeric text into a float amount when possible."""
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        amounts = self._extract_amounts(str(value))
        if amounts:
            return amounts[0]
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            return None

    def _coerce_datetime(self, value: Any) -> datetime | None:
        """Convert an arbitrary value into a datetime when possible."""
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return ensure_utc(value)
        if isinstance(value, str):
            try:
                return ensure_utc(datetime.fromisoformat(value))
            except ValueError:
                return self._extract_datetime(value)
        return None

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
                category=self._resolve_project_category(item, request),
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
        """Heuristically link a crawled notice to an existing project using explicit keys first."""
        target_title = self._normalize_title(item.get("title"))
        target_notice_number = self._normalize_notice_number(item.get("notice_number"))
        target_source_url = self._normalize_source_url(item.get("source_url"))
        target_agencies = self._extract_item_agency_keys(item)
        target_category = self._resolve_project_category(item, request)
        target_budget = self._resolve_budget_estimate(item)
        target_deadline = self._coerce_datetime(item.get("closing_at"))

        query = db.query(Project)
        if target_category:
            query = query.filter(Project.category == target_category)

        candidates = query.all()

        if target_notice_number:
            for candidate in candidates:
                candidate_notice_number = self._normalize_notice_number(
                    candidate.notice_number
                    or self._extract_project_notice_number(candidate)
                )
                if (
                    candidate_notice_number
                    and candidate_notice_number == target_notice_number
                ):
                    return candidate

        if target_source_url:
            for candidate in candidates:
                candidate_source_url = self._normalize_source_url(
                    candidate.source_url or self._extract_project_source_url(candidate)
                )
                if candidate_source_url and candidate_source_url == target_source_url:
                    return candidate

        if not target_title:
            return None

        best_candidate: Project | None = None
        best_score = -1
        for candidate in candidates:
            candidate_title = self._normalize_title(candidate.title)
            title_exact = candidate_title == target_title
            title_overlap = (
                not title_exact
                and bool(candidate_title)
                and (target_title in candidate_title or candidate_title in target_title)
            )
            if not title_exact and not title_overlap:
                continue

            budget_match = self._is_budget_compatible(candidate, target_budget)
            deadline_match = self._is_deadline_compatible(
                candidate.deadline, target_deadline
            )
            agency_overlap = len(
                self._extract_project_agency_keys(candidate) & target_agencies
            )

            matches = (
                (title_exact and agency_overlap > 0)
                or (title_exact and budget_match and deadline_match)
                or (
                    title_overlap
                    and agency_overlap > 0
                    and budget_match
                    and deadline_match
                )
            )
            if not matches:
                continue

            score = (
                (6 if title_exact else 3)
                + (3 if agency_overlap > 0 else 0)
                + (2 if budget_match else 0)
                + (1 if deadline_match else 0)
            )
            if score > best_score:
                best_candidate = candidate
                best_score = score

        return best_candidate

    def _update_project_from_item(
        self, project: Project, *, item: dict[str, Any], request: CrawlRequest
    ) -> None:
        """Apply crawled notice details onto a project without discarding user-entered context."""
        item_metadata = item.get("metadata", {})
        resolved_category = self._resolve_project_category(item, request)
        budget_estimate = self._resolve_budget_estimate(item)
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

        if item.get("title") and self._should_replace_project_title(
            project.title, item.get("title")
        ):
            project.title = str(item.get("title")).strip()
        notice_number = item.get("notice_number")
        if notice_number and (
            not project.notice_number
            or self._normalize_notice_number(project.notice_number)
            == self._normalize_notice_number(notice_number)
        ):
            project.notice_number = str(notice_number).strip()
        source_url = item.get("source_url")
        if source_url and (
            not project.source_url
            or self._normalize_source_url(project.source_url)
            == self._normalize_source_url(source_url)
        ):
            project.source_url = str(source_url).strip()
        issuing_agency = item_metadata.get("issuing_agency")
        if issuing_agency and (
            not project.issuing_agency
            or self._normalize_agency_name(project.issuing_agency)
            == self._normalize_agency_name(issuing_agency)
        ):
            project.issuing_agency = str(issuing_agency).strip()
        demand_agency = item_metadata.get("opening_demand_agency") or item_metadata.get(
            "demand_agency"
        )
        if demand_agency and (
            not project.demand_agency
            or self._normalize_agency_name(project.demand_agency)
            == self._normalize_agency_name(demand_agency)
        ):
            project.demand_agency = str(demand_agency).strip()
        project.description = self._merge_text_lines(
            project.description, description_lines
        )
        project.requirements = self._merge_text_lines(
            project.requirements, requirement_lines
        )
        project.category = resolved_category or project.category
        project.budget_estimate = budget_estimate or float(
            project.budget_estimate or 0.0
        )
        project.budget_min = min(budget_values) if budget_values else project.budget_min
        project.budget_max = max(budget_values) if budget_values else project.budget_max

        closing_at = self._coerce_datetime(item.get("closing_at"))
        if closing_at is not None:
            project.deadline = closing_at

        resolved_status = self._resolve_project_status(item)
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
        announced_at = self._coerce_datetime(item_metadata.get("opening_announced_at"))
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

    def _resolve_project_category(
        self, item: dict[str, Any], request: CrawlRequest
    ) -> str:
        """Resolve the internal project category for a crawled notice."""
        request_category = str(request.category or "").strip().lower()
        if request_category and request_category not in {"general", "기타", "other"}:
            return request_category

        business_type = str(item.get("business_type") or "").strip().lower()
        category_map = {
            "소프트웨어": "software",
            "software": "software",
            "기술용역": "technical-service",
            "technical-service": "technical-service",
            "일반용역": "service",
            "service": "service",
            "general-service": "service",
            "물품": "goods",
            "goods": "goods",
            "공사": "construction",
            "construction": "construction",
        }
        return category_map.get(
            business_type, request_category or business_type or "other"
        )

    def _resolve_budget_estimate(self, item: dict[str, Any]) -> float:
        """Prefer the most actionable estimate while falling back to the available base amount."""
        for value in (item.get("estimated_amount"), item.get("base_amount")):
            if value not in (None, "", 0, 0.0):
                return float(value)
        return 0.0

    def _resolve_project_status(self, item: dict[str, Any]) -> str:
        """Map crawl timing and opening metadata to an internal project lifecycle state."""
        item_metadata = item.get("metadata", {})
        status_text = " ".join(
            str(value or "")
            for value in (
                item_metadata.get("opening_status"),
                item_metadata.get("status"),
                item_metadata.get("opening_bid_classification"),
                item_metadata.get("opening_bid_progress_order"),
                item.get("title"),
            )
        )
        normalized_status_text = self._normalize_status_text(status_text)

        if any(
            keyword in normalized_status_text
            for keyword in ("취소", "공고취소", "입찰취소", "개찰취소", "정정취소")
        ):
            return "cancelled"
        if any(
            keyword in normalized_status_text
            for keyword in ("재공고", "재입찰", "재안내", "2차공고", "3차공고")
        ):
            return "re_notice"
        if any(
            keyword in normalized_status_text
            for keyword in ("유찰", "무응찰", "무투찰", "개찰불성립", "낙찰자없음")
        ):
            return "failed"
        if any(
            item_metadata.get(key)
            for key in (
                "winning_company",
                "winning_amount",
                "winning_rate",
                "opening_announced_at",
            )
        ):
            return "awarded"
        if any(keyword in normalized_status_text for keyword in ("낙찰", "계약완료")):
            return "awarded"
        if any(
            keyword in normalized_status_text
            for keyword in ("마감", "종료", "개찰완료", "개찰진행", "개찰대기")
        ):
            return "closed"

        closing_at = self._coerce_datetime(item.get("closing_at"))
        if closing_at is not None and closing_at <= utc_now():
            return "closed"
        return "open"

    def _normalize_status_text(self, value: Any) -> str:
        """Normalize crawl status text for keyword-based lifecycle mapping."""
        return re.sub(r"\s+", "", str(value or "").strip().lower())

    def _merge_text_lines(
        self, existing: str | None, new_lines: list[str | None]
    ) -> str:
        """Append unique crawl-derived text fragments while keeping any manual notes intact."""
        merged_lines = [
            line.strip()
            for line in str(existing or "").splitlines()
            if line and line.strip()
        ]
        merged_text = "\n".join(merged_lines)

        for line in new_lines:
            if not line:
                continue
            normalized_line = str(line).strip()
            if not normalized_line:
                continue
            if normalized_line in merged_lines:
                continue
            if normalized_line in merged_text:
                continue
            merged_lines.append(normalized_line)
            merged_text = "\n".join(merged_lines)

        return "\n".join(merged_lines)

    def _normalize_title(self, value: Any) -> str:
        """Normalize a notice title for strict duplicate detection."""
        return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").strip().lower())

    def _normalize_notice_number(self, value: Any) -> str:
        """Normalize notice numbers for direct key matching."""
        return re.sub(r"\s+", "", str(value or "").strip().upper())

    def _normalize_source_url(self, value: Any) -> str:
        """Normalize a URL so detail links can be compared across formatting differences."""
        if not value:
            return ""
        parsed = urlparse(str(value).strip())
        if not parsed.scheme and not parsed.netloc:
            return str(value).strip().rstrip("/").lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        normalized_url = f"{netloc}{path}".strip().lower()
        significant_query_pairs = [
            (key.strip().lower(), val.strip().lower())
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if key.strip().lower()
            in {
                "bidntceno",
                "bidntceord",
                "bidpbancno",
                "bidpbancord",
            }
        ]
        if significant_query_pairs:
            normalized_query = urlencode(sorted(significant_query_pairs))
            return f"{normalized_url}?{normalized_query}"
        return normalized_url

    def _normalize_agency_name(self, value: Any) -> str:
        """Normalize agency names for cross-source matching."""
        return "".join(str(value or "").strip().lower().split())

    def _extract_item_agency_keys(self, item: dict[str, Any]) -> set[str]:
        """Extract normalized agency names from a crawled notice payload."""
        item_metadata = item.get("metadata", {})
        return {
            normalized
            for normalized in (
                self._normalize_agency_name(item_metadata.get("issuing_agency")),
                self._normalize_agency_name(item_metadata.get("opening_demand_agency")),
                self._normalize_agency_name(item_metadata.get("demand_agency")),
            )
            if normalized
        }

    def _extract_project_agency_keys(self, project: Project) -> set[str]:
        """Extract normalized agency names from a stored project, including labeled notes."""
        agency_values = [project.issuing_agency, project.demand_agency]
        project_text = "\n".join(
            filter(None, [project.description, project.requirements])
        )
        for label in ("공고기관", "수요기관"):
            label_match = re.search(rf"{label}\s*[:：]\s*([^\n]+)", project_text)
            if label_match:
                agency_values.append(label_match.group(1).strip())

        return {
            normalized
            for normalized in (
                self._normalize_agency_name(value) for value in agency_values
            )
            if normalized
        }

    def _extract_project_notice_number(self, project: Project) -> str | None:
        """Read a notice number from explicit project metadata or free-form notes."""
        if project.notice_number:
            return project.notice_number

        project_text = "\n".join(
            filter(None, [project.description, project.requirements])
        )
        label_match = re.search(r"공고번호\s*[:：]\s*([A-Za-z0-9\-]+)", project_text)
        if label_match:
            return label_match.group(1).strip()
        return self._extract_notice_number(project_text)

    def _extract_project_source_url(self, project: Project) -> str | None:
        """Read a source URL from explicit project metadata or free-form notes."""
        if project.source_url:
            return project.source_url

        project_text = "\n".join(
            filter(None, [project.description, project.requirements])
        )
        url_match = re.search(r"https?://[^\s]+", project_text)
        if url_match:
            return url_match.group(0).strip()
        return None

    def _is_budget_compatible(self, project: Project, target_budget: float) -> bool:
        """Return whether an existing project's budget is close enough to a crawled notice."""
        if target_budget <= 0:
            return True

        candidate_budget = float(
            project.budget_estimate or project.budget_max or project.budget_min or 0.0
        )
        if candidate_budget <= 0:
            return True

        difference_ratio = abs(candidate_budget - target_budget) / max(
            candidate_budget, target_budget
        )
        return difference_ratio <= 0.15

    def _is_deadline_compatible(
        self, existing_deadline: datetime | None, target_deadline: datetime | None
    ) -> bool:
        """Return whether existing and crawled deadlines are close enough to represent the same notice."""
        if existing_deadline is None or target_deadline is None:
            return True
        return (
            abs(
                (
                    ensure_utc(existing_deadline) - ensure_utc(target_deadline)
                ).total_seconds()
            )
            <= 60 * 60 * 24 * 7
        )

    def _should_replace_project_title(
        self, existing_title: str | None, new_title: Any
    ) -> bool:
        """Prefer the crawled title only when the current one is missing or obviously synthetic."""
        existing = str(existing_title or "").strip()
        if not existing:
            return True
        return existing.startswith("KONEPS notice") or existing.startswith("KONEPS-")

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
