"""Notification persistence helpers for the single-operator workflow."""

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import DEFAULT_OPERATOR_USERNAME
from app.core.time import utc_now
from app.models.models import (
    Analytics,
    Bid,
    BidDecisionRecord,
    Notification,
    OperatorNotificationChannel,
    Project,
    User,
)
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
        delivery_plan = self.build_telegram_delivery_plan(db, operator_id=operator_id)
        if not bool(delivery_plan.get("can_send")):
            blocked_delivery = self._build_blocked_telegram_delivery(delivery_plan)
            self._record_telegram_delivery(
                db,
                operator_id=operator_id,
                notification_id=notification_id,
                source=source,
                delivery=blocked_delivery,
            )
            return blocked_delivery

        if not self.telegram.is_configured():
            delivery = self._with_telegram_channel_metadata(
                {
                    "sent": False,
                    "status": "pending_configuration",
                    "detail": "Telegram is not configured yet.",
                },
                delivery_plan,
            )
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
        delivery = self._with_telegram_channel_metadata(delivery, delivery_plan)
        self._record_telegram_delivery(
            db,
            operator_id=operator_id,
            notification_id=notification_id,
            source=source,
            delivery=delivery,
        )
        return delivery

    def build_telegram_delivery_plan(
        self,
        db: Session,
        *,
        operator_id: int,
    ) -> dict[str, object]:
        """Resolve the per-operator Telegram route without exposing raw targets."""
        operator = db.query(User).filter(User.id == operator_id).first()
        base: dict[str, object] = {
            "operator_id": int(operator_id),
            "channel_type": "telegram",
            "channel_id": None,
            "route_key": f"operator:{int(operator_id)}:telegram:unconfigured",
            "target_label": None,
            "channel_source": "missing_channel",
            "channel_active": False,
            "dry_run_only": True,
            "can_send": False,
        }
        if operator is None:
            return {
                **base,
                "status": "blocked_missing_operator",
                "detail": "Telegram delivery skipped because the notification owner does not exist.",
            }

        username = str(operator.username or "")
        channel = self._select_telegram_channel(db, operator_id=int(operator_id))
        if channel is not None:
            plan = {
                **base,
                "channel_id": int(channel.id),
                "route_key": str(channel.route_key),
                "target_label": channel.target_label,
                "channel_source": "operator_notification_channels",
                "channel_active": bool(channel.is_active),
                "dry_run_only": bool(channel.dry_run_only),
            }
            if not bool(channel.is_active):
                return {
                    **plan,
                    "status": "telegram_channel_inactive",
                    "detail": "Telegram delivery skipped because the operator channel is inactive.",
                }
            if bool(channel.dry_run_only):
                return {
                    **plan,
                    "status": "telegram_channel_dry_run",
                    "detail": "Telegram delivery recorded as dry-run evidence for this operator channel.",
                }
            if username != DEFAULT_OPERATOR_USERNAME:
                return {
                    **plan,
                    "status": "telegram_route_non_canonical",
                    "detail": "Telegram delivery skipped because non-canonical operator routes require an explicit secret resolver.",
                }
            if channel.route_key != self.telegram.LEGACY_CONFIGURED_CHAT_ROUTE_KEY:
                return {
                    **plan,
                    "status": "telegram_route_unresolved",
                    "detail": "Telegram delivery skipped because the channel route key has no configured sender.",
                }
            return {
                **plan,
                "target_label": channel.target_label or self.telegram.get_configured_target_label(),
                "can_send": True,
                "status": "ready",
                "detail": "Telegram delivery route is active.",
            }

        if username == DEFAULT_OPERATOR_USERNAME:
            return {
                **base,
                "route_key": self.telegram.LEGACY_CONFIGURED_CHAT_ROUTE_KEY,
                "target_label": self.telegram.get_configured_target_label(),
                "channel_source": "legacy_settings",
                "channel_active": True,
                "dry_run_only": False,
                "can_send": True,
                "status": "ready",
                "detail": "Telegram delivery route uses the legacy configured chat.",
            }

        status = (
            "skipped_synthetic_operator"
            if username.startswith("synthetic-")
            else "skipped_non_canonical_operator"
        )
        return {
            **base,
            "status": status,
            "detail": "Telegram delivery skipped because this operator has no active Telegram channel.",
        }

    def has_active_telegram_callback_route(self, db: Session, *, operator_id: int) -> bool:
        """Return whether bid-decision callbacks may mutate this operator's records."""
        plan = self.build_telegram_delivery_plan(db, operator_id=operator_id)
        return bool(plan.get("can_send"))

    def _select_telegram_channel(
        self,
        db: Session,
        *,
        operator_id: int,
    ) -> OperatorNotificationChannel | None:
        return (
            db.query(OperatorNotificationChannel)
            .filter(
                OperatorNotificationChannel.operator_id == int(operator_id),
                OperatorNotificationChannel.channel_type == "telegram",
            )
            .order_by(
                OperatorNotificationChannel.is_active.desc(),
                OperatorNotificationChannel.dry_run_only.asc(),
                OperatorNotificationChannel.id.desc(),
            )
            .first()
        )

    def _build_blocked_telegram_delivery(
        self,
        delivery_plan: dict[str, object],
    ) -> dict[str, object]:
        return self._with_telegram_channel_metadata(
            {
                "sent": False,
                "status": str(delivery_plan.get("status") or "telegram_delivery_blocked"),
                "detail": str(delivery_plan.get("detail") or "Telegram delivery was not sent."),
            },
            delivery_plan,
        )

    def _with_telegram_channel_metadata(
        self,
        delivery: dict[str, object],
        delivery_plan: dict[str, object],
    ) -> dict[str, object]:
        enriched = dict(delivery)
        enriched.update(
            {
                "channel_type": delivery_plan.get("channel_type"),
                "channel_id": delivery_plan.get("channel_id"),
                "route_key": delivery_plan.get("route_key"),
                "target_label": delivery_plan.get("target_label"),
                "channel_source": delivery_plan.get("channel_source"),
                "channel_active": bool(delivery_plan.get("channel_active")),
                "dry_run_only": bool(delivery_plan.get("dry_run_only")),
            }
        )
        return enriched

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
                    "channel_type": delivery.get("channel_type"),
                    "channel_id": delivery.get("channel_id"),
                    "route_key": delivery.get("route_key"),
                    "target_label": delivery.get("target_label"),
                    "channel_source": delivery.get("channel_source"),
                    "channel_active": bool(delivery.get("channel_active")),
                    "dry_run_only": bool(delivery.get("dry_run_only")),
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
