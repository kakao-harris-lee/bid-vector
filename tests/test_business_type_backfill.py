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
