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


# --------------------------------------------------------------------------- #
# base 정합화(P1): KONEPS ``sucsfbidRate`` = 낙찰가/**예정가**(실측 확정)이므로
# ``winning_amount / success_rate`` 는 기초금액이 아니라 예정가다. 이 값을 base_amount
# 로 폴백 저장하면 base==예정가 오염이 생겨 발주처 밴드가 예정가 기준으로 떠서 실투찰이
# 하한 아래로 내려간다. 따라서 base_amount 는 실 기초금액(detail)만, 없으면 미상(0.0).
# --------------------------------------------------------------------------- #
def test_build_award_item_does_not_use_winning_over_rate_as_base_amount():
    """detail에 실 기초금액이 없으면 base_amount는 예정가(=낙찰가/success_rate)로 폴백되면 안 된다."""
    req = CrawlRequest(source="scsbid-openapi", category="용역")
    raw_item = {
        "bidNtceNo": "20260201-001",
        "sucsfbidAmt": "100000000",  # 낙찰가
        "sucsfbidRate": "87.5",  # success_rate = 낙찰가/예정가 (NOT 낙찰가/기초금액)
    }
    # 실 기초금액(base_amount) 없이 예정가(planned_price)만 존재하는 reserve detail.
    detail = {"planned_price": 121_000_000}
    yega_reversal = 100_000_000.0 / 0.875  # = 예정가(114,285,714), 기초금액이 아님
    item = scsbid.build_scsbid_award_item(
        raw_item, detail=detail, request=req, operation="getOpengResultListInfoServc"
    )
    assert item is not None
    # base_amount는 예정가 역산값으로 오염되지 않는다 — 실 기초금액이 없으므로 미상(0.0).
    assert item["base_amount"] == 0.0
    assert item["base_amount"] != pytest.approx(yega_reversal)
    # 예정가는 detail 상세값을 그대로 노출한다(별도 필드; base 로 승격하지 않는다).
    assert item["metadata"]["planned_price"] == 121_000_000
    assert item["estimated_amount"] == pytest.approx(121_000_000.0)


def test_build_award_item_planned_price_recovered_from_rate_when_detail_empty():
    """detail이 비면 예정가(planned_price)는 낙찰가/success_rate로 역산되고 base_amount는 미상이다."""
    req = CrawlRequest(source="scsbid-openapi", category="용역")
    raw_item = {
        "bidNtceNo": "20260201-002",
        "sucsfbidAmt": "50000000",
        "sucsfbidRate": "88.0",
    }
    item = scsbid.build_scsbid_award_item(
        raw_item, detail={}, request=req, operation="getOpengResultListInfoServc"
    )
    assert item is not None
    # 낙찰가/success_rate = 예정가 추정치 → planned_price/estimated_amount 로만 흐른다.
    assert item["metadata"]["planned_price"] == pytest.approx(50_000_000.0 / 0.88)
    assert item["estimated_amount"] == pytest.approx(50_000_000.0 / 0.88)
    # base_amount는 예정가로 오염되지 않고 미상(0.0)으로 남는다.
    assert item["base_amount"] == 0.0


def test_build_award_item_recovers_base_estimate_from_reserves():
    """실 기초금액이 없어도 15개 복수예비가격이 있으면 base_amount_estimated로만 복구한다."""
    req = CrawlRequest(source="scsbid-openapi", category="공사")
    base = 30_000_000.0
    spread = base * 0.025
    step = (2 * spread) / 14
    reserves = [round(base - spread + step * i) for i in range(15)]
    raw_item = {
        "bidNtceNo": "20260301-001",
        "sucsfbidAmt": "26000000",
        "sucsfbidRate": "90.0",
    }
    item = scsbid.build_scsbid_award_item(
        raw_item,
        detail={"reserve_prices": reserves},
        request=req,
        operation="getOpengResultListInfoServc",
    )
    assert item is not None
    # 복구값은 base_amount_estimated(추정)로만 흐르고 원본 base_amount는 미상(0.0).
    assert item["base_amount"] == 0.0
    assert item["metadata"]["base_amount_estimated"] == pytest.approx(base, abs=1.0)


def test_build_award_item_no_base_estimate_without_full_reserves():
    """복수예비가격이 15개 미만이면 base_amount_estimated는 채워지지 않는다."""
    req = CrawlRequest(source="scsbid-openapi", category="용역")
    item = scsbid.build_scsbid_award_item(
        {"bidNtceNo": "N-9", "sucsfbidAmt": "1000", "sucsfbidRate": "90"},
        detail={"reserve_prices": [999, 1001]},
        request=req,
        operation="getOpengResultListInfoServc",
    )
    assert item is not None
    assert item["base_amount"] == 0.0
    assert item["metadata"]["base_amount_estimated"] is None


def test_build_award_item_prefers_real_base_amount_when_present():
    """진짜 기초금액이 있으면 그대로 쓰고 예정가와 구분된다(회귀 방지)."""
    req = CrawlRequest(source="scsbid-openapi", category="용역")
    raw_item = {"bidNtceNo": "N-3", "sucsfbidAmt": "100000000", "sucsfbidRate": "90"}
    detail = {"base_amount": 120_000_000, "planned_price": 119_500_000}
    item = scsbid.build_scsbid_award_item(
        raw_item, detail=detail, request=req, operation="getOpengResultListInfoServc"
    )
    assert item is not None
    assert item["base_amount"] == pytest.approx(120_000_000.0)
    assert item["metadata"]["planned_price"] == 119_500_000
    assert item["base_amount"] != item["metadata"]["planned_price"]


def test_scsbid_detail_page_size_default():
    """reserve-detail fetch page-size Settings 기본값이 기존 리터럴(100)과 동일한지 확인."""
    assert settings.KONEPS_SCSBID_DETAIL_PAGE_SIZE == 100
