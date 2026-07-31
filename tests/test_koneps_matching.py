"""Unit tests for the extracted pure KONEPS matching / interpretation helpers.

These guard the behavior-preserving extraction from
``KonepsCollectorService`` into ``app.services.koneps.matching``.
"""

from datetime import datetime, timedelta, timezone

from app.models.models import Project
from app.schemas.schemas import CrawlRequest
from app.services.koneps import matching
from tests.support.koneps_items import collected_item


def _request(category: str = "construction") -> CrawlRequest:
    return CrawlRequest(source="koneps", category=category, execution_mode="live")


def test_normalize_source_url_keeps_significant_query_only():
    base = "https://www.koneps.go.kr/detail"
    # Significant query params survive (sorted); insignificant ones are dropped.
    normalized = matching.normalize_source_url(
        f"{base}?bidNtceNo=2025001&utm=foo&bidNtceOrd=00"
    )
    assert normalized == "www.koneps.go.kr/detail?bidntceno=2025001&bidntceord=00"
    # Trailing slash + casing collapse; no significant query -> bare path.
    assert matching.normalize_source_url(f"{base}/") == "www.koneps.go.kr/detail"
    assert matching.normalize_source_url("") == ""
    assert matching.normalize_source_url(None) == ""


def test_resolve_project_category_prefers_specific_request_then_business_type():
    # Specific request category wins outright.
    assert (
        matching.resolve_project_category(
            collected_item(business_type="물품"), _request("software")
        )
        == "software"
    )
    # Generic request falls through to business_type mapping.
    assert (
        matching.resolve_project_category(
            collected_item(business_type="공사"), _request("general")
        )
        == "construction"
    )
    # Empty request category + unmapped business type falls back to the
    # business type (no request_category to shadow it).
    assert (
        matching.resolve_project_category(
            collected_item(business_type="기타용역"), _request("")
        )
        == "기타용역"
    )
    # Empty request + no business type falls back to "other".
    assert matching.resolve_project_category(collected_item(), _request("")) == "other"


def test_resolve_budget_estimate_prefers_estimated_then_base():
    assert (
        matching.resolve_budget_estimate(
            collected_item(estimated_amount=120.0, base_amount=100.0)
        )
        == 120.0
    )
    assert (
        matching.resolve_budget_estimate(collected_item(base_amount=100.0)) == 100.0
    )
    assert matching.resolve_budget_estimate(collected_item()) == 0.0


def test_resolve_project_status_classifies_lifecycle():
    assert (
        matching.resolve_project_status(
            collected_item(metadata={"opening_status": "입찰취소"})
        )
        == "cancelled"
    )
    assert (
        matching.resolve_project_status(
            collected_item(metadata={"opening_status": "유찰"})
        )
        == "failed"
    )
    assert (
        matching.resolve_project_status(
            collected_item(metadata={"winning_company": "ACME"})
        )
        == "awarded"
    )
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert (
        matching.resolve_project_status(collected_item(closing_at=future, metadata={}))
        == "open"
    )
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert (
        matching.resolve_project_status(collected_item(closing_at=past, metadata={}))
        == "closed"
    )


def test_extract_item_agency_keys_normalizes_and_dedupes():
    keys = matching.extract_item_agency_keys(
        collected_item(
            metadata={
                "issuing_agency": "서울특별시청",
                "opening_demand_agency": "",
                "demand_agency": None,
            }
        )
    )
    assert keys == {matching.parsing.normalize_agency_name("서울특별시청")}


def test_extract_project_agency_keys_reads_labeled_notes():
    project = Project(
        title="t",
        issuing_agency=None,
        demand_agency=None,
        description="공고기관: 한국전력공사\n수요기관: 부산광역시",
    )
    keys = matching.extract_project_agency_keys(project)
    assert matching.parsing.normalize_agency_name("한국전력공사") in keys
    assert matching.parsing.normalize_agency_name("부산광역시") in keys


def test_extract_project_notice_number_prefers_column_then_notes():
    assert (
        matching.extract_project_notice_number(
            Project(title="t", notice_number="2025-001")
        )
        == "2025-001"
    )
    from_notes = matching.extract_project_notice_number(
        Project(title="t", description="공고번호: ABC-123")
    )
    assert from_notes == "ABC-123"


def test_extract_project_source_url_prefers_column_then_notes():
    assert (
        matching.extract_project_source_url(
            Project(title="t", source_url="https://x.test/a")
        )
        == "https://x.test/a"
    )
    from_notes = matching.extract_project_source_url(
        Project(title="t", description="공고원문: https://koneps.test/detail?x=1")
    )
    assert from_notes == "https://koneps.test/detail?x=1"


def test_match_by_url_or_title_prioritises_source_url():
    target = Project(title="도로 포장 공사", source_url="https://koneps.test/a?bidNtceNo=1")
    other = Project(title="다른 공사", source_url="https://koneps.test/b")
    result = matching.match_by_url_or_title(
        [other, target],
        target_source_url=matching.normalize_source_url(
            "https://koneps.test/a?bidNtceNo=1"
        ),
        target_title="무관한제목",
        target_agencies=set(),
        target_budget=0.0,
        target_deadline=None,
    )
    assert result is target


def test_match_by_url_or_title_returns_none_without_signal():
    candidate = Project(title="도로 포장 공사")
    result = matching.match_by_url_or_title(
        [candidate],
        target_source_url="",
        target_title="",
        target_agencies=set(),
        target_budget=0.0,
        target_deadline=None,
    )
    assert result is None
