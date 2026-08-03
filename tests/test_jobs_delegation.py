"""Characterization for the thin ``@task`` delegation shells in ``app.tasks.jobs``.

The §4.5 size decomposition moved several task *bodies* into sibling
``app/tasks/`` modules; the ``@task`` entries stay in ``app.tasks.jobs`` (so their
Celery registration names are unchanged) as thin shells that delegate to the
extracted body. These tests lock the delegation contract: each shell **promotes the
broker payload to its validated DTO** and forwards it (plus the non-payload args
verbatim) and, where a helper that references a Celery task must stay in the
``jobs`` module (for the monkeypatch seam), passes that helper in by its
``jobs``-module binding so a patch on ``jobs.<helper>`` still flows through.
"""

import app.tasks.jobs as jobs
from app.schemas.crawl import CrawlRequest
from app.schemas.g2_evidence import (
    G2CandidateRecheckSummary,
    G2CollectEvidenceSummary,
)
from app.schemas.task_payloads import (
    HistoricalBacktestTaskRequest,
    ScsbidReserveDetailBackfillRequest,
    SyntheticOperatorBacktestTaskRequest,
)


class _DummyDB:
    """Minimal stand-in so db-owning shells can be exercised without a database."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_collect_koneps_notices_delegates_with_injected_enqueue_helpers(monkeypatch):
    captured: dict = {}

    def _spy(
        self_arg,
        *,
        request,
        crawl_job_id,
        notify_inference_outbox_committed,
        enqueue_deferred_reserve_detail_backfill,
    ):
        captured.update(
            request=request,
            crawl_job_id=crawl_job_id,
            inference=notify_inference_outbox_committed,
            res=enqueue_deferred_reserve_detail_backfill,
        )
        return {"ok": "collect"}

    monkeypatch.setattr(jobs, "run_koneps_collection_job", _spy)

    out = jobs.collect_koneps_notices.run(
        request_payload={"source": "scsbid-openapi"}, crawl_job_id=7
    )

    assert out == {"ok": "collect"}
    # The body receives the validated model, not the raw broker dict.
    assert isinstance(captured["request"], CrawlRequest)
    assert captured["request"].source == "scsbid-openapi"
    assert captured["crawl_job_id"] == 7
    # The injected notification must be this module's binding so monkeypatching
    # the task shell still controls post-commit dispatch.
    assert captured["inference"] is jobs.notify_inference_outbox_committed
    assert captured["res"] is jobs._enqueue_deferred_reserve_detail_backfill


def test_backfill_scsbid_reserve_detail_delegates_with_injected_continuation(monkeypatch):
    captured: dict = {}

    def _spy(request, *, enqueue_continuation):
        captured.update(request=request, cont=enqueue_continuation)
        return {"ok": "backfill"}

    monkeypatch.setattr(jobs, "run_scsbid_reserve_detail_backfill_job", _spy)

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "N1", "category": "service"}]
    )

    assert out == {"ok": "backfill"}
    assert captured["request"] == ScsbidReserveDetailBackfillRequest.model_validate(
        {"notices": [{"notice_number": "N1", "category": "service"}]}
    )
    assert captured["cont"] is jobs._enqueue_reserve_detail_continuation


def test_run_synthetic_operator_backtest_delegates(monkeypatch):
    captured: dict = {}

    def _spy(request):
        captured["request"] = request
        return {"ok": "synthetic"}

    monkeypatch.setattr(jobs, "run_synthetic_operator_backtest_job", _spy)

    out = jobs.run_synthetic_operator_backtest.run(payload={"limit": 5})

    assert out == {"ok": "synthetic"}
    assert captured["request"] == SyntheticOperatorBacktestTaskRequest(limit=5)


def test_run_historical_backtest_delegates(monkeypatch):
    captured: dict = {}

    def _spy(request):
        captured["request"] = request
        return {"ok": "historical"}

    monkeypatch.setattr(jobs, "run_historical_backtest_job", _spy)

    out = jobs.run_historical_backtest.run(request_payload={"limit": 3})

    assert out == {"ok": "historical"}
    assert captured["request"] == HistoricalBacktestTaskRequest(limit=3)


def test_collect_g2_evidence_delegates_with_db_and_injected_draft_writer(monkeypatch):
    dummy = _DummyDB()
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: dummy)
    captured: dict = {}

    def _spy(db, *, window_days, recent_limit, write_daily_draft):
        captured.update(
            db=db,
            window_days=window_days,
            recent_limit=recent_limit,
            writer=write_daily_draft,
        )
        return G2CollectEvidenceSummary(
            generated_window_days=window_days,
            recent_limit=recent_limit,
            operator_count=0,
            ready_count=0,
            error_count=0,
        )

    monkeypatch.setattr(jobs, "run_collect_g2_evidence_job", _spy)

    out = jobs.collect_g2_evidence.run(window_days=21, recent_limit=7)

    # The shell lowers the body's typed summary to a JSON-safe dict so the celery
    # result backend (json serializer) can carry it.
    assert out == {
        "generated_window_days": 21,
        "recent_limit": 7,
        "operator_count": 0,
        "ready_count": 0,
        "error_count": 0,
        "per_operator": [],
    }
    assert captured["db"] is dummy
    assert captured["window_days"] == 21
    assert captured["recent_limit"] == 7
    # The draft writer must be this module's binding so the
    # jobs._write_g2_daily_evidence_draft monkeypatch seam keeps working.
    assert captured["writer"] is jobs._write_g2_daily_evidence_draft
    # The shell owns the db lifecycle and closes it in ``finally``.
    assert dummy.closed is True


def test_run_g2_candidate_recheck_delegates_with_db(monkeypatch):
    dummy = _DummyDB()
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: dummy)
    captured: dict = {}

    def _spy(db):
        captured["db"] = db
        return G2CandidateRecheckSummary(
            operator_count=0,
            total_candidates=0,
            operators_with_candidates=0,
            error_count=0,
        )

    monkeypatch.setattr(jobs, "run_g2_candidate_recheck_job", _spy)

    out = jobs.run_g2_candidate_recheck.run()

    # Typed summary lowered to a JSON-safe dict by the shell.
    assert out == {
        "operator_count": 0,
        "total_candidates": 0,
        "operators_with_candidates": 0,
        "error_count": 0,
        "per_operator": [],
    }
    assert captured["db"] is dummy
    assert dummy.closed is True
