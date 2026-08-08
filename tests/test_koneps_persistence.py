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

from app.core.constants import (
    ESTIMATE_SOURCE_BASE_FALLBACK,
    ESTIMATE_SOURCE_DERIVED,
    ESTIMATE_SOURCE_NOTICE,
    ESTIMATED_AMOUNT_SOURCES,
)
from app.models.models import CrawlJob, HistoricalData, Project, TenderResult
from app.schemas.koneps_items import CrawlItemMetadataFacts, KonepsCollectedItem
from app.schemas.schemas import CrawlRequest
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
    BASIS_SUSPECT_RATIO,
)
from app.services.koneps import persistence
from app.services.koneps.collector import KonepsCollectorService


def _request() -> CrawlRequest:
    return CrawlRequest(
        source="scsbid-openapi", category="construction", execution_mode="live"
    )


def _award_item(notice_number: str, **overrides) -> KonepsCollectedItem:
    """개찰 표본을 수집 item DTO 로 만든다(consumer 는 dict 가 아니라 DTO 를 받는다)."""
    return KonepsCollectedItem(
        title=overrides.get("title", f"개찰결과 {notice_number}"),
        notice_number=notice_number,
        base_amount=100_000_000.0,
        estimated_amount=96_000_000.0,
        source_url=f"http://ebid.example.com/detail/{notice_number}",
        metadata=overrides.get("metadata", {}),
        award_floor_rate=overrides.get("award_floor_rate"),
        eligibility_raw=overrides.get("eligibility_raw"),
    )


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
    """A notice with no prior project creates one and reports semantic change."""
    historical = HistoricalData(notice_number="NEW-AWARD")
    test_db.add(historical)
    test_db.flush()

    project, semantic_input_changed = persistence.resolve_project_for_item(
        test_db,
        item=_award_item("NEW-AWARD"),
        request=_request(),
        historical_record=historical,
    )

    assert semantic_input_changed is True
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

    item = _award_item("R26BK01627948", award_floor_rate=0.88)
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
    item = _award_item("R26BK01627948", award_floor_rate=None)
    persistence.update_project_from_item(project, item=item, request=_request())
    assert project.award_floor_rate == 0.88


def test_update_project_drops_implausible_award_floor_rate(test_db):
    """성립 불가한 게시 하한율은 공고에 적재되지 않는다 — 수집 경로 끝에서 본 결과.

    게이트는 DTO 계약(``KonepsCollectedItem.award_floor_rate``)에 있어 item 이 만들어질
    때 이미 ``None`` 으로 접힌다. 여기서 확인하는 것은 그 결과가 persistence 의 anti-clobber
    가드와 맞물려 **컬럼에 1.0 이 남지 않는다**는 것이다. 이 컬럼은 라이브 가격 경로가
    예산 상한 초과 권한을 판정할 때 읽는 입력이다(#356 V3).
    """
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    item = _award_item("R26BK01654006", award_floor_rate=1.0)
    persistence.update_project_from_item(project, item=item, request=_request())

    assert project.award_floor_rate is None


def test_update_project_keeps_stored_rate_when_item_is_implausible(test_db):
    """비개연 값은 이미 저장된 정상 하한율을 덮지도 않는다(미보고와 동일 가드)."""
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
        notice_number="R26BK01654007",
        award_floor_rate=0.89745,
    )
    test_db.add(project)
    test_db.flush()

    item = _award_item("R26BK01654007", award_floor_rate=1.0)
    persistence.update_project_from_item(project, item=item, request=_request())

    assert project.award_floor_rate == pytest.approx(0.89745)


