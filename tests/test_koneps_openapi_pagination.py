"""Unit tests for collect_openapi_items pagination logic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collection import collect_openapi_items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Build a minimal requests.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    resp._json_data = json_data or {}
    return resp


def _api_payload(total_count: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a KONEPS-shaped JSON payload."""
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "totalCount": total_count,
                "items": {"item": items} if items else None,
            },
        }
    }


def _notice_item(n: int) -> dict[str, Any]:
    """Minimal raw notice item dict for a given index."""
    return {
        "bidNtceNo": f"20250101{n:06d}",
        "bidNtceOrd": "00",
        "bidNtceNm": f"Test notice {n}",
        "bidNtceDt": "2025010100000",
        "bidClseDt": "2025011523590",
        "asignBdgtAmt": "100000000",
        "presmptPrce": "97000000",
        "ntceInsttNm": "Test agency",
        "ntceInsttCd": "00001234",
        "rgstDt": "2025010100000",
    }


def _make_request(max_items: int = 500) -> CrawlRequest:
    return CrawlRequest(
        source="koneps-openapi",
        category="service",
        target_date="2025-01-01",
        execution_mode="live",
        max_items=max_items,
    )


# ---------------------------------------------------------------------------
# Fixtures / shared patch targets
# ---------------------------------------------------------------------------

SERVICE_KEY = "TESTKEY"


def _setup_monkeypatch(monkeypatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setattr(
        settings,
        "KONEPS_OPENAPI_BID_PUBLIC_INFO_URL",
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService",
    )
    # Disable the inter-page throttle so pagination tests stay fast.
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_REQUEST_DELAY_SECONDS", 0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_page_totalcount_below_100(monkeypatch):
    """totalCount=50 → only page 1 is fetched, 50 items returned."""
    _setup_monkeypatch(monkeypatch)

    raw_items = [_notice_item(i) for i in range(50)]
    payload = _api_payload(50, raw_items)
    mock_resp = _mock_response(json_data=payload)

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            return_value=(mock_resp, "raw"),
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            return_value=payload,
        ),
    ):
        result = collect_openapi_items(_make_request(max_items=500))

    meta = result["metadata"]
    assert meta["openapi_pages_fetched"] == 1
    assert meta["openapi_total_count"] == 50
    assert meta["openapi_num_of_rows"] == 100
    assert len(result["items"]) == 50


def test_three_pages_for_totalcount_250(monkeypatch):
    """totalCount=250 → 3 pages (100+100+50), all 250 items collected."""
    _setup_monkeypatch(monkeypatch)

    all_items = [_notice_item(i) for i in range(250)]

    def _side_effect(url, params, service_key, operation):
        page_no = params["pageNo"]
        start = (page_no - 1) * 100
        chunk = all_items[start : start + 100]
        payload = _api_payload(250, chunk)
        return _mock_response(json_data=payload), "raw"

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            side_effect=_side_effect,
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            side_effect=lambda resp: resp._json_data,
        ),
    ):
        result = collect_openapi_items(_make_request(max_items=500))

    meta = result["metadata"]
    assert meta["openapi_pages_fetched"] == 3
    assert meta["openapi_total_count"] == 250
    assert len(result["items"]) == 250


def test_early_exit_when_max_items_reached(monkeypatch):
    """max_items=150 → stops mid-page-2, returns exactly 150 items."""
    _setup_monkeypatch(monkeypatch)

    all_items = [_notice_item(i) for i in range(300)]

    call_count = {"n": 0}

    def _side_effect(url, params, service_key, operation):
        page_no = params["pageNo"]
        call_count["n"] += 1
        start = (page_no - 1) * 100
        chunk = all_items[start : start + 100]
        payload = _api_payload(300, chunk)
        return _mock_response(json_data=payload), "raw"

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            side_effect=_side_effect,
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            side_effect=lambda resp: resp._json_data,
        ),
    ):
        result = collect_openapi_items(_make_request(max_items=150))

    # Pages 1 and 2 are fetched; page 2 fills up to the max_items limit
    assert call_count["n"] == 2
    assert len(result["items"]) == 150
    assert result["metadata"]["openapi_pages_fetched"] == 2


def test_empty_totalcount_returns_no_items(monkeypatch):
    """totalCount=0 / empty body → loop exits after page 1, empty items list."""
    _setup_monkeypatch(monkeypatch)

    payload = _api_payload(0, [])

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            return_value=(_mock_response(json_data=payload), "raw"),
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            return_value=payload,
        ),
    ):
        result = collect_openapi_items(_make_request(max_items=500))

    assert result["items"] == []
    assert result["metadata"]["openapi_total_count"] == 0
    assert result["metadata"]["openapi_pages_fetched"] == 1


