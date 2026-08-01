"""Pure OpenAPI protocol helpers for the KONEPS collector.

These functions were extracted verbatim from ``KonepsCollectorService``
(``collector.py``). They have no
IO (``requests``), DB (``Session``), or HTML (``BeautifulSoup``) dependencies
and do not use instance state, so they live here as module-level pure helpers
to keep the collector focused on orchestration. The OpenAPI IO clients
(``request_openapi_with_key_variants`` / ``load_openapi_json``) live in
``app.services.koneps.http_client`` and consume these pure helpers.

소스 별칭 · 카테고리→오퍼레이션 **선언 테이블**은 ``openapi_operations`` 로 갈라 두고
(§4.5-1: 값 집합은 함수 밖 단일 출처) 여기서는 그 표를 해석하는 셀렉터만 유지한다 —
이 모듈이 500줄 한도 경계까지 자랐기 때문에 데이터/해석을 책임 단위로 나눈 것이다.
표는 여기서 다시 이름을 노출하므로(``openapi.SCSBID_OPENAPI_SOURCE_ALIASES``) 기존
호출부의 참조 경로는 그대로다.
"""

import re
from collections.abc import Collection, Sequence
from typing import Any
from urllib.parse import quote_plus

from app.core.config import settings
from app.core.time import utc_now
from app.schemas.koneps_items import KonepsCollectedItem
from app.schemas.schemas import CrawlRequest
from app.services.koneps import parsing
from app.services.koneps.field_contract_spec import (
    BASE_RESOLUTION_ORDER,
    ESTIMATED_RESOLUTION_ORDER,
)
from app.services.koneps.openapi_operations import (
    OPENAPI_CATEGORY_OPERATIONS,
    OPENAPI_SOURCE_ALIASES,
    SCSBID_CATEGORY_OPERATIONS,
    SCSBID_OPENAPI_SOURCE_ALIASES,
    SCSBID_OPENING_RESULT_OPERATIONS,
    SCSBID_RESERVE_DETAIL_OPERATIONS,
)


def _source_in(source: str | None, aliases: Collection[str]) -> bool:
    """Whether a crawl-request source matches one of the declared aliases.

    The single matcher behind the two named predicates below — they differ only
    in the alias table, which stays declared in ``openapi_operations`` (§4.5-3).
    """
    return str(source or "").strip().lower() in aliases


def is_openapi_source(source: str | None) -> bool:
    """Return whether the crawl request should use the KONEPS OpenAPI path."""
    return _source_in(source, OPENAPI_SOURCE_ALIASES)


def is_scsbid_openapi_source(source: str | None) -> bool:
    """Return whether the crawl request should use the KONEPS award OpenAPI path."""
    return _source_in(source, SCSBID_OPENAPI_SOURCE_ALIASES)


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


def opening_result_operation_for_category(category: str | None) -> str:
    """Choose the ScsbidInfoService 개찰결과 목록 operation for a category."""
    normalized_category = str(category or "").strip().lower()
    return SCSBID_OPENING_RESULT_OPERATIONS.get(
        normalized_category,
        "getOpengResultListInfoServc",
    )


def parse_openg_corp_info(value: Any) -> dict[str, Any] | None:
    """Parse the 개찰 1위 caret string into its fields, or None when unusable.

    Rows carry the top bidder as ``opengCorpInfo`` =
    ``"업체명^사업자번호^대표자명^투찰금액^투찰률"`` (실측). Pure and side-effect
    free (§4.7). Rules:

    - Empty/None, or no 상호 in the first field → ``None`` (no basis to record).
    - ``business_no`` is kept as the raw string with only outer whitespace
      trimmed — 사업자번호 is zero-padded and must never be int-coerced (applying
      the lesson of #210, a 차수 int-coercion 사고: KONEPS 식별자는 int 변환 금지).
    - ``amount`` / ``rate`` are best-effort numeric; missing 필드 → ``None``.
      ``rate`` is normalized to a fraction (e.g. "88.001" → 0.88001) so it
      matches the ``winning_rate`` convention.
    """
    if value is None:
        return None
    parts = str(value).split("^")
    company = parts[0].strip() if parts else ""
    if not company:
        return None

    def _field(index: int) -> str | None:
        if len(parts) <= index:
            return None
        text = parts[index].strip()
        return text or None

    return {
        "company": company,
        "business_no": _field(1),
        "representative": _field(2),
        "amount": parsing.coerce_amount(_field(3)),
        "rate": parsing.normalize_bid_rate_value(_field(4)),
    }