def test_update_project_persists_plausible_award_floor_rate(test_db):
    """게이트는 진짜 게시값(신율 0.89745)을 막지 않는다."""
    project = Project(
        title="placeholder",
        description="",
        requirements="",
        budget_estimate=0.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()

    item = _award_item("R26BK01654008", award_floor_rate=0.89745)
    persistence.update_project_from_item(project, item=item, request=_request())

    assert project.award_floor_rate == pytest.approx(0.89745)


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

    item = _award_item(
        "R26BK01628300",
        eligibility_raw={"lcnsLmtNm": "토목공사업", "prtcptLmtRgnNm": "부산광역시"},
    )
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
    none_item = _award_item("R26BK01628301", eligibility_raw=None)
    persistence.update_project_from_item(project, item=none_item, request=_request())
    assert project.eligibility_raw == stored

    # Empty dict (all raw fields blank) -> keep.
    empty_item = _award_item("R26BK01628301", eligibility_raw={})
    persistence.update_project_from_item(project, item=empty_item, request=_request())
    assert project.eligibility_raw == stored


def test_resolve_project_creation_persists_award_floor_rate(test_db):
    """A brand-new project created for an item stores the notice floor rate."""
    historical = HistoricalData(notice_number="NEW-FLOOR")
    test_db.add(historical)
    test_db.flush()

    item = _award_item("NEW-FLOOR", award_floor_rate=0.879)
    project, _ = persistence.resolve_project_for_item(
        test_db,
        item=item,
        request=_request(),
        historical_record=historical,
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

    facts = CrawlItemMetadataFacts.model_validate(metadata)
    first = persistence.resolve_tender_result(
        test_db,
        project_id=project.id,
        facts=facts,
        crawl_job_status="completed",
    )
    test_db.flush()
    second = persistence.resolve_tender_result(
        test_db,
        project_id=project.id,
        facts=facts,
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


def test_persist_tender_result_links_project_id_from_historical_record(test_db):
    """``project`` 가 없어도 HistoricalData 가 아는 project_id 로 TenderResult 를 잇는다.

    이 경로는 persist 정상 경로(``resolve_project_for_item`` 이 항상 Project 를 돌려준다)
    로는 타지 않아 골든에도 안 잡힌다. 그래서 경계 함수를 직접 구동해 고정한다 — 없으면
    개찰 결과가 project 미연결(orphan)로 남아 정산/대시보드에서 사라진다. 링크는
    ``resolve_tender_result`` 에 넘기는 ``project_id`` 인자 **한 곳**에서만 일어난다(호출
    뒤의 사후 백필은 도달 불가라 제거됐다 — 아래 불변식 테스트 참조).
    """
    project = Project(
        title="개찰 연결 대상",
        description="",
        requirements="",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number="BACKFILL-1",
    )
    test_db.add(project)
    test_db.flush()
    historical = HistoricalData(notice_number="BACKFILL-1", project_id=project.id)
    test_db.add(historical)
    test_db.flush()

    persistence._persist_tender_result_for_item(
        test_db,
        project=None,  # 매칭 실패/미생성 상황
        historical_record=historical,
        facts=CrawlItemMetadataFacts(
            winning_company="낙찰사",
            winning_amount=95_000_000.0,
            opening_status="낙찰",
        ),
        crawl_job_status="completed",
    )
    test_db.flush()

    tender_result = (
        test_db.query(TenderResult).filter(TenderResult.project_id == project.id).one()
    )
    assert tender_result.winning_company == "낙찰사"


@pytest.mark.parametrize("linked", [True, False])
def test_resolve_tender_result_always_stamps_the_given_project_id(test_db, linked):
    """불변식: 반환 행의 project_id 는 **항상** 인자와 같다(사후 백필 분기 도달 불가 근거).

    ``_persist_tender_result_for_item`` 에는 "반환 project_id 가 None 인데 historical 에는
    값이 있으면 채운다"는 분기가 있었다. 이 불변식 때문에 그 상태는 상호 배타(반환이 None
    이려면 인자가 None 이어야 하고, 인자가 None 이려면 historical 도 None) — 죽은 코드였다.
    """
    project_id = None
    if linked:
        project = Project(
            title="불변식 대상",
            description="",
            requirements="",
            budget_estimate=1.0,
            category="construction",
            notice_number="INVARIANT-1",
        )
        test_db.add(project)
        test_db.flush()
        project_id = project.id

    tender_result = persistence.resolve_tender_result(
        test_db,
        project_id=project_id,
        facts=CrawlItemMetadataFacts(winning_company="낙찰사"),
        crawl_job_status="completed",
    )
    test_db.flush()

    assert tender_result.project_id == project_id


def test_persist_tender_result_skips_when_no_award_signal(test_db):
    """개찰/낙찰 흔적이 전혀 없으면 TenderResult 를 만들지 않는다(게이트 방향 고정)."""
    historical = HistoricalData(notice_number="NO-SIGNAL")
    test_db.add(historical)
    test_db.flush()

    persistence._persist_tender_result_for_item(
        test_db,
        project=None,
        historical_record=historical,
        facts=CrawlItemMetadataFacts(),
        crawl_job_status="completed",
    )
    test_db.flush()

    assert test_db.query(TenderResult).count() == 0


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

    def _spy_persist(db, crawl_job, request, response):
        calls["persist"] = (db, crawl_job, request, response)
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
        service.persist_crawl_results(test_db, "job", request, {"items": []})
        == "persisted-job"
    )
    assert calls["persist"] == (test_db, "job", request, {"items": []})

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
def _update_base(
    historical, *, base_amount, estimated_amount=0.0, metadata=None, project=None
):
    """Drive the private base-field updater with a scsbid-shaped item DTO."""
    item = KonepsCollectedItem(
        notice_number=historical.notice_number,
        title="base guard",
        base_amount=base_amount,
        estimated_amount=estimated_amount,
        metadata=metadata or {},
    )
    persistence._update_historical_record_from_item(
        historical,
        item=item,
        request=_request(),
        project=project,
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


_POLLUTED_BASE = 140_800_000.0  # 추정가격 100,000,000 대비 1.408 (운영 실측 p50)


def _notice_project(test_db, *, budget_estimate: float) -> Project:
    project = Project(
        title="비율 게이트 공고",
        budget_estimate=budget_estimate,
        status="open",
        category="construction",
    )
    test_db.add(project)
    test_db.flush()
    return project


def test_recollection_does_not_revert_a_suspect_ratio_tag(test_db):
    """재수집이 백필의 ``suspect-ratio`` 재태깅을 ``clean`` 으로 되돌리면 안 된다.

    회귀 재현(코드리뷰 C1): 이 write 경로는 매 수집마다 **기존 행**의
    ``base_amount_basis`` 를 최종 base 기준으로 다시 계산해 덮어쓴다. 공고 추정가격을
    넘기지 않으면 비율 규칙이 꺼진 채 재분류되므로, 정수 오염 base 는 다시 ``clean`` 으로
    승격된다 — 즉 백필의 재태깅이 다음 수집 주기(1h/6h)에 소멸한다. 열린 공고 전량과
    scsbid 가 재방문하는 settled 행이 여기 해당하므로, 배선 없이는 재태깅이 지속되지 않는다.
    """
    project = _notice_project(test_db, budget_estimate=100_000_000.0)
    historical = HistoricalData(
        notice_number="GUARD-5",
        project_id=project.id,
        base_amount=_POLLUTED_BASE,
        base_amount_basis=BASIS_SUSPECT_RATIO,
    )
    test_db.add(historical)
    test_db.flush()

    _update_base(
        historical,
        base_amount=_POLLUTED_BASE,
        estimated_amount=100_000_000.0,
        project=project,
    )

    assert historical.base_amount == _POLLUTED_BASE  # 원본 금액 불변
    assert historical.base_amount_basis == BASIS_SUSPECT_RATIO  # 재태깅 지속


def test_collection_tags_a_newly_polluted_base_as_suspect_ratio(test_db):
    """신규 수집 행도 수집 시점에 비율 규칙으로 태깅된다(백필을 기다리지 않는다)."""
    project = _notice_project(test_db, budget_estimate=100_000_000.0)
    historical = HistoricalData(notice_number="GUARD-6", project_id=project.id)
    test_db.add(historical)
    test_db.flush()

    _update_base(
        historical,
        base_amount=_POLLUTED_BASE,
        estimated_amount=100_000_000.0,
        project=project,
    )

    assert historical.base_amount_basis == BASIS_SUSPECT_RATIO
    assert historical.basis_checked_at is not None


@pytest.mark.parametrize("budget_estimate", [0.0, None])
def test_collection_without_a_notice_estimate_keeps_prior_behaviour(
    test_db, budget_estimate
):
    """추정가격을 확보하지 못한 공고는 비율 규칙이 꺼진 채 기존 판정을 유지한다.

    ``matching.resolve_budget_estimate`` 를 쓰지 않는 이유가 이것과 짝이다: 그 헬퍼는
    추정가격이 없으면 ``base_amount`` 로 폴백하므로 비율이 1.0 으로 자기충족해 규칙이
    조용히 무력화된다. 여기서는 확보 못 한 것을 확보 못 한 대로 둔다.
    """
    project = _notice_project(test_db, budget_estimate=budget_estimate)
    historical = HistoricalData(notice_number="GUARD-7", project_id=project.id)
    test_db.add(historical)
    test_db.flush()

    _update_base(historical, base_amount=_POLLUTED_BASE, project=project)

    assert historical.base_amount_basis == BASIS_CLEAN


def test_collection_without_a_project_keeps_prior_behaviour(test_db):
    """project 를 넘기지 않는 호출부(레거시 경로)는 판정이 바이트 동일하다."""
    historical = HistoricalData(notice_number="GUARD-8")
    test_db.add(historical)
    test_db.flush()

    _update_base(historical, base_amount=_POLLUTED_BASE, estimated_amount=100_000_000.0)

    assert historical.base_amount_basis == BASIS_CLEAN


def test_persist_crawl_item_feeds_the_notice_estimate_to_the_classifier(test_db):
    """전체 persist 경로가 project 추정가격을 분류기까지 실어 나른다(배선 확인).

    단위 테스트가 헬퍼를 직접 부르는 것과 달리, 여기서는 ``_persist_crawl_item`` 이
    resolve 한 project 를 그대로 넘기는지 본다 — 그 배선이 빠지면 위 회귀가 그대로 살아난다.
    """
    item = KonepsCollectedItem(
        title="비율 오염 공고",
        notice_number="PERSIST-RATIO-1",
        base_amount=_POLLUTED_BASE,
        estimated_amount=100_000_000.0,
        source_url="http://ebid.example.com/detail/PERSIST-RATIO-1",
    )

    persistence.persist_crawl_results(
        test_db,
        persistence.create_crawl_job(test_db, _request()),
        _request(),
        {"items": [item], "job_status": "completed", "collected_count": 1},
    )

    historical = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "PERSIST-RATIO-1")
        .one()
    )
    assert historical.base_amount == _POLLUTED_BASE
    assert historical.base_amount_basis == BASIS_SUSPECT_RATIO


# --------------------------------------------------------------------------- #
# 추정가격 출처 인지 덮어쓰기 가드(#358 후속). 재태깅 지속을 깨던 두 프로덕션 시퀀스를
# 전체 persist 경로로 고정한다. #358 은 이 둘을 "되돌아가는 것이 현재 동작"인 알려진
# 갭으로 고정했고, 이 PR 이 그 기대값을 뒤집는다 — 분류기가 읽는 분모
# (``project.budget_estimate``)가 파생/폴백 값으로 덮이지 않기 때문이다.
# --------------------------------------------------------------------------- #
def _persist_one(test_db, item) -> None:
    persistence.persist_crawl_results(
        test_db,
        persistence.create_crawl_job(test_db, _request()),
        _request(),
        {"items": [item], "job_status": "completed", "collected_count": 1},
    )


def _basis_of(test_db, notice_number: str) -> str:
    return (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == notice_number)
        .one()
        .base_amount_basis
    )


