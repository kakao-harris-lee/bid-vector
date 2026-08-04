"""Claim and deliver post-commit operator notification work."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import NotificationDeliveryOutbox


class NotificationDeliveryOutboxService:
    """Deliver each claimed row once; ambiguous interrupted sends require review."""

    def __init__(self, notification_service=None):
        if notification_service is None:
            from app.services.notifications.manager import OperatorNotificationService

            notification_service = OperatorNotificationService()
        self.notification_service = notification_service

    def process(self, db: Session, *, limit: int = 50) -> dict[str, int | list[int]]:
        resolved_limit = max(1, int(limit or 50))
        ambiguous_count = self._quarantine_ambiguous_claims(db)
        processed_ids: list[int] = []
        failed_ids: list[int] = []
        for _ in range(resolved_limit):
            row = self._claim_one(db)
            if row is None:
                break
            row_id = int(row.id)
            try:
                payload = dict(row.payload_json or {})
                self.notification_service._deliver_telegram_message(
                    db,
                    operator_id=int(row.operator_id),
                    notification_id=int(row.notification_id),
                    project_id=(int(row.project_id) if row.project_id is not None else None),
                    source=str(payload.get("source") or "notification_outbox"),
                    message=str(payload.get("message") or ""),
                    reply_markup=payload.get("reply_markup"),
                )
                current = db.get(NotificationDeliveryOutbox, row_id)
                if current is None:
                    raise RuntimeError("notification delivery outbox row disappeared")
                current.status = "completed"
                current.processed_at = utc_now()
                current.last_error = None
                db.commit()
                processed_ids.append(row_id)
            except Exception as exc:
                db.rollback()
                current = db.get(NotificationDeliveryOutbox, row_id)
                if current is not None:
                    current.status = "failed"
                    current.last_error = str(exc)[:4000]
                    current.processed_at = utc_now()
                    db.commit()
                failed_ids.append(row_id)
        return {
            "processed_count": len(processed_ids),
            "failed_count": len(failed_ids),
            "ambiguous_count": ambiguous_count,
            "event_ids": processed_ids,
            "failed_event_ids": failed_ids,
        }

    def _claim_one(self, db: Session) -> NotificationDeliveryOutbox | None:
        query = (
            db.query(NotificationDeliveryOutbox)
            .filter(
                NotificationDeliveryOutbox.status == "pending",
                NotificationDeliveryOutbox.available_at <= utc_now(),
            )
            .order_by(NotificationDeliveryOutbox.id.asc())
        )
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        row = query.first()
        if row is None:
            return None
        row.status = "running"
        row.attempts = int(row.attempts or 0) + 1
        row.locked_at = utc_now()
        db.commit()
        db.refresh(row)
        return row

    def _quarantine_ambiguous_claims(self, db: Session) -> int:
        cutoff = utc_now() - timedelta(
            seconds=max(
                1, int(settings.NOTIFICATION_DELIVERY_OUTBOX_LOCK_TIMEOUT_SECONDS)
            )
        )
        rows = (
            db.query(NotificationDeliveryOutbox)
            .filter(
                NotificationDeliveryOutbox.status == "running",
                NotificationDeliveryOutbox.locked_at < cutoff,
            )
            .all()
        )
        for row in rows:
            row.status = "manual_review"
            row.last_error = (
                "delivery outcome is ambiguous after worker interruption; "
                "automatic resend suppressed"
            )
            row.processed_at = utc_now()
        if rows:
            db.commit()
        return len(rows)
