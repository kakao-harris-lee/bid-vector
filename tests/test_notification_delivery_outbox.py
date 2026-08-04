from datetime import timedelta

from app.core.time import utc_now
from app.models.models import Notification, NotificationDeliveryOutbox, User
from app.services.notifications.delivery_outbox import (
    NotificationDeliveryOutboxService,
)


class _RecordingNotificationService:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def _deliver_telegram_message(self, db, **kwargs):
        del db
        self.calls.append(int(kwargs["notification_id"]))


def _outbox_row(test_db, *, status: str = "pending"):
    user = User(username=f"outbox-{status}", email=f"{status}@example.com")
    test_db.add(user)
    test_db.flush()
    notification = Notification(
        user_id=user.id,
        title="title",
        message="message",
        type="recommendation",
    )
    test_db.add(notification)
    test_db.flush()
    row = NotificationDeliveryOutbox(
        notification_id=notification.id,
        operator_id=user.id,
        channel="telegram",
        payload_json={"source": "test", "message": "message"},
        status=status,
        locked_at=(utc_now() - timedelta(hours=1) if status == "running" else None),
    )
    test_db.add(row)
    test_db.commit()
    return notification, row


def test_delivery_outbox_claims_and_completes_once(test_db):
    notification, row = _outbox_row(test_db)
    recorder = _RecordingNotificationService()
    service = NotificationDeliveryOutboxService(recorder)

    result = service.process(test_db, limit=10)
    second = service.process(test_db, limit=10)

    test_db.refresh(row)
    assert result["processed_count"] == 1
    assert second["processed_count"] == 0
    assert recorder.calls == [notification.id]
    assert row.status == "completed"


def test_delivery_outbox_quarantines_ambiguous_running_without_resend(
    test_db, monkeypatch
):
    _, row = _outbox_row(test_db, status="running")
    recorder = _RecordingNotificationService()
    monkeypatch.setattr(
        "app.services.notifications.delivery_outbox.settings."
        "NOTIFICATION_DELIVERY_OUTBOX_LOCK_TIMEOUT_SECONDS",
        1,
    )

    result = NotificationDeliveryOutboxService(recorder).process(test_db, limit=10)

    test_db.refresh(row)
    assert result["ambiguous_count"] == 1
    assert recorder.calls == []
    assert row.status == "manual_review"
    assert "automatic resend suppressed" in row.last_error
