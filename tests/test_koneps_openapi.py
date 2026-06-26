"""Unit tests for the extracted pure KONEPS OpenAPI helpers.

These guard the behavior-preserving extraction of the OpenAPI protocol
cluster from ``KonepsCollectorService`` into
``app.services.koneps.openapi``. They assert pure-mapping behavior only;
the IO clients that call these helpers stay in ``collector.py`` and are
covered by the collector/scsbid suites.
"""

from app.schemas.schemas import CrawlRequest
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
    assert item["notice_number"] == "20250101-001"
    assert item["title"] == "테스트 공사"
    assert item["base_amount"] == 1000000.0
    assert item["estimated_amount"] == 900000.0
    assert item["region"] == "서울"
    assert item["metadata"]["mode"] == "openapi"
    assert item["metadata"]["openapi_operation"] == "getBidPblancListInfoCnstwk"
    assert item["metadata"]["demand_agency"] == "서울시청"


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
