"""Finalize-on-failure invariants for the operator strategy monitor.

Regression coverage for the orphaned ``running`` operator_strategy_runs root
cause of the operations dashboard ``task_stale_queue: critical`` KPI: a run that
raises after the row is created must never be left in ``running`` -- it must be
finalized to ``failed`` (with an error message), and a secondary failure of the
finalize DB write itself must never mask the original exception.
"""

from __future__ import annotations

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    ensure_operator_strategy,
)
from app.models.models import OperatorStrategyRun
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.opportunity_monitoring import StrategyMonitoringService


def _bootstrap(test_db) -> None:
    ensure_operator_account(test_db)
    ensure_operator_profile(test_db)
    ensure_operator_strategy(test_db)


def test_execute_monitoring_finalizes_run_failed_on_exception(test_db, monkeypatch):
    _bootstrap(test_db)
    service = StrategyMonitoringService()

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(service, "_collect_candidate_evaluations", boom)

    with pytest.raises(RuntimeError, match="connection refused"):
        service.execute_monitoring(
            test_db,
            request=OperatorStrategyMonitorRequest(limit=10, high_priority_only=False),
            trigger_source=StrategyMonitoringService.SYNC_TRIGGER_SOURCE,
        )

    runs = test_db.query(OperatorStrategyRun).all()
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed", "run must not be left in running"
    assert run.status != "running"
    assert run.error_message and "connection refused" in run.error_message
    assert run.completed_at is not None


def test_execute_monitoring_finalizes_run_failed_on_soft_time_limit(test_db, monkeypatch):
    _bootstrap(test_db)
    service = StrategyMonitoringService()

    def timeout(*args, **kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(service, "_collect_candidate_evaluations", timeout)

    with pytest.raises(SoftTimeLimitExceeded):
        service.execute_monitoring(
            test_db,
            request=OperatorStrategyMonitorRequest(limit=10, high_priority_only=False),
            trigger_source=StrategyMonitoringService.SCHEDULED_TRIGGER_SOURCE,
        )

    run = test_db.query(OperatorStrategyRun).one()
    assert run.status == "failed"
    assert run.error_message is not None
    assert run.completed_at is not None


def test_execute_monitoring_does_not_mask_original_exception_when_finalize_write_fails(
    test_db, monkeypatch
):
    """If the finalize DB write itself fails, the original error must still propagate."""
    _bootstrap(test_db)
    service = StrategyMonitoringService()

    original = RuntimeError("primary database error")

    def boom(*args, **kwargs):
        raise original

    monkeypatch.setattr(service, "_collect_candidate_evaluations", boom)

    # Both the live-session and fresh-session finalize writes fail -- simulating
    # the DB still being down when we try to record the failure.
    def failing_write(*args, **kwargs):
        raise RuntimeError("finalize write also failed")

    monkeypatch.setattr(service, "_write_run_failure", failing_write)
    monkeypatch.setattr(service, "_write_run_failure_on_new_session", failing_write)

    with pytest.raises(RuntimeError, match="primary database error"):
        service.execute_monitoring(
            test_db,
            request=OperatorStrategyMonitorRequest(limit=10, high_priority_only=False),
            trigger_source=StrategyMonitoringService.SYNC_TRIGGER_SOURCE,
        )


def test_execute_monitoring_falls_back_to_new_session_when_live_session_poisoned(
    test_db, monkeypatch
):
    """When the live session can't finalize, a fresh session still closes the orphan."""
    _bootstrap(test_db)
    service = StrategyMonitoringService()

    def boom(*args, **kwargs):
        raise RuntimeError("primary failure")

    monkeypatch.setattr(service, "_collect_candidate_evaluations", boom)

    # Live-session finalize fails; the fresh-session fallback (which uses the same
    # in-memory SQLite engine via SessionLocal override below) succeeds.
    monkeypatch.setattr(
        service,
        "_write_run_failure",
        lambda *a, **k: False,
    )

    captured = {}

    def fake_new_session(*, run_id, error_message):
        captured["run_id"] = run_id
        run = test_db.query(OperatorStrategyRun).filter_by(id=run_id).first()
        run.status = "failed"
        run.error_message = error_message
        from app.core.time import utc_now

        run.completed_at = utc_now()
        test_db.commit()
        return True

    monkeypatch.setattr(service, "_write_run_failure_on_new_session", fake_new_session)

    with pytest.raises(RuntimeError, match="primary failure"):
        service.execute_monitoring(
            test_db,
            request=OperatorStrategyMonitorRequest(limit=10, high_priority_only=False),
            trigger_source=StrategyMonitoringService.SYNC_TRIGGER_SOURCE,
        )

    run = test_db.query(OperatorStrategyRun).one()
    assert run.status == "failed"
    assert captured["run_id"] == run.id


def test_monitor_task_finalizes_run_failed_on_exception(test_db, monkeypatch):
    """The Celery task wrapper must also leave the run failed, not running."""
    from app.tasks import jobs

    _bootstrap(test_db)

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    def boom(self, *args, **kwargs):
        raise RuntimeError("task-level failure")

    monkeypatch.setattr(
        StrategyMonitoringService, "_collect_candidate_evaluations", boom
    )

    with pytest.raises(RuntimeError, match="task-level failure"):
        jobs.monitor_operator_strategy(
            request_payload={"limit": 10, "high_priority_only": False},
            trigger_source=StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
        )

    run = test_db.query(OperatorStrategyRun).one()
    assert run.status == "failed"
    assert run.status != "running"
