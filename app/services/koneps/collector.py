"""KONEPS collector service skeleton."""
import json
import re
from datetime import datetime, timedelta
from html import unescape
from math import ceil
from time import sleep
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CrawlJob, HistoricalData, TenderResult
from app.core.time import ensure_utc, utc_now
from app.schemas.schemas import CrawlNoticeItem, CrawlRequest


class KonepsCollectorService:
    """Collect KONEPS notices/opening data."""

    HOME_SEARCH_KEYWORD_ID = "mf_wfm_container_wq_uuid_925_wq_uuid_934_searchKeyword"
    HOME_SEARCH_BUTTON_ID = "mf_wfm_container_wq_uuid_925_wq_uuid_934_btnBidPbancDtlSrch"
    HOME_SEARCH_TYPE_RADIO_ID = "mf_wfm_container_wq_uuid_925_wq_uuid_934_rbxSrchType_input_0"
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
    OPENING_RESULT_MENU_ID = "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_genMenu2_4_genMenu3_0_btnMenu3"
    OPENING_RESULT_BID_NO_ID = "mf_wfm_container_ibxBidPbancNo"
    OPENING_RESULT_TITLE_ID = "mf_wfm_container_wq_uuid_4242"
    OPENING_RESULT_START_DATE_ID = "wq_uuid_4247_ibxStrDay"
    OPENING_RESULT_END_DATE_ID = "wq_uuid_4247_ibxEndDay"
    OPENING_RESULT_SEARCH_BUTTON_ID = "mf_wfm_container_btnS0001"
    OPENING_RESULT_GRID_ID = "mf_wfm_container_onbsRsltClsfInqyGrd"
    OPENING_RESULT_DATA_LIST_KEY = "mf_wfm_container_dlOnbsRsltClsfListOutL"

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

        if normalized_request.execution_mode in {"live", "auto"}:
            try:
                live_result = self._collect_live_items(normalized_request)
                items = live_result["items"]
                response_metadata.update(live_result["metadata"])
                job_status = "completed"
            except Exception as exc:  # pragma: no cover - fallback path is covered via monkeypatch test
                items = self._build_mock_items(normalized_request, mode="fallback_mock", fallback_reason=str(exc))
                response_metadata.update(
                    {
                        "resolved_mode": "fallback_mock",
                        "fallback_reason": str(exc),
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

    def create_crawl_job(self, db: Session, request: CrawlRequest) -> CrawlJob:
        """Create a crawl job record before execution starts."""
        crawl_job = CrawlJob(
            source=request.source,
            target_date=request.target_date,
            status="running",
            result_count=0,
        )
        db.add(crawl_job)
        db.commit()
        db.refresh(crawl_job)
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
        crawl_job.error_message = metadata.get("fallback_reason")
        crawl_job.completed_at = utc_now()

        for item in items:
            item_metadata = item.get("metadata", {})
            historical_record = (
                db.query(HistoricalData)
                .filter(HistoricalData.notice_number == item.get("notice_number"))
                .first()
            )
            if historical_record is None:
                historical_record = HistoricalData(notice_number=item.get("notice_number"))
                db.add(historical_record)

            historical_record.agency_name = (
                item_metadata.get("opening_demand_agency")
                or item_metadata.get("demand_agency")
                or item_metadata.get("issuing_agency")
                or ""
            )
            historical_record.category = item.get("business_type") or request.category or ""
            historical_record.base_amount = item.get("base_amount") or 0.0
            historical_record.predicted_price = item.get("estimated_amount") or 0.0
            historical_record.bid_rate = item_metadata.get("winning_rate") or 0.0
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
                tender_result = TenderResult(
                    winning_company=item_metadata.get("winning_company") or "",
                    winning_amount=item_metadata.get("winning_amount") or 0.0,
                    winning_rate=item_metadata.get("winning_rate") or 0.0,
                    result_status=item_metadata.get("opening_status") or crawl_job.status,
                    announced_at=self._coerce_datetime(item_metadata.get("opening_announced_at")),
                )
                db.add(tender_result)

        db.add(crawl_job)
        db.commit()
        db.refresh(crawl_job)
        return crawl_job

    def mark_crawl_job_failed(self, db: Session, crawl_job: CrawlJob, error_message: str) -> CrawlJob:
        """Update an existing crawl job when execution fails unexpectedly."""
        crawl_job.status = "failed"
        crawl_job.error_message = error_message
        crawl_job.completed_at = utc_now()
        db.add(crawl_job)
        db.commit()
        db.refresh(crawl_job)
        return crawl_job

    def _normalize_request(self, request: CrawlRequest) -> CrawlRequest:
        """Normalize optional request fields for downstream collection logic."""
        normalized_source = (request.source or "koneps").strip().lower()
        normalized_category = request.category.strip().lower() if request.category else "general"
        normalized_keyword = request.keyword.strip() if request.keyword else "AI"
        normalized_target_date = request.target_date or utc_now().date().isoformat()
        normalized_mode = request.execution_mode.strip().lower()
        normalized_max_items = min(request.max_items, settings.KONEPS_MAX_ITEMS)

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
            raise ValueError("No notice items could be parsed from the live KONEPS page")

        opening_result_metadata = {
            "opening_result_grid_id": self.OPENING_RESULT_GRID_ID,
            "opening_result_row_count": 0,
            "opening_result_enriched_count": 0,
        }
        try:
            opening_rows = self._collect_opening_result_rows(request)
            parsed_items, opening_result_metadata = self._merge_opening_result_rows(parsed_items, opening_rows)
        except Exception as exc:
            opening_result_metadata["opening_result_error"] = str(exc)

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

    def _collect_opening_result_rows(self, request: CrawlRequest) -> list[dict[str, Any]]:
        """Collect opening-result rows from 개찰결과분류조회 using the live SPA page."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.KONEPS_HEADLESS)
            try:
                last_error: Exception | None = None
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
                        context.close()
                        return rows
                    except Exception as exc:  # pragma: no cover - exercised by live browser only
                        last_error = exc
                        context.close()
                        if attempt >= settings.KONEPS_RETRY_COUNT:
                            raise
                        sleep((settings.KONEPS_RETRY_BACKOFF_MS * (attempt + 1)) / 1000)

                if last_error:
                    raise last_error
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
            for normalized_row in (self._normalize_opening_result_row(row) for row in rows or [])
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
                    "opening_scheduled_at": opening_row.get("scheduled_at").isoformat()
                    if opening_row.get("scheduled_at")
                    else None,
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
                item_metadata["opening_announced_at"] = opening_row["announced_at"].isoformat()

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

    def _gather_live_page_snapshots(self, request: CrawlRequest) -> list[dict[str, Any]]:
        """Search the public KONEPS homepage and gather result page snapshots."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.KONEPS_HEADLESS)
            try:
                last_error: Exception | None = None
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
                        context.close()
                        return snapshots
                    except Exception as exc:  # pragma: no cover - exercised indirectly in fallback path
                        last_error = exc
                        context.close()
                        if attempt >= settings.KONEPS_RETRY_COUNT:
                            raise
                        sleep((settings.KONEPS_RETRY_BACKOFF_MS * (attempt + 1)) / 1000)

                if last_error:
                    raise last_error
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
        self._set_input_value(page, self.HOME_SEARCH_START_DATE_ID, request.target_date.replace("-", "/"))
        self._set_input_value(page, self.HOME_SEARCH_END_DATE_ID, request.target_date.replace("-", "/"))

        search_button = page.locator(f"#{self.HOME_SEARCH_BUTTON_ID}")
        if search_button.count() == 0:
            raise ValueError("KONEPS public search button could not be located")

        search_button.click()
        page.wait_for_selector(f"#{self.HOME_SEARCH_RESULT_TABLE_ID}")
        page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)

    def _collect_result_page_snapshots(self, page: Any, request: CrawlRequest) -> list[dict[str, Any]]:
        """Collect HTML snapshots for the required number of result pages."""
        expected_pages = max(1, ceil(request.max_items / self.HOME_SEARCH_DEFAULT_PAGE_SIZE))
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
        detail_links = page.locator(f"#{self.HOME_SEARCH_RESULT_TABLE_ID} a[id$='btnOpenKonepsInfo']")
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
        detail_snapshot = detail_pages.get(detail_action_id) if detail_pages and detail_action_id else None
        detail_data = self._parse_detail_html(detail_snapshot["html"]) if detail_snapshot else {}
        row_text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
        region = detail_data.get("region") or self._extract_region([title, issuing_agency, demand_agency, row_text])
        source_url = detail_snapshot["url"] if detail_snapshot else (page_url or settings.KONEPS_HOME_URL)
        base_amount = detail_data.get("base_amount") or 0.0
        estimated_amount = detail_data.get("estimated_amount")
        closing_at = detail_data.get("closing_at") or self._extract_datetime(closing_at_text) or self._extract_datetime(opening_at_text)
        license_codes = detail_data.get("license_codes") or self._extract_license_codes(row_text)

        return CrawlNoticeItem(
            notice_number=notice_number or f"LIVE-{request.target_date.replace('-', '')}-{row_index + 1:03d}",
            title=detail_data.get("title") or title,
            base_amount=base_amount,
            estimated_amount=estimated_amount,
            closing_at=closing_at,
            business_type=detail_data.get("business_type") or business_type,
            region=region,
            license_codes=license_codes,
            source_url=source_url,
            metadata={
                "mode": "live",
                "page_number": page_number,
                "row_index": row_index,
                "amount_source": "detail_page" if detail_snapshot else "missing_in_list",
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
            texts = [cell.get_text(" ", strip=True) for cell in cells if cell.get_text(" ", strip=True)]
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

        return {
            "notice_number": field_map.get("입찰공고번호"),
            "title": field_map.get("입찰공고명"),
            "business_type": field_map.get("입찰유형"),
            "base_amount": base_amounts[0] if base_amounts else None,
            "estimated_amount": estimated_amounts[0] if estimated_amounts else None,
            "closing_at": self._extract_datetime(field_map.get("입찰마감일시", "")),
            "opening_at_text": field_map.get("개찰일시"),
            "license_codes": license_codes,
            "region": region,
        }

    def _normalize_opening_result_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Normalize an opening-result row or mocked detail payload into a stable structure."""
        detail_data = {}
        detail_html = row.get("detail_html")
        if isinstance(detail_html, str) and detail_html.strip():
            detail_data.update(self._parse_opening_detail_html(detail_html))
        if isinstance(row.get("detail"), dict):
            detail_data.update({key: value for key, value in row["detail"].items() if value not in (None, "", [], {})})

        notice_number = str(row.get("notice_number") or row.get("bidPbancNo") or detail_data.get("notice_number") or "").strip()
        notice_order = str(row.get("notice_order") or row.get("bidPbancOrd") or detail_data.get("notice_order") or "").strip()
        notice_full_number = str(
            row.get("notice_full_number")
            or row.get("bidPbancNoPbancOrd")
            or f"{notice_number}-{notice_order}".strip("-")
        ).strip()
        opening_amount = self._coerce_amount(row.get("opening_amount") or row.get("bizAmt"))

        return {
            "notice_number": notice_number,
            "notice_order": notice_order,
            "notice_full_number": notice_full_number,
            "title": unescape(
                str(row.get("title") or row.get("bidPbancNm") or detail_data.get("title") or "")
            ).strip(),
            "bid_classification": str(row.get("bid_classification") or row.get("bidClsfNo") or "").strip(),
            "bid_progress_order": str(row.get("bid_progress_order") or row.get("bidPrgrsOrd") or "").strip(),
            "demand_agency": str(row.get("demand_agency") or row.get("dmstGrpNm") or "").strip(),
            "status": str(row.get("status") or row.get("bidPgstCd") or detail_data.get("result_status") or "").strip(),
            "scheduled_at": self._coerce_datetime(
                row.get("scheduled_at") or row.get("onbsPrnmntDt") or detail_data.get("announced_at")
            ),
            "business_type": row.get("business_type")
            or self._map_opening_business_type(row.get("prcmBsneSeCd")),
            "opening_amount": opening_amount,
            "reserve_prices": detail_data.get("reserve_prices") or row.get("reserve_prices") or [],
            "selected_numbers": detail_data.get("selected_numbers") or row.get("selected_numbers") or [],
            "winning_company": detail_data.get("winning_company") or row.get("winning_company"),
            "winning_amount": detail_data.get("winning_amount")
            if detail_data.get("winning_amount") is not None
            else row.get("winning_amount"),
            "winning_rate": detail_data.get("winning_rate")
            if detail_data.get("winning_rate") is not None
            else row.get("winning_rate"),
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
            texts = [cell.get_text(" ", strip=True) for cell in cells if cell.get_text(" ", strip=True)]
            if len(texts) < 2:
                continue
            for index in range(0, len(texts) - 1, 2):
                key = texts[index]
                value = texts[index + 1]
                if key and value and key not in field_map:
                    field_map[key] = value

        all_text = soup.get_text(" ", strip=True)
        reserve_text = self._find_field_value(field_map, ["복수예비가격", "예비가격", "추첨예비가격"])
        if not reserve_text:
            reserve_match = re.search(
                r"복수예비가격\s*(.*?)(?:선택번호|추첨번호|낙찰자|낙찰업체|낙찰금액|낙찰률|개찰일시|$)",
                all_text,
            )
            reserve_text = reserve_match.group(1) if reserve_match else ""

        selected_text = self._find_field_value(field_map, ["선택번호", "추첨번호", "선정번호"])
        if not selected_text:
            selected_match = re.search(
                r"(?:선택번호|추첨번호|선정번호)\s*(.*?)(?:낙찰자|낙찰업체|낙찰금액|낙찰률|개찰일시|$)",
                all_text,
            )
            selected_text = selected_match.group(1) if selected_match else ""

        winning_company = self._find_field_value(field_map, ["낙찰업체", "낙찰자", "낙찰자명", "계약상대자"])
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
            "selected_numbers": self._extract_integer_tokens(selected_text, max_items=4),
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
        notice_number = self._extract_notice_number(combined_text) or f"LIVE-{request.target_date.replace('-', '')}-{row_index + 1:03d}"
        title = self._extract_title(cleaned_cells, notice_number, link.get_text(strip=True) if link else None)

        if not title:
            return None

        region = self._extract_region(cleaned_cells)
        closing_at = self._extract_datetime(combined_text)
        href = link.get("href") if link else None
        source_url = urljoin(page_url or settings.KONEPS_BASE_URL, href) if href else (page_url or settings.KONEPS_HOME_URL)

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

    def _extract_title(self, cells: list[str], notice_number: str, link_text: str | None) -> str | None:
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
            "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
            "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주", "전국",
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

    def _extract_integer_tokens(self, text: str, max_items: int | None = None) -> list[int]:
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

    def _build_mock_items(
        self,
        request: CrawlRequest,
        mode: str = "mock",
        fallback_reason: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build deterministic mock notice data while live crawling is under construction."""
        closing_at = utc_now() + timedelta(days=3)
        target_stamp = request.target_date.replace("-", "")
        source_root = settings.KONEPS_BASE_URL.rstrip("/")

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
                },
            ),
        ]

        return [item.model_dump(mode="json") for item in mock_items[: request.max_items]]
