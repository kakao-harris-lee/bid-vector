"""BusinessTypeEnrichmentService + beat schedule wiring."""

from __future__ import annotations

import pytest
from app.models.models import Project  # noqa: F401 — ensures Base.metadata is populated
from app.tasks.celery_app import (
    ENRICH_BUSINESS_TYPE_TASK_NAME,
    build_business_type_enrichment_beat_schedule,
)


def test_enrichment_schedule_is_empty_by_default(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_SCHEDULE_ENABLED", False)
    assert build_business_type_enrichment_beat_schedule() == {}


def test_enrichment_schedule_builds_when_enabled(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_INTERVAL_MINUTES", 20)
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_BATCH_LIMIT", 30)

    schedule = build_business_type_enrichment_beat_schedule()
    entry = schedule["business_type_enrichment_periodic"]
    assert entry["task"] == ENRICH_BUSINESS_TYPE_TASK_NAME
    assert entry["schedule"] == 20 * 60
    assert entry["kwargs"]["limit"] == 30


def test_enrich_pending_updates_business_type(test_db, monkeypatch):
    """Service writes business_type_code/label from detail fetcher."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(settings, "BUSINESS_TYPE_TITLE_RULES", [])

    project = Project(
        title="테스트 신축공사",
        source_url="https://www.g2b.go.kr/example",
        category="general",
        status="open",
        business_type_code=None,
        business_type_label=None,
    )
    test_db.add(project)
    test_db.commit()

    def fake_fetcher(url: str):
        assert url == "https://www.g2b.go.kr/example"
        return {"business_type_code": "0411", "business_type_label": "일반토목공사"}

    svc = BusinessTypeEnrichmentService(fetcher=fake_fetcher)
    stats = svc.enrich_pending(test_db, limit=10)
    assert stats["candidates"] == 1
    assert stats["updated_from_detail"] == 1
    assert stats["failed"] == 0

    test_db.refresh(project)
    assert project.business_type_code == "0411"
    assert project.business_type_label == "일반토목공사"


def test_enrich_skips_when_detail_returns_empty(test_db, monkeypatch):
    """Empty detail payload + no matching title rule counts as failed, project stays NULL."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(settings, "BUSINESS_TYPE_TITLE_RULES", [])

    project = Project(
        title="결과 없는 공고",
        source_url="https://www.g2b.go.kr/missing",
        category="general",
        status="open",
    )
    test_db.add(project)
    test_db.commit()

    svc = BusinessTypeEnrichmentService(fetcher=lambda url: {})
    stats = svc.enrich_pending(test_db, limit=10)
    assert stats["candidates"] == 1
    assert stats["updated_from_detail"] == 0
    assert stats["failed"] == 1

    test_db.refresh(project)
    assert project.business_type_code is None


def test_enrich_falls_back_to_title_rule_when_detail_returns_empty(test_db, monkeypatch):
    """When detail HTML fetch returns no business_type_code, title-rule matches title."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(
        settings,
        "BUSINESS_TYPE_TITLE_RULES",
        [
            {"pattern": r"공사|건축|토목", "code": "0411", "label": "일반토목공사"},
            {"pattern": r"용역|위탁", "code": "0611", "label": "일반용역"},
        ],
    )

    project = Project(
        title="제주대학교 안전환경개선 건축공사",
        source_url="https://www.g2b.go.kr/x",
        category="general",
        status="open",
    )
    test_db.add(project)
    test_db.commit()

    svc = BusinessTypeEnrichmentService(fetcher=lambda url: {})  # empty detail
    stats = svc.enrich_pending(test_db, limit=10)
    assert stats["candidates"] == 1
    assert stats["updated_from_detail"] == 0
    assert stats["updated_from_title_rule"] == 1
    assert stats["failed"] == 0

    test_db.refresh(project)
    assert project.business_type_code == "0411"
    assert project.business_type_label == "일반토목공사"


def test_enrich_detail_preferred_over_title_rule(test_db, monkeypatch):
    """Detail HTML hit takes precedence; title-rule is fallback only."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(
        settings,
        "BUSINESS_TYPE_TITLE_RULES",
        [{"pattern": r"용역|위탁", "code": "0611", "label": "일반용역"}],
    )

    project = Project(
        title="모종 위탁용역",  # matches title rule
        source_url="https://www.g2b.go.kr/y",
        category="general",
        status="open",
    )
    test_db.add(project)
    test_db.commit()

    # Detail returns a different code — should win over title rule
    svc = BusinessTypeEnrichmentService(
        fetcher=lambda url: {"business_type_code": "0413", "business_type_label": "조경공사"}
    )
    stats = svc.enrich_pending(test_db, limit=10)
    assert stats["updated_from_detail"] == 1
    assert stats["updated_from_title_rule"] == 0

    test_db.refresh(project)
    assert project.business_type_code == "0413"
