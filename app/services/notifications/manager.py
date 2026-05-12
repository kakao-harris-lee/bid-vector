"""Notification persistence helpers for the single-operator workflow."""

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import Analytics, Bid, BidDecisionRecord, Notification, Project
from app.services.notifications.telegram import TelegramNotificationService
from app.services.realtime import realtime_event_manager

logger = logging.getLogger(__name__)


class OperatorNotificationService:
    """Create and update operator-facing notification records and Telegram payloads."""

    DECISION_TYPE = "recommendation"
    BID_SUBMISSION_TYPE = "bid_update"

    def __init__(self) -> None:
        self.telegram = TelegramNotificationService()

    def create_bid_decision_notification(
        self,
        db: Session,
        operator_id: int,
        project: Project,
        decision_record: BidDecisionRecord,
    ) -> Notification:
        """Create or refresh a notification for a persisted bid decision."""
        message = self.telegram.build_bid_decision_message(
            project_title=project.title,
            project_id=project.id,
            action=decision_record.action,
            decision_status=decision_record.decision_status,
            priority_score=decision_record.priority_score,
            recommended_amount=decision_record.recommended_amount,
            probability_score=decision_record.probability_score,
            reasoning=decision_record.reasoning,
        )
        title = f"입찰 판단 · 프로젝트 {project.id}"
        notification = self._upsert_notification(
            db,
            operator_id=operator_id,
            title=title,
            message=message,
            notification_type=self.DECISION_TYPE,
        )

        if self.should_deliver_bid_decision_to_telegram(decision_record):
            self._deliver_telegram_message(
                db,
                operator_id=operator_id,
                notification_id=int(notification.id),
                source="bid_decision",
                message=message,
                reply_markup=self.telegram.build_bid_decision_reply_markup(decision_record.id),
            )
        realtime_event_manager.publish_event(
            "bid_decision.notification",
            {
                "notification_id": int(notification.id),
                "operator_id": int(operator_id),
                "project_id": int(project.id),
                "decision_record_id": int(decision_record.id),
                "action": decision_record.action,
                "decision_status": decision_record.decision_status,
                "priority_score": float(decision_record.priority_score or 0.0),
                "probability_score": float(decision_record.probability_score or 0.0),
                "recommended_amount": float(decision_record.recommended_amount or 0.0),
                "title": notification.title,
                "type": notification.type,
            },
        )
        return notification

    def create_bid_submission_notification(
        self,
        db: Session,
        operator_id: int,
        project: Project,
        bid: Bid,
        decision_record: BidDecisionRecord,
    ) -> Notification:
        """Create or refresh a notification for an actual bid submission."""
        message = self.telegram.build_bid_submission_message(
            project_title=project.title,
            project_id=project.id,
            bid_amount=bid.bid_amount,
            decision_status=decision_record.decision_status,
            reasoning=decision_record.reasoning,
        )
        title = f"투찰 완료 · 프로젝트 {project.id}"
        notification = self._upsert_notification(
            db,
            operator_id=operator_id,
            title=title,
            message=message,
            notification_type=self.BID_SUBMISSION_TYPE,
        )
        self._deliver_telegram_message(
            db,
            operator_id=operator_id,
            notification_id=int(notification.id),
            source="bid_submission",
            message=message,
        )
        realtime_event_manager.publish_event(
            "bid_submission.notification",
            {
                "notification_id": int(notification.id),
                "operator_id": int(operator_id),
                "project_id": int(project.id),
                "bid_id": int(bid.id),
                "decision_record_id": int(decision_record.id),
                "bid_amount": float(bid.bid_amount or 0.0),
                "decision_status": decision_record.decision_status,
                "title": notification.title,
                "type": notification.type,
            },
        )
        return notification

    def should_deliver_bid_decision_to_telegram(self, decision_record: BidDecisionRecord) -> bool:
        """Limit Telegram decision alerts to high-priority, immediately actionable opportunities."""
        return bool(
            decision_record.action == "bid_now"
            and decision_record.decision_status == "planned"
            and decision_record.priority_score >= settings.TELEGRAM_DECISION_PRIORITY_THRESHOLD
            and decision_record.probability_score >= settings.TELEGRAM_DECISION_PROBABILITY_THRESHOLD
        )

    def _deliver_telegram_message(
        self,
        db: Session,
        *,
        operator_id: int,
        notification_id: int,
        source: str,
        message: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Best-effort Telegram delivery so the web flow still succeeds on failures."""
        if not self.telegram.is_configured():
            delivery = {
                "sent": False,
                "status": "pending_configuration",
                "detail": "Telegram is not configured yet.",
            }
            self._record_telegram_delivery(
                db,
                operator_id=operator_id,
                notification_id=notification_id,
                source=source,
                delivery=delivery,
            )
            return delivery

        try:
            delivery = self.telegram.send_message(message, reply_markup=reply_markup)
        except RuntimeError as exc:
            logger.warning("Telegram delivery failed: %s", exc)
            delivery = {
                "sent": False,
                "status": "failed",
                "detail": str(exc),
            }
        self._record_telegram_delivery(
            db,
            operator_id=operator_id,
            notification_id=notification_id,
            source=source,
            delivery=delivery,
        )
        return delivery

    def _record_telegram_delivery(
        self,
        db: Session,
        *,
        operator_id: int,
        notification_id: int,
        source: str,
        delivery: dict[str, object],
    ) -> None:
        """Persist Telegram delivery telemetry for operations dashboard reporting."""
        event = Analytics(
            user_id=operator_id,
            event_type="telegram.delivery",
            event_data=json.dumps(
                {
                    "notification_id": int(notification_id),
                    "source": source,
                    "sent": bool(delivery.get("sent")),
                    "status": str(delivery.get("status") or "unknown"),
                    "detail": str(delivery.get("detail") or ""),
                    "telegram_message_id": delivery.get("telegram_message_id"),
                },
                ensure_ascii=False,
            ),
        )
        db.add(event)
        db.commit()

    def _upsert_notification(
        self,
        db: Session,
        operator_id: int,
        title: str,
        message: str,
        notification_type: str,
    ) -> Notification:
        """Reuse the latest unread notification with the same title/type to reduce noise."""
        notification = (
            db.query(Notification)
            .filter(
                Notification.user_id == operator_id,
                Notification.title == title,
                Notification.type == notification_type,
                Notification.is_read.is_(False),
            )
            .order_by(Notification.id.desc())
            .first()
        )

        if notification is None:
            notification = Notification(
                user_id=operator_id,
                title=title,
                message=message,
                type=notification_type,
                is_read=False,
            )
            db.add(notification)
        else:
            notification.message = message
            notification.is_read = False
            notification.read_at = None

        db.commit()
        db.refresh(notification)
        return notification

    def mark_as_read(self, db: Session, notification: Notification) -> Notification:
        """Mark a notification as read for the web dashboard."""
        notification.is_read = True
        notification.read_at = utc_now()
        db.commit()
        db.refresh(notification)
        return notification
