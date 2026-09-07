"""Pure scsbid (개찰/낙찰) helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``). They have no IO (``requests`` / Playwright ``page``), DB
(``Session`` / ``db.query`` / ``db.add``), or instance-state dependencies --
they operate only on plain dicts, the request schema, ``HistoricalData`` rows
(read-only attribute access), the KST clock, the already-extracted pure helpers
in ``parsing`` / ``openapi``, the pure 기초금액-복구 helper in
``base_amount_basis`` (``estimate_base_amount_from_reserves``), plus the
module-level ``settings`` (scsbid collection config) -- so they live here as
module-level pure functions to keep the collector class focused on
orchestration, DB persistence, and IO.

Behavior is intentionally identical to the original methods; the relocated
helpers are a pure move, not a rewrite (including the KST-anchored date window
used by the forward-coverage / timezone tests). The sweep-budget resolvers
(``item_cap`` / ``request_item_cap`` / ``inline_reserve_detail_allowed`` /
``inline_reserve_detail_max_fetches``) are the exception: they are newly
authored policy added when the sweep stopped reading ``request.max_items``, so
they have no pre-existing collector counterpart to be identical to.

To avoid an import cycle, this module must never import ``collector``: the
collector imports ``scsbid`` (and the sibling ``parsing`` / ``openapi`` /
``html_parsing`` / ``matching`` / ``live_failure`` modules), not the other way
around. The collector keeps thin delegator methods (``_scsbid_date_window`` /
``_has_persisted_reserve_prices``) for external callers (tests and
``app/tasks/jobs.py``) that invoke them as instance methods.
"""

import json
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from app.core.config import settings
from app.core.constants import ESTIMATE_SOURCE_DERIVED
from app.core.time import kst_now
from app.models.models import HistoricalData
from app.schemas.koneps_items import KonepsCollectedItem, ScsbidReserveDetail
from app.schemas.schemas import CrawlRequest
from app.services.base_amount_basis import estimate_base_amount_from_reserves
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


def categories_for_request(request: CrawlRequest) -> list[str]:
    """Resolve the ordered, de-duplicated category list for a scsbid sweep.

    Priority: ``request.categories`` > ``[request.category]`` > legacy default
    (empty category, which maps to the 용역 operation). The legacy single
    category path is preserved when ``categories`` is absent.

    Pure request-shaping (no IO/DB), so it sits next to the sibling resolvers
    (``date_window`` / ``page_size`` / ``max_pages``) instead of the collector;
    it has no external callers, so no delegator is kept.
    """
    if request.categories:
        raw_categories = [str(value) for value in request.categories]
    elif request.category:
        raw_categories = [str(request.category)]
    else:
        raw_categories = [str(request.category or "")]

    resolved: list[str] = []
    seen: set[str] = set()
    for value in raw_categories:
        normalized = value.strip().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return resolved or [""]


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


def award_page_params(
    *, page_size: int, page_no: int, begin_token: str, end_token: str
) -> dict[str, str | int]:
    """ScsbidInfoService 낙찰 목록 한 페이지의 OpenAPI 쿼리 파라미터.

    ``inqryDiv="1"`` 은 등록일시 구간 조회(목록)를 뜻하는 KONEPS 계약값이다. 이런
    고정 파라미터는 fetch 흐름 안의 리터럴이 아니라 여기서 한 번 선언하고 흐름은
    해석만 한다(§4.5-1). 키 순서가 곧 쿼리스트링 순서라 순서까지 계약이다.
    """
    return {
        "type": "json",
        "numOfRows": page_size,
        "pageNo": page_no,
        "inqryDiv": "1",
        "inqryBgnDt": begin_token,
        "inqryEndDt": end_token,
    }


def reserve_detail_params(notice_number: str) -> dict[str, str | int]:
    """복수예비가격 상세(공고번호 단건) 조회의 OpenAPI 쿼리 파라미터.

    ``inqryDiv="2"`` 는 공고번호 단건 조회다. 페이지 크기는 목록과 다른 상세 전용
    설정(``KONEPS_SCSBID_DETAIL_PAGE_SIZE``)을 호출 시점에 읽는다.
    """
    return {
        "type": "json",
        "numOfRows": settings.KONEPS_SCSBID_DETAIL_PAGE_SIZE,
        "pageNo": 1,
        "inqryDiv": "2",
        "bidNtceNo": notice_number,
    }


def page_size(request: CrawlRequest) -> int:
    """Resolve numOfRows per page for a scsbid sweep (default 100, <=999)."""
    configured = request.page_size or settings.KONEPS_SCSBID_COLLECTION_PAGE_SIZE
    return max(1, min(int(configured or 100), 999))


