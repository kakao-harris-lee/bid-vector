"""Telegram polling beat schedule wiring."""

from __future__ import annotations

from app.tasks.celery_app import (
    TELEGRAM_POLLING_TASK_NAME,
    build_celery_runtime_config,
    build_telegram_polling_beat_schedule,
)


def test_telegram_polling_schedule_is_empty_by_default(monkeypatch):
    """Without TELEGRAM_POLLING_SCHEDULE_ENABLED the entry is omitted."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", False)
    assert build_telegram_polling_beat_schedule() == {}


def test_telegram_polling_schedule_builds_when_enabled(monkeypatch):
    """When enabled, the entry polls Telegram updates on the configured cadence."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_INTERVAL_SECONDS", 12)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_LIMIT", 7)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_TIMEOUT_SECONDS", 0)

    schedule = build_telegram_polling_beat_schedule()
    entry = schedule["telegram_polling_periodic"]

    assert entry["task"] == TELEGRAM_POLLING_TASK_NAME
    assert entry["schedule"] == 12.0
    assert entry["kwargs"] == {"limit": 7, "timeout_seconds": 0}


def test_telegram_polling_schedule_has_minimum_interval(monkeypatch):
    """The polling interval is clamped to avoid a tight beat loop."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_INTERVAL_SECONDS", 0)

    entry = build_telegram_polling_beat_schedule()["telegram_polling_periodic"]
    assert entry["schedule"] == 5.0


def test_telegram_polling_schedule_included_in_runtime_config(monkeypatch):
    """The runtime config beat_schedule includes the Telegram polling entry when enabled."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", True)

    beat = build_celery_runtime_config()["beat_schedule"]
    assert "telegram_polling_periodic" in beat
