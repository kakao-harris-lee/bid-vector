"""Notification persistence helpers for the single-operator workflow."""

import logging
import re

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import NON_DELIVERING_ENVIRONMENTS, TELEGRAM_DELIVERY_EVENT_TYPE
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
from app.schemas.analytics_events import TelegramDeliveryEvent
from app.services.analytics_event_payload import dump_analytics_event
from app.services.notifications.fatigue_gate import (
    NotificationFatigueGate,
    record_fatigue_suppression,
)
from app.services.notifications.telegram import TelegramNotificationService
from app.services.notifications.telegram_delivery_plan import (
    DETAIL_TRANSPORT_CONTRACT_FAILURE,
    TelegramChannelFacts,
    TelegramDeliveryPlan,
    TelegramRouteContext,
    TelegramSendOutcome,
    blocked_send_outcome,
    build_telegram_delivery_event,
    failed_send_outcome,
    pending_configuration_outcome,
    resolve_telegram_delivery_plan,
)
from app.services.realtime import realtime_event_manager

logger = logging.getLogger(__name__)

_SAFE_OPERATOR_TELEGRAM_ROUTE_RE = re.compile(r"^operator:\d+:telegram:unconfigured$")
_SAFE_NAMED_ROUTE_RE = re.compile(r"^(?:telegram|app):[a-z][a-z0-9_-]{0,80}$")
_NUMERIC_TARGET_RE = re.compile(r"(?<!\d)-?\d{5,}(?!\d)")
_BRACKETED_TOKEN_RE = re.compile(r"(?i)(ExponentPushToken)\[([^\]]{8,})\]")
_KEYED_SECRET_RE = re.compile(
    r"(?i)\b(chat_id|device|device_token|target|token|secret)([=:]\s*)([^\s,;]{5,})"
)
_TOKENISH_TARGET_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9_.:-]{19,}[A-Za-z0-9])(?![A-Za-z0-9])"
)