def build_opening_result_summary(raw_item: dict[str, Any]) -> dict[str, Any] | None:
    """Project one 개찰결과 목록 row into the fields the collector persists.

    Pure projection (§4.7) mirroring ``scsbid.build_scsbid_award_item`` — no IO,
    no DB. Returns ``None`` when the row has no ``bidNtceNo`` to match against.
    ``notice_number`` / ``bid_notice_order`` stay raw strings (제로패딩 보존 —
    #210의 교훈 준용: KONEPS 식별자 int 변환 금지); the suffix-aware project
    matching is done by the caller.
    """
    notice_number = str(raw_item.get("bidNtceNo") or "").strip()
    if not notice_number:
        return None
    order = raw_item.get("bidNtceOrd")
    return {
        "notice_number": notice_number,
        "bid_notice_order": str(order).strip() if order is not None else None,
        "rank1": parse_openg_corp_info(raw_item.get("opengCorpInfo")),
        "participant_count": parsing.safe_int(raw_item.get("prtcptCnum")),
        "opened_at": parsing.coerce_datetime(raw_item.get("opengDt")),
        "progress": str(raw_item.get("progrsDivCdNm") or "").strip() or None,
    }


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
    """Extract the normalized OpenAPI response header (비-dict/누락은 빈 dict)."""
    response = payload.get("response")
    header = response.get("header") if isinstance(response, dict) else None
    return dict(header) if isinstance(header, dict) else {}


