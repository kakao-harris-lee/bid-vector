"""Pure OpenAPI protocol helpers for the KONEPS collector.

These functions and the source/category mapping constants were extracted
verbatim from ``KonepsCollectorService`` (``collector.py``). They have no
IO (``requests``), DB (``Session``), or HTML (``BeautifulSoup``)
dependencies and do not use instance state, so they live here as
module-level pure helpers and module-level constants to keep the
collector class focused on orchestration.

Behavior is intentionally identical to the original methods; this module
is a pure relocation, not a rewrite. The collector remains the single
caller of the OpenAPI IO clients (``_request_openapi_with_key_variants``
/ ``_load_openapi_json``) which stay in ``collector.py``.
"""

import re
from typing import Any
from urllib.parse import quote_plus

from app.core.config import settings
from app.core.time import utc_now
from app.schemas.schemas import CrawlRequest
from app.services.koneps import parsing


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


def is_openapi_source(source: str | None) -> bool:
    """Return whether the crawl request should use the KONEPS OpenAPI path."""
    return str(source or "").strip().lower() in OPENAPI_SOURCE_ALIASES


def is_scsbid_openapi_source(source: str | None) -> bool:
    """Return whether the crawl request should use the KONEPS award OpenAPI path."""
    return str(source or "").strip().lower() in SCSBID_OPENAPI_SOURCE_ALIASES


def scsbid_operation_for_category(category: str | None) -> str:
    """Choose the ScsbidInfoService award operation for an internal category."""
    normalized_category = str(category or "").strip().lower()
    return SCSBID_CATEGORY_OPERATIONS.get(
        normalized_category,
        "getScsbidListSttusServc",
    )


def scsbid_reserve_detail_operation_for_category(
    category: str | None,
) -> str:
    """Choose the ScsbidInfoService reserve-detail operation for a category."""
    normalized_category = str(category or "").strip().lower()
    return SCSBID_RESERVE_DETAIL_OPERATIONS.get(
        normalized_category,
        "getOpengResultListInfoServcPreparPcDetail",
    )


def summarize_scsbid_reserve_detail(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize reserve-detail rows into prices, selected numbers, and prices."""
    reserve_rows: list[tuple[int, float]] = []
    selected_numbers: list[int] = []
    planned_price: float | None = None
    base_amount: float | None = None

    for row in rows:
        if planned_price is None:
            planned_price = parsing.coerce_amount(row.get("plnprc"))
        if base_amount is None:
            base_amount = parsing.coerce_amount(row.get("bssamt"))

        sequence = parsing.coerce_int_value(row.get("compnoRsrvtnPrceSno"))
        reserve_price = parsing.coerce_amount(row.get("bsisPlnprc"))
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


def openapi_service_key_variants(
    service_key: str,
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


def openapi_query_string(
    *,
    params: dict[str, Any],
    preencoded_keys: set[str],
) -> str:
    """Build a query string while preserving already-encoded key values."""
    query_parts: list[str] = []
    for key, value in params.items():
        encoded_key = quote_plus(str(key), safe="")
        encoded_value = (
            str(value) if key in preencoded_keys else quote_plus(str(value), safe="")
        )
        query_parts.append(f"{encoded_key}={encoded_value}")
    return "&".join(query_parts)


def openapi_operation_for_category(category: str | None) -> str:
    """Choose the BidPublicInfoService operation matching the internal category."""
    normalized_category = str(category or "").strip().lower()
    return OPENAPI_CATEGORY_OPERATIONS.get(
        normalized_category,
        "getBidPblancListInfoServc",
    )


def openapi_date_token(target_date: str | None) -> str:
    """Return YYYYMMDD for OpenAPI date-time query parameters."""
    raw_value = str(target_date or utc_now().date().isoformat()).strip()
    compact = re.sub(r"\D", "", raw_value)
    if len(compact) >= 8:
        return compact[:8]
    parsed = parsing.coerce_datetime(raw_value)
    if parsed is None:
        raise ValueError(f"target_date must be parseable as a date: {raw_value}")
    return parsed.strftime("%Y%m%d")


def openapi_header(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the normalized OpenAPI response header."""
    response = (
        payload.get("response") if isinstance(payload.get("response"), dict) else {}
    )
    header = response.get("header") if isinstance(response.get("header"), dict) else {}
    return dict(header)


def openapi_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the normalized OpenAPI response body."""
    response = (
        payload.get("response") if isinstance(payload.get("response"), dict) else {}
    )
    body = response.get("body") if isinstance(response.get("body"), dict) else {}
    return dict(body)


def openapi_item_list(body: dict[str, Any]) -> list[dict[str, Any]]:
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


def build_openapi_notice_item(
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
    base_amount = first_openapi_amount(
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
    estimated_amount = first_openapi_amount(
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
        parsing.coerce_datetime(raw_item.get("bidClseDt"))
        or parsing.coerce_datetime(opening_at)
        or parsing.coerce_datetime(raw_item.get("bidNtceDt"))
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
        "license_codes": parsing.extract_license_codes(license_text),
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


def first_openapi_amount(
    raw_item: dict[str, Any],
    candidate_keys: list[str],
) -> float | None:
    """Return the first parseable amount from one OpenAPI row."""
    for key in candidate_keys:
        value = parsing.coerce_amount(raw_item.get(key))
        if value is not None:
            return value
    return None
