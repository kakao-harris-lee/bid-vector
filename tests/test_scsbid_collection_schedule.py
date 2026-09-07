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
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_EXECUTION_MODE", "auto")

    schedule = build_scsbid_collection_beat_schedule()
    assert "koneps_scsbid_collection_periodic" in schedule

    entry = schedule["koneps_scsbid_collection_periodic"]
    assert entry["task"] == COLLECT_KONEPS_NOTICES_TASK_NAME
    assert entry["schedule"] == 360 * 60

    payload = entry["kwargs"]["request_payload"]
    assert payload["source"] == "scsbid-openapi"
    assert payload["category"] is None  # empty string normalized to None
    assert payload["execution_mode"] == "auto"
    # sweep 상한은 요청이 아니라 설정에서 읽는다 — payload 에 실으면 스키마 상한
    # (le=500)에 눌려 sweep 이 잘린다(2026-08-04 회귀).
    assert "max_items" not in payload


def test_scsbid_schedule_passes_forward_coverage_payload(monkeypatch):
    """The beat payload carries the multi-category date-window sweep params."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(
        settings, "KONEPS_SCSBID_COLLECTION_CATEGORIES", "construction,service,goods"
    )
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_LOOKBACK_DAYS", 3)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_PAGE_SIZE", 100)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_MAX_PAGES", 30)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_RESERVE_DETAIL", True)

    payload = build_scsbid_collection_beat_schedule()[
        "koneps_scsbid_collection_periodic"
    ]["kwargs"]["request_payload"]

    assert payload["categories"] == ["construction", "service", "goods"]
    assert payload["lookback_days"] == 3
    assert payload["page_size"] == 100
    assert payload["max_pages"] == 30
    assert payload["collect_reserve_detail"] is True


def test_scsbid_schedule_clamps_page_size_to_openapi_limit(monkeypatch):
    """page_size above 999 is clamped to satisfy the OpenAPI numOfRows ceiling."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_PAGE_SIZE", 5000)
    payload = build_scsbid_collection_beat_schedule()[
        "koneps_scsbid_collection_periodic"
    ]["kwargs"]["request_payload"]
    assert payload["page_size"] == 999


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


def test_scsbid_schedule_never_carries_max_items_in_the_payload(monkeypatch):
    """설정값이 무엇이든 beat payload 는 max_items 를 싣지 않는다.

    sweep 은 ``KONEPS_SCSBID_COLLECTION_MAX_ITEMS`` 를 직접 읽는다. payload 로
    실어 보내면 ``CrawlRequest.max_items`` 스키마 상한에 눌린 값이 sweep 상한이
    되어 카테고리 전체를 돌지 못한다(2026-08-04 회귀).
    """
    from app.core.config import settings
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_MAX_ITEMS", 500)
    payload = build_scsbid_collection_beat_schedule()[
        "koneps_scsbid_collection_periodic"
    ]["kwargs"]["request_payload"]
    assert "max_items" not in payload


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
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORIES", "")
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_CATEGORY", "")
    monkeypatch.setattr(settings, "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED", False)
    notice_schedule = build_koneps_collection_beat_schedule()
    assert len(notice_schedule) >= 1  # at least one notice entry produced
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
    # With no category set, the notice schedule uses the "general" slug key
    notice_keys = [k for k in beat if k.startswith("koneps_collection_") and not k.startswith("koneps_collection_scsbid")]
    assert len(notice_keys) >= 1
    assert "koneps_scsbid_collection_periodic" in beat

    # The notice and scsbid entries must carry distinct sources.
    notice_source = beat[notice_keys[0]]["kwargs"]["request_payload"]["source"]
    scsbid_source = beat["koneps_scsbid_collection_periodic"]["kwargs"]["request_payload"]["source"]
    assert notice_source != scsbid_source