def _notice_item(notice: str, **overrides) -> KonepsCollectedItem:
    """공고 피드가 배출하는 모양의 item(진짜 추정가격 + ``notice`` 출처)."""
    fields = {
        "title": "공고",
        "notice_number": notice,
        "base_amount": _POLLUTED_BASE,
        "estimated_amount": 100_000_000.0,
        "estimated_amount_source": ESTIMATE_SOURCE_NOTICE,
        "source_url": f"http://ebid.example.com/detail/{notice}",
    }
    return KonepsCollectedItem(**{**fields, **overrides})


def _project_of(test_db, notice: str) -> Project:
    historical = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == notice)
        .one()
    )
    return test_db.query(Project).filter(Project.id == historical.project_id).one()


def test_scsbid_pass_no_longer_reverts_the_tag(test_db):
    """scsbid 개찰 패스가 분모를 예정가로 바꾸지 못한다(#358 known gap 반전).

    시퀀스: 공고 수집(추정가격 실림) → 태그 ``suspect-ratio`` → 6시간 주기 scsbid 개찰
    패스. 그 패스의 item 은 ``base_amount=0.0`` + ``estimated_amount=예정가``
    (``scsbid.py`` 의 ``planned_price`` 또는 ``winning ÷ success_rate``)를 ``derived``
    출처로 싣는다. 가드 이전에는 ``update_project_from_item`` 이 태깅보다 먼저 그 예정가로
    ``project.budget_estimate`` 를 덮어 분모가 +10% 뜨고, 임계 바로 위 밴드
    (실측 1.16·1.20·1.24)가 clean 으로 복귀했다. 이제 파생 출처는 저장된 양수 추정가격을
    덮지 못하므로 분모가 보존되고 태그도 유지된다.

    이 가드가 지키는 전망 코호트 실측(활성 14,840 / 비율>1.15 1,625 / published 하한 보고
    1,444, scsbid 경로 618)은 ``docs/operations/base-amount-basis-backfill.md`` §6 참조.
    """
    notice = "SCSBID-REVERT-1"
    _persist_one(
        test_db,
        _notice_item(notice, base_amount=116_000_000.0),  # 추정가격의 1.16배
    )
    assert _basis_of(test_db, notice) == BASIS_SUSPECT_RATIO

    # 개찰 패스: base 미상(0.0) + 예정가를 estimated_amount 로 싣는다.
    _persist_one(
        test_db,
        KonepsCollectedItem(
            title="개찰결과",
            notice_number=notice,
            base_amount=0.0,
            estimated_amount=111_000_000.0,  # 예정가 ≈ 기초금액 × 사정률
            estimated_amount_source=ESTIMATE_SOURCE_DERIVED,
        ),
    )

    historical = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == notice)
        .one()
    )
    assert historical.base_amount == 116_000_000.0  # 원본 금액은 그대로
    assert historical.base_amount_basis == BASIS_SUSPECT_RATIO  # 재태깅 유지
    # 분모가 보존됐다: 116,000,000 ÷ 100,000,000 = 1.16 > 1.15.
    assert _project_of(test_db, notice).budget_estimate == 100_000_000.0


