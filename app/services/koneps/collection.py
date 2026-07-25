"""OpenAPI collection loop + request-normalization/mock helpers for the collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``). They have no IO beyond the already-extracted ``http_client``
HTTP helpers, no DB (``Session`` / ``db.query`` / ``db.add``), and no
instance-state dependencies -- they operate only on the request schema, plain
dicts, the module-level ``settings``, the clock helpers (``kst_now`` /
``utc_now``), and the already-extracted pure helpers in ``openapi`` / ``parsing``
plus the keyed-HTTP helpers in ``http_client``. They live here as module-level
functions to keep the collector class focused on orchestration, the scsbid
award sweep, DB persistence, and live-crawl IO.

Scope note: this module deliberately holds only the *general* (non-scsbid)
KONEPS OpenAPI notice loop. The ScsbidInfoService award sweep
(``_collect_scsbid_openapi_items`` and its page/item helpers) stays in the
collector because it is orchestration over a mutable sweep state and is being
evolved separately.

Behavior is intentionally identical to the original methods; this module is a
pure relocation, not a rewrite. To avoid an import cycle, this module must never
import ``collector``: the collector imports ``collection`` (and the sibling
``http_client`` / ``openapi`` / ``parsing`` modules), not the other way around.
The collector keeps a thin ``_normalize_request`` delegator for external callers
(tests) that invoke it as an instance method; the two fully-extracted helpers
(``collect_openapi_items`` / ``build_mock_items``) have no external callers and
are referenced only from ``collect_notices``.
"""

import logging
import math
from datetime import timedelta
from time import sleep
from typing import Any

from app.core.config import settings
from app.core.time import kst_now, utc_now
from app.schemas.schemas import CrawlNoticeItem, CrawlRequest
from app.services.koneps import http_client, openapi, parsing
from app.services.koneps.field_contract_observer import FieldContractObservation

logger = logging.getLogger(__name__)


def normalize_request(request: CrawlRequest) -> CrawlRequest:
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


def collect_openapi_items(request: CrawlRequest) -> dict[str, Any]:
    """Collect notice rows from the public KONEPS BidPublicInfoService OpenAPI.

    Paginates through all available pages (100 items per page) until either
    ``max_items`` unique notices are collected or the API returns no more items.
    ``totalCount`` from page 1 determines how many pages to fetch.
    """
    service_key = str(settings.KONEPS_OPENAPI_SERVICE_KEY or "").strip()
    if not service_key:
        raise ValueError(
            "KONEPS_OPENAPI_SERVICE_KEY is required for source=koneps-openapi"
        )

    operation = openapi.openapi_operation_for_category(request.category)
    date_token = openapi.openapi_date_token(request.target_date)
    per_page = settings.KONEPS_OPENAPI_COLLECTION_PAGE_SIZE  # KONEPS API 호출당 고정 페이지 크기
    max_total = max(1, int(request.max_items))
    # Runaway guard: totalCount 누락 + API가 pageNo를 무시하고 full 중복 페이지를
    # 무한 반환하는 이중 오작동 시 무한 루프(→ Celery time limit SIGKILL → orphan)를
    # 막는다. 정상 데이터는 max_total/total_pages가 먼저 종료하므로 이 캡은 totalCount
    # 누락 경로에서만 백스톱으로 작동한다(정상 truncation 위험 없음).
    max_pages = math.ceil(max_total / per_page) + 2
    url = f"{settings.KONEPS_OPENAPI_BID_PUBLIC_INFO_URL.rstrip('/')}/{operation}"
    base_params = {
        "type": "json",
        "numOfRows": per_page,
        "inqryDiv": "1",
        "inqryBgnDt": f"{date_token}0000",
        "inqryEndDt": f"{date_token}2359",
    }

    parsed_items: list[dict[str, Any]] = []
    seen_notice_numbers: set[str] = set()
    total_count = 0
    result_code = ""
    result_message = ""
    key_variant = ""
    pages_fetched = 0
    # 관찰 전용 계약 관찰기(#227 배선): 토글 ON 일 때만 생성해 raw item 을 순수 검증기에
    # 흘려보내며 위반·미지 필드를 run 단위로 센다. OFF 면 None → 무비용. 수집 동작 불변.
    contract_observer = (
        FieldContractObservation()
        if settings.KONEPS_FIELD_CONTRACT_LIVE_CHECK
        else None
    )

    page_no = 1
    while True:
        params = {**base_params, "pageNo": page_no}
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
        result_code, result_message = http_client.check_result_code(
            payload, source="OpenAPI returned"
        )

        body = openapi.openapi_body(payload)
        if page_no == 1:
            total_count = parsing.safe_int(body.get("totalCount")) or 0

        pages_fetched += 1
        raw_items = openapi.openapi_item_list(body)
        if not raw_items:
            break

        for raw_item in raw_items:
            if contract_observer is not None:
                # 관찰 전용: raw item 을 그대로 검증기에 통과시켜 집계만 한다(변경/드롭 없음).
                contract_observer.observe(raw_item, operation=operation)
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
            if len(parsed_items) >= max_total:
                break

        if len(parsed_items) >= max_total:
            break

        # totalCount를 신뢰할 수 없을 때(누락/0) full page면 다음 페이지를 계속 시도한다.
        # short page(<per_page)면 마지막 페이지로 간주하고 종료.
        total_pages = math.ceil(total_count / per_page) if total_count > 0 else None
        if total_pages is not None and page_no >= total_pages:
            break
        if total_pages is None and len(raw_items) < per_page:
            break
        if total_pages is None and pages_fetched >= max_pages:
            break
        # 다음 페이지 요청 전 throttle (첫 호출 뒤부터)
        if settings.KONEPS_OPENAPI_REQUEST_DELAY_SECONDS > 0:
            sleep(settings.KONEPS_OPENAPI_REQUEST_DELAY_SECONDS)
        page_no += 1

    # run 단위 계약 관찰 요약: per-item 이 아니라 run 당 한 줄만 로그(폭주 방지). 위반·미지
    # 필드가 있을 때만 WARN 을 남기고, 집계는 metadata 에 실어 소비자가 읽게 한다.
    contract_observation = None
    if contract_observer is not None:
        if contract_observer.has_findings:
            logger.warning(contract_observer.summary_line())
        contract_observation = contract_observer.summary()

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
            "openapi_total_count": total_count,
            "openapi_pages_fetched": pages_fetched,
            "openapi_last_page_no": page_no,
            "openapi_num_of_rows": per_page,
            "query_date": date_token,
            "query_type": "registration_datetime",
            "field_contract_observation": contract_observation,
        },
    }


def build_mock_items(
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

    return [item.model_dump(mode="json") for item in mock_items[: request.max_items]]