def test_missing_totalcount_continues_on_full_page(monkeypatch):
    """totalCount 누락/0 + full page → keeps paginating until a short page.

    Page 1 omits totalCount but returns a full page (100 items); page 2 omits
    totalCount and returns a short page (10 items), signalling the last page.
    All 110 items are collected across 2 fetched pages.
    """
    _setup_monkeypatch(monkeypatch)

    page1_items = [_notice_item(i) for i in range(100)]  # full page
    page2_items = [_notice_item(100 + i) for i in range(10)]  # short page

    def _side_effect(url, params, service_key, operation):
        page_no = params["pageNo"]
        items = page1_items if page_no == 1 else page2_items
        # Build a body WITHOUT a totalCount key (unreliable / missing).
        payload = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                "body": {"items": {"item": items}},
            }
        }
        return _mock_response(json_data=payload), "raw"

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            side_effect=_side_effect,
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            side_effect=lambda resp: resp._json_data,
        ),
    ):
        result = collect_openapi_items(_make_request(max_items=500))

    meta = result["metadata"]
    assert meta["openapi_pages_fetched"] == 2
    assert meta["openapi_total_count"] == 0  # missing → defaults to 0
    assert len(result["items"]) == 110


def test_missing_totalcount_runaway_guard(monkeypatch):
    """totalCount 누락 + API가 pageNo를 무시하고 full 중복 페이지를 무한 반환해도
    max_pages 백스톱이 무한 루프를 막는다.

    Every page returns the SAME 100 items (full page, all duplicates) and omits
    totalCount. Without the runaway guard this loops forever; with it the loop
    stops at ceil(max_items/per_page)+2 fetched pages.
    """
    _setup_monkeypatch(monkeypatch)

    dup_items = [_notice_item(i) for i in range(100)]  # identical full page every call

    def _side_effect(url, params, service_key, operation):
        payload = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                "body": {"items": {"item": dup_items}},  # no totalCount, always full+dup
            }
        }
        return _mock_response(json_data=payload), "raw"

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            side_effect=_side_effect,
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            side_effect=lambda resp: resp._json_data,
        ),
    ):
        result = collect_openapi_items(_make_request(max_items=500))

    meta = result["metadata"]
    # ceil(500/100) + 2 = 7 pages, then the guard breaks the loop.
    assert meta["openapi_pages_fetched"] == 7
    # All pages are duplicates → only the first page's 100 unique items survive.
    assert len(result["items"]) == 100


def test_http_error_raises(monkeypatch):
    """HTTP 400 from KONEPS → ValueError is raised."""
    _setup_monkeypatch(monkeypatch)

    bad_resp = _mock_response(status_code=400)
    bad_resp.text = "Bad Request"

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            return_value=(bad_resp, "raw"),
        ),
    ):
        with pytest.raises(ValueError, match="HTTP 400"):
            collect_openapi_items(_make_request())


def test_missing_service_key_raises(monkeypatch):
    """No KONEPS_OPENAPI_SERVICE_KEY → ValueError before any HTTP call."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "")

    with pytest.raises(ValueError, match="KONEPS_OPENAPI_SERVICE_KEY"):
        collect_openapi_items(_make_request())


def test_metadata_keys_present(monkeypatch):
    """All expected metadata keys are present in the return value."""
    _setup_monkeypatch(monkeypatch)

    payload = _api_payload(5, [_notice_item(i) for i in range(5)])

    with (
        patch(
            "app.services.koneps.http_client.request_openapi_with_key_variants",
            return_value=(_mock_response(json_data=payload), "raw"),
        ),
        patch(
            "app.services.koneps.http_client.load_openapi_json",
            return_value=payload,
        ),
    ):
        result = collect_openapi_items(_make_request(max_items=500))

    meta = result["metadata"]
    for key in (
        "resolved_mode",
        "openapi_service",
        "openapi_operation",
        "openapi_endpoint",
        "openapi_service_key_variant",
        "openapi_result_code",
        "openapi_result_message",
        "openapi_total_count",
        "openapi_pages_fetched",
        "openapi_last_page_no",
        "openapi_num_of_rows",
        "query_date",
        "query_type",
    ):
        assert key in meta, f"Missing metadata key: {key}"


def test_openapi_collection_page_size_default():
    """collect_openapi_items per-page Settings 기본값이 기존 리터럴(100)과 동일한지 확인."""
    assert settings.KONEPS_OPENAPI_COLLECTION_PAGE_SIZE == 100