def max_pages(request: CrawlRequest) -> int:
    """Resolve the per-category page ceiling for a scsbid sweep (default 30)."""
    configured = request.max_pages or settings.KONEPS_SCSBID_COLLECTION_MAX_PAGES
    return max(1, int(configured or 30))


def item_cap(
    *,
    configured_max_items: int | None,
    page_size: int,
    max_pages: int,
    category_count: int,
) -> int:
    """Resolve the normalized-item ceiling for one scsbid award sweep.

    The cap is declared by configuration, not by the crawl request: a positive
    ``configured_max_items`` is the operator's explicit ceiling, while 0/None
    means "no explicit cap" and falls back to the page budget the sweep can
    fetch anyway (``page_size`` x ``max_pages`` x category count). Pure
    arithmetic so the decision is testable without settings or IO (§4.7-4).

    Args:
        configured_max_items: Operator-declared ceiling; 0 or None = unset.
        page_size: Resolved numOfRows per page for this sweep.
        max_pages: Resolved per-category page ceiling for this sweep.
        category_count: Number of categories the sweep visits (0 reads as 1).

    Returns:
        The maximum number of normalized items this sweep may keep.
    """
    if configured_max_items is not None and int(configured_max_items) > 0:
        return int(configured_max_items)
    return int(page_size) * int(max_pages) * max(1, int(category_count))


def request_item_cap(request: CrawlRequest, categories: Sequence[str]) -> int:
    """Resolve the item cap for a concrete sweep request.

    Thin settings/request reader over :func:`item_cap` — it is the only place
    the sweep learns its ceiling, so ``request.max_items`` (a schema-bounded
    field meant for the notice-collection path) can no longer truncate an award
    sweep.

    Args:
        request: The normalized crawl request driving this sweep.
        categories: The resolved category list for this sweep.

    Returns:
        The maximum number of normalized items this sweep may keep.
    """
    return item_cap(
        configured_max_items=settings.KONEPS_SCSBID_COLLECTION_MAX_ITEMS,
        page_size=page_size(request),
        max_pages=max_pages(request),
        category_count=len(categories),
    )


def inline_reserve_detail_allowed(fetched: int, cap: int) -> bool:
    """Whether one more INLINE reserve-detail fetch fits this sweep's budget.

    The inline branch (non-deferred callers: the synchronous crawl route and the
    backfill script) pays an HTTP call plus a throttle sleep per notice, so an
    uncapped sweep can spend tens of minutes inside one request and trip the
    ScsbidInfoService rate limit. The deferred Celery path never fetches inline
    and is unaffected. Pure arithmetic so the decision is testable without IO.

    Args:
        fetched: Inline reserve-detail fetches already spent this sweep.
        cap: Per-sweep ceiling; 0 or negative means unbounded.

    Returns:
        True when the fetch may proceed, False when the budget is spent.
    """
    if cap <= 0:
        return True
    return int(fetched) < int(cap)


def inline_reserve_detail_max_fetches() -> int:
    """Read the per-sweep inline reserve-detail budget (never negative)."""
    return max(0, int(settings.KONEPS_SCSBID_INLINE_RESERVE_DETAIL_MAX_FETCHES or 0))


def request_delay_seconds() -> float:
    """Return the inter-call throttle delay (seconds, never negative)."""
    return max(
        0.0,
        float(settings.KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS or 0.0),
    )


