"""Award-result Telegram beat schedule wiring."""

from __future__ import annotations

from app.tasks.celery_app import (
    NOTIFY_AWARD_RESULTS_TASK_NAME,
    build_award_result_notify_beat_schedule,
    build_celery_runtime_config,
    build_task_routes,
)


def test_award_result_notify_schedule_is_empty_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED", False)
    assert build_award_result_notify_beat_schedule() == {}


def test_award_result_notify_schedule_builds_when_enabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_INTERVAL_MINUTES", 360)
    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_LIMIT", 25)

    schedule = build_award_result_notify_beat_schedule()
    entry = schedule["award_result_notify_periodic"]
    assert entry["task"] == NOTIFY_AWARD_RESULTS_TASK_NAME
    assert entry["schedule"] == 360 * 60
    assert entry["kwargs"] == {"limit": 25}


def test_award_result_notify_schedule_minimum_interval_floor(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_INTERVAL_MINUTES", 0)
    entry = build_award_result_notify_beat_schedule()["award_result_notify_periodic"]
    assert entry["schedule"] == 60


def test_award_result_notify_limit_floor(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_LIMIT", 0)
    entry = build_award_result_notify_beat_schedule()["award_result_notify_periodic"]
    assert entry["kwargs"]["limit"] == 1


def test_award_result_notify_schedule_included_in_runtime_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED", True)
    config = build_celery_runtime_config()
    assert "award_result_notify_periodic" in config["beat_schedule"]


def test_award_result_notify_routed_to_ops_queue():
    routes = build_task_routes()
    assert NOTIFY_AWARD_RESULTS_TASK_NAME in routes


def test_award_result_notify_schedule_independent_of_other_schedules(monkeypatch):
    """Enabling award-notify must not pull in unrelated schedules."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_SCHEDULE_ENABLED", False)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_ENABLED", False)
    beat = build_celery_runtime_config()["beat_schedule"]
    assert "award_result_notify_periodic" in beat
    assert "forward_settlement_periodic" not in beat
    assert "historical_backtest_periodic" not in beat
