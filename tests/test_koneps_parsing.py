"""Unit tests for the extracted pure KONEPS parsing primitives.

These guard the behavior-preserving extraction from
``KonepsCollectorService`` into ``app.services.koneps.parsing``.
"""

from datetime import datetime, timezone

import pytest

from app.services.koneps import parsing


def test_safe_int():
    assert parsing.safe_int("12") == 12
    assert parsing.safe_int(7) == 7
    assert parsing.safe_int(None) is None
    assert parsing.safe_int("abc") is None


def test_coerce_amount():
    assert parsing.coerce_amount("1,250,000") == 1250000.0
    assert parsing.coerce_amount(99) == 99.0
    assert parsing.coerce_amount("기준가 125,000,000원") == 125000000.0
    assert parsing.coerce_amount("") is None
    assert parsing.coerce_amount("not-a-number") is None


def test_coerce_datetime():
    result = parsing.coerce_datetime("2025-01-02T03:04:05")
    assert result is not None
    assert result.tzinfo is not None
    assert parsing.coerce_datetime("") is None
    aware = datetime(2025, 1, 2, tzinfo=timezone.utc)
    assert parsing.coerce_datetime(aware) == aware
    # Falls back to fuzzy extraction for non-ISO strings.
    fuzzy = parsing.coerce_datetime("마감 2025.03.04 17:00")
    assert fuzzy is not None
    assert (fuzzy.year, fuzzy.month, fuzzy.day) == (2025, 3, 4)


def test_coerce_int_value():
    assert parsing.coerce_int_value("1,234") == 1234
    assert parsing.coerce_int_value("12.0") == 12
    assert parsing.coerce_int_value("") is None
    assert parsing.coerce_int_value("xx") is None


def test_extract_integer_tokens():
    assert parsing.extract_integer_tokens("등급 1 2 3 99 100") == [1, 2, 3, 99]
    assert parsing.extract_integer_tokens("1 2 3 4", max_items=2) == [1, 2]


def test_extract_amounts():
    assert parsing.extract_amounts("기초 125,000,000 추정 121,500,000") == [
        125000000.0,
        121500000.0,
    ]
    assert parsing.extract_amounts("금액 없음") == []


def test_extract_datetime():
    parsed = parsing.extract_datetime("개찰 2025-05-06 09:30")
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2025, 5, 6, 9)
    assert parsing.extract_datetime("날짜 없음") is None


def test_extract_percentage():
    assert parsing.extract_percentage("87.5%") == 87.5
    assert parsing.extract_percentage("0.875") == 0.875
    assert parsing.extract_percentage("") is None
    assert parsing.extract_percentage("없음") is None


def test_normalize_bid_rate_value():
    assert parsing.normalize_bid_rate_value("87.5%") == 0.875
    assert parsing.normalize_bid_rate_value(0.875) == 0.875
    assert parsing.normalize_bid_rate_value(0) is None
    assert parsing.normalize_bid_rate_value(None) is None


def test_extract_notice_number():
    assert parsing.extract_notice_number("공고 20250102-01 안내") == "20250102-01"
    # Uppercase letter-prefix codes need 2+ leading letters per the regex.
    assert parsing.extract_notice_number("코드 BK01552430 입찰") == "BK01552430"
    assert parsing.extract_notice_number("긴번호 20250102001 안내") == "20250102001"
    assert parsing.extract_notice_number("번호 없음") is None


def test_normalize_title():
    assert parsing.normalize_title("  유지 관리(용역) ") == "유지관리용역"
    assert parsing.normalize_title(None) == ""


def test_normalize_notice_number():
    assert parsing.normalize_notice_number("r26bk 01552430") == "R26BK01552430"
    assert parsing.normalize_notice_number(None) == ""


def test_normalize_agency_name():
    assert parsing.normalize_agency_name("  서울 특별시 ") == "서울특별시"
    assert parsing.normalize_agency_name(None) == ""


def test_normalize_status_text():
    assert parsing.normalize_status_text("  마감  임박 ") == "마감임박"
    assert parsing.normalize_status_text(None) == ""


def test_merge_text_lines():
    merged = parsing.merge_text_lines("기존 메모", ["기존 메모", "신규 라인", None, ""])
    assert merged == "기존 메모\n신규 라인"
    assert parsing.merge_text_lines(None, ["하나"]) == "하나"


def test_find_field_value():
    field_map = {"공고기관명": "서울시", "예정가격": "1,000"}
    assert parsing.find_field_value(field_map, ["공고기관"]) == "서울시"
    assert parsing.find_field_value(field_map, ["없는라벨"]) == ""


def test_extract_title():
    cells = ["20250102-01", "1,250,000", "교량 보수 공사"]
    assert parsing.extract_title(cells, "20250102-01", "상세보기") == "교량 보수 공사"
    assert parsing.extract_title(cells, "20250102-01", "실제 제목") == "실제 제목"


def test_extract_region():
    assert parsing.extract_region(["발주 서울 지역"]) == "서울"
    assert parsing.extract_region(["지역 미정"]) is None


def test_extract_license_codes():
    assert parsing.extract_license_codes("SW001 IT002 sw003") == ["IT002", "SW001"]
    assert parsing.extract_license_codes("면허 없음") == []


def test_extract_koneps_title():
    class _Cell:
        def __init__(self, text, title=None):
            self._text = text
            self._title = title

        def select_one(self, _selector):
            return None

        def get(self, key):
            return self._title if key == "title" else None

        def get_text(self, _sep, strip=False):
            return self._text.strip() if strip else self._text

    assert parsing.extract_koneps_title(_Cell("도로 공사")) == "도로 공사"
    assert parsing.extract_koneps_title(_Cell("fallback", title="제목속성")) == "제목속성"


def test_is_budget_compatible():
    class _Project:
        budget_estimate = 100_000_000.0
        budget_max = None
        budget_min = None

    assert parsing.is_budget_compatible(_Project(), 0) is True
    assert parsing.is_budget_compatible(_Project(), 105_000_000.0) is True
    assert parsing.is_budget_compatible(_Project(), 200_000_000.0) is False


def test_is_deadline_compatible():
    a = datetime(2025, 1, 1, tzinfo=timezone.utc)
    b = datetime(2025, 1, 3, tzinfo=timezone.utc)
    far = datetime(2025, 2, 1, tzinfo=timezone.utc)
    assert parsing.is_deadline_compatible(None, b) is True
    assert parsing.is_deadline_compatible(a, b) is True
    assert parsing.is_deadline_compatible(a, far) is False


def test_should_replace_project_title():
    assert parsing.should_replace_project_title(None, "신규") is True
    assert parsing.should_replace_project_title("", "신규") is True
    assert parsing.should_replace_project_title("KONEPS notice 1", "신규") is True
    assert parsing.should_replace_project_title("KONEPS-2025", "신규") is True
    assert parsing.should_replace_project_title("실제 사업명", "신규") is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
