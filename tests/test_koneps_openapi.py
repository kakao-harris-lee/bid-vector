"""Unit tests for the extracted pure KONEPS OpenAPI helpers.

These guard the behavior-preserving extraction of the OpenAPI protocol
cluster from ``KonepsCollectorService`` into
``app.services.koneps.openapi``. They assert pure-mapping behavior only;
the IO clients that call these helpers stay in ``collector.py`` and are
covered by the collector/scsbid suites.
"""

from app.schemas.schemas import CrawlRequest
from app.schemas.koneps_items import ScsbidReserveDetail
from app.services.koneps import openapi


def test_is_openapi_source():
    assert openapi.is_openapi_source("koneps-openapi") is True
    assert openapi.is_openapi_source("BID-PUBLIC-INFO") is True
    assert openapi.is_openapi_source("  koneps_api  ") is True
    assert openapi.is_openapi_source("koneps") is False
    assert openapi.is_openapi_source(None) is False


def test_is_scsbid_openapi_source():
    assert openapi.is_scsbid_openapi_source("scsbid-openapi") is True
    assert openapi.is_scsbid_openapi_source("SCSBID") is True
    assert openapi.is_scsbid_openapi_source("koneps-openapi") is False
    assert openapi.is_scsbid_openapi_source(None) is False


def test_openapi_operation_for_category():
    assert (
        openapi.openapi_operation_for_category("construction")
        == "getBidPblancListInfoCnstwk"
    )
    assert openapi.openapi_operation_for_category("공사") == "getBidPblancListInfoCnstwk"
    assert openapi.openapi_operation_for_category("goods") == "getBidPblancListInfoThng"
    # Unknown categories fall back to the service operation.
    assert (
        openapi.openapi_operation_for_category("unknown") == "getBidPblancListInfoServc"
    )
    assert openapi.openapi_operation_for_category(None) == "getBidPblancListInfoServc"


def test_scsbid_operation_for_category():
    assert (
        openapi.scsbid_operation_for_category("construction")
        == "getScsbidListSttusCnstwk"
    )
    assert openapi.scsbid_operation_for_category("물품") == "getScsbidListSttusThng"
    assert openapi.scsbid_operation_for_category(None) == "getScsbidListSttusServc"


def test_scsbid_reserve_detail_operation_for_category():
    assert (
        openapi.scsbid_reserve_detail_operation_for_category("construction")
        == "getOpengResultListInfoCnstwkPreparPcDetail"
    )
    assert (
        openapi.scsbid_reserve_detail_operation_for_category("unknown")
        == "getOpengResultListInfoServcPreparPcDetail"
    )


def test_openapi_date_token():
    assert openapi.openapi_date_token("2025-01-02") == "20250102"
    assert openapi.openapi_date_token("20250102") == "20250102"
    assert openapi.openapi_date_token("2025-01-02T03:04:05") == "20250102"


def test_openapi_header_and_body():
    payload = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {"totalCount": 3, "items": {"item": []}},
        }
    }
    assert openapi.openapi_header(payload) == {
        "resultCode": "00",
        "resultMsg": "OK",
    }
    assert openapi.openapi_body(payload)["totalCount"] == 3
    # Missing/odd shapes degrade to empty dicts, not errors.
    assert openapi.openapi_header({}) == {}
    assert openapi.openapi_body({"response": "nope"}) == {}


def test_openapi_item_list_shapes():
    # Nested items.item list shape.
    body_nested = {"items": {"item": [{"a": 1}, {"b": 2}]}}
    assert openapi.openapi_item_list(body_nested) == [{"a": 1}, {"b": 2}]
    # Single dict item is wrapped into a list.
    body_single = {"items": {"item": {"a": 1}}}
    assert openapi.openapi_item_list(body_single) == [{"a": 1}]
    # Top-level list shape.
    body_list = {"items": [{"a": 1}]}
    assert openapi.openapi_item_list(body_list) == [{"a": 1}]
    # Empty / missing shapes return [].
    assert openapi.openapi_item_list({}) == []
    assert openapi.openapi_item_list({"items": None}) == []


def test_first_openapi_amount():
    row = {"asignBdgtAmt": "1,250,000", "presmptPrce": "2,000,000"}
    assert (
        openapi.first_openapi_amount(row, ["asignBdgtAmt", "presmptPrce"]) == 1250000.0
    )
    # Skips missing keys until a parseable one is found.
    assert openapi.first_openapi_amount(row, ["missing", "presmptPrce"]) == 2000000.0
    assert openapi.first_openapi_amount(row, ["missing"]) is None


