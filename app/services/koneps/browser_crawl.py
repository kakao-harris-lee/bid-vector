"""Playwright live-crawl cluster for the KONEPS collector.

This module holds the homepage HTML-scraping fallback path that the collector
uses when the OpenAPI sources are not selected: it drives the public KONEPS
WebSquare SPA with the Playwright *sync* API (form fill, click, pagination,
detail-tab capture, opening-result grid read). 페이지 스냅샷은 원시 dict 로 넘기고, 개찰결과
그리드 행은 ``html_parsing`` 승격기를 거친 ``OpeningResultRow`` 로 돌려준다.

The page-driving functions and the 13 form/selector constants here were relocated
verbatim from ``KonepsCollectorService`` (``collector.py``): same Playwright call
order, selectors, retry/backoff, and error classification.

Import direction (to avoid a cycle): ``browser_crawl`` must never import
``collector``. It depends only on the already-extracted sibling modules
(``html_parsing`` for the result-table/grid constants and row parsers,
``live_failure`` for retry/error classification), plus ``settings`` and
Playwright. The collector imports ``browser_crawl`` (not the other way around)
and keeps thin delegator methods (``_collect_live_items`` /
``_gather_live_page_snapshots`` / ``_collect_opening_result_rows``) so existing
tests that monkeypatch those names on the service surface keep working: the
orchestrator :func:`collect_live_items` receives the service and dispatches the
two inner steps back through it, so a monkeypatched hook is still honored.

``sync_playwright`` is imported *inside* the two functions that launch a browser
(matching the original collector pattern) so importing this module never
requires Playwright to be installed.
"""

from collections.abc import Sequence
from math import ceil
from time import sleep
from typing import Any

from app.core.config import settings
from app.schemas.koneps_items import KonepsCollectedItem, OpeningResultRow
from app.schemas.schemas import CrawlRequest
from app.services.koneps import html_parsing, live_failure


HOME_SEARCH_KEYWORD_ID = "mf_wfm_container_wq_uuid_925_wq_uuid_934_searchKeyword"
HOME_SEARCH_BUTTON_ID = "mf_wfm_container_wq_uuid_925_wq_uuid_934_btnBidPbancDtlSrch"
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


def collect_live_items(service: Any, request: CrawlRequest) -> dict[str, Any]:
    """Collect live KONEPS items via the public homepage search flow.

    The orchestrator dispatches the two inner steps
    (``_gather_live_page_snapshots`` / ``_collect_opening_result_rows``) back
    through ``service`` so existing tests that monkeypatch those names on the
    service surface stay honored. The parsing/merge/metadata logic is unchanged.
    """
    page_snapshots = service._gather_live_page_snapshots(request)
    # ``parse_live_html`` 이 검증해 만든 DTO 를 그대로 운반한다(과거에는 여기서
    # ``model_dump`` 로 즉시 dict 로 강등해 타입을 버렸다 — 방어적 DTO Phase 3).
    parsed_items: list[KonepsCollectedItem] = []
    seen_notice_numbers: set[str] = set()

    for snapshot in page_snapshots:
        snapshot_items = html_parsing.parse_live_html(
            snapshot["html"],
            request,
            page_url=snapshot["url"],
            page_number=snapshot["page_number"],
            detail_pages=snapshot.get("detail_pages"),
        )
        for item in snapshot_items:
            if item.notice_number in seen_notice_numbers:
                continue
            seen_notice_numbers.add(item.notice_number)
            parsed_items.append(item)
            if len(parsed_items) >= request.max_items:
                break
        if len(parsed_items) >= request.max_items:
            break

    if not parsed_items:
        raise ValueError("No notice items could be parsed from the live KONEPS page")

    opening_result_metadata = {
        "opening_result_grid_id": html_parsing.OPENING_RESULT_GRID_ID,
        "opening_result_row_count": 0,
        "opening_result_enriched_count": 0,
    }
    try:
        opening_rows = promote_opening_result_rows(
            service._collect_opening_result_rows(request)
        )
        (
            parsed_items,
            opening_result_metadata,
        ) = html_parsing.merge_opening_result_rows(parsed_items, opening_rows)
    except Exception as exc:
        failure_payload = live_failure.live_failure_payload(exc, stage="opening_result")
        opening_result_metadata.update(
            {
                "opening_result_error": failure_payload["detail"],
                "opening_result_failure_category": failure_payload["category"],
                "opening_result_failure_stage": failure_payload["stage"],
                "opening_result_retryable": failure_payload["retryable"],
                "opening_result_failure": failure_payload,
                "opening_result_retry_attempts": failure_payload.get("attempts", []),
            }
        )

    return {
        "items": parsed_items[: request.max_items],
        "metadata": {
            "resolved_mode": "live",
            "page_count": len(page_snapshots),
            "search_entry_url": settings.KONEPS_HOME_URL,
            "result_table_id": html_parsing.HOME_SEARCH_RESULT_TABLE_ID,
            "pager_id_prefix": HOME_SEARCH_PAGER_ID_PREFIX,
            **opening_result_metadata,
        },
    }


