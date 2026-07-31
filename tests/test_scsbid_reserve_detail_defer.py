"""Tests for deferring the scsbid reserve-detail HTTP fetch out of the crawl task.

Background (fix/scsbid-reserve-detail-defer)
--------------------------------------------
The 6-hourly scsbid (open-bid result) sweep ran one reserve-detail HTTP call —
each preceded by a throttle sleep — *inline* per non-settled award. Across 3
categories x up to 30 pages x 100 rows that is up to ~9,000 sequential calls
whose sleeps alone exceeded the Celery hard time limit (1800s). The SIGKILLed
task was redelivered (task_acks_late) and, because persist only ran after the
*whole* sweep completed, every run persisted 0 rows — so the same notices were
re-fetched forever.

This mirrors the embedding defer+chunk pattern (PR#82) onto reserve-detail:

1. ``_collect_scsbid_openapi_items(defer_reserve_detail=True)`` skips the inline
   ``_fetch_scsbid_reserve_detail`` call (and its sleep) and instead records the
   not-yet-settled notices as ``(notice_number, category)`` in
   ``metadata['deferred_reserve_detail_notices']``.
2. ``defer_reserve_detail=False`` (synchronous callers) keeps the inline fetch.
3. ``backfill_scsbid_reserve_detail`` fetches those notices async, persisting the
   reserve price onto ``HistoricalData`` — idempotent (skips already-settled),
   and one notice failing does not abort the chunk.
4. ``_enqueue_deferred_reserve_detail_backfill`` enqueues a single serial-chain
   root task; the task processes one chunk and self-chains the remainder.

Throttle follow-up (fix/scsbid-reserve-detail-throttle)
------------------------------------------------------
ScsbidInfoService rate-limits *concurrent* reserve-detail calls (HTTP 429), so the
backfill is serialized: the enqueue helper hands one root task the full notice list
and ``backfill_scsbid_reserve_detail`` processes one chunk then enqueues a single
continuation for the remainder. A closing/opening age-gate
(``KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS``) keeps just-opened notices
out of the deferred set so their unsettled reserve is not re-fetched every sweep.

All external HTTP is mocked; no real KONEPS calls under ``ENVIRONMENT=test``.
"""

from __future__ import annotations

import json

from app.core.config import settings
from app.models.models import HistoricalData
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService


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


def _reserve_detail_body():
    """A reserve-detail response carrying two reserve-price rows."""
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL"},
            "body": {
                "items": {
                    "item": [
                        {
                            "compnoRsrvtnPrceSno": "1",
                            "bsisPlnprc": "101000000",
                            "plnprc": "100000000",
                            "bssamt": "100000000",
                            "drwtYn": "Y",
                        },
                        {
                            "compnoRsrvtnPrceSno": "2",
                            "bsisPlnprc": "102000000",
                            "plnprc": "100000000",
                            "bssamt": "100000000",
                            "drwtYn": "N",
                        },
                    ]
                },
                "numOfRows": "100",
                "pageNo": "1",
                "totalCount": "2",
            },
        }
    }


def _scsbid_request():
    return CrawlRequest(
        source="scsbid-openapi",
        categories=["construction"],
        start_date="20260501",
        end_date="20260507",
        collect_reserve_detail=True,
        execution_mode="auto",
    )


# ---------------------------------------------------------------------------
# 1. defer_reserve_detail=True: no inline fetch, notices surfaced for backfill.
# ---------------------------------------------------------------------------
def test_defer_true_skips_inline_fetch_and_surfaces_notices(test_db, monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)

    fetch_calls: list[str] = []

    def _spy_fetch(self, raw_item, *, category, service_key):
        fetch_calls.append(str(raw_item.get("bidNtceNo")))
        return {"reserve_prices": [1], "selected_numbers": []}

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _spy_fetch
    )

    def fake_get(url, params, timeout):
        return FakeOpenApiResponse(
            _award_body(
                [_award_item("NEW-A"), _award_item("NEW-B")],
                total_count=2,
                num_of_rows=100,
            )
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=True,
    )

    # The inline reserve-detail fetch was never called.
    assert fetch_calls == []

    deferred = result["metadata"]["deferred_reserve_detail_notices"]
    assert {(d["notice_number"], d["category"]) for d in deferred} == {
        ("NEW-A", "construction"),
        ("NEW-B", "construction"),
    }
    assert result["metadata"]["reserve_detail_deferred_count"] == 2
    assert result["metadata"]["reserve_detail_collected_count"] == 0
    # Items still build (with empty reserve detail) so the award row persists.
    assert {item["notice_number"] for item in result["items"]} == {"NEW-A", "NEW-B"}
    assert result["items"][0]["metadata"]["reserve_prices"] == []


