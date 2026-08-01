"""Pure HTML / result-table parsing helpers for the KONEPS collector.

These functions and the home-search / opening-result element id constants
were extracted verbatim from ``KonepsCollectorService`` (``collector.py``).
They have no IO (``requests`` / Playwright ``page``), DB (``Session``), or
instance-state dependencies -- they operate only on ``BeautifulSoup`` parse
trees, plain dicts/lists, module-level constants, request schemas, and the
already-extracted pure helpers in ``parsing`` -- so they live here as
module-level pure functions to keep the collector class focused on
orchestration and IO.

Behavior is intentionally identical to the original methods; this module is
a pure relocation, not a rewrite. To avoid an import cycle, this module must
never import ``collector``: the collector imports ``html_parsing`` (and the
sibling ``parsing`` module), not the other way around. The collector remains
the single owner of the browser IO (``page.evaluate`` /
``page.wait_for_selector``) and HTTP clients that consume these helpers.
"""

import re
from collections.abc import Sequence
from html import unescape
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.core.config import settings
from app.schemas.koneps_items import KonepsCollectedItem, OpeningResultRow
from app.schemas.schemas import CrawlRequest
from app.services.koneps import parsing


HOME_SEARCH_RESULT_TABLE_ID = "mf_wfm_container_testTable"
OPENING_RESULT_GRID_ID = "mf_wfm_container_onbsRsltClsfInqyGrd"
OPENING_RESULT_DATA_LIST_KEY = "mf_wfm_container_dlOnbsRsltClsfListOutL"


def merge_opening_result_rows(
    items: list[KonepsCollectedItem],
    opening_rows: Sequence[OpeningResultRow],
) -> tuple[list[KonepsCollectedItem], dict[str, str | int]]:
    """Merge opening-result data into collected notice items by notice number.

    ``items`` 와 개찰결과 행 모두 승격된 DTO 다 — 순수 코어는 타입 있는 입력만 받고 병합
    규칙만 소유한다. dict 로 도착하는 행(대체된 수집 훅/외부 payload)의 승격은 경계인
    ``browser_crawl.promote_opening_result_rows`` 가 맡는다.
    """
    opening_index: dict[str, OpeningResultRow] = {}
    for row in opening_rows:
        if row.notice_number:
            opening_index[row.notice_number] = row
        if row.notice_full_number:
            opening_index[row.notice_full_number] = row

    enriched_count = 0
    for item in items:
        notice_number = item.notice_number or ""
        opening_row = opening_index.get(notice_number)
        if opening_row is None and "-" in notice_number:
            opening_row = opening_index.get(notice_number.split("-", maxsplit=1)[0])
        if opening_row is None:
            continue

        scheduled_at = opening_row.scheduled_at
        item_metadata = dict(item.metadata or {})
        item_metadata.update(
            {
                "opening_notice_full_number": opening_row.notice_full_number,
                "opening_status": opening_row.status,
                "opening_scheduled_at": (
                    scheduled_at.isoformat() if scheduled_at else None
                ),
                "opening_bid_classification": opening_row.bid_classification,
                "opening_bid_progress_order": opening_row.bid_progress_order,
                "opening_demand_agency": opening_row.demand_agency,
                "opening_business_type": opening_row.business_type,
                "opening_amount": opening_row.opening_amount,
                "opening_detail_collected": bool(
                    opening_row.reserve_prices
                    or opening_row.selected_numbers
                    or opening_row.winning_company
                ),
            }
        )

        if opening_row.reserve_prices:
            item_metadata["reserve_prices"] = opening_row.reserve_prices
        if opening_row.selected_numbers:
            item_metadata["selected_numbers"] = opening_row.selected_numbers
        if opening_row.winning_company:
            item_metadata["winning_company"] = opening_row.winning_company
        if opening_row.winning_amount is not None:
            item_metadata["winning_amount"] = opening_row.winning_amount
        if opening_row.winning_rate is not None:
            item_metadata["winning_rate"] = opening_row.winning_rate
        if opening_row.announced_at is not None:
            item_metadata["opening_announced_at"] = opening_row.announced_at.isoformat()

        item.metadata = item_metadata
        if not item.business_type and opening_row.business_type:
            item.business_type = opening_row.business_type
        if not item.region and opening_row.demand_agency:
            item.region = parsing.extract_region([opening_row.demand_agency])
        enriched_count += 1

    return items, {
        "opening_result_grid_id": OPENING_RESULT_GRID_ID,
        "opening_result_row_count": len(opening_rows),
        "opening_result_enriched_count": enriched_count,
    }