def test_recollection_without_an_estimate_no_longer_reverts_the_tag(test_db):
    """추정가격을 싣지 않은 재수집도 태그를 되돌리지 못한다(#358 known gap 반전).

    ``matching.resolve_budget_estimate`` 의 ``base_amount`` 폴백 자체는 그대로 둔다
    (``budget_min``/``budget_max`` 등 다른 소비자가 있다). 대신 그 값이 **기초금액 폴백**
    이라는 사실을 item 이 신고하고, write 가드가 이미 저장된 양수 추정가격을 지키므로
    비율이 1.0 으로 자기충족하는 ``est_equals_base`` 코호트로 떨어지지 않는다.

    같이 사라지던 부수 피해도 함께 막힌다: 저장된 진짜 추정가격이 base 로 덮여
    **복구 불가**하게 소실되던 경로가 이 가드의 반대편이다. 이미 그렇게 굳은 행은
    ``--reclassify-clean`` 으로도 회복되지 않는다(실측 3,982건, 런북 §6).
    """
    notice = "NOESTIMATE-REVERT-1"
    _persist_one(test_db, _notice_item(notice))
    assert _basis_of(test_db, notice) == BASIS_SUSPECT_RATIO

    # 재수집이 추정가격을 못 얻으면 생산자는 기초금액을 폴백으로 실어 그 사실을 신고한다.
    _persist_one(
        test_db,
        _notice_item(
            notice,
            title="공고(추정가격 미공급)",
            estimated_amount=_POLLUTED_BASE,
            estimated_amount_source=ESTIMATE_SOURCE_BASE_FALLBACK,
        ),
    )

    historical = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == notice)
        .one()
    )
    assert historical.base_amount_basis == BASIS_SUSPECT_RATIO  # 재태깅 유지
    assert _project_of(test_db, notice).budget_estimate == 100_000_000.0  # 소실 없음


