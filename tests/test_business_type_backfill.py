"""Tests for the business_type columns on Project + the backfill pipeline."""

import pytest
from app.models.models import Project


def test_project_has_business_type_columns(test_db):
    """Project model must expose business_type_code and business_type_label."""
    project = Project(
        title="컬럼 추가 검증 공고",
        description="신규 컬럼 존재 검증",
        requirements="-",
        budget_estimate=100_000_000.0,
        category="construction",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    test_db.add(project)
    test_db.flush()
    test_db.refresh(project)
    assert project.business_type_code == "0411"
    assert project.business_type_label == "건축공사"


def test_project_business_type_columns_are_nullable(test_db):
    """Existing rows pre-backfill must coexist with NULL columns."""
    project = Project(
        title="레거시 호환 공고",
        description="-",
        requirements="-",
        budget_estimate=50_000_000.0,
        category="service",
    )
    test_db.add(project)
    test_db.flush()
    test_db.refresh(project)
    assert project.business_type_code is None
    assert project.business_type_label is None


def test_crawl_notice_item_carries_business_type_code():
    from app.schemas.schemas import CrawlNoticeItem

    item = CrawlNoticeItem(
        notice_number="2026-001",
        title="OO 건축공사",
        base_amount=100_000_000.0,
        business_type="공사",
        business_type_code="0411",
        business_type_label="건축공사",
    )
    assert item.business_type_code == "0411"
    assert item.business_type_label == "건축공사"


def test_extract_business_type_code_from_cell_text():
    """Collector helper은 KONEPS HTML 셀의 'code 라벨' 형식을 분리해야 한다."""
    from app.services.koneps.collector import KonepsCollectorService

    service = KonepsCollectorService()
    code, label = service._split_business_type_cell("0411 건축공사")
    assert code == "0411"
    assert label == "건축공사"

    code, label = service._split_business_type_cell("건축공사")
    assert code is None
    assert label == "건축공사"

    code, label = service._split_business_type_cell("")
    assert code is None
    assert label is None


def test_upsert_project_persists_business_type(test_db):
    """KonepsCollectorService._update_project_from_item가 business_type 필드를 영속화한다."""
    from app.models.models import Project
    from app.schemas.schemas import CrawlRequest
    from app.services.koneps.collector import KonepsCollectorService

    service = KonepsCollectorService()
    request = CrawlRequest(source="test", category="construction")

    # Create a fresh project row (mimics the "new project" path in _resolve_project_for_item)
    project = Project(
        title="건축공사 upsert 검증",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    # Build the item dict the same way CrawlNoticeItem.model_dump(mode="json") would produce
    item = {
        "notice_number": "UPSERT-0411-1",
        "title": "건축공사 upsert 검증",
        "base_amount": 100_000_000.0,
        "business_type": "공사",
        "business_type_code": "0411",
        "business_type_label": "건축공사",
    }

    service._update_project_from_item(project, item=item, request=request)
    test_db.flush()
    test_db.refresh(project)

    assert project.business_type_code == "0411"
    assert project.business_type_label == "건축공사"