def build_scsbid_award_item(
    raw_item: dict[str, Any],
    *,
    detail: ScsbidReserveDetail,
    request: CrawlRequest,
    operation: str,
    category: str | None = None,
) -> KonepsCollectedItem | None:
    """Promote one raw ScsbidInfoService 개찰/낙찰 row into the typed collection item.

    승격 지점(방어적 DTO Phase 3): 원시 응답(``raw_item``)과 복수예비가격 상세
    (``detail`` — 호출부가 ``ScsbidReserveDetail`` 로 이미 승격해 넘긴다)를 합쳐 타입
    있는 item 을 만든다. 공고번호가 없는 행은 ``None`` 으로 되돌려 항목 단위로만
    버린다(수집 best-effort).
    """
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
    # base 정합화(P1): KONEPS ``sucsfbidRate`` = 낙찰가/**예정가**(사정률-기준)이므로
    # ``winning_amount / success_rate`` 는 기초금액이 아니라 **예정가**다(실측 확정).
    # 따라서 이 값을 base_amount 로 저장하면 base==예정가 오염이 발생해(발주처 밴드가
    # 예정가 기준으로 떠서 실투찰이 하한 아래로 내려간 근본원인) 절대 base 로 쓰지 않는다.
    #   - base_amount 는 reserve detail 상세의 실 기초금액(``detail['base_amount']``) 만.
    #   - 실 기초금액이 없으면 복수예비가격 15개 중앙값으로 기초금액을 복구해 추정치
    #     (``base_amount_estimated``)로만 노출한다(원본 base 는 오염값으로 채우지 않음).
    #   - 둘 다 불가하면 base 는 미상(0.0)으로 남기고, persistence 가드가 기존의 더 나은
    #     base 를 이 0.0 으로 덮지 않게 한다.
    detail_base = parsing.coerce_amount(detail.base_amount)
    base_amount = detail_base if detail_base and detail_base > 0 else None
    recovered_base = estimate_base_amount_from_reserves(detail.reserve_prices)
    # 예정가: reserve detail 상세값 우선, 없으면 낙찰가/success_rate(=낙찰가/예정가) 역산
    # 추정 예정가. 예정가는 planned_price / estimated_amount 로만 흐르고 base 로 승격하지
    # 않는다.
    planned_price_estimate = (
        winning_amount / success_rate
        if winning_amount is not None and success_rate
        else None
    )
    # 상세 예정가도 base 와 같은 관용 파싱을 거친다. KONEPS 원시 토큰은 문자열일 수
    # 있고(``RawAmount``), 아래 ``planned_price > 0`` 비교가 문자열이면 TypeError 로
    # sweep 을 죽인다 — 타입화가 드러낸 잠재 결함이다. float 입력에는 무연산이라 산출 불변.
    planned_price = parsing.coerce_amount(detail.planned_price) or planned_price_estimate
    # 투찰률(bid_rate) = 낙찰가/기초금액. 실 기초금액이 있을 때만 역산하고, 없으면
    # success_rate(낙찰가/예정가)를 유지한다(기존 동작 — base 미상 시 회귀 없음).
    bid_rate = (
        winning_amount / base_amount
        if winning_amount is not None and base_amount and base_amount > 0
        else success_rate
    )
    opened_at = (
        raw_item.get("rlOpengDt")
        or raw_item.get("fnlSucsfDate")
        or raw_item.get("rgstDt")
    )
    demand_agency = str(raw_item.get("dminsttNm") or "").strip()

    return KonepsCollectedItem(
        notice_number=notice_number,
        title=title,
        # base 미상(실 기초금액 없음)이면 0.0 을 배출한다. persistence 가 이 0.0 으로
        # 기존 base 를 덮지 않도록 가드하므로, 예정가 오염값이 base 로 새어들지 않는다.
        base_amount=float(base_amount) if base_amount and base_amount > 0 else 0.0,
        estimated_amount=(
            float(planned_price) if planned_price and planned_price > 0 else 0.0
        ),
        # 이 자리는 공고 추정가격이 아니라 **예정가**(상세값 또는 낙찰가÷사정률 역산)다.
        # 신고해 두면 write 가드가 저장된 공고 추정가격을 이 파생값으로 덮지 않는다.
        estimated_amount_source=ESTIMATE_SOURCE_DERIVED,
        award_floor_rate=parsing.normalize_bid_rate_value(
            raw_item.get("sucsfbidLwltRate")
        ),
        # eligibility_raw는 배출하지 않는다: scsbid 개찰 응답에 자격 상세가 없고,
        # eligibility_raw의 유일한 writer는 backfill 스크립트로 일원화한다(openapi.py
        # build_openapi_notice_item 주석 참조).
        closing_at=parsing.coerce_datetime(opened_at),
        business_type=resolved_category or request.category,
        region=parsing.extract_region([demand_agency, title]),
        license_codes=[],
        source_url=None,
        metadata={
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
            "reserve_prices": detail.reserve_prices or [],
            "selected_numbers": detail.selected_numbers or [],
            # 예정가: 상세값 우선, 없으면 낙찰가/success_rate(=예정가) 역산 추정치.
            "planned_price": planned_price,
            # 복수예비가격 15개로 복구한 기초금액 추정치(원본 base 는 오염값으로 채우지
            # 않고, 여기·persistence 의 base_amount_estimated 로만 흐른다).
            "base_amount_estimated": (
                float(recovered_base) if recovered_base else None
            ),
            "reserve_detail_error": detail.reserve_detail_error,
            "raw_openapi_item": raw_item,
            "raw_reserve_detail_items": detail.raw_reserve_detail_items or [],
        },
    )