def split_business_type_cell(raw: str | None) -> tuple[str | None, str | None]:
    """Split 'NNNN 라벨' KONEPS 업종 셀로부터 코드/라벨을 분리.

    - 'NNNN 라벨' → ('NNNN', '라벨')
    - '라벨' 단독 → (None, '라벨')
    - 빈 문자열/None → (None, None)
    """
    if not raw:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    parts = text.split(maxsplit=1)
    if parts and parts[0].isdigit() and 3 <= len(parts[0]) <= 8:
        code = parts[0]
        label = parts[1].strip() if len(parts) > 1 else None
        return code, (label or None)
    return None, text


def parse_live_html(
    html: str,
    request: CrawlRequest,
    page_url: str | None = None,
    page_number: int = 1,
    detail_pages: dict[str, dict[str, str]] | None = None,
) -> list[KonepsCollectedItem]:
    """Parse a live KONEPS list page into crawl notice items."""
    soup = BeautifulSoup(html, "html.parser")
    result_table = soup.select_one(f"#{HOME_SEARCH_RESULT_TABLE_ID}")
    if result_table:
        return parse_koneps_result_table(
            result_table,
            request,
            page_url=page_url,
            page_number=page_number,
            detail_pages=detail_pages,
        )

    rows = soup.select("table tbody tr, table tr")
    parsed_items: list[KonepsCollectedItem] = []

    for index, row in enumerate(rows):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td, th")]
        link = row.select_one("a[href]")
        item = build_notice_from_cells(
            cells=cells,
            link=link,
            request=request,
            row_index=index,
            page_url=page_url,
            page_number=page_number,
        )
        if item:
            parsed_items.append(item)
        if len(parsed_items) >= request.max_items:
            break

    return parsed_items


def parse_koneps_result_table(
    table: Any,
    request: CrawlRequest,
    page_url: str | None,
    page_number: int,
    detail_pages: dict[str, dict[str, str]] | None = None,
) -> list[KonepsCollectedItem]:
    """Parse the real KONEPS 입찰공고 검색결과 테이블."""
    parsed_items: list[KonepsCollectedItem] = []

    for row_index, row in enumerate(table.select("tr")):
        cells = row.select("td")
        if len(cells) < 10:
            continue
        item = build_notice_from_result_row(
            cells=cells,
            request=request,
            row_index=row_index,
            page_url=page_url,
            page_number=page_number,
            detail_pages=detail_pages,
        )
        if item:
            parsed_items.append(item)
        if len(parsed_items) >= request.max_items:
            break

    return parsed_items


