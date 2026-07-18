"""Pure scsbid (개찰/낙찰) helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``). They have no IO (``requests`` / Playwright ``page``), DB
(``Session`` / ``db.query`` / ``db.add``), or instance-state dependencies --
they operate only on plain dicts, the request schema, ``HistoricalData`` rows
(read-only attribute access), the KST clock, and the already-extracted pure
helpers in ``parsing`` / ``openapi`` plus the module-level ``settings`` (scsbid
collection config) -- so they live here as module-level pure functions to keep
the collector class focused on orchestration, DB persistence, and IO.

Behavior is intentionally identical to the original methods; this module is a
pure relocation, not a rewrite (including the KST-anchored date window used by
the forward-coverage / timezone tests). To avoid an import cycle, this module
must never import ``collector``: the collector imports ``scsbid`` (and the
sibling ``parsing`` / ``openapi`` / ``html_parsing`` / ``matching`` /
``live_failure`` modules), not the other way around. The collector keeps thin
delegator methods (``_scsbid_date_window`` / ``_has_persisted_reserve_prices``)
for external callers (tests and ``app/tasks/jobs.py``) that invoke them as
instance methods.
"""

import json
from datetime import timedelta
from typing import Any

from app.core.config import settings
from app.core.time import kst_now
from app.models.models import HistoricalData
from app.schemas.schemas import CrawlRequest
from app.services.koneps import openapi, parsing


def has_persisted_reserve_prices(historical_record: HistoricalData) -> bool:
    """Whether a HistoricalData row already stores a non-empty reserve price."""
    stored = historical_record.reserve_prices
    if not stored:
        return False
    try:
        return bool(json.loads(stored))
    except (TypeError, ValueError):
        return bool(str(stored).strip() not in {"", "[]"})


def date_window(request: CrawlRequest) -> tuple[str, str]:
    """Resolve (inqryBgnDt, inqryEndDt) tokens for the scsbid date window.

    Priority: explicit ``start_date``/``end_date`` > ``lookback_days``
    (end=today) > ``target_date`` single-day (legacy). Returns
    ``YYYYMMDDHHMM`` tokens.
    """
    if request.start_date and request.end_date:
        begin = openapi.openapi_date_token(request.start_date)
        end = openapi.openapi_date_token(request.end_date)
    elif request.lookback_days is not None:
        # KONEPS opening dates are KST — anchor the rolling window on the
        # Korean calendar day so the latest ~9h of openings are not missed
        # while UTC is still on the previous date (KST 00:00-09:00).
        today = kst_now().date()
        start_day = today - timedelta(days=max(0, int(request.lookback_days)))
        begin = start_day.strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
    else:
        token = openapi.openapi_date_token(request.target_date)
        begin = token
        end = token
    return f"{begin}0000", f"{end}2359"


def page_size(request: CrawlRequest) -> int:
    """Resolve numOfRows per page for a scsbid sweep (default 100, <=999)."""
    configured = request.page_size or settings.KONEPS_SCSBID_COLLECTION_PAGE_SIZE
    return max(1, min(int(configured or 100), 999))


def max_pages(request: CrawlRequest) -> int:
    """Resolve the per-category page ceiling for a scsbid sweep (default 30)."""
    configured = request.max_pages or settings.KONEPS_SCSBID_COLLECTION_MAX_PAGES
    return max(1, int(configured or 30))


def request_delay_seconds() -> float:
    """Return the inter-call throttle delay (seconds, never negative)."""
    return max(
        0.0,
        float(settings.KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS or 0.0),
    )


def build_scsbid_award_item(
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
    winning_amount = parsing.coerce_amount(raw_item.get("sucsfbidAmt"))
    success_rate = parsing.normalize_bid_rate_value(raw_item.get("sucsfbidRate"))
    # base 정합화(P0): 기초금액(사업금액)은 reserve detail 상세의 base_amount, 없으면
    # 낙찰가/낙찰률(≈기초금액) 추정치만 쓴다. 예정가(planned_price)를 base_amount 폴백
    # 으로 저장하지 않는다 — 예정가와 기초금액을 뒤섞으면(base_amt==planned_est) 이후
    # 사정률(예정가/기초금액) 캘리브레이션의 분모가 오염되고 투찰률(낙찰가/기초금액)도
    # 예정가 기준으로 왜곡된다(발주처 밴드가 예정가 기준으로 캘리브레이션된 원인).
    base_amount = (
        detail.get("base_amount")
        or (
            winning_amount / success_rate
            if winning_amount is not None and success_rate
            else None
        )
        or winning_amount
        or 0.0
    )
    # 예정가는 reserve detail 상세에서만 취득한다. 낙찰가/낙찰률은 기초금액 추정치이지
    # 예정가가 아니므로 예정가 폴백으로 쓰면 base_amt==planned가 되어 재오염된다.
    planned_price = detail.get("planned_price")
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
        "award_floor_rate": parsing.normalize_bid_rate_value(
            raw_item.get("sucsfbidLwltRate")
        ),
        "eligibility_raw": openapi.extract_eligibility_raw(raw_item),
        "closing_at": parsing.coerce_datetime(opened_at),
        "business_type": resolved_category or request.category,
        "region": parsing.extract_region([demand_agency, title]),
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
            "participant_count": parsing.safe_int(raw_item.get("prtcptCnum")),
            "winning_company": raw_item.get("bidwinnrNm"),
            "winning_business_no": raw_item.get("bidwinnrBizno"),
            "winning_amount": winning_amount,
            "winning_rate": success_rate,
            "bid_rate": parsing.normalize_bid_rate_value(bid_rate),
            "final_success_date": raw_item.get("fnlSucsfDate"),
            "reserve_prices": detail.get("reserve_prices") or [],
            "selected_numbers": detail.get("selected_numbers") or [],
            "planned_price": detail.get("planned_price"),
            "reserve_detail_error": detail.get("reserve_detail_error"),
            "raw_openapi_item": raw_item,
            "raw_reserve_detail_items": detail.get("raw_reserve_detail_items") or [],
        },
    }
