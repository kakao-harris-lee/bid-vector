"""Tests for the stale task-run reconciler (durable orphaned-run backstop)."""

from __future__ import annotations

from datetime import timedelta

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import CrawlJob, OperatorStrategyRun, User
from app.services.stale_task_reconciler import (
    RECONCILED_MARKER,
    StaleTaskReconcilerService,
)


def _operator(db) -> User:
    operator = User(username="operator", email="op@example.com", hashed_password="x")
    db.add(operator)
    db.commit()
    db.refresh(operator)
    return operator


def _stale_age_seconds() -> int:
    # Mirror the service threshold: hard limit + grace (floored at 60).
    return max(60, settings.CELERY_TASK_TIME_LIMIT_SECONDS + settings.STALE_TASK_RECONCILER_GRACE_SECONDS) + 60


def test_reconciler_finalizes_stale_running_strategy_run(test_db):
    operator = _operator(test_db)
    stale_at = utc_now() - timedelta(seconds=_stale_age_seconds())
    run = OperatorStrategyRun(
        operator_id=operator.id,
        status="running",
        trigger_source="scheduled",
        started_at=stale_at,
        created_at=stale_at,
    )
    test_db.add(run)
    test_db.commit()
    run_id = run.id

    result = StaleTaskReconcilerService().reconcile(test_db)

    assert result["strategy_runs_finalized"] == 1
    assert result["total_finalized"] == 1
    refreshed = test_db.query(OperatorStrategyRun).filter_by(id=run_id).first()
    assert refreshed.status == "failed"
    assert RECONCILED_MARKER in (refreshed.error_message or "")
    assert refreshed.completed_at is not None


def test_reconciler_finalizes_stale_queued_run_without_started_at(test_db):
    """A queued row that never transitioned to running is still reconciled by created_at age."""
    operator = _operator(test_db)
    stale_at = utc_now() - timedelta(seconds=_stale_age_seconds())
    run = OperatorStrategyRun(
        operator_id=operator.id,
        status="queued",
        trigger_source="scheduled",
        started_at=None,
        created_at=stale_at,
    )
    test_db.add(run)
    test_db.commit()
    run_id = run.id

    result = StaleTaskReconcilerService().reconcile(test_db)

    assert result["strategy_runs_finalized"] == 1
    refreshed = test_db.query(OperatorStrategyRun).filter_by(id=run_id).first()
    assert refreshed.status == "failed"


def test_reconciler_finalizes_stale_running_crawl_job(test_db):
    stale_at = utc_now() - timedelta(seconds=_stale_age_seconds())
    job = CrawlJob(source="koneps", status="running", created_at=stale_at)
    test_db.add(job)
    test_db.commit()
    job_id = job.id

    result = StaleTaskReconcilerService().reconcile(test_db)

    assert result["crawl_jobs_finalized"] == 1
    refreshed = test_db.query(CrawlJob).filter_by(id=job_id).first()
    assert refreshed.status == "failed"
    assert RECONCILED_MARKER in (refreshed.error_message or "")
    assert refreshed.completed_at is not None


def test_reconciler_skips_recent_non_terminal_rows(test_db):
    """A run that started just now is a genuinely live task and must not be reconciled."""
    operator = _operator(test_db)
    fresh = utc_now()
    run = OperatorStrategyRun(
        operator_id=operator.id,
        status="running",
        trigger_source="scheduled",
        started_at=fresh,
        created_at=fresh,
    )
    job = CrawlJob(source="koneps", status="running", created_at=fresh)
    test_db.add_all([run, job])
    test_db.commit()
    run_id, job_id = run.id, job.id

    result = StaleTaskReconcilerService().reconcile(test_db)

    assert result["total_finalized"] == 0
    assert test_db.query(OperatorStrategyRun).filter_by(id=run_id).first().status == "running"
    assert test_db.query(CrawlJob).filter_by(id=job_id).first().status == "running"


def test_reconciler_skips_already_terminal_rows(test_db):
    """Completed/failed/cancelled rows are never touched, even when old."""
    operator = _operator(test_db)
    stale_at = utc_now() - timedelta(seconds=_stale_age_seconds())
    completed = OperatorStrategyRun(
        operator_id=operator.id,
        status="completed",
        trigger_source="scheduled",
        started_at=stale_at,
        created_at=stale_at,
        completed_at=stale_at,
    )
    failed = OperatorStrategyRun(
        operator_id=operator.id,
        status="failed",
        trigger_source="scheduled",
        started_at=stale_at,
        created_at=stale_at,
        completed_at=stale_at,
        error_message="original failure",
    )
    done_job = CrawlJob(
        source="koneps", status="completed", created_at=stale_at, completed_at=stale_at
    )
    test_db.add_all([completed, failed, done_job])
    test_db.commit()
    failed_id = failed.id

    result = StaleTaskReconcilerService().reconcile(test_db)

    assert result["total_finalized"] == 0
    # The pre-existing failure message must be left intact.
    refreshed_failed = test_db.query(OperatorStrategyRun).filter_by(id=failed_id).first()
    assert refreshed_failed.error_message == "original failure"


def test_reconciler_threshold_uses_hard_limit_plus_grace(monkeypatch):
    monkeypatch.setattr(settings, "CELERY_TASK_TIME_LIMIT_SECONDS", 1800)
    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_GRACE_SECONDS", 300)
    assert StaleTaskReconcilerService()._stale_threshold_seconds() == 2100


def test_reconciler_threshold_floor_of_one_minute(monkeypatch):
    monkeypatch.setattr(settings, "CELERY_TASK_TIME_LIMIT_SECONDS", 0)
    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_GRACE_SECONDS", 0)
    assert StaleTaskReconcilerService()._stale_threshold_seconds() == 60


def test_reconciler_preserves_prior_error_context(test_db):
    operator = _operator(test_db)
    stale_at = utc_now() - timedelta(seconds=_stale_age_seconds())
    run = OperatorStrategyRun(
        operator_id=operator.id,
        status="running",
        trigger_source="scheduled",
        started_at=stale_at,
        created_at=stale_at,
        error_message="partial progress note",
    )
    test_db.add(run)
    test_db.commit()
    run_id = run.id

    StaleTaskReconcilerService().reconcile(test_db)

    refreshed = test_db.query(OperatorStrategyRun).filter_by(id=run_id).first()
    assert RECONCILED_MARKER in refreshed.error_message
    assert "partial progress note" in refreshed.error_message