@pytest.mark.parametrize("corrected", [120_000_000.0, 80_000_000.0])
def test_notice_feed_still_applies_a_corrected_estimate(test_db, corrected):
    """정정공고/재공고의 **진짜 추정가격**은 상향·하향 모두 그대로 반영된다.

    무차별 "양수 불변" 가드를 채택하지 않은 이유가 이것이다: KONEPS 는 정정공고로
    추정가격을 바꿔 게시하고, 그 갱신을 막으면 저장값이 첫 게시 시점에 얼어붙는다.

    **하향** 정정을 함께 고정하는 이유(리뷰 api-n2): 이 컬럼은 #356 V3 게이트의
    ``budget_cap`` 이라 하향 정정은 상한을 **조이는** 방향이다. 가드가 하향을 막으면 게이트가
    실제보다 느슨한 상한 위에서 판정한다 — 상향을 막는 것보다 나쁜 실패 방향이다.
    """
    notice = f"NOTICE-CORRECTION-{int(corrected)}"
    _persist_one(test_db, _notice_item(notice))
    assert _project_of(test_db, notice).budget_estimate == 100_000_000.0

    _persist_one(
        test_db, _notice_item(notice, title="정정공고", estimated_amount=corrected)
    )

    assert _project_of(test_db, notice).budget_estimate == corrected


