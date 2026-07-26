"""Characterization for the thin ``@task`` delegation shells in ``app.tasks.jobs``.

The §4.5 size decomposition moved several task *bodies* into sibling
``app/tasks/`` modules; the ``@task`` entries stay in ``app.tasks.jobs`` (so their
Celery registration names are unchanged) as thin shells that delegate to the
extracted body. These tests lock the delegation contract: each shell forwards its
arguments verbatim and, where a helper that references a Celery task must stay in
the ``jobs`` module (for the monkeypatch seam), passes that helper in by its
``jobs``-module binding so a patch on ``jobs.<helper>`` still flows through.
"""

import app.tasks.jobs as jobs


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
        request_payload,
        crawl_job_id,
        enqueue_deferred_embedding_backfill,
        enqueue_deferred_reserve_detail_backfill,
    ):
        captured.update(
            request_payload=request_payload,
            crawl_job_id=crawl_job_id,
            emb=enqueue_deferred_embedding_backfill,
            res=enqueue_deferred_reserve_detail_backfill,
        )
        return {"ok": "collect"}

    monkeypatch.setattr(jobs, "run_koneps_collection_job", _spy)

    out = jobs.collect_koneps_notices.run(
        request_payload={"source": "scsbid-openapi"}, crawl_job_id=7
    )

    assert out == {"ok": "collect"}
    assert captured["request_payload"] == {"source": "scsbid-openapi"}
    assert captured["crawl_job_id"] == 7
    # The injected enqueue helpers must be this module's bindings so the
    # jobs._enqueue_deferred_embedding_backfill monkeypatch seam keeps working.
    assert captured["emb"] is jobs._enqueue_deferred_embedding_backfill
    assert captured["res"] is jobs._enqueue_deferred_reserve_detail_backfill


def test_backfill_scsbid_reserve_detail_delegates_with_injected_continuation(monkeypatch):
    captured: dict = {}

    def _spy(notices, *, enqueue_continuation):
        captured.update(notices=notices, cont=enqueue_continuation)
        return {"ok": "backfill"}

    monkeypatch.setattr(jobs, "run_scsbid_reserve_detail_backfill_job", _spy)

    out = jobs.backfill_scsbid_reserve_detail.run(
        notices=[{"notice_number": "N1", "category": "service"}]
    )

    assert out == {"ok": "backfill"}
    assert captured["notices"] == [{"notice_number": "N1", "category": "service"}]
    assert captured["cont"] is jobs._enqueue_reserve_detail_continuation


def test_run_synthetic_operator_backtest_delegates(monkeypatch):
    captured: dict = {}

    def _spy(payload):
        captured["payload"] = payload
        return {"ok": "synthetic"}

    monkeypatch.setattr(jobs, "run_synthetic_operator_backtest_job", _spy)

    out = jobs.run_synthetic_operator_backtest.run(payload={"limit": 5})

    assert out == {"ok": "synthetic"}
    assert captured["payload"] == {"limit": 5}


def test_run_historical_backtest_delegates(monkeypatch):
    captured: dict = {}

    def _spy(request_payload):
        captured["request_payload"] = request_payload
        return {"ok": "historical"}

    monkeypatch.setattr(jobs, "run_historical_backtest_job", _spy)

    out = jobs.run_historical_backtest.run(request_payload={"limit": 3})

    assert out == {"ok": "historical"}
    assert captured["request_payload"] == {"limit": 3}


def test_collect_g2_evidence_delegates_with_db_and_injected_draft_writer(monkeypatch):
    dummy = _DummyDB()
    monkeypatch.setattr(jobs, "SessionLocal", lambda: dummy)
    captured: dict = {}

    def _spy(db, *, window_days, recent_limit, write_daily_draft):
        captured.update(
            db=db,
            window_days=window_days,
            recent_limit=recent_limit,
            writer=write_daily_draft,
        )
        return {"ok": "collect_g2"}

    monkeypatch.setattr(jobs, "run_collect_g2_evidence_job", _spy)

    out = jobs.collect_g2_evidence.run(window_days=21, recent_limit=7)

    assert out == {"ok": "collect_g2"}
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
    monkeypatch.setattr(jobs, "SessionLocal", lambda: dummy)
    captured: dict = {}

    def _spy(db):
        captured["db"] = db
        return {"ok": "recheck"}

    monkeypatch.setattr(jobs, "run_g2_candidate_recheck_job", _spy)

    out = jobs.run_g2_candidate_recheck.run()

    assert out == {"ok": "recheck"}
    assert captured["db"] is dummy
    assert dummy.closed is True
