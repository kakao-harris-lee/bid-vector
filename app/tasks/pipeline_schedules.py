"""Celery names, routes, and schedules for durable pipeline outboxes."""

from app.core.config import settings

INFERENCE_OUTBOX_PROCESS_TASK_NAME = "jobs.process_inference_outbox"
SIMILARITY_PROJECTION_BACKFILL_TASK_NAME = (
    "jobs.stage_active_similarity_projection_backfill"
)
NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME = "jobs.process_notification_delivery_outbox"


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
        schedule["inference_outbox_periodic"] = {
            "task": INFERENCE_OUTBOX_PROCESS_TASK_NAME,
            "schedule": float(max(1, settings.INFERENCE_OUTBOX_INTERVAL_SECONDS)),
            "kwargs": {"limit": max(1, settings.INFERENCE_OUTBOX_BATCH_LIMIT)},
        }
    if settings.SIMILARITY_PROJECTION_BACKFILL_SCHEDULE_ENABLED:
        schedule["similarity_projection_backfill_periodic"] = {
            "task": SIMILARITY_PROJECTION_BACKFILL_TASK_NAME,
            "schedule": float(
                max(1, settings.SIMILARITY_PROJECTION_BACKFILL_INTERVAL_SECONDS)
            ),
            "kwargs": {
                "limit": max(1, settings.SIMILARITY_PROJECTION_BACKFILL_BATCH_LIMIT)
            },
        }
    if settings.NOTIFICATION_DELIVERY_OUTBOX_SCHEDULE_ENABLED:
        schedule["notification_delivery_outbox_periodic"] = {
            "task": NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME,
            "schedule": float(
                max(1, settings.NOTIFICATION_DELIVERY_OUTBOX_INTERVAL_SECONDS)
            ),
            "kwargs": {
                "limit": max(1, settings.NOTIFICATION_DELIVERY_OUTBOX_BATCH_LIMIT)
            },
        }
    return schedule