def build_notice_from_result_row(
    cells: list[Any],
    request: CrawlRequest,
    row_index: int,
    page_url: str | None,
    page_number: int,
    detail_pages: dict[str, dict[str, str]] | None = None,
) -> KonepsCollectedItem | None:
    """Build a notice item from the observed KONEPS search result row format."""
    if len(cells) < 15:
        return None

    notice_number = cells[2].get_text(" ", strip=True)
    title_cell = cells[3]
    title = parsing.extract_koneps_title(title_cell)
    business_type = cells[1].get_text(" ", strip=True)
    business_type_code, business_type_label = split_business_type_cell(business_type)
    status = cells[5].get_text(" ", strip=True)
    procurement_scope = cells[6].get_text(" ", strip=True)
    posted_at_text = cells[7].get_text(" ", strip=True)
    opening_at_text = cells[8].get_text(" ", strip=True)
    closing_at_text = cells[9].get_text(" ", strip=True)
    issuing_agency = cells[10].get_text(" ", strip=True)
    demand_agency = cells[11].get_text(" ", strip=True)
    contract_method = cells[12].get_text(" ", strip=True)
    detail_link = cells[4].select_one("a")
    detail_action_id = detail_link.get("id") if detail_link else None
    detail_snapshot = (
        detail_pages.get(detail_action_id)
        if detail_pages and detail_action_id
        else None
    )
    detail_data = parse_detail_html(detail_snapshot["html"]) if detail_snapshot else {}
    row_text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
    region = detail_data.get("region") or parsing.extract_region(
        [title, issuing_agency, demand_agency, row_text]
    )
    source_url = (
        detail_snapshot["url"]
        if detail_snapshot
        else (page_url or settings.KONEPS_HOME_URL)
    )
    base_amount = detail_data.get("base_amount") or 0.0
    estimated_amount = detail_data.get("estimated_amount")
    closing_at = (
        detail_data.get("closing_at")
        or parsing.extract_datetime(closing_at_text)
        or parsing.extract_datetime(opening_at_text)
    )
    license_codes = detail_data.get("license_codes") or parsing.extract_license_codes(
        row_text
    )

    return KonepsCollectedItem(
        notice_number=notice_number
        or f"LIVE-{(request.target_date or '').replace('-', '')}-{row_index + 1:03d}",
        title=detail_data.get("title") or title,
        base_amount=base_amount,
        estimated_amount=estimated_amount,
        closing_at=closing_at,
        business_type=detail_data.get("business_type") or business_type,
        business_type_code=detail_data.get("business_type_code") or business_type_code,
        business_type_label=detail_data.get("business_type_label")
        or business_type_label,
        region=region,
        license_codes=license_codes,
        source_url=source_url,
        metadata={
            "mode": "live",
            "page_number": page_number,
            "row_index": row_index,
            "amount_source": ("detail_page" if detail_snapshot else "missing_in_list"),
            "posted_at": posted_at_text,
            "opening_at": detail_data.get("opening_at_text") or opening_at_text,
            "status": status,
            "procurement_scope": procurement_scope,
            "issuing_agency": issuing_agency,
            "demand_agency": demand_agency,
            "contract_method": contract_method,
            "detail_action_id": detail_action_id,
            "detail_collected": bool(detail_snapshot),
            "detail_notice_number": detail_data.get("notice_number"),
            "target_date": request.target_date,
        },
    )


def parse_detail_html(html: str) -> dict[str, Any]:
    """Parse a detail popup page into normalized notice fields."""
    soup = BeautifulSoup(html, "html.parser")
    field_map: dict[str, str] = {}

    for row in soup.select("tr"):
        cells = row.select("th, td")
        texts = [
            cell.get_text(" ", strip=True)
            for cell in cells
            if cell.get_text(" ", strip=True)
        ]
        if len(texts) < 2:
            continue
        for index in range(0, len(texts) - 1, 2):
            key = texts[index]
            value = texts[index + 1]
            if key and value and key not in field_map:
                field_map[key] = value

    base_amounts = parsing.extract_amounts(field_map.get("기초금액", ""))
    estimated_amounts = parsing.extract_amounts(field_map.get("추정가격", ""))
    license_codes = parsing.extract_license_codes(field_map.get("면허제한", ""))
    region = parsing.extract_region([field_map.get("제한지역", "")])
    business_type_raw = field_map.get("입찰유형") or field_map.get("업무구분")
    business_type_code, business_type_label = split_business_type_cell(
        business_type_raw
    )

    return {
        "notice_number": field_map.get("입찰공고번호"),
        "title": field_map.get("입찰공고명"),
        "business_type": business_type_raw,
        "business_type_code": business_type_code,
        "business_type_label": business_type_label,
        "base_amount": base_amounts[0] if base_amounts else None,
        "estimated_amount": estimated_amounts[0] if estimated_amounts else None,
        "closing_at": parsing.extract_datetime(field_map.get("입찰마감일시", "")),
        "opening_at_text": field_map.get("개찰일시"),
        "license_codes": license_codes,
        "region": region,
    }


