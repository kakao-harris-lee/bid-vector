"""Forward-coverage tests for the scsbid award collector sweep.

Covers multi-category, paginated, date-window collection. All external HTTP is
mocked via ``app.services.koneps.http_client.requests.get`` (the existing scsbid
test pattern); no real KONEPS calls are made under ``ENVIRONMENT=test``.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import UTC, datetime

from app.core.config import settings
from app.core.time import KST
from app.models.models import CrawlJob, Project, TenderResult
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService


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
            str(item.get("notice_number")) for item in resp.get("items", [])
        ]
        return job

    monkeypatch.setattr(
        KonepsCollectorService, "persist_crawl_results", _fake_persist
    )
    return captured


def _backfill_response():
    return {
        "items": [
            {
                "notice_number": "R-EXIST",
                "title": "기존 공고",
                "base_amount": 100_000_000.0,
                "metadata": {"winning_amount": 95_000_000, "opening_status": "낙찰"},
            },
            {
                "notice_number": "R-NEW",
                "title": "신규 공고",
                "base_amount": 100_000_000.0,
                "metadata": {"winning_amount": 80_000_000, "opening_status": "낙찰"},
            },
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


class FakeOpenApiResponse:
    status_code = 200
    text = "{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _award_body(items, *, total_count, num_of_rows, page_no=1):
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL"},
            "body": {
                "items": {"item": items},
                "numOfRows": str(num_of_rows),
                "pageNo": str(page_no),
                "totalCount": str(total_count),
            },
        }
    }


def _award_item(notice_number, *, title="테스트 낙찰", amount="88,000,000"):
    return {
        "bidNtceNo": notice_number,
        "bidNtceOrd": "000",
        "bidClsfcNo": "0",
        "rbidNo": "0",
        "bidNtceNm": title,
        "prtcptCnum": "10",
        "bidwinnrNm": "낙찰사",
        "bidwinnrBizno": "1234567890",
        "sucsfbidAmt": amount,
        "sucsfbidRate": "88.0",
        "rlOpengDt": "2026-05-13 11:00:00",
        "dminsttNm": "서울특별시",
        "rgstDt": "2026-05-13 12:00:00",
        "fnlSucsfDate": "2026-05-13",
    }


def _empty_reserve_body():
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL"},
            "body": {
                "items": {"item": []},
                "numOfRows": "100",
                "pageNo": "1",
                "totalCount": "0",
            },
        }
    }


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

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
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
    notice_numbers = {item["notice_number"] for item in result["items"]}
    assert notice_numbers == {"C-1", "S-1"}


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

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
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

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
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
    assert result["items"][0]["metadata"]["reserve_prices"] == []


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

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    request = CrawlRequest(
        source="scsbid-openapi",
        categories=["construction", "service"],
        start_date="20260501",
        end_date="20260507",
        collect_reserve_detail=False,
        execution_mode="auto",
    )
    result = service._collect_scsbid_openapi_items(service._normalize_request(request))

    notice_numbers = [item["notice_number"] for item in result["items"]]
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

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

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

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
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
    assert result["items"][0]["notice_number"] == "LEG-1"
    # Reserve detail still fetched by default in the legacy path.
    assert any("PreparPcDetail" in entry["url"] for entry in captured)