def _mask_secret_fragment(value: str) -> str:
    """Mask one target-like fragment while keeping the last four chars for evidence."""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def mask_notification_target(value: object | None) -> str | None:
    """Return API/telemetry-safe notification target text.

    Operators may accidentally save a raw Telegram chat id, device token, or
    secret target in ``target_label``. Treat labels as untrusted and mask
    target-shaped fragments before they leave the service boundary.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    masked = _NUMERIC_TARGET_RE.sub(
        lambda match: _mask_secret_fragment(match.group(0)),
        text,
    )
    masked = _BRACKETED_TOKEN_RE.sub(
        lambda match: f"{match.group(1)}[{_mask_secret_fragment(match.group(2))}]",
        masked,
    )
    masked = _KEYED_SECRET_RE.sub(
        lambda match: (
            match.group(0)
            if "*" in match.group(3)
            else f"{match.group(1)}{match.group(2)}{_mask_secret_fragment(match.group(3))}"
        ),
        masked,
    )
    return _TOKENISH_TARGET_RE.sub(
        lambda match: _mask_secret_fragment(match.group(1)),
        masked,
    )


def mask_notification_route_key(value: object | None) -> str:
    """Return a route key suitable for evidence without leaking secret targets."""
    text = str(value or "").strip()
    if not text:
        return ""
    if (
        text == TelegramNotificationService.LEGACY_CONFIGURED_CHAT_ROUTE_KEY
        or _SAFE_OPERATOR_TELEGRAM_ROUTE_RE.match(text)
        or _SAFE_NAMED_ROUTE_RE.match(text)
    ):
        return text
    return mask_notification_target(text) or ""


class OperatorNotificationService:
    """Create and update operator-facing notification records and Telegram payloads."""

    DECISION_TYPE = "recommendation"
    BID_SUBMISSION_TYPE = "bid_update"

    def __init__(self, *, fatigue_gate: NotificationFatigueGate | None = None) -> None:
        self.telegram = TelegramNotificationService()
        self.fatigue_gate = fatigue_gate or NotificationFatigueGate()

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
            self._deliver_bid_decision_to_telegram(
                db,
                operator_id=operator_id,
                project_id=int(project.id),
                notification_id=int(notification.id),
                message=message,
                decision_record=decision_record,
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
            project_id=int(project.id),
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

    def _deliver_bid_decision_to_telegram(
        self,
        db: Session,
        *,
        operator_id: int,
        project_id: int,
        notification_id: int,
        message: str,
        decision_record: BidDecisionRecord,
    ) -> None:
        """Apply the fatigue budget to an alert the value gate already approved.

        The web notification is created either way — suppression only withholds
        the Telegram push, and leaves an audit row explaining why.
        """
        fatigue_decision = self.fatigue_gate.evaluate(
            db,
            operator_id=operator_id,
            project_id=project_id,
        )
        if not fatigue_decision.allowed:
            record_fatigue_suppression(
                db,
                operator_id=operator_id,
                notification_id=notification_id,
                project_id=project_id,
                source="bid_decision",
                decision=fatigue_decision,
            )
            return
        self._deliver_telegram_message(
            db,
            operator_id=operator_id,
            notification_id=notification_id,
            project_id=project_id,
            source="bid_decision",
            message=message,
            reply_markup=self.telegram.build_bid_decision_reply_markup(
                decision_record.id,
                operator_id=operator_id,
            ),
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
        project_id: int | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramDeliveryEvent:
        """Best-effort Telegram delivery so the web flow still succeeds on failures."""
        plan = self.build_telegram_delivery_plan(db, operator_id=operator_id)
        outcome = self._attempt_telegram_send(plan, message, reply_markup=reply_markup)
        return self._record_telegram_delivery(
            db,
            operator_id=operator_id,
            notification_id=notification_id,
            project_id=project_id,
            source=source,
            plan=plan,
            outcome=outcome,
        )

    def _attempt_telegram_send(
        self,
        plan: TelegramDeliveryPlan,
        message: str,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> TelegramSendOutcome:
        """Send when the resolved route allows it, else report why it was withheld.

        ``send_message`` still answers with an untyped dict (D2a transport
        contract), so this is where that dict is promoted to a validated outcome
        instead of being relayed into the audit record as-is. The promotion stays
        INSIDE the best-effort boundary: a transport response that does not match
        the outcome contract is recorded as a failed delivery, exactly like a
        ``RuntimeError``, so the web notification flow still succeeds.
        """
        if not plan.route_send_allowed:
            return blocked_send_outcome(plan)
        if not self.telegram.is_configured():
            return pending_configuration_outcome()
        try:
            sent = self.telegram.send_message(message, reply_markup=reply_markup)
        except RuntimeError as exc:
            logger.warning("Telegram delivery failed: %s", exc)
            return failed_send_outcome(str(exc))
        try:
            return TelegramSendOutcome.model_validate(sent)
        except ValidationError as exc:
            # 검증 오류 원문은 입력값을 되풀이하므로 감사 detail 로 남기지 않는다.
            logger.warning(
                "Telegram transport response broke the delivery outcome contract "
                "(errors=%d)",
                exc.error_count(),
            )
            return failed_send_outcome(DETAIL_TRANSPORT_CONTRACT_FAILURE)

    def build_telegram_delivery_plan(
        self,
        db: Session,
        *,
        operator_id: int,
    ) -> TelegramDeliveryPlan:
        """Resolve the per-operator Telegram route without exposing raw targets.

        This is the I/O half only: it reads the operator and channel rows, masks
        target-shaped values, and hands those facts to the pure resolver in
        ``telegram_delivery_plan`` (§4.7-1/4).
        """
        operator = db.query(User).filter(User.id == operator_id).first()
        username = str(getattr(operator, "username", "") or "")
        context = TelegramRouteContext(
            operator_id=int(operator_id),
            operator_exists=operator is not None,
            is_canonical_operator=username == DEFAULT_OPERATOR_USERNAME,
            is_synthetic_operator=username.startswith("synthetic-"),
            telegram_configured=self.telegram.is_configured(),
            configured_route_key=self.telegram.LEGACY_CONFIGURED_CHAT_ROUTE_KEY,
            configured_target_label=self.telegram.get_configured_target_label(),
            can_send_when_allowed=self._can_actually_send_telegram(
                route_send_allowed=True
            ),
        )
        channel_facts = (
            None
            if operator is None
            else self._telegram_channel_facts(db, operator_id=int(operator_id))
        )
        return resolve_telegram_delivery_plan(context=context, channel=channel_facts)

    def _telegram_channel_facts(
        self,
        db: Session,
        *,
        operator_id: int,
    ) -> TelegramChannelFacts | None:
        """Mask one operator channel row down to the facts the resolver needs."""
        channel = self._select_telegram_channel(db, operator_id=operator_id)
        if channel is None:
            return None
        raw_route_key = str(channel.route_key)
        return TelegramChannelFacts(
            channel_id=int(channel.id),
            route_key=mask_notification_route_key(raw_route_key),
            target_label=mask_notification_target(channel.target_label),
            is_active=bool(channel.is_active),
            dry_run_only=bool(channel.dry_run_only),
            matches_configured_sender=(
                raw_route_key == self.telegram.LEGACY_CONFIGURED_CHAT_ROUTE_KEY
            ),
        )

    def has_active_telegram_callback_route(self, db: Session, *, operator_id: int) -> bool:
        """Return whether bid-decision callbacks may mutate this operator's records."""
        return self.build_telegram_delivery_plan(
            db, operator_id=operator_id
        ).route_send_allowed

    def _can_actually_send_telegram(self, *, route_send_allowed: bool) -> bool:
        """Return whether this process may perform a real Telegram send now.

        환경 판정은 인라인 스니핑이 아니라 선언 데이터(``NON_DELIVERING_ENVIRONMENTS``)
        멤버십이다 — transport 선택과 같은 집합을 봐야 게이트와 transport 가 갈라지지 않는다.
        """
        return bool(
            route_send_allowed
            and self.telegram.is_configured()
            and settings.ENVIRONMENT not in NON_DELIVERING_ENVIRONMENTS
        )

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

    def _record_telegram_delivery(
        self,
        db: Session,
        *,
        operator_id: int,
        notification_id: int,
        source: str,
        plan: TelegramDeliveryPlan,
        outcome: TelegramSendOutcome,
        project_id: int | None = None,
    ) -> TelegramDeliveryEvent:
        """Persist Telegram delivery telemetry for operations dashboard reporting.

        The stored payload is a declared contract
        (:class:`~app.schemas.analytics_events.TelegramDeliveryEvent`) because the
        fatigue gate reads it back as a *decision* input, not as free-form
        telemetry.
        """
        delivery_event = build_telegram_delivery_event(
            plan=plan,
            outcome=outcome,
            notification_id=notification_id,
            source=source,
            project_id=project_id,
        )
        event = Analytics(
            user_id=operator_id,
            event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
            event_data=dump_analytics_event(delivery_event),
        )
        db.add(event)
        db.commit()
        return delivery_event

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
