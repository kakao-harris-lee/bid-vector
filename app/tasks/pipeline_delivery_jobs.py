"""Post-commit delivery tasks for durable pipeline outboxes."""

from app.core.database import task_session
from app.services.notifications.delivery_outbox import (
    NotificationDeliveryOutboxService,
)
from app.tasks.celery_app import celery_app
from app.tasks.pipeline_schedules import NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME


@celery_app.task(name=NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME)
def process_notification_delivery_outbox(
    limit: int = 50,
) -> dict[str, int | list[int]]:
    """Deliver committed monitor notifications from the durable outbox."""
    with task_session() as db:
        return NotificationDeliveryOutboxService().process(
            db, limit=max(1, int(limit or 50))
        )