def test_build_openapi_notice_item():
    request = CrawlRequest(source="koneps-openapi", category="construction")
    raw_item = {
        "bidNtceNo": "20250101-001",
        "bidNtceNm": "테스트 공사",
        "asignBdgtAmt": "1,000,000",
        "presmptPrce": "900,000",
        "bidClseDt": "2025-01-10 18:00:00",
        "prtcptLmtRgnNm": "서울",
        "dminsttNm": "서울시청",
        "ntceInsttNm": "서울조달청",
    }
    item = openapi.build_openapi_notice_item(
        raw_item, request=request, operation="getBidPblancListInfoCnstwk"
    )
    assert item is not None
    assert item.notice_number == "20250101-001"
    assert item.title == "테스트 공사"
    assert item.base_amount == 1000000.0
    assert item.estimated_amount == 900000.0
    assert item.region == "서울"
    assert item.metadata["mode"] == "openapi"
    assert item.metadata["openapi_operation"] == "getBidPblancListInfoCnstwk"
    assert item.metadata["demand_agency"] == "서울시청"
    # No sucsfbidLwltRate on this row -> award floor rate stays None.
    assert item.award_floor_rate is None


def test_build_openapi_notice_item_extracts_award_floor_rate():
    """``sucsfbidLwltRate`` (percent string) is normalized into a fraction."""
    request = CrawlRequest(source="koneps-openapi", category="service")

    def _item(rate):
        raw = {
            "bidNtceNo": "R26BK01627948",
            "bidNtceNm": "테스트",
            "sucsfbidLwltRate": rate,
        }
        return openapi.build_openapi_notice_item(
            raw, request=request, operation="getBidPblancListInfoServc"
        )

    assert _item("88").award_floor_rate == 0.88
    assert _item("87.995").award_floor_rate == 0.87995
    assert _item(88).award_floor_rate == 0.88
    # Missing / empty / zero-like values degrade to None (never fabricated).
    assert _item("").award_floor_rate is None
    assert _item("0").award_floor_rate is None
    assert _item(None).award_floor_rate is None


def test_build_scsbid_award_item_extracts_award_floor_rate_when_present():
    """The scsbid builder mirrors the field when the row carries it (else None)."""
    from app.services.koneps import scsbid

    request = CrawlRequest(source="scsbid-openapi", category="construction")
    detail = ScsbidReserveDetail()

    with_rate = scsbid.build_scsbid_award_item(
        {"bidNtceNo": "R26BK01628093", "bidNtceNm": "개찰", "sucsfbidLwltRate": "88"},
        detail=detail,
        request=request,
        operation="getScsbidListSttusCnstwk",
    )
    without_rate = scsbid.build_scsbid_award_item(
        {"bidNtceNo": "R26BK01628093", "bidNtceNm": "개찰"},
        detail=detail,
        request=request,
        operation="getScsbidListSttusCnstwk",
    )

    assert with_rate.award_floor_rate == 0.88
    assert without_rate.award_floor_rate is None


def test_extract_eligibility_flags_copies_declared_flag_keys():
    """Only the declared participation-flag keys are copied, whitespace-stripped."""
    raw = {
        "indstrytyLmtYn": " Y ",
        "bidPrtcptLmtYn": "N",
        "rgnLmtBidLocplcJdgmBssNm": "주된 영업소",
        "lcnsLmtNm": "토목공사업",  # detail key absent from list responses -> dropped
        "bidNtceNm": "무관한 필드",  # not an eligibility key -> dropped
    }
    assert openapi.extract_eligibility_flags(raw) == {
        "indstrytyLmtYn": "Y",
        "bidPrtcptLmtYn": "N",
        "rgnLmtBidLocplcJdgmBssNm": "주된 영업소",
    }


def test_extract_eligibility_flags_excludes_blank_and_missing():
    """Empty / whitespace / None values are never copied; all-missing -> None."""
    raw = {
        "indstrytyLmtYn": "Y",
        "bidPrtcptLmtYn": "   ",  # whitespace -> excluded
        "prdctClsfcLmtYn": "",  # empty -> excluded
        "cmmnSpldmdCorpRgnLmtYn": None,  # None -> excluded
    }
    assert openapi.extract_eligibility_flags(raw) == {"indstrytyLmtYn": "Y"}
    assert openapi.extract_eligibility_flags({"bidNtceNm": "무관"}) is None
    assert openapi.extract_eligibility_flags({}) is None


def test_build_eligibility_raw_combines_flags_and_license_limits():
    """Both parts present -> nested {"flags", "license_limits"} with projected rows."""
    flags = {"indstrytyLmtYn": "Y"}
    license_limit_items = [
        {
            "lcnsLmtNm": " 종합여행업/1261 ",
            "permsnIndstrytyList": "",  # blank -> dropped from the projected row
            "lmtGrpNo": "1",
            "lmtSno": "1",
            "bidNtceNo": "R0001",  # not a declared license-limit key -> dropped
        }
    ]
    assert openapi.build_eligibility_raw(flags, license_limit_items) == {
        "flags": {"indstrytyLmtYn": "Y"},
        "license_limits": [{"lcnsLmtNm": "종합여행업/1261", "lmtGrpNo": "1", "lmtSno": "1"}],
    }


