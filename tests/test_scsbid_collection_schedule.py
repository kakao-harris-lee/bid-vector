"""KONEPS scsbid (open-bid result) collection beat schedule wiring.

Verifies the independent ``koneps_scsbid_collection_periodic`` beat entry
that re-uses the ``collect_koneps_notices`` task to drive the scsbid OpenAPI
branch of ``KonepsCollectorService`` via ``request_payload.source``.
"""

from __future__ import annotations

from app.tasks.celery_app import (
    COLLECT_KONEPS_NOTICES_TASK_NAME,
    build_celery_runtime_config,
    build_koneps_collection_beat_schedule,
    build_scsbid_collection_beat_schedule,
)


def test_scsbid_schedule_is_empty_by_default(monkeypatch):
    """Without KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED the entry is omitted."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", False)
    assert build_scsbid_collection_beat_schedule() == {}


def test_scsbid_schedule_builds_when_enabled(monkeypatch):
    """When enabled, the entry references the collect task with scsbid source."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_INTERVAL_MINUTES", 360)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SOURCE", "scsbid-openapi")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_CATEGORY", "")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_MAX_ITEMS", 50)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_EXECUTION_MODE", "auto")

    schedule = build_scsbid_collection_beat_schedule()
    assert "koneps_scsbid_collection_periodic" in schedule

    entry = schedule["koneps_scsbid_collection_periodic"]
    assert entry["task"] == COLLECT_KONEPS_NOTICES_TASK_NAME
    assert entry["schedule"] == 360 * 60

    payload = entry["kwargs"]["request_payload"]
    assert payload["source"] == "scsbid-openapi"
    assert payload["category"] is None  # empty string normalized to None
    assert payload["max_items"] == 50
    assert payload["execution_mode"] == "auto"


def test_scsbid_source_alias_is_recognized_by_collector():
    """The default scsbid source key must be a KonepsCollectorService alias.

    Without this, the beat schedule would silently fall through to the legacy
    crawl path instead of the scsbid OpenAPI branch.
    """
    from app.services.koneps.collector import KonepsCollectorService

    assert (
        "scsbid-openapi" in KonepsCollectorService.SCSBID_OPENAPI_SOURCE_ALIASES
    )


def test_scsbid_schedule_included_in_celery_runtime_config(monkeypatch):
    """The runtime config beat_schedule includes the scsbid entry when enabled."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    config = build_celery_runtime_config()
    beat = config["beat_schedule"]
    assert "koneps_scsbid_collection_periodic" in beat


def test_scsbid_schedule_minimum_interval_floor(monkeypatch):
    """Negative or zero interval is clamped to 1 minute (60 seconds)."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_INTERVAL_MINUTES", 0)
    entry = build_scsbid_collection_beat_schedule()[
        "koneps_scsbid_collection_periodic"
    ]
    assert entry["schedule"] == 60


def test_scsbid_schedule_max_items_clamped_to_crawl_request_limit(monkeypatch):
    """max_items >100 is clamped to 100 to satisfy CrawlRequest.max_items le=100."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_MAX_ITEMS", 500)
    payload = build_scsbid_collection_beat_schedule()[
        "koneps_scsbid_collection_periodic"
    ]["kwargs"]["request_payload"]
    assert payload["max_items"] == 100


def test_scsbid_schedule_invalid_execution_mode_falls_back_to_auto(monkeypatch):
    """An unrecognized execution_mode value is coerced back to 'auto'."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_COLLECTION_EXECUTION_MODE", "garbage"
    )
    payload = build_scsbid_collection_beat_schedule()[
        "koneps_scsbid_collection_periodic"
    ]["kwargs"]["request_payload"]
    assert payload["execution_mode"] == "auto"


def test_scsbid_schedule_blank_source_falls_back_to_default(monkeypatch):
    """A blank source string is normalized to the scsbid-openapi default."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SOURCE", "   ")
    payload = build_scsbid_collection_beat_schedule()[
        "koneps_scsbid_collection_periodic"
    ]["kwargs"]["request_payload"]
    assert payload["source"] == "scsbid-openapi"


def test_scsbid_and_notice_schedules_are_independent(monkeypatch):
    """Notice and scsbid schedules toggle independently; neither blocks the other."""
    from app.core.config import settings

    # Only notice ON → only the notice entry registers.
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", False)
    assert "koneps_collection_periodic" in build_koneps_collection_beat_schedule()
    assert build_scsbid_collection_beat_schedule() == {}

    # Only scsbid ON → only the scsbid entry registers.
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", False)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    assert build_koneps_collection_beat_schedule() == {}
    assert (
        "koneps_scsbid_collection_periodic"
        in build_scsbid_collection_beat_schedule()
    )

    # Both ON → both entries coexist in the merged runtime beat_schedule.
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    beat = build_celery_runtime_config()["beat_schedule"]
    assert "koneps_collection_periodic" in beat
    assert "koneps_scsbid_collection_periodic" in beat

    # And the two entries must carry distinct sources (notice vs. scsbid).
    assert (
        beat["koneps_collection_periodic"]["kwargs"]["request_payload"]["source"]
        != beat["koneps_scsbid_collection_periodic"]["kwargs"]["request_payload"][
            "source"
        ]
    )
