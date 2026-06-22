"""Stale task-run reconciler beat schedule wiring."""

from __future__ import annotations

from app.tasks.celery_app import (
    RECONCILE_STALE_TASK_RUNS_TASK_NAME,
    build_celery_runtime_config,
    build_stale_task_reconciler_beat_schedule,
    build_task_routes,
)


def test_reconciler_schedule_is_empty_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_SCHEDULE_ENABLED", False)
    assert build_stale_task_reconciler_beat_schedule() == {}


def test_reconciler_schedule_builds_when_enabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_INTERVAL_MINUTES", 10)

    schedule = build_stale_task_reconciler_beat_schedule()
    entry = schedule["stale_task_reconciler_periodic"]
    assert entry["task"] == RECONCILE_STALE_TASK_RUNS_TASK_NAME
    assert entry["schedule"] == 10 * 60


def test_reconciler_schedule_minimum_interval_floor(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_INTERVAL_MINUTES", 0)
    entry = build_stale_task_reconciler_beat_schedule()["stale_task_reconciler_periodic"]
    assert entry["schedule"] == 60


def test_reconciler_schedule_included_in_celery_runtime_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_SCHEDULE_ENABLED", True)
    config = build_celery_runtime_config()
    assert "stale_task_reconciler_periodic" in config["beat_schedule"]


def test_reconciler_task_is_routed_to_ops_queue():
    from app.core.config import settings

    routes = build_task_routes()
    assert routes[RECONCILE_STALE_TASK_RUNS_TASK_NAME]["queue"] == settings.CELERY_OPS_QUEUE


def test_reconciler_schedule_independent_of_other_schedules(monkeypatch):
    """Enabling the reconciler must not pull in unrelated schedules."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "STALE_TASK_RECONCILER_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_SCHEDULE_ENABLED", False)
    monkeypatch.setattr(settings, "OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED", False)
    config = build_celery_runtime_config()
    beat = config["beat_schedule"]
    assert "stale_task_reconciler_periodic" in beat
    assert "forward_settlement_periodic" not in beat
    assert "operator_strategy_monitor_periodic" not in beat
