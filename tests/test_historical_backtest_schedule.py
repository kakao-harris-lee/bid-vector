"""Historical backtest beat schedule wiring."""

from __future__ import annotations

from app.tasks.celery_app import (
    HISTORICAL_BACKTEST_TASK_NAME,
    build_celery_runtime_config,
    build_historical_backtest_beat_schedule,
)


def test_historical_backtest_schedule_is_empty_by_default(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_ENABLED", False)
    assert build_historical_backtest_beat_schedule() == {}


def test_historical_backtest_schedule_builds_when_enabled(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_INTERVAL_MINUTES", 720)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_LOOKBACK_DAYS", 14)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_LIMIT", 50)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_CATEGORY", "")
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_SCENARIO", "base")
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_HISTORY_LIMIT", 80)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_CUTOFF_HOURS", 2)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_SETTLE_ACTIONS", "bid_now,review")
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_PERSIST", True)

    schedule = build_historical_backtest_beat_schedule()
    entry = schedule["historical_backtest_periodic"]
    assert entry["task"] == HISTORICAL_BACKTEST_TASK_NAME
    assert entry["schedule"] == 720 * 60

    payload = entry["kwargs"]["request_payload"]
    assert payload["category"] is None
    assert payload["limit"] == 50
    assert payload["scenario"] == "base"
    assert payload["lookback_days"] == 14
    assert payload["settle_actions"] == "bid_now,review"
    assert payload["persist"] is True


def test_historical_backtest_schedule_included_in_celery_runtime_config(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_ENABLED", True)
    config = build_celery_runtime_config()
    assert "historical_backtest_periodic" in config["beat_schedule"]


def test_historical_backtest_schedule_minimum_interval_floor(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_INTERVAL_MINUTES", 0)
    entry = build_historical_backtest_beat_schedule()["historical_backtest_periodic"]
    assert entry["schedule"] == 60
