"""Forward-coverage tests for the scsbid award collector sweep.

Covers multi-category, paginated, date-window collection. All external HTTP is
replaced by injecting the collector's ``http_get`` seam
(``KonepsCollectorService(http_get=...)``); the one API-driven test overrides the
crawl route's collector provider (``get_koneps_collector``) with an injected
collector. No real KONEPS calls are made under ``ENVIRONMENT=test``.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime

from app.api.providers import get_koneps_collector
from app.core.config import settings
from app.core.time import KST
from app.main import app
from app.models.models import CrawlJob, Project, TenderResult
from app.schemas.koneps_items import KonepsCollectedItem
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService
from tests.support.koneps_openapi_fakes import (
    FakeOpenApiResponse,
    award_body as _award_body,
    award_item as _award_item,
    empty_reserve_body as _empty_reserve_body,
)


def _load_backfill_module():
    """Dynamically load scripts/backfill_scsbid_awards.py (not a package)."""
    repo = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "backfill_scsbid_awards", repo / "scripts" / "backfill_scsbid_awards.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve string annotations
    # (the script uses ``from __future__ import annotations``).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch_collector_for_backfill(monkeypatch, response):
    """Stub collect/persist so run_backfill is exercised without HTTP/DB writes.

    Returns a dict whose ``items`` key is filled with the notice numbers that
    actually reached ``persist_crawl_results`` (i.e. survived matched-only).
    """
    captured: dict = {}
    monkeypatch.setattr(
        KonepsCollectorService,
        "collect_notices",
        lambda self, req, db=None: response,
    )
    monkeypatch.setattr(
        KonepsCollectorService,
        "create_crawl_job",
        lambda self, db, req: CrawlJob(source="scsbid-openapi"),
    )

    def _fake_persist(self, db, job, req, resp):
        captured["items"] = [
            str(item.notice_number) for item in resp.get("items", [])
        ]
        return job

    monkeypatch.setattr(
        KonepsCollectorService, "persist_crawl_results", _fake_persist
    )
    return captured


def _backfill_response():
    # ``collect_notices`` 는 수집 DTO 를 싣는다(방어적 DTO Phase 3) — stub 도 같은 계약.
    return {
        "items": [
            KonepsCollectedItem(
                notice_number="R-EXIST",
                title="기존 공고",
                base_amount=100_000_000.0,
                metadata={"winning_amount": 95_000_000, "opening_status": "낙찰"},
            ),
            KonepsCollectedItem(
                notice_number="R-NEW",
                title="신규 공고",
                base_amount=100_000_000.0,
                metadata={"winning_amount": 80_000_000, "opening_status": "낙찰"},
            ),
        ],
        "metadata": {"scsbid_api_call_count": 3},
        "collected_count": 2,
        "job_status": "completed",
    }


def test_backfill_matched_only_persists_only_existing_projects(test_db, monkeypatch):
    """matched_only must drop awards whose notice has no existing Project."""
    mod = _load_backfill_module()
    test_db.add(
        Project(
            title="기존 공고",
            description="-",
            requirements="-",
            budget_estimate=100_000_000.0,
            category="construction",
            notice_number="R-EXIST",
        )
    )
    test_db.flush()
    captured = _patch_collector_for_backfill(monkeypatch, _backfill_response())

    stats = mod.run_backfill(
        test_db,
        start="20260526",
        end="20260530",
        categories=["construction"],
        page_size=100,
        max_pages=5,
        collect_reserve_detail=False,
        execution_mode="mock",
        persist=True,
        matched_only=True,
    )

    assert captured["items"] == ["R-EXIST"]
    assert stats.total_persisted_items == 1
    assert stats.total_matched_existing == 1
    assert stats.total_new_projects == 1


def test_backfill_persist_all_keeps_unmatched_awards(test_db, monkeypatch):
    """Without matched_only the full feed (incl. new-corpus) is persisted."""
    mod = _load_backfill_module()
    test_db.add(
        Project(
            title="기존 공고",
            description="-",
            requirements="-",
            budget_estimate=100_000_000.0,
            category="construction",
            notice_number="R-EXIST",
        )
    )
    test_db.flush()
    captured = _patch_collector_for_backfill(monkeypatch, _backfill_response())

    stats = mod.run_backfill(
        test_db,
        start="20260526",
        end="20260530",
        categories=["construction"],
        page_size=100,
        max_pages=5,
        collect_reserve_detail=False,
        execution_mode="mock",
        persist=True,
        matched_only=False,
    )

    assert sorted(captured["items"]) == ["R-EXIST", "R-NEW"]
    assert stats.total_persisted_items == 2


def test_scsbid_sweep_visits_each_category_operation(monkeypatch):
    """A multi-category request hits each category's award operation."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    captured = []

    def fake_get(url, params, timeout):
        captured.append({"url": url, "params": params})
        if "getScsbidListSttusCnstwk" in url:
            return FakeOpenApiResponse(
                _award_body([_award_item("C-1")], total_count=1, num_of_rows=100)
            )
        if "getScsbidListSttusServc" in url:
            return FakeOpenApiResponse(
                _award_body([_award_item("S-1")], total_count=1, num_of_rows=100)
            )
        if "PreparPcDetail" in url:
            return FakeOpenApiResponse(_empty_reserve_body())
        raise AssertionError(f"unexpected URL: {url}")

    service = KonepsCollectorService(http_get=fake_get)
    request = CrawlRequest(
        source="scsbid-openapi",
        categories=["construction", "service"],
        start_date="20260501",
        end_date="20260507",
        execution_mode="auto",
    )
    result = service._collect_scsbid_openapi_items(service._normalize_request(request))

    operations = {part for entry in captured for part in [entry["url"]]}
    assert any("getScsbidListSttusCnstwk" in url for url in operations)
    assert any("getScsbidListSttusServc" in url for url in operations)
    assert result["metadata"]["scsbid_categories"] == ["construction", "service"]
    notice_numbers = {item.notice_number for item in result["items"]}
    assert notice_numbers == {"C-1", "S-1"}


