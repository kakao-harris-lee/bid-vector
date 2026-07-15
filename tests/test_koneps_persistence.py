"""Unit tests for the extracted ``app.services.koneps.persistence`` module.

These lock in the module-level (db-as-parameter) contract of the DB read /
resolve helpers extracted from ``KonepsCollectorService`` in Phase C1. They
call the module functions directly (not through the service surface) and assert
the behaviour is identical to the original methods. In particular they cover
``resolve_tender_result``, which no longer keeps a service-method surface, and
confirm the collector delegators forward to these same functions.
"""

from __future__ import annotations

import json

from app.models.models import CrawlJob, HistoricalData, Project, TenderResult
from app.schemas.schemas import CrawlRequest
from app.services.koneps import persistence
from app.services.koneps.collector import KonepsCollectorService


def _request() -> CrawlRequest:
    return CrawlRequest(
        source="scsbid-openapi", category="construction", execution_mode="live"
    )


def _award_item(notice_number: str, **overrides) -> dict:
    item = {
        "title": overrides.get("title", f"개찰결과 {notice_number}"),
        "notice_number": notice_number,
        "base_amount": 100_000_000.0,
        "estimated_amount": 96_000_000.0,
        "source_url": f"http://ebid.example.com/detail/{notice_number}",
        "metadata": overrides.get("metadata", {}),
    }
    return item


def test_notice_numbers_with_persisted_reserve_filters_empty(test_db):
    """Only notice numbers carrying a non-empty JSON reserve list are returned."""
    test_db.add(
        HistoricalData(
            notice_number="HAS-RESERVE",
            reserve_prices=json.dumps([101000000.0, 102000000.0]),
        )
    )
    test_db.add(HistoricalData(notice_number="EMPTY-LIST", reserve_prices="[]"))
    test_db.add(HistoricalData(notice_number="EMPTY-STR", reserve_prices=""))
    test_db.add(HistoricalData(notice_number="NULL-RESERVE", reserve_prices=None))
    test_db.flush()

    result = persistence.notice_numbers_with_persisted_reserve(test_db)

    assert result == {"HAS-RESERVE"}


def test_find_matching_project_resolves_via_notice_index(test_db):
    """A notice number matching the indexed column resolves to that project."""
    existing = Project(
        title="기존 공고",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="R26BK01552430",
    )
    test_db.add(existing)
    test_db.flush()

    matched = persistence.find_matching_project(
        test_db, item=_award_item("R26BK01552430"), request=_request()
    )

    assert matched is not None
    assert matched.id == existing.id


def test_find_matching_project_returns_none_for_unknown_notice(test_db):
    """An unmatched notice number does not get fuzzy-merged into another project."""
    other = Project(
        title="개찰결과 R26BK01552430",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="DIFFERENT-NOTICE",
    )
    test_db.add(other)
    test_db.flush()

    matched = persistence.find_matching_project(
        test_db, item=_award_item("R26BK01552430"), request=_request()
    )

    assert matched is None


def test_update_project_from_item_persists_canonical_notice(test_db):
    """``update_project_from_item`` writes the canonical notice number in place."""
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    persistence.update_project_from_item(
        project, item=_award_item("R26BK01552430"), request=_request()
    )

    assert project.notice_number == "R26BK01552430"
    assert project.business_type_label is None  # untouched when absent
    assert project.budget_estimate > 0.0


def test_resolve_project_for_item_creates_and_enriches(test_db):
    """A notice with no prior project creates one, enriched and embedding-deferred."""
    historical = HistoricalData(notice_number="NEW-AWARD")
    test_db.add(historical)
    test_db.flush()

    project, deferred = persistence.resolve_project_for_item(
        test_db,
        item=_award_item("NEW-AWARD"),
        request=_request(),
        historical_record=historical,
        project_similarity=None,  # unused on the defer path
        defer_embeddings=True,
    )

    assert deferred is True
    assert project is not None
    assert project.id is not None
    assert project.notice_number == "NEW-AWARD"


def test_resolve_tender_result_upserts_without_duplicate(test_db):
    """Repeated resolution of the same award reuses the existing tender result."""
    project = Project(
        title="개찰 대상",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="AWARD-1",
    )
    test_db.add(project)
    test_db.flush()

    metadata = {
        "winning_company": "낙찰업체",
        "winning_amount": 95_000_000.0,
        "winning_rate": 87.5,
        "opening_status": "개찰완료",
    }

    first = persistence.resolve_tender_result(
        test_db,
        project_id=project.id,
        item_metadata=metadata,
        crawl_job_status="completed",
    )
    test_db.flush()
    second = persistence.resolve_tender_result(
        test_db,
        project_id=project.id,
        item_metadata=metadata,
        crawl_job_status="completed",
    )
    test_db.flush()

    assert first.id == second.id
    rows = (
        test_db.query(TenderResult)
        .filter(TenderResult.project_id == project.id)
        .count()
    )
    assert rows == 1
    assert second.winning_company == "낙찰업체"
    assert float(second.winning_amount) == 95_000_000.0


