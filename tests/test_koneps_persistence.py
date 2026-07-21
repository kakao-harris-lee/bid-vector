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

import pytest

from app.models.models import CrawlJob, HistoricalData, Project, TenderResult
from app.schemas.schemas import CrawlRequest
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
)
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


def test_update_project_persists_award_floor_rate(test_db):
    """An item carrying ``award_floor_rate`` writes it onto the project."""
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    item = _award_item("R26BK01627948")
    item["award_floor_rate"] = 0.88
    persistence.update_project_from_item(project, item=item, request=_request())

    assert project.award_floor_rate == 0.88


def test_update_project_keeps_award_floor_rate_when_item_missing(test_db):
    """A re-collected item without the field must not wipe the stored rate."""
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
        notice_number="R26BK01627948",
        award_floor_rate=0.88,
    )
    test_db.add(project)
    test_db.flush()

    # No award_floor_rate key (e.g. scsbid award item) -> keep the existing value.
    persistence.update_project_from_item(
        project, item=_award_item("R26BK01627948"), request=_request()
    )
    assert project.award_floor_rate == 0.88

    # Explicit None also must not overwrite.
    item = _award_item("R26BK01627948")
    item["award_floor_rate"] = None
    persistence.update_project_from_item(project, item=item, request=_request())
    assert project.award_floor_rate == 0.88


def test_update_project_persists_eligibility_raw(test_db):
    """An item carrying a non-empty eligibility_raw dict writes it onto the project."""
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    item = _award_item("R26BK01628300")
    item["eligibility_raw"] = {"lcnsLmtNm": "토목공사업", "prtcptLmtRgnNm": "부산광역시"}
    persistence.update_project_from_item(project, item=item, request=_request())

    assert project.eligibility_raw == {
        "lcnsLmtNm": "토목공사업",
        "prtcptLmtRgnNm": "부산광역시",
    }


def test_update_project_keeps_eligibility_raw_when_item_missing(test_db):
    """A re-collected/scsbid item without eligibility raw must not wipe the stored dict."""
    stored = {"lcnsLmtNm": "토목공사업"}
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
        notice_number="R26BK01628301",
        eligibility_raw=stored,
    )
    test_db.add(project)
    test_db.flush()

    # No eligibility_raw key at all -> keep the existing value.
    persistence.update_project_from_item(
        project, item=_award_item("R26BK01628301"), request=_request()
    )
    assert project.eligibility_raw == stored

    # Explicit None -> keep.
    none_item = _award_item("R26BK01628301")
    none_item["eligibility_raw"] = None
    persistence.update_project_from_item(project, item=none_item, request=_request())
    assert project.eligibility_raw == stored

    # Empty dict (all raw fields blank) -> keep.
    empty_item = _award_item("R26BK01628301")
    empty_item["eligibility_raw"] = {}
    persistence.update_project_from_item(project, item=empty_item, request=_request())
    assert project.eligibility_raw == stored


def test_resolve_project_creation_persists_award_floor_rate(test_db):
    """A brand-new project created for an item stores the notice floor rate."""
    historical = HistoricalData(notice_number="NEW-FLOOR")
    test_db.add(historical)
    test_db.flush()

    item = _award_item("NEW-FLOOR")
    item["award_floor_rate"] = 0.879
    project, _ = persistence.resolve_project_for_item(
        test_db,
        item=item,
        request=_request(),
        historical_record=historical,
        project_similarity=None,
        defer_embeddings=True,
    )

    assert project is not None
    assert project.award_floor_rate == 0.879


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

    monkeypatch.setattr(persistence.realtime_event_manager, "publish_event", _fake)
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


# --------------------------------------------------------------------------- #
# base 정합화(P1): 개찰 후 UPDATE 가 기존의 더 나은 base 를 0.0(미상)/예정가로 덮지
# 않게 가드하고, base_amount_basis 를 최종 base 기준으로 태깅한다.
# --------------------------------------------------------------------------- #
def _update_base(historical, *, base_amount, estimated_amount=0.0, metadata=None):
    """Drive the private base-field updater with a scsbid-shaped item."""
    item = {"base_amount": base_amount, "estimated_amount": estimated_amount}
    persistence._update_historical_record_from_item(
        historical,
        item=item,
        item_metadata=metadata or {},
        request=_request(),
    )


def test_base_guard_keeps_existing_base_when_incoming_is_zero(test_db):
    """A post-개찰 pass with base_amount=0.0 (미상) must not clobber a stored base."""
    historical = HistoricalData(
        notice_number="GUARD-1", base_amount=100_000_000.0, predicted_price=99_000_000.0
    )
    test_db.add(historical)
    test_db.flush()

    _update_base(
        historical,
        base_amount=0.0,
        estimated_amount=0.0,
        metadata={"winning_amount": 88_000_000.0, "winning_rate": 0.88},
    )

    assert historical.base_amount == 100_000_000.0  # preserved, not zeroed
    assert historical.predicted_price == 99_000_000.0  # preserved


def test_base_guard_writes_real_positive_incoming_base(test_db):
    """A real 기초금액 (positive) overwrites and is tagged clean."""
    historical = HistoricalData(notice_number="GUARD-2")
    test_db.add(historical)
    test_db.flush()

    _update_base(
        historical,
        base_amount=120_000_000.0,
        estimated_amount=121_000_000.0,
        metadata={"winning_amount": 100_000_000.0, "winning_rate": 0.85},
    )

    assert historical.base_amount == 120_000_000.0
    assert historical.predicted_price == 121_000_000.0
    assert historical.base_amount_basis == BASIS_CLEAN
    assert historical.basis_checked_at is not None


def test_base_guard_persists_recovered_estimate_without_touching_base(test_db):
    """A recovered 기초금액 lands in base_amount_estimated; base_amount stays 미상(unset)."""
    historical = HistoricalData(notice_number="GUARD-3")
    test_db.add(historical)
    test_db.flush()

    _update_base(
        historical,
        base_amount=0.0,
        estimated_amount=45_000_000.0,
        metadata={"base_amount_estimated": 30_000_000.0},
    )

    assert historical.base_amount_estimated == 30_000_000.0
    # base_amount was never fed a positive value → stays falsy, tagged suspect.
    assert not historical.base_amount
    assert historical.base_amount_basis == BASIS_SUSPECT_FRACTIONAL


def test_base_guard_tags_yega_when_stored_base_is_yega_reversal(test_db):
    """If the stored base equals 예정가 역산, the write tags it derived-yega (not clean)."""
    yega_base = 43_996_200.0 / 0.88035  # non-integer 예정가 역산
    historical = HistoricalData(notice_number="GUARD-4", base_amount=yega_base)
    test_db.add(historical)
    test_db.flush()

    # incoming base 0.0 keeps the (polluted) stored base; classify tags it yega.
    _update_base(
        historical,
        base_amount=0.0,
        metadata={"winning_amount": 43_996_200.0, "winning_rate": 0.88035},
    )

    assert historical.base_amount == pytest.approx(yega_base)  # unchanged
    assert historical.base_amount_basis == BASIS_DERIVED_YEGA