def test_scsbid_sweep_honors_global_max_items_and_accounts_for_cap(monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0
    )

    def fake_get(url, params, timeout):
        del params, timeout
        if "PreparPcDetail" in url:
            return FakeOpenApiResponse(_empty_reserve_body())
        items = [_award_item(f"CAP-{index}") for index in range(5)]
        return FakeOpenApiResponse(
            _award_body(items, total_count=5, num_of_rows=5)
        )

    service = KonepsCollectorService(http_get=fake_get)
    request = CrawlRequest(
        source="scsbid-openapi",
        categories=["construction"],
        start_date="20260501",
        end_date="20260507",
        execution_mode="auto",
        page_size=5,
        max_items=3,
    )
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(request)
    )

    assert [item.notice_number for item in result["items"]] == [
        "CAP-0",
        "CAP-1",
        "CAP-2",
    ]
    assert result["metadata"]["received_count"] == 5
    assert result["metadata"]["normalized_count"] == 3
    assert result["metadata"]["dropped_count"] == 2
    assert result["metadata"]["drop_reasons"]["max_items_cap"] == 2
    assert result["metadata"]["truncated"] is True


def test_scsbid_sweep_paginates_until_total_count(monkeypatch):
    """When totalCount exceeds page_size, additional pages are fetched."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    page_calls = []

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            return FakeOpenApiResponse(_empty_reserve_body())
        page_no = int(params["pageNo"])
        page_calls.append(page_no)
        if page_no == 1:
            items = [_award_item(f"P1-{i}") for i in range(2)]
        elif page_no == 2:
            items = [_award_item(f"P2-{i}") for i in range(2)]
        else:
            raise AssertionError(f"unexpected pageNo={page_no}")
        return FakeOpenApiResponse(
            _award_body(items, total_count=4, num_of_rows=2, page_no=page_no)
        )

    service = KonepsCollectorService(http_get=fake_get)
    request = CrawlRequest(
        source="scsbid-openapi",
        categories=["construction"],
        start_date="20260501",
        end_date="20260507",
        page_size=2,
        max_pages=10,
        collect_reserve_detail=False,
        execution_mode="auto",
    )
    result = service._collect_scsbid_openapi_items(service._normalize_request(request))

    assert page_calls == [1, 2]  # stops once page_no*page_size >= totalCount
    assert len(result["items"]) == 4
    assert result["metadata"]["scsbid_category_breakdown"][0]["pages_fetched"] == 2


def test_scsbid_date_window_resolution_three_paths(monkeypatch):
    """start/end, lookback_days, and target_date each build the expected tokens."""
    service = KonepsCollectorService()

    explicit = service._scsbid_date_window(
        CrawlRequest(
            source="scsbid-openapi", start_date="20260501", end_date="20260507"
        )
    )
    assert explicit == ("202605010000", "202605072359")

    # lookback anchors on the KST calendar day (KONEPS opening dates are KST).
    # The window logic lives in ``app.services.koneps.scsbid`` (extracted from
    # the collector); patch its clock, not the collector's.
    fixed_kst = datetime(2026, 6, 8, 9, 0, tzinfo=KST)
    monkeypatch.setattr("app.services.koneps.scsbid.kst_now", lambda: fixed_kst)
    lookback = service._scsbid_date_window(
        CrawlRequest(source="scsbid-openapi", lookback_days=3)
    )
    assert lookback == ("202606050000", "202606082359")

    single = service._scsbid_date_window(
        CrawlRequest(source="scsbid-openapi", target_date="2026-05-13")
    )
    assert single == ("202605130000", "202605132359")


def test_scsbid_reserve_detail_skipped_when_disabled(monkeypatch):
    """collect_reserve_detail=False must not call the reserve-detail operation."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    seen_urls = []

    def fake_get(url, params, timeout):
        seen_urls.append(url)
        if "PreparPcDetail" in url:
            raise AssertionError("reserve-detail must not be called when disabled")
        return FakeOpenApiResponse(
            _award_body([_award_item("C-1")], total_count=1, num_of_rows=100)
        )

    service = KonepsCollectorService(http_get=fake_get)
    request = CrawlRequest(
        source="scsbid-openapi",
        categories=["construction"],
        start_date="20260501",
        end_date="20260507",
        collect_reserve_detail=False,
        execution_mode="auto",
    )
    result = service._collect_scsbid_openapi_items(service._normalize_request(request))

    assert all("PreparPcDetail" not in url for url in seen_urls)
    assert result["metadata"]["reserve_detail_enabled"] is False
    assert result["items"][0].metadata["reserve_prices"] == []


