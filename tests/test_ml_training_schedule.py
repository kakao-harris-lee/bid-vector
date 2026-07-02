"""Price-predictor ML training weekly beat schedule wiring.

Verifies the ``price_predictor_training_weekly`` beat entry that schedules the
``ml.train_price_predictor`` task once per week. The ``beat`` compose service
schedules it; the ``training-worker`` compose service executes it via the ML
training queue. Default OFF; opt-in via
``PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED``.
"""

from __future__ import annotations

from app.tasks.celery_app import (
    PRICE_PREDICTOR_TRAINING_TASK_NAME,
    build_celery_runtime_config,
    build_price_predictor_training_beat_schedule,
    build_task_routes,
)


def test_ml_training_schedule_is_empty_by_default(monkeypatch):
    """Without PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED the entry is omitted."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", False)
    assert build_price_predictor_training_beat_schedule() == {}


def test_ml_training_schedule_absent_from_runtime_config_by_default(monkeypatch):
    """The runtime beat_schedule omits the entry while disabled."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", False)
    beat = build_celery_runtime_config()["beat_schedule"]
    assert "price_predictor_training_weekly" not in beat


def test_ml_training_schedule_builds_when_enabled(monkeypatch):
    """When enabled, the entry references the training task on a weekly crontab."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_DAY_OF_WEEK", 0)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_HOUR_KST", 18)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_MINUTE", 0)

    schedule = build_price_predictor_training_beat_schedule()
    assert "price_predictor_training_weekly" in schedule

    entry = schedule["price_predictor_training_weekly"]
    assert entry["task"] == PRICE_PREDICTOR_TRAINING_TASK_NAME
    assert entry["task"] == "ml.train_price_predictor"

    crontab = entry["schedule"]
    # crontab stores its fields as sets (both real Celery and the test shim).
    assert crontab.day_of_week == {0}
    assert crontab.hour == {18}
    assert crontab.minute == {0}


def test_ml_training_schedule_adds_category_scoped_candidate_runs(monkeypatch):
    """Weekly retrain should also build category-scoped candidate artifacts."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "PRICE_PREDICTOR_TRAINING_SCHEDULE_CATEGORIES",
        "construction,service,goods",
    )

    schedule = build_price_predictor_training_beat_schedule()

    for category in ("construction", "service", "goods"):
        entry = schedule[f"price_predictor_training_weekly_{category}"]
        assert entry["task"] == PRICE_PREDICTOR_TRAINING_TASK_NAME
        payload = entry["kwargs"]["request_payload"]
        assert payload["category"] == category
        assert payload["create_manifest"] is False
        assert payload["publish_remote"] is False


def test_ml_training_schedule_is_candidate_only(monkeypatch):
    """Safety invariant: the scheduled run never creates/publishes/activates a release.

    The weekly job must pass create_manifest=False and publish_remote=False so it
    only retrains candidate artifacts; release/promotion stays manual & gated
    (scripts/promote_ml_release.py). Pins the no-auto-promotion guarantee against
    future drift in the scheduled payload.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True)
    entry = build_price_predictor_training_beat_schedule()[
        "price_predictor_training_weekly"
    ]
    payload = entry["kwargs"]["request_payload"]
    assert payload["create_manifest"] is False
    assert payload["publish_remote"] is False


def test_ml_training_schedule_clamps_out_of_range_values(monkeypatch):
    """Out-of-range day/hour/minute inputs are clamped to valid crontab ranges."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_DAY_OF_WEEK", 99)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_HOUR_KST", 99)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_MINUTE", 99)

    crontab = build_price_predictor_training_beat_schedule()[
        "price_predictor_training_weekly"
    ]["schedule"]
    assert crontab.day_of_week == {6}
    assert crontab.hour == {23}
    assert crontab.minute == {59}


def test_ml_training_schedule_clamps_negative_values(monkeypatch):
    """Negative day/hour/minute inputs are floored to 0."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_DAY_OF_WEEK", -5)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_HOUR_KST", -1)
    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_MINUTE", -1)

    crontab = build_price_predictor_training_beat_schedule()[
        "price_predictor_training_weekly"
    ]["schedule"]
    assert crontab.day_of_week == {0}
    assert crontab.hour == {0}
    assert crontab.minute == {0}


def test_ml_training_schedule_included_in_celery_runtime_config(monkeypatch):
    """The runtime config beat_schedule includes the entry when enabled."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True)
    beat = build_celery_runtime_config()["beat_schedule"]
    assert "price_predictor_training_weekly" in beat
    assert (
        beat["price_predictor_training_weekly"]["task"]
        == PRICE_PREDICTOR_TRAINING_TASK_NAME
    )


def test_ml_training_schedule_independent_of_other_schedules(monkeypatch):
    """Enabling ML training must not pull in unrelated schedules."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "SMOKE_TEST_SCHEDULE_ENABLED", False)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", False)
    beat = build_celery_runtime_config()["beat_schedule"]
    assert "price_predictor_training_weekly" in beat
    assert "smoke_test_daily" not in beat
    assert "telegram_polling_periodic" not in beat


def test_ml_training_task_routes_to_ml_training_queue():
    """The training task stays routed onto the dedicated ML training queue."""
    from app.core.config import settings

    routes = build_task_routes()
    assert routes[PRICE_PREDICTOR_TRAINING_TASK_NAME]["queue"] == (
        settings.CELERY_ML_TRAINING_QUEUE
    )