def promote_opening_result_rows(
    rows: Sequence[OpeningResultRow | dict[str, Any]],
) -> list[OpeningResultRow]:
    """개찰결과 행을 순수 코어(merge)에 넘기기 전에 ``OpeningResultRow`` 로 승격한다.

    실 브라우저 경로는 이미 DTO 를 돌려주지만 ``_collect_opening_result_rows`` 훅을 대체한
    호출부는 dict 를 돌려줄 수 있어, 타입 없는 값이 들어오는 이 경계에서 한 번만 승격한다.
    판정은 dict 릴레이 시절과 같다: 내부 이름(``notice_number``)을 실은 행은 원시 키 재해석
    없이 모델 검증만, WebSquare 원시 키 행은 정규화를 태운다.
    """
    promoted: list[OpeningResultRow] = []
    for row in rows:
        if isinstance(row, OpeningResultRow):
            promoted.append(row)
        elif row.get("notice_number"):
            promoted.append(OpeningResultRow.model_validate(row))
        else:
            promoted.append(html_parsing.normalize_opening_result_row(row))
    return promoted


def collect_opening_result_rows(request: CrawlRequest) -> list[OpeningResultRow]:
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
                    open_opening_result_page(page, request)
                    rows = read_opening_result_rows(page)
                    close_browser_context(context)
                    return rows
                except (
                    Exception
                ) as exc:  # pragma: no cover - exercised by live browser only
                    last_error = exc
                    close_browser_context(context)
                    final_attempt = attempt >= settings.KONEPS_RETRY_COUNT
                    attempts.append(
                        live_failure.build_live_retry_attempt(
                            stage="opening_result",
                            attempt_index=attempt,
                            exc=exc,
                            final_attempt=final_attempt,
                        )
                    )
                    if final_attempt:
                        raise live_failure.live_collection_error(
                            stage="opening_result",
                            attempts=attempts,
                            original_error=exc,
                        ) from exc
                    sleep(live_failure.retry_delay_seconds(attempt))

            if last_error:
                raise live_failure.live_collection_error(
                    stage="opening_result",
                    attempts=attempts,
                    original_error=last_error,
                ) from last_error
            raise RuntimeError("Failed to collect KONEPS opening-result rows")
        finally:
            browser.close()


def open_opening_result_page(page: Any, request: CrawlRequest) -> None:
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
        OPENING_RESULT_MENU_ID,
    )
    if not menu_clicked:
        raise ValueError("KONEPS opening-result menu could not be located")

    page.wait_for_selector(f"#{OPENING_RESULT_BID_NO_ID}")
    page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)

    target_date = request.target_date.replace("-", "/")
    set_input_value(page, OPENING_RESULT_START_DATE_ID, target_date)
    set_input_value(page, OPENING_RESULT_END_DATE_ID, target_date)
    if request.keyword:
        set_input_value(page, OPENING_RESULT_TITLE_ID, request.keyword)

    search_button = page.locator(f"#{OPENING_RESULT_SEARCH_BUTTON_ID}")
    if search_button.count() == 0:
        raise ValueError("KONEPS opening-result search button could not be located")

    search_button.click()
    page.wait_for_selector(f"#{html_parsing.OPENING_RESULT_GRID_ID}")
    page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)


def read_opening_result_rows(page: Any) -> list[OpeningResultRow]:
    """Read opening-result rows from the page's WebSquare data list (승격해 반환)."""
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
        if normalized_row.notice_number
    ]