def openapi_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the normalized OpenAPI response body (비-dict/누락은 빈 dict)."""
    response = payload.get("response")
    body = response.get("body") if isinstance(response, dict) else None
    return dict(body) if isinstance(body, dict) else {}


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


# 공고 목록/표적조회 응답이 실제로 싣는 참가자격 **플래그** 필드 선언 테이블
# (실측 2026-07-19). 목록 응답에는 면허명 등 자격 상세가 **없고** 아래 플래그와
# 소액 필드만 온다 — 자격 상세는 ``LICENSE_LIMIT_OPERATION`` 서브콜로만 얻는다.
# 여기 선언된 키 중 비어있지 않은 값만 flags dict로 보존한다. 규칙은 데이터로,
# 코드는 해석기만 유지한다(§4.5). 값은 대개 "Y"/"N" 또는 지역판단기준 코드/명이다.
ELIGIBILITY_RAW_KEYS = (
    "indstrytyLmtYn",  # 업종제한 여부
    "bidPrtcptLmtYn",  # 입찰참가제한 여부
    "prdctClsfcLmtYn",  # 물품분류제한 여부
    "cmmnSpldmdCorpRgnLmtYn",  # 공동수급체 지역제한 여부
    "rgnLmtBidLocplcJdgmBssCd",  # 지역제한 낙찰지 판단기준 코드
    "rgnLmtBidLocplcJdgmBssNm",  # 지역제한 낙찰지 판단기준 명
)

# 참가자격 상세 서브 오퍼레이션(실측 2026-07-19). 목록/표적조회 item에는 면허명
# 등 자격 상세가 없고 이 오퍼레이션만이 lcnsLmtNm/permsnIndstrytyList 등을 돌려
# 준다(BidPublicInfoService 소속 — ``KONEPS_OPENAPI_BID_PUBLIC_INFO_URL`` 사용).
# 필수 파라미터(실측): inqryDiv=2 + bidNtceNo + bidNtceOrd(차수). bidNtceOrd 누락 시
# resultCode 08("필수값 입력 에러"). 제한 없는 공고는 resultCode 00 + totalCount=0.
LICENSE_LIMIT_OPERATION = "getBidPblancListInfoLicenseLimit"

# 자격 상세(license-limit) 서브 응답 rows에서 보존하는 키 선언 테이블. lcnsLmtNm
# (면허제한 한글 원문)이 자격 판정의 주 소스이고, permsnIndstrytyList(허용 업종
# 목록)와 그룹/일련번호는 provenance·식별용이다.
LICENSE_LIMIT_ITEM_KEYS = (
    "lcnsLmtNm",  # 면허제한(한글 원문) — 자격 판정 주 소스
    "permsnIndstrytyList",  # 허용 업종 목록
    "lmtGrpNo",  # 제한 그룹 번호
    "lmtSno",  # 제한 일련번호
)


def project_declared_keys(
    raw_item: dict[str, Any], keys: Sequence[str]
) -> dict[str, Any] | None:
    """선언된 키 표로 raw item을 투영한다 — 비어있지 않은 값만, 전부 결측이면 ``None``.

    위 두 선언 테이블(``ELIGIBILITY_RAW_KEYS`` · ``LICENSE_LIMIT_ITEM_KEYS``)을
    해석하는 유일한 코드다. 규칙은 데이터로, 코드는 해석기만(§4.5-3) — 새 키 집합은
    표를 추가해 확장하고 이 루프는 그대로 둔다. IO/DB 없음.
    """
    projected: dict[str, Any] = {}
    for key in keys:
        value = raw_item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            projected[key] = text
    return projected or None


def extract_eligibility_flags(raw_item: dict[str, Any]) -> dict[str, Any] | None:
    """공고 item에서 참가자격 **플래그** 필드만 골라 dict로 보존한다(순수 함수).

    ``ELIGIBILITY_RAW_KEYS``에 선언된 키 중 **비어있지 않은 값**만 복사한다. 전부
    결측이면 ``None``. 목록/표적조회 응답에는 자격 상세가 없고 플래그만 있으므로
    (실측) 이 함수는 그 플래그만 추출한다. IO/DB 없음.
    """
    return project_declared_keys(raw_item, ELIGIBILITY_RAW_KEYS)


def _project_license_limit_item(raw_item: dict[str, Any]) -> dict[str, Any] | None:
    """license-limit 서브 응답 한 행을 ``LICENSE_LIMIT_ITEM_KEYS``로 투영한다.

    선언된 키 중 비어있지 않은 값만 남긴다. 전부 결측이면 ``None``. 순수 함수.
    """
    return project_declared_keys(raw_item, LICENSE_LIMIT_ITEM_KEYS)


def build_eligibility_raw(
    flags: dict[str, Any] | None,
    license_limit_items: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """플래그 + license-limit rows를 ``eligibility_raw`` 구조로 합성한다(순수 함수).

    반환 ``{"flags": {...}, "license_limits": [{...}, ...]}``. 각 파트는 비어있으면
    생략하고, 둘 다 비어있으면 ``None``(빈 dict 저장을 피해, 재수집이 ``IS NULL``
    재개 시맨틱을 깨지 않도록). license_limits 행은 ``LICENSE_LIMIT_ITEM_KEYS``로
    투영한다. IO/DB 없음.
    """
    result: dict[str, Any] = {}
    if flags:
        result["flags"] = dict(flags)
    projected_limits = [
        projected
        for projected in (
            _project_license_limit_item(raw)
            for raw in (license_limit_items or [])
            if isinstance(raw, dict)
        )
        if projected
    ]
    if projected_limits:
        result["license_limits"] = projected_limits
    return result or None


def build_openapi_notice_item(
    raw_item: dict[str, Any],
    *,
    request: CrawlRequest,
    operation: str,
) -> KonepsCollectedItem | None:
    """Promote one raw OpenAPI row into the typed collection item (승격 지점, Phase 3).

    원시 응답의 관용 정규화는 여기서 끝내고 이후 persistence 까지 ``KonepsCollectedItem``
    만 흐른다. 공고번호 없는 행은 ``None`` — 항목 단위로만 버린다(수집 best-effort).
    """
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
    # base_amount/estimated_amount 후보 키는 field_contract 가 단일 출처로 선언한다
    # (#220 base==예정가 오염의 발원지 정리 — 순서·basis 는 그 모듈에서만 바뀐다).
    base_amount = first_openapi_amount(raw_item, list(BASE_RESOLUTION_ORDER))
    estimated_amount = first_openapi_amount(
        raw_item, list(ESTIMATED_RESOLUTION_ORDER)
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
    award_floor_rate = parsing.normalize_bid_rate_value(
        raw_item.get("sucsfbidLwltRate")
    )

    return KonepsCollectedItem(
        notice_number=notice_number,
        title=title,
        base_amount=float(base_amount or 0.0),
        estimated_amount=float(estimated_amount or base_amount or 0.0),
        award_floor_rate=award_floor_rate,
        # eligibility_raw는 여기서 배출하지 않는다: 목록/표적조회 응답에 자격 상세가
        # 없고(실측 2026-07-19), 유일한 writer는 backfill 스크립트(표적조회 + license-
        # limit 서브콜)로 일원화한다. 수집 피드가 flags-only를 쓰면 IS NULL 재개
        # 시맨틱이 깨져 상세가 영영 안 채워진다.
        closing_at=closing_at,
        business_type=business_type or request.category,
        region=str(raw_item.get("prtcptLmtRgnNm") or "").strip() or None,
        license_codes=parsing.extract_license_codes(license_text),
        source_url=source_url,
        metadata={
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
    )


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
