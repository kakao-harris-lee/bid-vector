"""KONEPS collection beat schedule wiring."""

from __future__ import annotations

from app.tasks.celery_app import (
    COLLECT_KONEPS_NOTICES_TASK_NAME,
    build_celery_runtime_config,
    build_koneps_collection_beat_schedule,
)


def test_koneps_schedule_is_empty_by_default(monkeypatch):
    """Without KONEPS_COLLECTION_SCHEDULE_ENABLED the entry is omitted."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", False)
    assert build_koneps_collection_beat_schedule() == {}


def test_koneps_schedule_builds_when_enabled(monkeypatch):
    """When enabled, the entry references the collect task with configured kwargs."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_INTERVAL_MINUTES", 30)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SOURCE", "koneps-openapi")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORY", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_MAX_ITEMS", 75)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_EXECUTION_MODE", "auto")

    schedule = build_koneps_collection_beat_schedule()
    assert "koneps_collection_periodic" in schedule

    entry = schedule["koneps_collection_periodic"]
    assert entry["task"] == COLLECT_KONEPS_NOTICES_TASK_NAME
    assert entry["schedule"] == 30 * 60

    payload = entry["kwargs"]["request_payload"]
    assert payload["source"] == "koneps-openapi"
    assert payload["category"] is None  # empty string normalized to None
    assert payload["max_items"] == 75
    assert payload["execution_mode"] == "auto"


def test_koneps_schedule_included_in_celery_runtime_config(monkeypatch):
    """The runtime config beat_schedule includes the koneps entry when enabled."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    config = build_celery_runtime_config()
    beat = config["beat_schedule"]
    assert "koneps_collection_periodic" in beat


def test_koneps_schedule_minimum_interval_floor(monkeypatch):
    """Negative or zero interval is clamped to 1 minute (60 seconds)."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_INTERVAL_MINUTES", 0)
    entry = build_koneps_collection_beat_schedule()["koneps_collection_periodic"]
    assert entry["schedule"] == 60


def test_koneps_schedule_max_items_clamped_to_crawl_request_limit(monkeypatch):
    """max_items >100 is clamped to 100 to satisfy CrawlRequest.max_items le=100."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_MAX_ITEMS", 500)
    payload = build_koneps_collection_beat_schedule()["koneps_collection_periodic"]["kwargs"]["request_payload"]
    assert payload["max_items"] == 100
