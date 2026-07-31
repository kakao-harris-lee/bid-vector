"""Tests for reuse of persisted reserve prices in the scsbid award sweep.

The scheduled scsbid sweep re-collects a rolling date window every run, so the
same opened awards keep reappearing. A reserve price (예정가) is a settled,
immutable value, so re-fetching it per notice on every run is pure HTTP waste
that grows the run past the RabbitMQ consumer ack timeout. These tests pin the
behaviour added on ``perf/scsbid-scheduled-reserve-detail``:

* awards that already have a persisted non-empty ``reserve_prices`` row skip the
  per-notice reserve-detail HTTP fetch and keep their stored value,
* brand-new awards still trigger the fetch,
* the "already persisted" set is loaded with one query (no N+1),
* an empty incoming reserve price never clobbers a stored non-empty one.

All external HTTP is mocked via ``app.services.koneps.http_client.requests.get``;
no real KONEPS calls are made under ``ENVIRONMENT=test``.
"""

from __future__ import annotations

import json

from app.core.config import settings
from app.models.models import HistoricalData
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService
from tests.support.koneps_openapi_fakes import (
    FakeOpenApiResponse,
    award_body as _award_body,
    award_item as _award_item,
    reserve_detail_body as _reserve_detail_body,
)


def _scsbid_request():
    return CrawlRequest(
        source="scsbid-openapi",
        categories=["construction"],
        start_date="20260501",
        end_date="20260507",
        collect_reserve_detail=True,
        execution_mode="auto",
    )


def _persist_award(db, notice_number, *, reserve_prices):
    db.add(
        HistoricalData(
            notice_number=notice_number,
            reserve_prices=json.dumps(reserve_prices),
            selected_numbers="[]",
        )
    )
    db.commit()


def test_persisted_reserve_skips_reserve_detail_fetch(test_db, monkeypatch):
    """An award already carrying a reserve price must not re-fetch reserve detail."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    _persist_award(test_db, "HAS-RESERVE", reserve_prices=[101000000, 102000000])

    reserve_calls: list[str] = []

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            reserve_calls.append(str(params.get("bidNtceNo")))
            return FakeOpenApiResponse(_reserve_detail_body())
        return FakeOpenApiResponse(
            _award_body([_award_item("HAS-RESERVE")], total_count=1, num_of_rows=100)
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()), db=test_db
    )

    assert reserve_calls == []  # no reserve-detail HTTP for the settled award
    assert result["metadata"]["reserve_detail_reused_count"] == 1
    assert result["metadata"]["reserve_detail_collected_count"] == 0
    # The item carries an empty list (not fetched) so persist preserves the row.
    assert result["items"][0].metadata["reserve_prices"] == []


def test_new_award_still_fetches_reserve_detail(test_db, monkeypatch):
    """An award with no persisted reserve price must trigger the fetch."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    # HAS-RESERVE already persisted; NEW-AWARD is fresh.
    _persist_award(test_db, "HAS-RESERVE", reserve_prices=[101000000])

    reserve_calls: list[str] = []

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            reserve_calls.append(str(params.get("bidNtceNo")))
            return FakeOpenApiResponse(_reserve_detail_body())
        return FakeOpenApiResponse(
            _award_body(
                [_award_item("HAS-RESERVE"), _award_item("NEW-AWARD")],
                total_count=2,
                num_of_rows=100,
            )
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()), db=test_db
    )

    assert reserve_calls == ["NEW-AWARD"]  # only the fresh award is fetched
    assert result["metadata"]["reserve_detail_reused_count"] == 1
    assert result["metadata"]["reserve_detail_collected_count"] == 1
    new_item = next(
        item for item in result["items"] if item.notice_number == "NEW-AWARD"
    )
    assert new_item.metadata["reserve_prices"] == [101000000.0, 102000000.0]


