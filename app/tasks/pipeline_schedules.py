"""Celery names, routes, and schedules for durable pipeline outboxes."""

from typing import TypedDict

from app.core.config import settings


class SweepScheduleEntry(TypedDict):
    """One celery beat entry for an idempotent periodic sweep."""

    task: str
    schedule: float
    kwargs: dict[str, int]
    options: dict[str, float]

INFERENCE_OUTBOX_PROCESS_TASK_NAME = "jobs.process_inference_outbox"
SIMILARITY_PROJECTION_BACKFILL_TASK_NAME = (
    "jobs.stage_active_similarity_projection_backfill"
)
NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME = "jobs.process_notification_delivery_outbox"


def _sweep_entry(
    task_name: str, interval_seconds: int, batch_limit: int
) -> SweepScheduleEntry:
    """One periodic sweep entry, with a lifetime on the message it publishes.

    ``expires`` is the structural cap on the backlog. beat does not stop when the
    consumer does, so without it a blocked consumer accumulates messages in
    proportion to its downtime — that is literally how 21,321 messages reached
    ``bid_vector_ml_inference`` on 2026-08-13. These tasks are idempotent sweeps,
    so a message older than a few intervals has no value left: the tick that
    superseded it already did the same work.
    """
    interval = float(max(1, interval_seconds))
    return SweepScheduleEntry(
        task=task_name,
        schedule=interval,
        kwargs={"limit": max(1, batch_limit)},
        options={
            "expires": interval
            * max(1, settings.PERIODIC_SWEEP_EXPIRY_INTERVAL_MULTIPLE)
        },
    )


def build_pipeline_task_routes():
    return {
        INFERENCE_OUTBOX_PROCESS_TASK_NAME: {
            "queue": settings.CELERY_ML_INFERENCE_QUEUE
        },
        SIMILARITY_PROJECTION_BACKFILL_TASK_NAME: {
            "queue": settings.CELERY_ML_INFERENCE_QUEUE
        },
        NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
    }


def build_pipeline_beat_schedule():
    schedule = {}
    if settings.INFERENCE_OUTBOX_SCHEDULE_ENABLED:
        schedule["inference_outbox_periodic"] = _sweep_entry(
            INFERENCE_OUTBOX_PROCESS_TASK_NAME,
            settings.INFERENCE_OUTBOX_INTERVAL_SECONDS,
            settings.INFERENCE_OUTBOX_BATCH_LIMIT,
        )
    if settings.SIMILARITY_PROJECTION_BACKFILL_SCHEDULE_ENABLED:
        schedule["similarity_projection_backfill_periodic"] = _sweep_entry(
            SIMILARITY_PROJECTION_BACKFILL_TASK_NAME,
            settings.SIMILARITY_PROJECTION_BACKFILL_INTERVAL_SECONDS,
            settings.SIMILARITY_PROJECTION_BACKFILL_BATCH_LIMIT,
        )
    if settings.NOTIFICATION_DELIVERY_OUTBOX_SCHEDULE_ENABLED:
        schedule["notification_delivery_outbox_periodic"] = _sweep_entry(
            NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME,
            settings.NOTIFICATION_DELIVERY_OUTBOX_INTERVAL_SECONDS,
            settings.NOTIFICATION_DELIVERY_OUTBOX_BATCH_LIMIT,
        )
    return schedule