def normalize_opening_result_row(row: dict[str, Any]) -> OpeningResultRow:
    """Normalize a WebSquare opening-result row into the typed row DTO (승격 지점).

    원시 키(``bidPbancNo`` …) + 상세 팝업 파싱 결과를 합친다. 값 해석 규칙(결측을 빈
    문자열로 싣는 것 포함)은 dict 를 돌려주던 시절과 동일하다(소비자 falsy 판정 보존).
    """
    detail_data = {}
    detail_html = row.get("detail_html")
    if isinstance(detail_html, str) and detail_html.strip():
        detail_data.update(parse_opening_detail_html(detail_html))
    if isinstance(row.get("detail"), dict):
        detail_data.update(
            {
                key: value
                for key, value in row["detail"].items()
                if value not in (None, "", [], {})
            }
        )

    notice_number = str(
        row.get("notice_number")
        or row.get("bidPbancNo")
        or detail_data.get("notice_number")
        or ""
    ).strip()
    notice_order = str(
        row.get("notice_order")
        or row.get("bidPbancOrd")
        or detail_data.get("notice_order")
        or ""
    ).strip()
    notice_full_number = str(
        row.get("notice_full_number")
        or row.get("bidPbancNoPbancOrd")
        or f"{notice_number}-{notice_order}".strip("-")
    ).strip()
    opening_amount = parsing.coerce_amount(
        row.get("opening_amount") or row.get("bizAmt")
    )

    return OpeningResultRow(
        notice_number=notice_number,
        notice_order=notice_order,
        notice_full_number=notice_full_number,
        title=unescape(
            str(
                row.get("title")
                or row.get("bidPbancNm")
                or detail_data.get("title")
                or ""
            )
        ).strip(),
        bid_classification=str(
            row.get("bid_classification") or row.get("bidClsfNo") or ""
        ).strip(),
        bid_progress_order=str(
            row.get("bid_progress_order") or row.get("bidPrgrsOrd") or ""
        ).strip(),
        demand_agency=str(
            row.get("demand_agency") or row.get("dmstGrpNm") or ""
        ).strip(),
        status=str(
            row.get("status")
            or row.get("bidPgstCd")
            or detail_data.get("result_status")
            or ""
        ).strip(),
        scheduled_at=parsing.coerce_datetime(
            row.get("scheduled_at")
            or row.get("onbsPrnmntDt")
            or detail_data.get("announced_at")
        ),
        business_type=row.get("business_type")
        or map_opening_business_type(row.get("prcmBsneSeCd")),
        opening_amount=opening_amount,
        reserve_prices=detail_data.get("reserve_prices")
        or row.get("reserve_prices")
        or [],
        selected_numbers=detail_data.get("selected_numbers")
        or row.get("selected_numbers")
        or [],
        winning_company=detail_data.get("winning_company")
        or row.get("winning_company"),
        winning_amount=(
            detail_data.get("winning_amount")
            if detail_data.get("winning_amount") is not None
            else row.get("winning_amount")
        ),
        winning_rate=(
            detail_data.get("winning_rate")
            if detail_data.get("winning_rate") is not None
            else row.get("winning_rate")
        ),
        announced_at=parsing.coerce_datetime(
            detail_data.get("announced_at") or row.get("announced_at")
        ),
        raw=row,
    )