@pytest.mark.parametrize("source", [*ESTIMATED_AMOUNT_SOURCES, None])
def test_first_collection_fills_an_empty_estimate_from_any_source(test_db, source):
    """최초 수집은 출처와 무관하게 빈 자리를 채운다(신규 공고에서 값이 사라지지 않게)."""
    notice = f"FIRST-FILL-{source or 'none'}"
    _persist_one(
        test_db,
        _notice_item(
            notice, estimated_amount=90_000_000.0, estimated_amount_source=source
        ),
    )

    assert _project_of(test_db, notice).budget_estimate == 90_000_000.0


def test_legacy_dict_payload_without_the_flag_is_treated_conservatively(test_db):
    """출처 필드를 모르는 구 dict payload 는 승격돼도 저장 추정가격을 덮지 못한다.

    ``_promote_items`` 승격 경로의 하위 호환: 필드 부재는 ``None`` 이고, ``None`` 은
    파생/폴백과 같은 '미신고' 취급이라 빈 자리만 채운다.
    """
    notice = "LEGACY-DICT-1"
    _persist_one(test_db, _notice_item(notice))

    _persist_one(
        test_db,
        {
            "title": "구 payload(출처 필드 없음)",
            "notice_number": notice,
            "base_amount": _POLLUTED_BASE,
            "estimated_amount": 111_000_000.0,
        },
    )

    assert _project_of(test_db, notice).budget_estimate == 100_000_000.0
    assert _basis_of(test_db, notice) == BASIS_SUSPECT_RATIO
