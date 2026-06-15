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


def test_enrich_skips_award_result_urls_without_fetching(test_db, monkeypatch):
    """scsbid 개찰결과(PNPE027) URL 프로젝트는 후보에서 제외 — detail fetcher 미호출."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(settings, "BUSINESS_TYPE_TITLE_RULES", [])
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_SKIP_AWARD_RESULT_URLS", True)
    monkeypatch.setattr(
        settings, "BUSINESS_TYPE_ENRICHMENT_AWARD_RESULT_URL_MARKERS", ["PNPE027"]
    )

    award = Project(
        title="개찰결과만 있는 공고",
        source_url=(
            "https://www.g2b.go.kr/link/PNPE027_01/single/"
            "?bidPbancNo=R26BK01521673&bidPbancOrd=000"
        ),
        category="general",
        status="open",
        business_type_code=None,
    )
    test_db.add(award)
    test_db.commit()

    calls: list[str] = []

    def spy_fetcher(url: str):
        calls.append(url)
        return {"business_type_code": "0411", "business_type_label": "일반토목공사"}

    svc = BusinessTypeEnrichmentService(fetcher=spy_fetcher)
    stats = svc.enrich_pending(test_db, limit=10)

    # Award-result URL never becomes a candidate -> fetcher untouched, no churn.
    assert calls == []
    assert stats["candidates"] == 0
    assert stats["failed"] == 0
    assert stats["updated_from_detail"] == 0

    test_db.refresh(award)
    assert award.business_type_code is None


def test_enrich_processes_non_award_urls_alongside_award(test_db, monkeypatch):
    """비-개찰결과 URL은 후보로 처리되고, 같이 있는 개찰결과 URL만 제외된다."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(settings, "BUSINESS_TYPE_TITLE_RULES", [])
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_SKIP_AWARD_RESULT_URLS", True)
    monkeypatch.setattr(
        settings, "BUSINESS_TYPE_ENRICHMENT_AWARD_RESULT_URL_MARKERS", ["PNPE027"]
    )

    award = Project(
        title="개찰결과 공고",
        source_url="https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=R26BK0001",
        category="general",
        status="open",
        business_type_code=None,
    )
    notice = Project(
        title="정상 공고",
        source_url="https://www.g2b.go.kr/pn/pnp/pnpe/commipbid/PNPE018_01.do?bidNo=1",
        category="general",
        status="open",
        business_type_code=None,
    )
    test_db.add_all([award, notice])
    test_db.commit()

    fetched: list[str] = []

    def spy_fetcher(url: str):
        fetched.append(url)
        return {"business_type_code": "0411", "business_type_label": "일반토목공사"}

    svc = BusinessTypeEnrichmentService(fetcher=spy_fetcher)
    stats = svc.enrich_pending(test_db, limit=10)

    # Only the notice-detail URL is fetched; the award-result URL is skipped.
    assert fetched == [notice.source_url]
    assert stats["candidates"] == 1
    assert stats["updated_from_detail"] == 1

    test_db.refresh(notice)
    test_db.refresh(award)
    assert notice.business_type_code == "0411"
    assert award.business_type_code is None


def test_enrich_skip_disabled_includes_award_urls(test_db, monkeypatch):
    """플래그 OFF면 기존 동작 유지 — 개찰결과 URL도 후보로 잡힌다(롤백 경로)."""
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(settings, "BUSINESS_TYPE_TITLE_RULES", [])
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_SKIP_AWARD_RESULT_URLS", False)
    monkeypatch.setattr(
        settings, "BUSINESS_TYPE_ENRICHMENT_AWARD_RESULT_URL_MARKERS", ["PNPE027"]
    )

    award = Project(
        title="개찰결과 공고",
        source_url="https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=R26BK0009",
        category="general",
        status="open",
        business_type_code=None,
    )
    test_db.add(award)
    test_db.commit()

    fetched: list[str] = []

    def spy_fetcher(url: str):
        fetched.append(url)
        return {}  # award-result page has no business_type -> stays failed

    svc = BusinessTypeEnrichmentService(fetcher=spy_fetcher)
    stats = svc.enrich_pending(test_db, limit=10)

    assert fetched == [award.source_url]
    assert stats["candidates"] == 1
    assert stats["failed"] == 1


def test_enrich_empty_marker_does_not_collapse_candidates(test_db, monkeypatch):
    """빈 마커([""])는 `if marker:` 가드에 걸러져 후보가 0으로 붕괴하지 않는다.

    가드가 없으면 ``~source_url.contains("")``가 모든 행을 제외해 enrichment가
    조용히 멈추는 footgun이 된다. 이 테스트가 그 가드를 잠근다.
    """
    from app.core.config import settings
    from app.services.business_type_enrichment import BusinessTypeEnrichmentService

    monkeypatch.setattr(settings, "BUSINESS_TYPE_TITLE_RULES", [])
    monkeypatch.setattr(settings, "BUSINESS_TYPE_ENRICHMENT_SKIP_AWARD_RESULT_URLS", True)
    monkeypatch.setattr(
        settings, "BUSINESS_TYPE_ENRICHMENT_AWARD_RESULT_URL_MARKERS", [""]
    )

    project = Project(
        title="정상 공고",
        source_url="https://www.g2b.go.kr/pn/pnp/pnpe/commipbid/PNPE018_01.do?bidNo=1",
        category="general",
        status="open",
        business_type_code=None,
    )
    test_db.add(project)
    test_db.commit()

    fetched: list[str] = []

    def spy_fetcher(url: str):
        fetched.append(url)
        return {"business_type_code": "0411", "business_type_label": "일반토목공사"}

    svc = BusinessTypeEnrichmentService(fetcher=spy_fetcher)
    stats = svc.enrich_pending(test_db, limit=10)

    # Empty marker must NOT exclude every row — candidate survives and is enriched.
    assert fetched == [project.source_url]
    assert stats["candidates"] == 1
    assert stats["updated_from_detail"] == 1

    test_db.refresh(project)
    assert project.business_type_code == "0411"


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
