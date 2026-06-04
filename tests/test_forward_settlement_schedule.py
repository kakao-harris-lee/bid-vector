"""Forward paper-bid settlement beat schedule wiring."""

from __future__ import annotations

from app.tasks.celery_app import (
    FORWARD_SETTLEMENT_TASK_NAME,
    build_celery_runtime_config,
    build_forward_settlement_beat_schedule,
)


def test_forward_settlement_schedule_is_empty_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_SCHEDULE_ENABLED", False)
    assert build_forward_settlement_beat_schedule() == {}


def test_forward_settlement_schedule_builds_when_enabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_INTERVAL_MINUTES", 720)
    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_LIMIT", 200)
    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_PERSIST", True)

    schedule = build_forward_settlement_beat_schedule()
    entry = schedule["forward_settlement_periodic"]
    assert entry["task"] == FORWARD_SETTLEMENT_TASK_NAME
    assert entry["schedule"] == 720 * 60

    kwargs = entry["kwargs"]
    assert kwargs["limit"] == 200
    assert kwargs["persist"] is True


def test_forward_settlement_schedule_minimum_interval_floor(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_INTERVAL_MINUTES", 0)
    entry = build_forward_settlement_beat_schedule()["forward_settlement_periodic"]
    assert entry["schedule"] == 60


def test_forward_settlement_schedule_included_in_celery_runtime_config(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_SCHEDULE_ENABLED", True)
    config = build_celery_runtime_config()
    assert "forward_settlement_periodic" in config["beat_schedule"]


def test_forward_settlement_schedule_independent_of_other_schedules(monkeypatch):
    """Enabling forward settlement must not pull in unrelated schedules."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FORWARD_SETTLEMENT_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED", False)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_ENABLED", False)
    config = build_celery_runtime_config()
    beat = config["beat_schedule"]
    assert "forward_settlement_periodic" in beat
    assert "paper_bidding_forward_periodic" not in beat
    assert "historical_backtest_periodic" not in beat
