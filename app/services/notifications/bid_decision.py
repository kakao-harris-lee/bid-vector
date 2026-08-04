"""Bid-decision notification persistence and delivery staging."""

from sqlalchemy.orm import Session

from app.models.models import (
    BidDecisionRecord,
    Notification,
    NotificationDeliveryOutbox,
    Project,
)
from app.services.realtime import realtime_event_manager


class BidDecisionNotificationMixin:
    """Create a web notification and route Telegram work at the commit boundary."""

    def create_bid_decision_notification(
        self,
        db: Session,
        operator_id: int,
        project: Project,
        decision_record: BidDecisionRecord,
    ) -> Notification:
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
        notification = self._upsert_notification(
            db,
            operator_id=operator_id,
            title=f"입찰 판단 · 프로젝트 {project.id}",
            message=message,
            notification_type=self.DECISION_TYPE,
            project_id=int(project.id),
            decision_record_id=int(decision_record.id),
        )
        self._route_bid_decision_delivery(
            db, operator_id, project, decision_record, notification, message
        )
        if not self.defer_commit:
            self._publish_bid_decision_notification(
                operator_id, project, decision_record, notification
            )
        return notification

    def _route_bid_decision_delivery(
        self, db, operator_id, project, decision, notification, message
    ) -> None:
        if not self.should_deliver_bid_decision_to_telegram(decision):
            return
        if self.defer_delivery:
            self._stage_bid_decision_delivery(
                db,
                operator_id=operator_id,
                project_id=int(project.id),
                notification=notification,
                message=message,
                decision_record=decision,
            )
            return
        self._deliver_bid_decision_to_telegram(
            db,
            operator_id=operator_id,
            project_id=int(project.id),
            notification_id=int(notification.id),
            message=message,
            decision_record=decision,
        )

    def _stage_bid_decision_delivery(
        self,
        db: Session,
        *,
        operator_id: int,
        project_id: int,
        notification: Notification,
        message: str,
        decision_record: BidDecisionRecord,
    ) -> NotificationDeliveryOutbox:
        row = (
            db.query(NotificationDeliveryOutbox)
            .filter(
                NotificationDeliveryOutbox.notification_id == int(notification.id),
                NotificationDeliveryOutbox.channel == "telegram",
            )
            .first()
        )
        payload = {
            "source": "bid_decision",
            "message": message,
            "reply_markup": self.telegram.build_bid_decision_reply_markup(
                decision_record.id, operator_id=operator_id
            ),
        }
        if row is None:
            row = NotificationDeliveryOutbox(
                notification_id=int(notification.id),
                monitor_run_id=self.monitor_run_id,
                operator_id=operator_id,
                project_id=project_id,
                decision_record_id=int(decision_record.id),
                channel="telegram",
                payload_json=payload,
                status="pending",
            )
            db.add(row)
        else:
            row.payload_json = payload
            if row.status == "failed":
                row.status = "pending"
                row.last_error = None
        db.flush()
        return row

    def _publish_bid_decision_notification(
        self, operator_id, project, decision, notification
    ) -> None:
        realtime_event_manager.publish_event(
            "bid_decision.notification",
            {
                "notification_id": int(notification.id),
                "operator_id": int(operator_id),
                "project_id": int(project.id),
                "decision_record_id": int(decision.id),
                "action": decision.action,
                "decision_status": decision.decision_status,
                "priority_score": float(decision.priority_score or 0.0),
                "probability_score": float(decision.probability_score or 0.0),
                "matched_score": float(decision.matched_score or 0.0),
                "recommended_amount": float(decision.recommended_amount or 0.0),
                "title": notification.title,
                "type": notification.type,
            },
        )
