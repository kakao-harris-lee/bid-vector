"""Notification persistence helpers for the single-operator workflow."""

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import DEFAULT_OPERATOR_USERNAME
from app.core.time import utc_now
from app.models.models import Analytics, Bid, BidDecisionRecord, Notification, Project, User
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
        operator_id = self._require_bid_decision_owner(operator_id, decision_record)
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
                reply_markup=self.telegram.build_bid_decision_reply_markup(
                    decision_record.id,
                    operator_id=operator_id,
                ),
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
        operator_id = self._require_bid_submission_owner(operator_id, bid, decision_record)
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

    def _require_bid_decision_owner(
        self,
        operator_id: int,
        decision_record: BidDecisionRecord,
    ) -> int:
        """Return a concrete operator id and reject mismatched decision owners."""
        resolved_operator_id = self._require_operator_id(operator_id)
        record_operator_id = getattr(decision_record, "operator_id", None)
        if record_operator_id is None or int(record_operator_id) != resolved_operator_id:
            raise ValueError("Notification owner does not match bid decision owner")
        return resolved_operator_id

    def _require_bid_submission_owner(
        self,
        operator_id: int,
        bid: Bid,
        decision_record: BidDecisionRecord,
    ) -> int:
        """Return a concrete operator id and reject mismatched bid/decision owners."""
        resolved_operator_id = self._require_bid_decision_owner(operator_id, decision_record)
        bid_operator_id = getattr(bid, "user_id", None)
        if bid_operator_id is None or int(bid_operator_id) != resolved_operator_id:
            raise ValueError("Notification owner does not match bid owner")
        return resolved_operator_id

    def _require_operator_id(self, operator_id: int | None) -> int:
        """Require every notification path to carry an explicit operator owner."""
        if operator_id is None:
            raise ValueError("Notification operator owner is required")
        try:
            resolved_operator_id = int(operator_id)
        except (TypeError, ValueError):
            raise ValueError("Notification operator owner is invalid") from None
        if resolved_operator_id <= 0:
            raise ValueError("Notification operator owner is invalid")
        return resolved_operator_id

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
        blocked_delivery = self._build_non_canonical_delivery_evidence(db, operator_id=operator_id)
        if blocked_delivery is not None:
            self._record_telegram_delivery(
                db,
                operator_id=operator_id,
                notification_id=notification_id,
                source=source,
                delivery=blocked_delivery,
            )
            return blocked_delivery

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

    def _build_non_canonical_delivery_evidence(
        self,
        db: Session,
        *,
        operator_id: int,
    ) -> dict[str, object] | None:
        """Skip Telegram delivery for synthetic/non-canonical operators.

        The project currently has one configured Telegram chat. Routing
        non-canonical operator notifications to it would mix callback ownership,
        so only the canonical legacy operator may actually deliver.
        """
        operator = db.query(User).filter(User.id == operator_id).first()
        if operator is None:
            return {
                "sent": False,
                "status": "blocked_missing_operator",
                "detail": "Telegram delivery skipped because the notification owner does not exist.",
            }

        username = str(operator.username or "")
        if username == DEFAULT_OPERATOR_USERNAME:
            return None

        status = (
            "skipped_synthetic_operator"
            if username.startswith("synthetic-")
            else "skipped_non_canonical_operator"
        )
        return {
            "sent": False,
            "status": status,
            "detail": "Telegram delivery is limited to the canonical operator; notification was recorded only.",
        }

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
                    "operator_id": int(operator_id),
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