def test_build_eligibility_raw_flags_only_and_limits_only():
    """Each part is omitted when empty; a flags-only notice still yields a dict."""
    assert openapi.build_eligibility_raw({"indstrytyLmtYn": "N"}, []) == {
        "flags": {"indstrytyLmtYn": "N"}
    }
    assert openapi.build_eligibility_raw(None, [{"lcnsLmtNm": "토목공사업"}]) == {
        "license_limits": [{"lcnsLmtNm": "토목공사업"}]
    }


def test_build_eligibility_raw_both_empty_is_none():
    """No flags and no non-empty license rows -> None (never an empty dict)."""
    assert openapi.build_eligibility_raw(None, []) is None
    assert openapi.build_eligibility_raw(None, None) is None
    # A license row with only blank values contributes nothing.
    assert (
        openapi.build_eligibility_raw(None, [{"lcnsLmtNm": "  ", "lmtSno": ""}]) is None
    )
    assert openapi.build_eligibility_raw({}, []) is None


def test_build_openapi_notice_item_does_not_emit_eligibility_raw():
    """The collection feed no longer carries eligibility_raw (writer is the backfill)."""
    request = CrawlRequest(source="koneps-openapi", category="construction")
    raw = {
        "bidNtceNo": "R26BK01628100",
        "bidNtceNm": "테스트 공사",
        "indstrytyLmtYn": "Y",
        "lcnsLmtNm": "토목공사업",
    }
    item = openapi.build_openapi_notice_item(
        raw, request=request, operation="getBidPblancListInfoCnstwk"
    )
    # 수집 피드는 자격 원문을 채우지 않는다(유일한 writer 는 backfill 스크립트).
    assert item.eligibility_raw is None


def test_build_scsbid_award_item_does_not_emit_eligibility_raw():
    """The scsbid builder likewise no longer emits eligibility_raw."""
    from app.services.koneps import scsbid

    request = CrawlRequest(source="scsbid-openapi", category="construction")
    detail = ScsbidReserveDetail()

    item = scsbid.build_scsbid_award_item(
        {"bidNtceNo": "R26BK01628200", "bidNtceNm": "개찰", "indstrytyLmtYn": "Y"},
        detail=detail,
        request=request,
        operation="getScsbidListSttusCnstwk",
    )

    # 수집 피드는 자격 원문을 채우지 않는다(유일한 writer 는 backfill 스크립트).
    assert item.eligibility_raw is None


def test_build_openapi_notice_item_requires_notice_number():
    request = CrawlRequest(source="koneps-openapi")
    assert (
        openapi.build_openapi_notice_item(
            {"bidNtceNm": "no number"},
            request=request,
            operation="getBidPblancListInfoServc",
        )
        is None
    )


def test_summarize_scsbid_reserve_detail():
    rows = [
        {
            "compnoRsrvtnPrceSno": "2",
            "bsisPlnprc": "1,002",
            "drwtYn": "Y",
            "plnprc": "1,000",
            "bssamt": "900",
        },
        {
            "compnoRsrvtnPrceSno": "1",
            "bsisPlnprc": "1,001",
            "drwtYn": "N",
        },
    ]
    summary = openapi.summarize_scsbid_reserve_detail(rows)
    # Reserve prices are ordered by sequence number.
    assert summary["reserve_prices"] == [1001.0, 1002.0]
    assert summary["selected_numbers"] == [2]
    assert summary["planned_price"] == 1000.0
    assert summary["base_amount"] == 900.0
    assert summary["raw_reserve_detail_items"] == rows


def test_openapi_query_string_preserves_preencoded_keys():
    query = openapi.openapi_query_string(
        params={"type": "json", "ServiceKey": "a%2Bb"},
        preencoded_keys={"ServiceKey"},
    )
    # The pre-encoded key keeps its value verbatim; others get encoded.
    assert "ServiceKey=a%2Bb" in query
    assert "type=json" in query


def test_openapi_service_key_variants_does_not_leak_or_duplicate():
    variants = openapi.openapi_service_key_variants("raw key+value")
    names = [name for name, _, _ in variants]
    assert names[0] == "configured"
    # The raw form is always first and non-preencoded.
    assert variants[0][1] == "raw key+value"
    assert variants[0][2] is False
    # A URL-encoded variant is added when it differs from the raw key.
    assert "url_encoded" in names


def test_module_constant_is_single_source_for_class_alias():
    from app.services.koneps.collector import KonepsCollectorService

    # The class attribute is an alias to the module constant, not a copy.
    assert (
        KonepsCollectorService.SCSBID_OPENAPI_SOURCE_ALIASES
        is openapi.SCSBID_OPENAPI_SOURCE_ALIASES
    )