def parse_opening_detail_html(html: str) -> dict[str, Any]:
    """Parse opening-result detail HTML into reserve prices, picked numbers, and winner info."""
    soup = BeautifulSoup(html, "html.parser")
    field_map: dict[str, str] = {}

    for row in soup.select("tr"):
        cells = row.select("th, td")
        texts = [
            cell.get_text(" ", strip=True)
            for cell in cells
            if cell.get_text(" ", strip=True)
        ]
        if len(texts) < 2:
            continue
        for index in range(0, len(texts) - 1, 2):
            key = texts[index]
            value = texts[index + 1]
            if key and value and key not in field_map:
                field_map[key] = value

    all_text = soup.get_text(" ", strip=True)
    reserve_text = parsing.find_field_value(field_map, ["복수예비가격", "예비가격", "추첨예비가격"])
    if not reserve_text:
        reserve_match = re.search(
            r"복수예비가격\s*(.*?)(?:선택번호|추첨번호|낙찰자|낙찰업체|낙찰금액|낙찰률|개찰일시|$)",
            all_text,
        )
        reserve_text = reserve_match.group(1) if reserve_match else ""

    selected_text = parsing.find_field_value(field_map, ["선택번호", "추첨번호", "선정번호"])
    if not selected_text:
        selected_match = re.search(
            r"(?:선택번호|추첨번호|선정번호)\s*(.*?)(?:낙찰자|낙찰업체|낙찰금액|낙찰률|개찰일시|$)",
            all_text,
        )
        selected_text = selected_match.group(1) if selected_match else ""

    winning_company = parsing.find_field_value(
        field_map, ["낙찰업체", "낙찰자", "낙찰자명", "계약상대자"]
    )
    winning_amount = parsing.coerce_amount(
        parsing.find_field_value(field_map, ["낙찰금액", "낙찰가격", "투찰금액"])
    )
    winning_rate = parsing.extract_percentage(
        parsing.find_field_value(field_map, ["낙찰률", "투찰률", "낙찰하한율"])
    )
    announced_at = parsing.coerce_datetime(
        parsing.find_field_value(field_map, ["개찰일시", "개찰완료일시", "낙찰일시"])
    )
    status = parsing.find_field_value(field_map, ["진행상태", "개찰상태", "낙찰상태"])

    return {
        "reserve_prices": parsing.extract_amounts(reserve_text)[:15],
        "selected_numbers": parsing.extract_integer_tokens(selected_text, max_items=4),
        "winning_company": winning_company or None,
        "winning_amount": winning_amount,
        "winning_rate": winning_rate,
        "announced_at": announced_at,
        "result_status": status or None,
    }


def build_notice_from_cells(
    cells: list[str],
    link: Any,
    request: CrawlRequest,
    row_index: int,
    page_url: str | None,
    page_number: int,
) -> KonepsCollectedItem | None:
    """Convert parsed table cells into a normalized crawl notice item."""
    cleaned_cells = [cell for cell in cells if cell]
    if not cleaned_cells:
        return None

    combined_text = " ".join(cleaned_cells)
    amounts = parsing.extract_amounts(combined_text)
    notice_number = (
        parsing.extract_notice_number(combined_text)
        or f"LIVE-{(request.target_date or '').replace('-', '')}-{row_index + 1:03d}"
    )
    title = parsing.extract_title(
        cleaned_cells, notice_number, link.get_text(strip=True) if link else None
    )

    if not title:
        return None

    region = parsing.extract_region(cleaned_cells)
    closing_at = parsing.extract_datetime(combined_text)
    href = link.get("href") if link else None
    source_url = (
        urljoin(page_url or settings.KONEPS_BASE_URL, href)
        if href
        else (page_url or settings.KONEPS_HOME_URL)
    )

    return KonepsCollectedItem(
        notice_number=notice_number,
        title=title,
        base_amount=amounts[0] if amounts else 0.0,
        estimated_amount=amounts[1] if len(amounts) > 1 else None,
        closing_at=closing_at,
        business_type=request.category,
        region=region,
        license_codes=parsing.extract_license_codes(combined_text),
        source_url=source_url,
        metadata={
            "mode": "live",
            "cell_count": len(cleaned_cells),
            "page_number": page_number,
            "row_index": row_index,
            "target_date": request.target_date,
        },
    )


def map_opening_business_type(code: str | None) -> str | None:
    """Map observed 개찰결과 업무코드 values to readable business types."""
    code_map = {
        "01": "물품",
        "03": "일반용역",
        "05": "기술용역",
        "07": "공사",
    }
    if not code:
        return None
    return code_map.get(str(code).strip(), str(code).strip())