# ---------------------------------------------------------------------------
# 1b. closing_at age-gate: just-opened notices are NOT deferred (rate-limit
#     backoff), so they are not re-fetched every 6h sweep before they settle.
# ---------------------------------------------------------------------------
def _award_item_opened_at(notice_number, opened_at):
    item = _award_item(notice_number)
    item["rlOpengDt"] = opened_at
    item["fnlSucsfDate"] = ""
    item["rgstDt"] = ""
    return item


def test_age_gate_excludes_recently_opened_notice(test_db, monkeypatch):
    from datetime import timedelta

    from app.core import time as time_module

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS", 24
    )

    # coerce_datetime treats a naive ISO string as UTC, so build the opening
    # timestamps in UTC wall-clock to control their instant deterministically.
    # The gate compares the UTC-aware opening instant against ``kst_now()`` minus
    # the threshold; aware-datetime comparison is by instant, so the frame the
    # cutoff is expressed in does not change the boundary.
    now_utc = time_module.utc_now()
    recent = (now_utc - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    future = (now_utc + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    aged = (now_utc - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

    def _spy_fetch(self, raw_item, *, category, service_key):
        raise AssertionError("must not fetch when deferring")

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _spy_fetch
    )

    def fake_get(url, params, timeout):
        return FakeOpenApiResponse(
            _award_body(
                [
                    _award_item_opened_at("RECENT", recent),
                    _award_item_opened_at("FUTURE", future),
                    _award_item_opened_at("AGED", aged),
                ],
                total_count=3,
                num_of_rows=100,
            )
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=True,
    )

    deferred = result["metadata"]["deferred_reserve_detail_notices"]
    # Only the aged notice (opened > 24h ago) is deferred; recent/future ones are
    # backed off so they are not re-fetched before they have a chance to settle.
    assert [d["notice_number"] for d in deferred] == ["AGED"]
    assert result["metadata"]["reserve_detail_deferred_count"] == 1
    assert result["metadata"]["reserve_detail_backoff_skipped_count"] == 2
    # The award rows still build for all three (the gate only defers the fetch).
    assert {item["notice_number"] for item in result["items"]} == {
        "RECENT",
        "FUTURE",
        "AGED",
    }


def test_age_gate_defers_notice_with_unknown_opening(test_db, monkeypatch):
    """A notice with no opening datetime cannot be gated, so it is still deferred."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS", 24
    )

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: (_ for _ in ()).throw(
            AssertionError("must not fetch when deferring")
        ),
    )

    def fake_get(url, params, timeout):
        item = _award_item("NO-DATE")
        item["rlOpengDt"] = ""
        item["fnlSucsfDate"] = ""
        item["rgstDt"] = ""
        return FakeOpenApiResponse(_award_body([item], total_count=1, num_of_rows=100))

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=True,
    )

    deferred = result["metadata"]["deferred_reserve_detail_notices"]
    assert [d["notice_number"] for d in deferred] == ["NO-DATE"]
    assert result["metadata"]["reserve_detail_backoff_skipped_count"] == 0


def test_age_gate_disabled_when_threshold_zero(test_db, monkeypatch):
    """MIN_SETTLE_AGE_HOURS=0 disables the gate: even recent notices are deferred."""
    from datetime import timedelta

    from app.core import time as time_module

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS", 0
    )

    recent = (time_module.utc_now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: (_ for _ in ()).throw(
            AssertionError("must not fetch when deferring")
        ),
    )

    def fake_get(url, params, timeout):
        return FakeOpenApiResponse(
            _award_body(
                [_award_item_opened_at("RECENT", recent)],
                total_count=1,
                num_of_rows=100,
            )
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=True,
    )

    deferred = result["metadata"]["deferred_reserve_detail_notices"]
    assert [d["notice_number"] for d in deferred] == ["RECENT"]
    assert result["metadata"]["reserve_detail_backoff_skipped_count"] == 0


def test_defer_true_omits_already_settled_notice(test_db, monkeypatch):
    """A notice that already has a persisted reserve price is neither fetched nor deferred."""
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    test_db.add(
        HistoricalData(
            notice_number="HAS-RESERVE",
            reserve_prices=json.dumps([101000000]),
            selected_numbers="[]",
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: (_ for _ in ()).throw(
            AssertionError("must not fetch when deferring")
        ),
    )

    def fake_get(url, params, timeout):
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
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=True,
    )

    deferred = result["metadata"]["deferred_reserve_detail_notices"]
    assert [d["notice_number"] for d in deferred] == ["NEW-AWARD"]
    assert result["metadata"]["reserve_detail_reused_count"] == 1
    assert result["metadata"]["reserve_detail_deferred_count"] == 1


# ---------------------------------------------------------------------------
# 2. defer_reserve_detail=False: inline fetch still happens (regression guard).
# ---------------------------------------------------------------------------
def test_defer_false_still_fetches_inline(test_db, monkeypatch):
    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)

    reserve_calls: list[str] = []

    def fake_get(url, params, timeout):
        if "PreparPcDetail" in url:
            reserve_calls.append(str(params.get("bidNtceNo")))
            return FakeOpenApiResponse(_reserve_detail_body())
        return FakeOpenApiResponse(
            _award_body([_award_item("NEW-AWARD")], total_count=1, num_of_rows=100)
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=False,
    )

    assert reserve_calls == ["NEW-AWARD"]  # inline fetch preserved
    assert result["metadata"]["reserve_detail_collected_count"] == 1
    assert result["metadata"]["reserve_detail_deferred_count"] == 0
    assert result["metadata"]["deferred_reserve_detail_notices"] == []


# ---------------------------------------------------------------------------
# 3. backfill task: fetch+persist, idempotent skip, per-notice error isolation.
# ---------------------------------------------------------------------------
def test_backfill_fetches_and_persists_reserve_prices(test_db, monkeypatch):
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    # Keep the test session open across the task body.
    monkeypatch.setattr(test_db, "close", lambda: None)

    # A row exists but with no settled reserve yet (the deferral case).
    test_db.add(
        HistoricalData(notice_number="N-1", reserve_prices="[]", selected_numbers="[]")
    )
    test_db.commit()

    def _fake_fetch(self, raw_item, *, category, service_key):
        assert raw_item == {"bidNtceNo": "N-1"}
        assert category == "construction"
        return {"reserve_prices": [101000000.0, 102000000.0], "selected_numbers": [1]}

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _fake_fetch
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "N-1", "category": "construction"}]
    )

    assert out == {
        "requested": 1,
        "processed": 1,
        "fetched": 1,
        "skipped_existing": 0,
        "not_settled": 0,
        "errors": 0,
        "error_types": {},
        "error_samples": [],
        "remaining": 0,
        "continued": False,
    }
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "N-1")
        .one()
    )
    assert json.loads(stored.reserve_prices) == [101000000.0, 102000000.0]
    assert json.loads(stored.selected_numbers) == [1]


def test_backfill_skips_already_settled_notice(test_db, monkeypatch):
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    test_db.add(
        HistoricalData(
            notice_number="DONE-1",
            reserve_prices=json.dumps([999]),
            selected_numbers="[]",
        )
    )
    test_db.commit()

    def _must_not_fetch(self, raw_item, *, category, service_key):
        raise AssertionError("must not fetch an already-settled notice")

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _must_not_fetch
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "DONE-1", "category": "construction"}]
    )

    assert out["skipped_existing"] == 1
    assert out["fetched"] == 0
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "DONE-1")
        .one()
    )
    assert json.loads(stored.reserve_prices) == [999]  # untouched


def test_backfill_one_failure_does_not_abort_chunk(test_db, monkeypatch):
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    for nn in ("OK-1", "BOOM", "OK-2"):
        test_db.add(
            HistoricalData(notice_number=nn, reserve_prices="[]", selected_numbers="[]")
        )
    test_db.commit()

    def _fetch(self, raw_item, *, category, service_key):
        notice_number = raw_item["bidNtceNo"]
        if notice_number == "BOOM":
            raise ValueError("KONEPS HTTP 500")
        return {"reserve_prices": [123], "selected_numbers": []}

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _fetch
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[
            {"notice_number": "OK-1", "category": "construction"},
            {"notice_number": "BOOM", "category": "construction"},
            {"notice_number": "OK-2", "category": "construction"},
        ]
    )

    assert out["fetched"] == 2
    assert out["errors"] == 1
    assert out["requested"] == 3
    # Diagnostics: the previously-swallowed exception is now surfaced so the
    # cause of a failing cycle is visible in the result dict / summary log.
    assert out["error_types"] == {"ValueError": 1}
    assert "ValueError: KONEPS HTTP 500" in out["error_samples"]
    test_db.expire_all()
    for nn in ("OK-1", "OK-2"):
        stored = (
            test_db.query(HistoricalData)
            .filter(HistoricalData.notice_number == nn)
            .one()
        )
        assert json.loads(stored.reserve_prices) == [123]
    boom = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "BOOM")
        .one()
    )
    assert json.loads(boom.reserve_prices) == []  # failed fetch left it empty


def test_backfill_empty_reserve_counts_as_not_settled(test_db, monkeypatch):
    """A successful fetch with no reserve yet is benign (not_settled), not an error.

    Distinguishes the "notice closed but not opened" case from real fetch
    failures so a cycle of unsettled notices does not look like errors.
    """
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    test_db.add(
        HistoricalData(
            notice_number="UNSETTLED", reserve_prices="[]", selected_numbers="[]"
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [],
            "selected_numbers": [],
        },
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "UNSETTLED", "category": "service"}]
    )

    assert out["not_settled"] == 1
    assert out["fetched"] == 0
    assert out["errors"] == 0
    assert out["error_types"] == {}
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "UNSETTLED")
        .one()
    )
    assert json.loads(stored.reserve_prices) == []  # untouched, retried later


# ---------------------------------------------------------------------------
# 4. enqueue helper: a single serial-chain root task (throttle fix).
#
# Background (fix/scsbid-reserve-detail-throttle)
# -----------------------------------------------
# ScsbidInfoService imposes a *rate* limit (HTTP 429 "API token quota exceeded"),
# not a daily quota. The old helper split the deferred notices into N chunks and
# enqueued them all at once, so several ops workers burst the reserve-detail API
# concurrently and tripped the rate limit (large bursts saw mass 429s; a small
# 9-chunk burst passed with 0 errors). The fix enqueues a *single* root task with
# the full notice list; ``backfill_scsbid_reserve_detail`` processes one chunk and
# self-chains a continuation for the remainder, so the API is hit strictly serially.
# ---------------------------------------------------------------------------
def test_enqueue_creates_single_root_task(monkeypatch):
    from app.tasks import jobs

    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE", 200
    )

    captured: list[dict] = []

    def _fake_apply_async(*, kwargs, queue):
        captured.append({"kwargs": kwargs, "queue": queue})
        return object()

    monkeypatch.setattr(
        jobs.backfill_scsbid_reserve_detail, "apply_async", _fake_apply_async
    )

    notices = [
        {"notice_number": f"N-{i}", "category": "construction"} for i in range(450)
    ]
    enqueued = jobs._enqueue_deferred_reserve_detail_backfill(notices)

    # A single root task carries the *entire* cleaned notice list (no parallel
    # chunk burst). Chunking now happens serially inside the task via self-chaining.
    assert enqueued == 1
    assert len(captured) == 1
    assert captured[0]["queue"] == settings.CELERY_OPS_QUEUE
    assert len(captured[0]["kwargs"]["notices"]) == 450
    assert [n["notice_number"] for n in captured[0]["kwargs"]["notices"]] == [
        n["notice_number"] for n in notices
    ]


def test_enqueue_dedupes_into_single_task(monkeypatch):
    from app.tasks import jobs

    captured: list[dict] = []

    monkeypatch.setattr(
        jobs.backfill_scsbid_reserve_detail,
        "apply_async",
        lambda *, kwargs, queue: captured.append({"kwargs": kwargs, "queue": queue}),
    )

    notices = [
        {"notice_number": "DUP", "category": "construction"},
        {"notice_number": "DUP", "category": "construction"},
        {"notice_number": "OTHER", "category": "service"},
    ]
    assert jobs._enqueue_deferred_reserve_detail_backfill(notices) == 1
    assert [n["notice_number"] for n in captured[0]["kwargs"]["notices"]] == [
        "DUP",
        "OTHER",
    ]


def test_enqueue_empty_is_noop(monkeypatch):
    from app.tasks import jobs

    called = {"count": 0}

    def _fake_apply_async(*, kwargs, queue):
        called["count"] += 1
        return object()

    monkeypatch.setattr(
        jobs.backfill_scsbid_reserve_detail, "apply_async", _fake_apply_async
    )

    assert jobs._enqueue_deferred_reserve_detail_backfill([]) == 0
    assert called["count"] == 0


# ---------------------------------------------------------------------------
# 5. backfill self-chaining: one chunk processed per run, continuation enqueued.
# ---------------------------------------------------------------------------
def test_backfill_processes_one_chunk_and_chains_remainder(test_db, monkeypatch):
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr(settings, "KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE", 2)
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    for i in range(5):
        test_db.add(
            HistoricalData(
                notice_number=f"C-{i}", reserve_prices="[]", selected_numbers="[]"
            )
        )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [111],
            "selected_numbers": [],
        },
    )

    continuations: list[dict] = []
    monkeypatch.setattr(
        jobs.backfill_scsbid_reserve_detail,
        "apply_async",
        lambda *, kwargs, queue: continuations.append(
            {"kwargs": kwargs, "queue": queue}
        ),
    )

    notices = [
        {"notice_number": f"C-{i}", "category": "construction"} for i in range(5)
    ]
    out = jobs.backfill_scsbid_reserve_detail.run(notices=notices)

    # Only the first chunk_size notices were processed this run.
    assert out["processed"] == 2
    assert out["fetched"] == 2
    assert out["requested"] == 2
    assert out["remaining"] == 3
    assert out["continued"] is True

    # Exactly one continuation task carrying the remaining notices.
    assert len(continuations) == 1
    assert continuations[0]["queue"] == settings.CELERY_OPS_QUEUE
    assert [n["notice_number"] for n in continuations[0]["kwargs"]["notices"]] == [
        "C-2",
        "C-3",
        "C-4",
    ]

    # Only the first two rows were settled this run.
    test_db.expire_all()
    settled = {
        row.notice_number
        for row in test_db.query(HistoricalData).all()
        if json.loads(row.reserve_prices)
    }
    assert settled == {"C-0", "C-1"}


def test_backfill_last_chunk_does_not_chain(test_db, monkeypatch):
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE", 5)
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    test_db.add(
        HistoricalData(notice_number="ONLY", reserve_prices="[]", selected_numbers="[]")
    )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [222],
            "selected_numbers": [],
        },
    )

    continuations: list[dict] = []
    monkeypatch.setattr(
        jobs.backfill_scsbid_reserve_detail,
        "apply_async",
        lambda *, kwargs, queue: continuations.append(kwargs),
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "ONLY", "category": "construction"}]
    )

    assert out["processed"] == 1
    assert out["remaining"] == 0
    assert out["continued"] is False
    assert continuations == []  # nothing left to chain


def test_backfill_missing_service_key_surfaces_errors_without_fetch(
    test_db, monkeypatch
):
    """No ``KONEPS_OPENAPI_SERVICE_KEY`` -> surface every notice as an error
    without any HTTP fetch and WITHOUT chaining a continuation (the remainder
    would fail identically; the next 6h collect re-defers once a key exists).

    Pins the branch whose ``service_key`` resolution the config-prologue
    extraction relocates into ``_plan_reserve_detail_backfill``.
    """
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE", 5)
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    def _must_not_fetch(self, raw_item, *, category, service_key):
        raise AssertionError("must not fetch without a service key")

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _must_not_fetch
    )

    continuations: list[dict] = []
    monkeypatch.setattr(
        jobs.backfill_scsbid_reserve_detail,
        "apply_async",
        lambda *, kwargs, queue: continuations.append(kwargs),
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[
            {"notice_number": "K-1", "category": "construction"},
            {"notice_number": "K-2", "category": "construction"},
        ]
    )

    assert out == {
        "requested": 2,
        "processed": 0,
        "fetched": 0,
        "skipped_existing": 0,
        "errors": 2,
        "error": "missing_service_key",
        "remaining": 0,
        "continued": False,
    }
    assert continuations == []  # no continuation chained without a key


def test_backfill_soft_time_limit_commits_progress_without_chaining(
    test_db, monkeypatch
):
    """``SoftTimeLimitExceeded`` mid-chunk: commit the work done so far, return a
    graceful soft-limit summary, and do NOT enqueue a continuation even though a
    remainder exists (the next 6h collect self-heals) -- so a time-limited run
    never orphans the serial chain. Pins the soft-limit early-return branch.
    """
    from celery.exceptions import SoftTimeLimitExceeded

    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr(settings, "KONEPS_SCSBID_RESERVE_DETAIL_BACKFILL_CHUNK_SIZE", 2)
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    for nn in ("S-0", "S-1", "S-2"):
        test_db.add(
            HistoricalData(notice_number=nn, reserve_prices="[]", selected_numbers="[]")
        )
    test_db.commit()

    def _fetch(self, raw_item, *, category, service_key):
        if raw_item["bidNtceNo"] == "S-0":
            return {"reserve_prices": [123], "selected_numbers": []}
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _fetch
    )

    continuations: list[dict] = []
    monkeypatch.setattr(
        jobs.backfill_scsbid_reserve_detail,
        "apply_async",
        lambda *, kwargs, queue: continuations.append(kwargs),
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[
            {"notice_number": nn, "category": "construction"}
            for nn in ("S-0", "S-1", "S-2")
        ]
    )

    # Chunk_size=2 -> {S-0, S-1} processed, S-2 is the remainder. The soft limit
    # hits on S-1; S-0's fetch is committed and the run chains nothing.
    assert out["soft_time_limit_exceeded"] is True
    assert out["continued"] is False
    assert out["fetched"] == 1
    assert out["remaining"] == 1
    assert continuations == []  # non-empty remainder, but no orphaning continuation

    # The already-fetched reserve was committed before the soft limit aborted.
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "S-0")
        .one()
    )
    assert json.loads(stored.reserve_prices) == [123]


# ---------------------------------------------------------------------------
# 6. backfill stamps reserve_detail_checked_at on a not_settled fetch.
#
# Background (fix/scsbid-reserve-detail-quota)
# --------------------------------------------
# Some notices stay empty forever (reserve never published): they pass the
# age-gate (opening > MIN_SETTLE_AGE_HOURS old) yet a fetch returns no reserve,
# so they were re-fetched every 6h sweep, perpetually burning the rate limit.
# The backfill now stamps ``HistoricalData.reserve_detail_checked_at`` on a
# successful-but-empty fetch so the collector can back the notice off the
# deferred set for one recheck window. A fetched (non-empty) reserve sets
# reserve_prices instead and needs no marker (the reuse query already skips it).
# 429/exceptions must NOT stamp (they stay retryable).
# ---------------------------------------------------------------------------
def test_backfill_stamps_checked_at_on_not_settled(test_db, monkeypatch):
    from app.core.time import utc_now
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    test_db.add(
        HistoricalData(
            notice_number="EMPTY-1", reserve_prices="[]", selected_numbers="[]"
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [],
            "selected_numbers": [],
        },
    )

    # Capture the value assigned by the task before it is committed so we can
    # assert it is a UTC-AWARE instant (SQLite drops tzinfo on round-trip; only
    # Postgres timestamptz preserves it, so check the in-memory ORM object).
    stamped: list[object] = []
    real_utc_now = utc_now

    def _capturing_utc_now():
        value = real_utc_now()
        stamped.append(value)
        return value

    monkeypatch.setattr("app.tasks.jobs.utc_now", _capturing_utc_now, raising=False)
    # The task imports utc_now locally; patch the source module too.
    import app.core.time as _time_mod

    monkeypatch.setattr(_time_mod, "utc_now", _capturing_utc_now)

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "EMPTY-1", "category": "service"}]
    )

    assert out["not_settled"] == 1
    assert out["fetched"] == 0
    # The value the task stamped is a timezone-aware UTC instant.
    assert stamped, "task did not stamp a checked-at value"
    assert stamped[-1].tzinfo is not None

    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "EMPTY-1")
        .one()
    )
    # reserve_prices is left empty (retried later once it settles) but the row is
    # now stamped so the collector backs it off the deferred set.
    assert json.loads(stored.reserve_prices) == []
    assert stored.reserve_detail_checked_at is not None


def test_backfill_creates_row_to_stamp_missing_notice(test_db, monkeypatch):
    """A not_settled fetch for a notice with no row creates the marker row."""
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [],
            "selected_numbers": [],
        },
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "NO-ROW", "category": "service"}]
    )

    assert out["not_settled"] == 1
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "NO-ROW")
        .one()
    )
    assert stored.reserve_detail_checked_at is not None
    assert json.loads(stored.reserve_prices or "[]") == []


def test_backfill_does_not_stamp_on_fetched_reserve(test_db, monkeypatch):
    """A fetched (non-empty) reserve sets reserve_prices; checked_at stays untouched."""
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    test_db.add(
        HistoricalData(
            notice_number="WILL-FILL", reserve_prices="[]", selected_numbers="[]"
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [777],
            "selected_numbers": [],
        },
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "WILL-FILL", "category": "service"}]
    )

    assert out["fetched"] == 1
    assert out["not_settled"] == 0
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "WILL-FILL")
        .one()
    )
    assert json.loads(stored.reserve_prices) == [777]
    # The settled (fetched) path does not stamp the not_settled marker.
    assert stored.reserve_detail_checked_at is None


def test_backfill_does_not_stamp_on_fetch_error(test_db, monkeypatch):
    """A fetch that raises is retryable: no checked_at marker is written."""
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    test_db.add(
        HistoricalData(
            notice_number="RATE-LIMITED", reserve_prices="[]", selected_numbers="[]"
        )
    )
    test_db.commit()

    def _boom(self, raw_item, *, category, service_key):
        raise ValueError("API token quota exceeded")

    monkeypatch.setattr(
        KonepsCollectorService, "_fetch_scsbid_reserve_detail", _boom
    )

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "RATE-LIMITED", "category": "service"}]
    )

    assert out["errors"] == 1
    assert out["not_settled"] == 0
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "RATE-LIMITED")
        .one()
    )
    # No marker: a 429/error notice stays retryable on the next sweep.
    assert stored.reserve_detail_checked_at is None


# ---------------------------------------------------------------------------
# 7. backfill delay uses the dedicated reserve-detail setting (Part 2).
# ---------------------------------------------------------------------------
def test_backfill_uses_dedicated_delay_setting(test_db, monkeypatch):
    """The inter-call sleep reads RESERVE_DETAIL_REQUEST_DELAY_SECONDS, not the
    collection page delay, and 0 disables the sleep."""
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    # Collection delay is non-zero, but the backfill must ignore it.
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 9.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 0.0
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    # The task does ``from time import sleep`` locally, so patch time.sleep.
    import time as _time_module

    sleeps: list[float] = []
    monkeypatch.setattr(_time_module, "sleep", lambda s: sleeps.append(s))

    for nn in ("S-1", "S-2", "S-3"):
        test_db.add(
            HistoricalData(notice_number=nn, reserve_prices="[]", selected_numbers="[]")
        )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [1],
            "selected_numbers": [],
        },
    )

    notices = [
        {"notice_number": nn, "category": "service"} for nn in ("S-1", "S-2", "S-3")
    ]
    out = jobs.backfill_scsbid_reserve_detail.run(notices=notices)

    assert out["fetched"] == 3
    # Delay setting is 0 => no inter-call sleeps even across 3 notices.
    assert sleeps == []


def test_backfill_sleeps_between_calls_when_delay_set(test_db, monkeypatch):
    """A non-zero reserve-detail delay sleeps once between each pair of calls."""
    from app.tasks import jobs

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_REQUEST_DELAY_SECONDS", 1.5
    )
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    import time as _time_module

    sleeps: list[float] = []
    monkeypatch.setattr(_time_module, "sleep", lambda s: sleeps.append(s))

    for nn in ("D-1", "D-2", "D-3"):
        test_db.add(
            HistoricalData(notice_number=nn, reserve_prices="[]", selected_numbers="[]")
        )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: {
            "reserve_prices": [1],
            "selected_numbers": [],
        },
    )

    notices = [
        {"notice_number": nn, "category": "service"} for nn in ("D-1", "D-2", "D-3")
    ]
    jobs.backfill_scsbid_reserve_detail.run(notices=notices)

    # index>0 for two of the three notices => two sleeps of the configured value.
    assert sleeps == [1.5, 1.5]


# ---------------------------------------------------------------------------
# 8. collector defer recheck-gate (Part 3): a recently-checked notice is skipped.
# ---------------------------------------------------------------------------
def test_recheck_gate_excludes_recently_checked_notice(test_db, monkeypatch):
    from datetime import timedelta

    from app.core.time import utc_now

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    # Disable the age-gate so only the recheck-gate decides here.
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS", 0
    )
    monkeypatch.setattr(settings, "KONEPS_SCSBID_RESERVE_DETAIL_RECHECK_HOURS", 48)

    # CHECKED was fetched-empty 1h ago (within the 48h window) => skip this sweep.
    # STALE was checked 72h ago (outside the window) => defer again.
    test_db.add(
        HistoricalData(
            notice_number="CHECKED",
            reserve_prices="[]",
            selected_numbers="[]",
            reserve_detail_checked_at=utc_now() - timedelta(hours=1),
        )
    )
    test_db.add(
        HistoricalData(
            notice_number="STALE",
            reserve_prices="[]",
            selected_numbers="[]",
            reserve_detail_checked_at=utc_now() - timedelta(hours=72),
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: (_ for _ in ()).throw(
            AssertionError("must not fetch when deferring")
        ),
    )

    def fake_get(url, params, timeout):
        return FakeOpenApiResponse(
            _award_body(
                [_award_item("CHECKED"), _award_item("STALE"), _award_item("FRESH")],
                total_count=3,
                num_of_rows=100,
            )
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=True,
    )

    deferred = result["metadata"]["deferred_reserve_detail_notices"]
    deferred_numbers = {d["notice_number"] for d in deferred}
    # CHECKED is backed off; STALE (window expired) and FRESH (never checked) defer.
    assert deferred_numbers == {"STALE", "FRESH"}
    assert result["metadata"]["reserve_detail_recheck_skipped_count"] == 1


def test_recheck_gate_disabled_when_hours_zero(test_db, monkeypatch):
    """RECHECK_HOURS=0 disables the recheck-gate: even a just-checked notice defers."""
    from datetime import timedelta

    from app.core.time import utc_now

    monkeypatch.setattr(settings, "KONEPS_OPENAPI_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_RESERVE_DETAIL_MIN_SETTLE_AGE_HOURS", 0
    )
    monkeypatch.setattr(settings, "KONEPS_SCSBID_RESERVE_DETAIL_RECHECK_HOURS", 0)

    test_db.add(
        HistoricalData(
            notice_number="CHECKED",
            reserve_prices="[]",
            selected_numbers="[]",
            reserve_detail_checked_at=utc_now() - timedelta(hours=1),
        )
    )
    test_db.commit()

    monkeypatch.setattr(
        KonepsCollectorService,
        "_fetch_scsbid_reserve_detail",
        lambda self, raw_item, *, category, service_key: (_ for _ in ()).throw(
            AssertionError("must not fetch when deferring")
        ),
    )

    def fake_get(url, params, timeout):
        return FakeOpenApiResponse(
            _award_body([_award_item("CHECKED")], total_count=1, num_of_rows=100)
        )

    monkeypatch.setattr("app.services.koneps.http_client.requests.get", fake_get)

    service = KonepsCollectorService()
    result = service._collect_scsbid_openapi_items(
        service._normalize_request(_scsbid_request()),
        db=test_db,
        defer_reserve_detail=True,
    )

    deferred = result["metadata"]["deferred_reserve_detail_notices"]
    assert [d["notice_number"] for d in deferred] == ["CHECKED"]
    assert result["metadata"]["reserve_detail_recheck_skipped_count"] == 0


# ---------------------------------------------------------------------------
# 9. model regression: the new reserve_detail_checked_at column exists & defaults None.
# ---------------------------------------------------------------------------
def test_historical_data_has_reserve_detail_checked_at_column():
    column = HistoricalData.__table__.columns["reserve_detail_checked_at"]
    assert column.nullable is True
    # DateTime(timezone=True) so the marker is a UTC-aware instant.
    assert getattr(column.type, "timezone", False) is True


def test_historical_data_reserve_detail_checked_at_defaults_none(test_db):
    row = HistoricalData(
        notice_number="DEFAULT-NONE", reserve_prices="[]", selected_numbers="[]"
    )
    test_db.add(row)
    test_db.commit()
    test_db.expire_all()
    stored = (
        test_db.query(HistoricalData)
        .filter(HistoricalData.notice_number == "DEFAULT-NONE")
        .one()
    )
    assert stored.reserve_detail_checked_at is None
