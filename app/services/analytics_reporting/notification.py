"""Notification delivery reporting mixin."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.models import (
    Analytics,
    Notification,
)
from app.services.notifications.telegram import TelegramNotificationService


class _NotificationMixin:
    """Notification delivery summary and Telegram delivery cards."""

    def _build_notification_summary(
        self,
        db: Session,
        *,
        operator_id: int,
        date_from,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Aggregate web notification and Telegram delivery telemetry."""
        notifications = (
            db.query(Notification)
            .filter(
                Notification.user_id == operator_id,
                Notification.created_at >= date_from,
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .all()
        )
        telegram_events = (
            db.query(Analytics)
            .filter(
                Analytics.user_id == operator_id,
                Analytics.event_type == "telegram.delivery",
                Analytics.timestamp >= date_from,
            )
            .order_by(Analytics.timestamp.desc(), Analytics.id.desc())
            .all()
        )
        event_payloads = [
            {
                "event_id": int(event.id),
                "timestamp": event.timestamp,
                **self._load_event_payload(event.event_data),
            }
            for event in telegram_events
        ]
        sent_count = sum(1 for item in event_payloads if bool(item.get("sent")))
        failed_count = sum(1 for item in event_payloads if str(item.get("status") or "") == "failed")
        pending_configuration_count = sum(
            1 for item in event_payloads if str(item.get("status") or "") == "pending_configuration"
        )
        skipped_count = sum(1 for item in event_payloads if str(item.get("status") or "").startswith("skipped"))
        delivery_attempt_count = len(event_payloads)
        success_rate = self._rate(sent_count, delivery_attempt_count)
        telegram_configured = TelegramNotificationService().is_configured()
        status, detail = self._telegram_delivery_status(
            configured=telegram_configured,
            notification_count=len(notifications),
            delivery_attempt_count=delivery_attempt_count,
            sent_count=sent_count,
            failed_count=failed_count,
            pending_configuration_count=pending_configuration_count,
            success_rate=success_rate,
        )
        recent_failures = [
            item for item in event_payloads
            if str(item.get("status") or "") in {"failed", "pending_configuration"}
        ][:recent_limit]
        return {
            "notification_count": len(notifications),
            "unread_count": sum(1 for item in notifications if not bool(item.is_read)),
            "decision_notification_count": sum(1 for item in notifications if str(item.type or "") == "recommendation"),
            "bid_submission_notification_count": sum(1 for item in notifications if str(item.type or "") == "bid_update"),
            "telegram_configured": telegram_configured,
            "telegram_delivery_attempt_count": delivery_attempt_count,
            "telegram_sent_count": sent_count,
            "telegram_failed_count": failed_count,
            "telegram_pending_configuration_count": pending_configuration_count,
            "telegram_skipped_count": skipped_count,
            "telegram_success_rate": success_rate,
            "telegram_status": status,
            "telegram_detail": detail,
            "telegram_status_counts": self._count_payloads_by_key(event_payloads, "status"),
            "telegram_failure_reason_breakdown": self._reason_breakdown(
                [
                    str(item.get("detail") or "")
                    for item in event_payloads
                    if str(item.get("status") or "") in {"failed", "pending_configuration"}
                    and item.get("detail")
                ]
            ),
            "recent_telegram_failures": [
                {
                    "event_id": int(item["event_id"]),
                    "notification_id": self._optional_int(item.get("notification_id")),
                    "source": str(item.get("source") or "unknown"),
                    "status": str(item.get("status") or "unknown"),
                    "detail": str(item.get("detail") or ""),
                    "timestamp": item["timestamp"],
                }
                for item in recent_failures
            ],
        }

    @staticmethod
    def _telegram_delivery_card(
        notification_summary: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "key": "telegram_delivery_rate",
            "label": "Telegram delivery rate",
            "value": notification_summary["telegram_success_rate"],
            "unit": "ratio",
            "status": notification_summary["telegram_status"],
            "detail": notification_summary["telegram_detail"],
        }

    def _telegram_delivery_status(
        self,
        *,
        configured: bool,
        notification_count: int,
        delivery_attempt_count: int,
        sent_count: int,
        failed_count: int,
        pending_configuration_count: int,
        success_rate: float,
    ) -> tuple[str, str]:
        """Convert Telegram delivery telemetry into a dashboard status."""
        if not configured:
            if notification_count > 0 or pending_configuration_count > 0:
                return "watch", "Telegram is not configured while operator notifications are being created."
            return "info", "Telegram is not configured and no delivery attempts were recorded."
        if delivery_attempt_count == 0:
            return "info", "Telegram is configured, but no eligible delivery attempts were recorded in this window."
        if failed_count > 0 or success_rate < 0.9:
            return (
                "critical",
                f"{sent_count}/{delivery_attempt_count} Telegram delivery attempt(s) succeeded.",
            )
        if success_rate < 1.0:
            return "watch", f"{sent_count}/{delivery_attempt_count} Telegram delivery attempt(s) succeeded."
        return "healthy", f"All {delivery_attempt_count} Telegram delivery attempt(s) succeeded."