def gather_live_page_snapshots(request: CrawlRequest) -> list[dict[str, Any]]:
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
                    open_live_search_results(page, request)
                    snapshots = collect_result_page_snapshots(page, request)
                    close_browser_context(context)
                    return snapshots
                except (
                    Exception
                ) as exc:  # pragma: no cover - exercised indirectly in fallback path
                    last_error = exc
                    close_browser_context(context)
                    final_attempt = attempt >= settings.KONEPS_RETRY_COUNT
                    attempts.append(
                        live_failure.build_live_retry_attempt(
                            stage="notice_search",
                            attempt_index=attempt,
                            exc=exc,
                            final_attempt=final_attempt,
                        )
                    )
                    if final_attempt:
                        raise live_failure.live_collection_error(
                            stage="notice_search",
                            attempts=attempts,
                            original_error=exc,
                        ) from exc
                    sleep(live_failure.retry_delay_seconds(attempt))

            if last_error:
                raise live_failure.live_collection_error(
                    stage="notice_search",
                    attempts=attempts,
                    original_error=last_error,
                ) from last_error
            raise RuntimeError("Failed to gather KONEPS live page snapshots")
        finally:
            browser.close()


def open_live_search_results(page: Any, request: CrawlRequest) -> None:
    """Navigate to the public search form and execute an 입찰공고 search."""
    page.goto(settings.KONEPS_HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(settings.KONEPS_REQUEST_DELAY_MS)

    set_input_value(page, HOME_SEARCH_KEYWORD_ID, request.keyword)
    set_checked_state(page, HOME_SEARCH_TYPE_RADIO_ID, True)
    apply_business_type_filter(page, request.category)
    set_input_value(
        page, HOME_SEARCH_START_DATE_ID, request.target_date.replace("-", "/")
    )
    set_input_value(
        page, HOME_SEARCH_END_DATE_ID, request.target_date.replace("-", "/")
    )

    search_button = page.locator(f"#{HOME_SEARCH_BUTTON_ID}")
    if search_button.count() == 0:
        raise ValueError("KONEPS public search button could not be located")

    search_button.click()
    page.wait_for_selector(f"#{html_parsing.HOME_SEARCH_RESULT_TABLE_ID}")
    page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)


def collect_result_page_snapshots(
    page: Any, request: CrawlRequest
) -> list[dict[str, Any]]:
    """Collect HTML snapshots for the required number of result pages."""
    expected_pages = max(1, ceil(request.max_items / HOME_SEARCH_DEFAULT_PAGE_SIZE))
    snapshots: list[dict[str, Any]] = []

    for page_number in range(1, expected_pages + 1):
        page.wait_for_selector(f"#{html_parsing.HOME_SEARCH_RESULT_TABLE_ID}")
        page.wait_for_timeout(settings.KONEPS_REQUEST_DELAY_MS)
        snapshots.append(
            {
                "page_number": page_number,
                "url": page.url,
                "html": page.content(),
                "detail_pages": collect_detail_page_snapshots(page),
            }
        )

        next_page_number = page_number + 1
        if next_page_number > expected_pages:
            break
        if not go_to_result_page(page, next_page_number):
            break

    return snapshots


def collect_detail_page_snapshots(page: Any) -> dict[str, dict[str, str]]:
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


def go_to_result_page(page: Any, page_number: int) -> bool:
    """Move to a numbered KONEPS result page when available."""
    pager = page.locator(f"#{HOME_SEARCH_PAGER_ID_PREFIX}{page_number}")
    if pager.count() == 0:
        return False

    pager.click()
    page.wait_for_timeout(settings.KONEPS_SEARCH_WAIT_MS)
    page.wait_for_selector(f"#{html_parsing.HOME_SEARCH_RESULT_TABLE_ID}")
    return True


def set_input_value(page: Any, input_id: str, value: str) -> bool:
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


def set_checked_state(page: Any, input_id: str, checked: bool) -> bool:
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


def apply_business_type_filter(page: Any, category: str) -> None:
    """Apply a known 업무구분 filter when the incoming category maps cleanly to KONEPS options."""
    filter_id = HOME_SEARCH_CATEGORY_IDS.get(category)
    if not filter_id:
        return

    for category_id in HOME_SEARCH_CATEGORY_GROUP_IDS:
        set_checked_state(page, category_id, False)
    set_checked_state(page, filter_id, True)


def close_browser_context(context: Any) -> None:
    """Best-effort browser context cleanup without masking crawl failures."""
    try:
        context.close()
    except Exception:
        return