def test_persisted_reserve_set_loaded_with_single_query(test_db, monkeypatch):
    """The 'already persisted' set must be one query, not one per notice (N+1)."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    for idx in range(3):
        _persist_award(test_db, f"HAS-{idx}", reserve_prices=[100 + idx])

    preload_calls = {"count": 0}
    original = KonepsCollectorService._notice_numbers_with_persisted_reserve

    def spy(self, db):
        preload_calls["count"] += 1
        return original(self, db)

    monkeypatch.setattr(
        KonepsCollectorService, "_notice_numbers_with_persisted_reserve", spy
    )

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            return FakeOpenApiResponse(_reserve_detail_body())
        return FakeOpenApiResponse(
            _award_body(
                [_award_item(f"HAS-{i}") for i in range(3)],
                total_count=3,
                num_of_rows=100,
            )
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()), db=test_db
    )

    assert preload_calls["count"] == 1  # one pre-load, regardless of notice count
    assert result["metadata"]["reserve_detail_reused_count"] == 3


def test_reuse_disabled_falls_back_to_fetch(test_db, monkeypatch):
    """With the reuse flag off, persisted awards still re-fetch reserve detail."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_REUSE_PERSISTED_RESERVE_DETAIL", False)
    _persist_award(test_db, "HAS-RESERVE", reserve_prices=[101000000])

    reserve_calls: list[str] = []

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            reserve_calls.append(str(params.get("bidNtceNo")))
            return FakeOpenApiResponse(_reserve_detail_body())
        return FakeOpenApiResponse(
            _award_body([_award_item("HAS-RESERVE")], total_count=1, num_of_rows=100)
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()), db=test_db
    )

    assert reserve_calls == ["HAS-RESERVE"]  # flag off => fetch happens
    assert result["metadata"]["reserve_detail_reused_count"] == 0


def test_persist_empty_reserve_preserves_stored_value(test_db):
    """An incoming empty reserve_prices must not clobber a stored non-empty one."""
    service = KonepsCollectorService()
    test_db.add(
        HistoricalData(
            notice_number="KEEP-1",
            reserve_prices=json.dumps([111111111, 222222222]),
            selected_numbers=json.dumps([1, 2]),
        )
    )
    test_db.commit()

    crawl_job = service.create_crawl_job(
        test_db, CrawlRequest(source="scsbid-openapi", category="construction")
    )
    # A re-collected award whose reserve detail was skipped this run -> empty.
    response = {
        "items": [
            {
                "notice_number": "KEEP-1",
                "title": "재수집 낙찰",
                "base_amount": 100_000_000.0,
                "estimated_amount": 100_000_000.0,
                "metadata": {
                    "mode": "scsbid_openapi",
                    "opening_status": "낙찰",
                    "winning_amount": 90_000_000,
                    "winning_rate": 0.9,
                    "reserve_prices": [],
                    "selected_numbers": [],
                    "opening_announced_at": "2026-05-13 11:00:00",
                },
            }
        ],
        "metadata": {},
        "collected_count": 1,
        "job_status": "completed",
    }

    service.persist_crawl_results(
        test_db,
        crawl_job,
        CrawlRequest(source="scsbid-openapi", category="construction"),
        response,
    )

    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "KEEP-1")
        .one()
    )
    assert json.loads(stored.reserve_prices) == [111111111, 222222222]
    assert json.loads(stored.selected_numbers) == [1, 2]


def test_persist_new_reserve_overwrites_empty_stored_value(test_db):
    """A fresh non-empty reserve price must overwrite an empty/absent stored one."""
    service = KonepsCollectorService()
    test_db.add(
        HistoricalData(
            notice_number="FILL-1", reserve_prices="[]", selected_numbers="[]"
        )
    )
    test_db.commit()

    crawl_job = service.create_crawl_job(
        test_db, CrawlRequest(source="scsbid-openapi", category="construction")
    )
    response = {
        "items": [
            {
                "notice_number": "FILL-1",
                "title": "신규 예정가",
                "base_amount": 100_000_000.0,
                "estimated_amount": 100_000_000.0,
                "metadata": {
                    "mode": "scsbid_openapi",
                    "opening_status": "낙찰",
                    "winning_amount": 90_000_000,
                    "winning_rate": 0.9,
                    "reserve_prices": [333333333],
                    "selected_numbers": [3],
                    "opening_announced_at": "2026-05-13 11:00:00",
                },
            }
        ],
        "metadata": {},
        "collected_count": 1,
        "job_status": "completed",
    }

    service.persist_crawl_results(
        test_db,
        crawl_job,
        CrawlRequest(source="scsbid-openapi", category="construction"),
        response,
    )

    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "FILL-1")
        .one()
    )
    assert json.loads(stored.reserve_prices) == [333333333]
    assert json.loads(stored.selected_numbers) == [3]
