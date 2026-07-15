"""Unit tests for the extracted KONEPS scsbid helpers.

These lock the date-window resolution (incl. the KST-anchored rolling window),
the page-size / max-pages / request-delay config clamps, the reserve-price
persistence check, and the award-item payload shape of
``app.services.koneps.scsbid`` after the pure relocation out of
``KonepsCollectorService``. Behavior must stay byte-identical to the original
collector methods.
"""

from datetime import datetime, timezone, timedelta

import pytest

from app.core.config import settings
from app.schemas.schemas import CrawlRequest
from app.services.koneps import scsbid


KST = timezone(timedelta(hours=9))


class _FakeHistoricalData:
    """Minimal stand-in exposing only ``reserve_prices`` (read-only)."""

    def __init__(self, reserve_prices):
        self.reserve_prices = reserve_prices


# --------------------------------------------------------------------------- #
# date_window
# --------------------------------------------------------------------------- #
def test_date_window_explicit_start_end():
    req = CrawlRequest(
        source="scsbid-openapi", start_date="20260501", end_date="20260507"
    )
    assert scsbid.date_window(req) == ("202605010000", "202605072359")


def test_date_window_single_target_date():
    req = CrawlRequest(source="scsbid-openapi", target_date="2026-05-13")
    assert scsbid.date_window(req) == ("202605130000", "202605132359")


def test_date_window_lookback_anchors_on_kst_day(monkeypatch):
    # 2026-06-26T20:00Z == 2026-06-27 05:00 KST; the window's "today" must be
    # the KST calendar day, not the UTC day.
    instant_utc = datetime(2026, 6, 26, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.koneps.scsbid.kst_now",
        lambda: instant_utc.astimezone(KST),
    )
    req = CrawlRequest(source="scsbid-openapi", lookback_days=1)
    begin, end = scsbid.date_window(req)
    assert begin == "202606260000"
    assert end == "202606272359"  # KST day, NOT 202606262359


# --------------------------------------------------------------------------- #
# page_size / max_pages / request_delay_seconds
# --------------------------------------------------------------------------- #
def test_page_size_clamped_to_999_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_PAGE_SIZE", 5000)
    req = CrawlRequest(source="scsbid-openapi")
    assert scsbid.page_size(req) == 999


def test_page_size_default_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_PAGE_SIZE", 0)
    req = CrawlRequest(source="scsbid-openapi")
    assert scsbid.page_size(req) == 100


def test_page_size_request_override(monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_PAGE_SIZE", 100)
    req = CrawlRequest(source="scsbid-openapi", page_size=250)
    assert scsbid.page_size(req) == 250


def test_max_pages_floor_at_one(monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_MAX_PAGES", 0)
    req = CrawlRequest(source="scsbid-openapi")
    assert scsbid.max_pages(req) == 30  # default kicks in for falsy config


def test_max_pages_request_override(monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_MAX_PAGES", 30)
    req = CrawlRequest(source="scsbid-openapi", max_pages=7)
    assert scsbid.max_pages(req) == 7


def test_request_delay_never_negative(monkeypatch):
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", -3.0
    )
    assert scsbid.request_delay_seconds() == 0.0


def test_request_delay_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 1.5)
    assert scsbid.request_delay_seconds() == 1.5


# --------------------------------------------------------------------------- #
# has_persisted_reserve_prices
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "stored, expected",
    [
        (None, False),
        ("", False),
        ("[]", False),
        ("   ", False),
        ("[12345, 67890]", True),
        ("not-json-but-nonempty", True),
    ],
)
def test_has_persisted_reserve_prices(stored, expected):
    record = _FakeHistoricalData(stored)
    assert scsbid.has_persisted_reserve_prices(record) is expected


# --------------------------------------------------------------------------- #
# build_scsbid_award_item
# --------------------------------------------------------------------------- #
def test_build_award_item_returns_none_without_notice_number():
    req = CrawlRequest(source="scsbid-openapi", category="용역")
    assert (
        scsbid.build_scsbid_award_item(
            {"bidNtceNo": ""},
            detail={},
            request=req,
            operation="getOpengResultListInfoServc",
        )
        is None
    )


def test_build_award_item_shape_and_category_tagging():
    req = CrawlRequest(source="scsbid-openapi", category="용역")
    raw_item = {
        "bidNtceNo": "20260101-001",
        "bidNtceNm": "도로 보수 공사",
        "sucsfbidAmt": "100000000",
        "sucsfbidRate": "87.5",
        "dminsttNm": "서울특별시청",
        "rlOpengDt": "2026-01-05 11:00:00",
        "bidwinnrNm": "한빛건설",
        "prtcptCnum": "12",
    }
    detail = {
        "base_amount": 120000000,
        "planned_price": 121000000,
        "reserve_prices": [119000000, 121000000],
        "selected_numbers": [1, 3],
    }
    item = scsbid.build_scsbid_award_item(
        raw_item,
        detail=detail,
        request=req,
        operation="getOpengResultListInfoServc",
        category="공사",
    )
    assert item is not None
    assert item["notice_number"] == "20260101-001"
    assert item["title"] == "도로 보수 공사"
    # category tagging honours the swept category, not request.category.
    assert item["business_type"] == "공사"
    assert item["metadata"]["mode"] == "scsbid_openapi"
    assert item["metadata"]["openapi_service"] == "ScsbidInfoService"
    assert item["metadata"]["opening_status"] == "낙찰"
    assert item["metadata"]["participant_count"] == 12
    assert item["metadata"]["reserve_prices"] == [119000000, 121000000]
    assert item["metadata"]["raw_openapi_item"] is raw_item


def test_build_award_item_falls_back_to_request_category_when_unspecified():
    req = CrawlRequest(source="scsbid-openapi", category="용역")
    item = scsbid.build_scsbid_award_item(
        {"bidNtceNo": "N-1", "sucsfbidAmt": "1000", "sucsfbidRate": "90"},
        detail={},
        request=req,
        operation="getOpengResultListInfoServc",
        category=None,
    )
    assert item is not None
    assert item["business_type"] == "용역"


def test_scsbid_detail_page_size_default():
    """reserve-detail fetch page-size Settings 기본값이 기존 리터럴(100)과 동일한지 확인."""
    assert settings.KONEPS_SCSBID_DETAIL_PAGE_SIZE == 100
