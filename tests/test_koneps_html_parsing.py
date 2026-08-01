"""Unit tests for the extracted pure KONEPS HTML / result-table helpers.

These guard the behavior-preserving extraction from
``KonepsCollectorService`` into ``app.services.koneps.html_parsing``.
"""

from app.schemas.koneps_items import OpeningResultRow
from app.schemas.schemas import CrawlRequest
from app.services.koneps import html_parsing
from tests.support.koneps_items import collected_item


def _live_request() -> CrawlRequest:
    return CrawlRequest(
        source="koneps",
        category="software",
        execution_mode="live",
        keyword="AI",
    )


def test_split_business_type_cell():
    assert html_parsing.split_business_type_cell("0411 건축공사") == (
        "0411",
        "건축공사",
    )
    assert html_parsing.split_business_type_cell("건축공사") == (None, "건축공사")
    assert html_parsing.split_business_type_cell("") == (None, None)
    assert html_parsing.split_business_type_cell(None) == (None, None)


def test_map_opening_business_type():
    assert html_parsing.map_opening_business_type("01") == "물품"
    assert html_parsing.map_opening_business_type("07") == "공사"
    # Unknown codes pass through unchanged; empty/None -> None.
    assert html_parsing.map_opening_business_type("99") == "99"
    assert html_parsing.map_opening_business_type(None) is None


def test_parse_live_html_fallback_table():
    """The generic-table fallback path (no KONEPS result table id)."""
    html = """
    <html><body><table><tbody>
        <tr>
            <td>20260507-001</td>
            <td>AI 소프트웨어 통합 구축</td>
            <td>125,000,000</td>
            <td>121,500,000</td>
            <td>2026-05-10 18:00</td>
            <td>서울</td>
            <td><a href="https://www.g2b.go.kr/notice/20260507-001">상세보기</a></td>
        </tr>
    </tbody></table></body></html>
    """
    parsed = html_parsing.parse_live_html(
        html,
        _live_request(),
        page_url="https://www.g2b.go.kr/",
        page_number=1,
    )
    assert len(parsed) == 1
    assert parsed[0].notice_number == "20260507-001"
    assert parsed[0].title == "AI 소프트웨어 통합 구축"
    assert parsed[0].base_amount == 125000000.0
    assert parsed[0].region == "서울"


def test_parse_detail_html_field_map():
    html = """
    <html><body><table>
        <tr><th>입찰공고번호</th><td>20260507-001</td></tr>
        <tr><th>입찰공고명</th><td>AI 소프트웨어 통합 구축</td></tr>
        <tr><th>기초금액</th><td>125,000,000</td></tr>
        <tr><th>추정가격</th><td>121,500,000</td></tr>
        <tr><th>업무구분</th><td>0411 건축공사</td></tr>
        <tr><th>제한지역</th><td>서울</td></tr>
    </table></body></html>
    """
    detail = html_parsing.parse_detail_html(html)
    assert detail["notice_number"] == "20260507-001"
    assert detail["base_amount"] == 125000000.0
    assert detail["estimated_amount"] == 121500000.0
    assert detail["business_type_code"] == "0411"
    assert detail["business_type_label"] == "건축공사"
    assert detail["region"] == "서울"


def test_normalize_opening_result_row_maps_codes():
    row = {
        "bidPbancNo": "20260507-001",
        "bidPbancOrd": "00",
        "bidPbancNm": "도로 &amp; 교량 보수",
        "prcmBsneSeCd": "07",
        "bizAmt": "1,000,000",
    }
    normalized = html_parsing.normalize_opening_result_row(row)
    assert normalized.notice_number == "20260507-001"
    assert normalized.notice_full_number == "20260507-001-00"
    assert normalized.business_type == "공사"
    assert normalized.title == "도로 & 교량 보수"
    assert normalized.opening_amount == 1000000.0
    # 원시 행은 provenance 로 보존된다(승격이 정보를 버리지 않는다).
    assert normalized.raw == row


def test_merge_opening_result_rows_enriches_items():
    items = [
        collected_item(notice_number="20260507-001", metadata={"mode": "live"}),
        collected_item(notice_number="99999999-999", metadata={}),
    ]
    opening_rows = [
        OpeningResultRow(
            notice_number="20260507-001",
            notice_full_number="20260507-001-00",
            status="개찰완료",
            business_type="공사",
            demand_agency="서울특별시청",
            winning_company="가나건설",
            winning_amount=980000.0,
        )
    ]
    merged_items, meta = html_parsing.merge_opening_result_rows(items, opening_rows)
    assert meta["opening_result_enriched_count"] == 1
    assert meta["opening_result_grid_id"] == html_parsing.OPENING_RESULT_GRID_ID
    enriched = merged_items[0].metadata
    assert enriched["opening_status"] == "개찰완료"
    assert enriched["winning_company"] == "가나건설"
    # 비어 있던 item 필드는 개찰행에서 채워진다(모델 필드 대입 경로 — dict 릴레이 시절의
    # ``item["business_type"] = ...`` 를 대체한 두 줄이 실제로 반영되는지 고정).
    assert merged_items[0].business_type == "공사"
    assert merged_items[0].region == "서울"  # 수요기관에서 지역 추출
    # Unmatched item is left untouched.
    assert merged_items[1].metadata == {}
    assert merged_items[1].business_type is None
    assert merged_items[1].region is None


def test_merge_opening_result_rows_does_not_overwrite_filled_item_fields():
    """이미 값이 있는 business_type/region 은 개찰행이 덮지 않는다(가드 방향 고정)."""
    items = [
        collected_item(
            notice_number="20260507-002",
            business_type="일반용역",
            region="부산",
            metadata={},
        )
    ]
    opening_rows = [
        OpeningResultRow(
            notice_number="20260507-002",
            status="개찰완료",
            business_type="공사",
            demand_agency="서울특별시청",
        )
    ]

    merged_items, meta = html_parsing.merge_opening_result_rows(items, opening_rows)

    assert meta["opening_result_enriched_count"] == 1
    assert merged_items[0].business_type == "일반용역"
    assert merged_items[0].region == "부산"
    # metadata 병합 자체는 그대로 일어난다.
    assert merged_items[0].metadata["opening_status"] == "개찰완료"


def test_parse_opening_detail_html_extracts_results():
    html = """
    <html><body><table>
        <tr><th>복수예비가격</th><td>1,000,000 1,010,000 1,020,000</td></tr>
        <tr><th>선택번호</th><td>1 3</td></tr>
        <tr><th>낙찰업체</th><td>가나건설</td></tr>
        <tr><th>낙찰금액</th><td>980,000</td></tr>
        <tr><th>낙찰률</th><td>87.745%</td></tr>
    </table></body></html>
    """
    result = html_parsing.parse_opening_detail_html(html)
    assert result["reserve_prices"][:3] == [1000000.0, 1010000.0, 1020000.0]
    assert result["selected_numbers"] == [1, 3]
    assert result["winning_company"] == "가나건설"
    assert result["winning_amount"] == 980000.0
    assert result["winning_rate"] == 87.745
