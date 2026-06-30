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


def test_koneps_schedule_builds_when_enabled_no_category(monkeypatch):
    """When enabled with no category, a single 'general' entry is produced."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_INTERVAL_MINUTES", 30)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SOURCE", "koneps-openapi")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORY", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORIES", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_MAX_ITEMS", 75)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_EXECUTION_MODE", "auto")

    schedule = build_koneps_collection_beat_schedule()
    assert "koneps_collection_general_periodic" in schedule

    entry = schedule["koneps_collection_general_periodic"]
    assert entry["task"] == COLLECT_KONEPS_NOTICES_TASK_NAME
    assert entry["schedule"] == 30 * 60

    payload = entry["kwargs"]["request_payload"]
    assert payload["source"] == "koneps-openapi"
    assert payload["category"] is None  # empty string normalized to None
    assert payload["max_items"] == 75
    assert payload["execution_mode"] == "auto"


def test_koneps_schedule_multi_category(monkeypatch):
    """When KONEPS_COLLECTION_CATEGORIES is set, one entry per category is produced."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_INTERVAL_MINUTES", 60)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SOURCE", "koneps-openapi")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORIES", "construction,service")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_MAX_ITEMS", 500)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_EXECUTION_MODE", "auto")

    schedule = build_koneps_collection_beat_schedule()
    assert "koneps_collection_construction_periodic" in schedule
    assert "koneps_collection_service_periodic" in schedule
    assert len(schedule) == 2

    for cat in ("construction", "service"):
        entry = schedule[f"koneps_collection_{cat}_periodic"]
        assert entry["task"] == COLLECT_KONEPS_NOTICES_TASK_NAME
        assert entry["schedule"] == 60 * 60
        payload = entry["kwargs"]["request_payload"]
        assert payload["category"] == cat
        assert payload["max_items"] == 500


def test_koneps_schedule_single_category_fallback(monkeypatch):
    """KONEPS_COLLECTION_CATEGORIES empty → falls back to KONEPS_COLLECTION_CATEGORY."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORIES", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORY", "service")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_MAX_ITEMS", 200)

    schedule = build_koneps_collection_beat_schedule()
    assert list(schedule.keys()) == ["koneps_collection_service_periodic"]
    payload = schedule["koneps_collection_service_periodic"]["kwargs"]["request_payload"]
    assert payload["category"] == "service"


def test_koneps_schedule_included_in_celery_runtime_config(monkeypatch):
    """The runtime config beat_schedule includes the koneps entry when enabled."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORIES", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORY", "")
    config = build_celery_runtime_config()
    beat = config["beat_schedule"]
    assert "koneps_collection_general_periodic" in beat


def test_koneps_schedule_minimum_interval_floor(monkeypatch):
    """Negative or zero interval is clamped to 1 minute (60 seconds)."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_INTERVAL_MINUTES", 0)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORIES", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORY", "")
    entry = build_koneps_collection_beat_schedule()["koneps_collection_general_periodic"]
    assert entry["schedule"] == 60


def test_koneps_schedule_max_items_clamped_to_crawl_request_limit(monkeypatch):
    """max_items >500 is clamped to 500 to satisfy CrawlRequest.max_items le=500."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_MAX_ITEMS", 9999)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORIES", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORY", "")
    payload = build_koneps_collection_beat_schedule()["koneps_collection_general_periodic"]["kwargs"]["request_payload"]
    assert payload["max_items"] == 500