def test_scsbid_sweep_dedupes_notice_numbers_across_categories(monkeypatch):
    """A notice appearing under multiple categories is persisted only once."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            return FakeOpenApiResponse(_empty_reserve_body())
        # Same notice number returned by both category feeds.
        return FakeOpenApiResponse(
            _award_body([_award_item("DUP-1")], total_count=1, num_of_rows=100)
        )

    service = KonepsCollectorService(http_get=fake_get)
    request = CrawlRequest(
        source="scsbid-openapi",
        categories=["construction", "service"],
        start_date="20260501",
        end_date="20260507",
        collect_reserve_detail=False,
        execution_mode="auto",
    )
    result = service._collect_scsbid_openapi_items(service._normalize_request(request))

    notice_numbers = [item.notice_number for item in result["items"]]
    assert notice_numbers == ["DUP-1"]


def test_scsbid_award_attaches_to_existing_forward_project(
    client, test_db, monkeypatch
):
    """An award matching an existing forward project attaches winning_amount.

    No new project is created; the existing project gets a TenderResult with
    winning_amount > 0 so the forward settlement sweep can settle it.
    """
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)

    existing = Project(
        notice_number="FWD-100",
        title="기존 forward 공고",
        category="construction",
        budget_estimate=0.0,
        status="open",
    )
    test_db.add(existing)
    test_db.commit()
    test_db.refresh(existing)
    existing_id = existing.id
    project_count_before = test_db.query(Project).count()

    def fake_get(url, params, timeout):
        if "getScsbidListSttusCnstwk" in url:
            return FakeOpenApiResponse(
                _award_body(
                    [_award_item("FWD-100", amount="90,000,000")],
                    total_count=1,
                    num_of_rows=100,
                )
            )
        raise AssertionError(f"unexpected URL: {url}")

    # 수집기는 크롤 라우트의 provider 로 주입되므로, 전역 transport patch 대신 획득 seam 이
    # 주입된 수집기를 ``dependency_overrides`` 로 넘긴다(§4.7-3).
    app.dependency_overrides[get_koneps_collector] = lambda: KonepsCollectorService(
        http_get=fake_get
    )
    try:
        response = client.post(
            "/api/v1/operations/crawl",
            json={
                "source": "koneps-scsbid",
                "categories": ["construction"],
                "start_date": "20260501",
                "end_date": "20260513",
                "collect_reserve_detail": False,
            },
        )
        assert response.status_code == 200

        test_db.expire_all()
        assert test_db.query(Project).count() == project_count_before  # no new project
        tender_result = (
            test_db.query(TenderResult).filter(TenderResult.project_id == existing_id).one()
        )
        assert tender_result.winning_amount == 90000000.0
        assert tender_result.result_status == "낙찰"
    finally:
        app.dependency_overrides.pop(get_koneps_collector, None)


def test_scsbid_legacy_single_day_single_category_regression(monkeypatch):
    """Without the new fields, the single-day single-category path is preserved."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    captured = []

    def fake_get(url, params, timeout):
        captured.append({"url": url, "params": params})
        if "getScsbidListSttusCnstwk" in url:
            return FakeOpenApiResponse(
                _award_body([_award_item("LEG-1")], total_count=1, num_of_rows=100)
            )
        if "PreparPcDetail" in url:
            return FakeOpenApiResponse(_empty_reserve_body())
        raise AssertionError(f"unexpected URL: {url}")

    service = KonepsCollectorService(http_get=fake_get)
    request = CrawlRequest(
        source="koneps-scsbid",
        category="construction",
        target_date="2026-05-13",
        execution_mode="auto",
    )
    result = service._collect_scsbid_openapi_items(service._normalize_request(request))

    assert result["metadata"]["openapi_operation"] == "getScsbidListSttusCnstwk"
    assert captured[0]["params"]["inqryBgnDt"] == "202605130000"
    assert captured[0]["params"]["inqryEndDt"] == "202605132359"
    assert result["items"][0].notice_number == "LEG-1"
    # Reserve detail still fetched by default in the legacy path.
    assert any("PreparPcDetail" in entry["url"] for entry in captured)
