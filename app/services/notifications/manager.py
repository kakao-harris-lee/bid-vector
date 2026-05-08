"""Notification persistence helpers for the single-operator workflow."""

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import Bid, BidDecisionRecord, Notification, Project
from app.services.notifications.telegram import TelegramNotificationService

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
                message,
                reply_markup=self.telegram.build_bid_decision_reply_markup(decision_record.id),
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
        self._deliver_telegram_message(message)
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
        message: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Best-effort Telegram delivery so the web flow still succeeds on failures."""
        if not self.telegram.is_configured():
            return None

        try:
            return self.telegram.send_message(message, reply_markup=reply_markup)
        except RuntimeError as exc:
            logger.warning("Telegram delivery failed: %s", exc)
            return {
                "sent": False,
                "status": "failed",
                "detail": str(exc),
            }

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