def test_collector_delegators_forward_to_persistence(test_db):
    """The retained collector methods delegate to the persistence module."""
    existing = Project(
        title="기존 공고",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="DELEGATE-1",
    )
    test_db.add(existing)
    test_db.flush()

    service = KonepsCollectorService()
    via_method = service._find_matching_project(
        test_db, item=_award_item("DELEGATE-1"), request=_request()
    )
    via_function = persistence.find_matching_project(
        test_db, item=_award_item("DELEGATE-1"), request=_request()
    )

    assert via_method is not None
    assert via_method.id == via_function.id == existing.id


def test_create_crawl_job_commits_running_row(test_db):
    """``create_crawl_job`` inserts a committed ``running`` row stamped with the task id."""
    crawl_job = persistence.create_crawl_job(
        test_db, _request(), celery_task_id="task-123"
    )

    assert crawl_job.id is not None
    assert crawl_job.status == "running"
    assert crawl_job.result_count == 0
    assert crawl_job.celery_task_id == "task-123"
    assert crawl_job.source == "scsbid-openapi"

    # The single commit means a fresh session-independent query sees the row.
    test_db.expire_all()
    reloaded = test_db.query(CrawlJob).filter(CrawlJob.id == crawl_job.id).one()
    assert reloaded.status == "running"
    assert reloaded.celery_task_id == "task-123"


def test_mark_crawl_job_failed_commits_failure(test_db):
    """``mark_crawl_job_failed`` flips an existing row to failed and commits once."""
    crawl_job = persistence.create_crawl_job(test_db, _request())
    assert crawl_job.status == "running"

    failed = persistence.mark_crawl_job_failed(
        test_db, crawl_job, "boom: collection blew up"
    )

    assert failed.id == crawl_job.id
    assert failed.status == "failed"
    assert failed.error_message == "boom: collection blew up"
    assert failed.completed_at is not None

    test_db.expire_all()
    reloaded = test_db.query(CrawlJob).filter(CrawlJob.id == crawl_job.id).one()
    assert reloaded.status == "failed"
    assert reloaded.error_message == "boom: collection blew up"


def test_write_delegators_forward_to_persistence(test_db, monkeypatch):
    """The retained collector write methods delegate to the persistence module."""
    calls: dict[str, tuple] = {}

    def _spy_create(db, request, *, celery_task_id=None):
        calls["create"] = (db, request, celery_task_id)
        return "created-job"

    def _spy_persist(db, crawl_job, request, response, *, defer_embeddings=False):
        calls["persist"] = (db, crawl_job, request, response, defer_embeddings)
        return "persisted-job"

    def _spy_mark(db, crawl_job, error_message):
        calls["mark"] = (db, crawl_job, error_message)
        return "failed-job"

    monkeypatch.setattr(persistence, "create_crawl_job", _spy_create)
    monkeypatch.setattr(persistence, "persist_crawl_results", _spy_persist)
    monkeypatch.setattr(persistence, "mark_crawl_job_failed", _spy_mark)

    service = KonepsCollectorService()
    request = _request()

    assert (
        service.create_crawl_job(test_db, request, celery_task_id="t-9")
        == "created-job"
    )
    assert calls["create"] == (test_db, request, "t-9")

    assert (
        service.persist_crawl_results(
            test_db, "job", request, {"items": []}, defer_embeddings=True
        )
        == "persisted-job"
    )
    assert calls["persist"] == (test_db, "job", request, {"items": []}, True)

    assert service.mark_crawl_job_failed(test_db, "job", "err") == "failed-job"
    assert calls["mark"] == (test_db, "job", "err")


def _capture_crawl_events(monkeypatch) -> list[str]:
    """Patch the persistence module's realtime manager and record event names."""
    events: list[str] = []

    def _fake(event_type, payload=None):
        events.append(event_type)
        return {}

    monkeypatch.setattr(
        persistence.realtime_event_manager, "publish_event", _fake
    )
    return events


def test_persist_crawl_results_publishes_completed_event(test_db, monkeypatch):
    """The success path emits ``crawl.completed`` for a completed job, never ``crawl.failed``."""
    events = _capture_crawl_events(monkeypatch)
    crawl_job = persistence.create_crawl_job(test_db, _request())

    persistence.persist_crawl_results(
        test_db,
        crawl_job,
        _request(),
        {"items": [], "job_status": "completed", "collected_count": 0},
    )

    assert crawl_job.status == "completed"
    # create_crawl_job (running -> crawl.fallback) then the completed publish.
    assert events[-1] == "crawl.completed"
    assert "crawl.failed" not in events


def test_persist_crawl_results_publishes_fallback_event(test_db, monkeypatch):
    """A fallback job status maps to ``crawl.fallback`` (still not ``crawl.failed``)."""
    events = _capture_crawl_events(monkeypatch)
    crawl_job = persistence.create_crawl_job(test_db, _request())

    persistence.persist_crawl_results(
        test_db,
        crawl_job,
        _request(),
        {"items": [], "job_status": "fallback", "collected_count": 0},
    )

    assert crawl_job.status == "fallback"
    assert events[-1] == "crawl.fallback"
    assert "crawl.failed" not in events
