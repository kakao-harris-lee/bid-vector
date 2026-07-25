"""Delivery-history boundary for the notification fatigue core (§4.7.1/3).

The pure rules live in :mod:`app.services.notifications.fatigue`; this module is
the thin I/O half that answers the two questions those rules need: how many
decision alerts this operator actually received today (KST), and when this
notice was last announced.

Both answers come from the ``analytics`` rows the delivery path already writes
(``telegram.delivery``), so no schema change is involved. Only deliveries with
``sent: true`` count — a dry-run or route-blocked attempt puts no message in
front of the operator, so it must not consume the operator's alert budget.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    TELEGRAM_DELIVERY_EVENT_TYPE,
    TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
)
from app.core.time import ensure_utc, kst_day_bounds_utc, utc_now
from app.models.models import Analytics
from app.services.notifications.fatigue import (
    FatigueDecision,
    FatigueSignals,
    NotificationFatigueLimits,
    evaluate_notification_fatigue,
)

logger = logging.getLogger(__name__)

# Delivery telemetry rows carry the originating flow in ``source``; only bid
# decision alerts are subject to the fatigue budget (a bid submission receipt is
# a confirmation of the operator's own action, not proactive noise).
DECISION_DELIVERY_SOURCE = "bid_decision"


def _load_event_payload(raw_payload: object) -> dict:
    """Parse an analytics event payload, treating unusable data as empty."""
    if isinstance(raw_payload, dict):
        return raw_payload
    try:
        payload = json.loads(str(raw_payload or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


class NotificationFatigueGate:
    """Resolve the fatigue verdict for one pending decision alert."""

    def __init__(
        self,
        *,
        limits: NotificationFatigueLimits | None = None,
        now_provider: Callable[[], datetime] = utc_now,
    ) -> None:
        # ``limits=None`` re-reads settings per evaluation so an operator's
        # ``.env`` change takes effect on the next alert, not the next deploy.
        self._limits = limits
        self._now_provider = now_provider

    def evaluate(
        self,
        db: Session,
        *,
        operator_id: int,
        project_id: int | None,
    ) -> FatigueDecision:
        """Return whether one more decision alert fits the operator's budget."""
        limits = self._limits or NotificationFatigueLimits.from_settings(settings)
        now = self._now_provider()
        if not limits.is_active:
            # Shipped default: no query, no behavior change.
            return evaluate_notification_fatigue(
                limits=limits,
                signals=FatigueSignals(),
                now=now,
            )
        signals = FatigueSignals(
            daily_sent_count=self._count_delivered_today(
                db,
                operator_id=operator_id,
                now=now,
                limits=limits,
            ),
            last_project_sent_at=self._last_delivered_at_for_project(
                db,
                operator_id=operator_id,
                project_id=project_id,
                now=now,
                limits=limits,
            ),
        )
        return evaluate_notification_fatigue(limits=limits, signals=signals, now=now)

    def _count_delivered_today(
        self,
        db: Session,
        *,
        operator_id: int,
        now: datetime,
        limits: NotificationFatigueLimits,
    ) -> int:
        if not limits.daily_cap_enabled:
            return 0
        day_start, day_end = kst_day_bounds_utc(now)
        return len(
            self._delivered_decision_events(
                db,
                operator_id=operator_id,
                since=day_start,
                until=day_end,
            )
        )

    def _last_delivered_at_for_project(
        self,
        db: Session,
        *,
        operator_id: int,
        project_id: int | None,
        now: datetime,
        limits: NotificationFatigueLimits,
    ) -> datetime | None:
        if not limits.renotify_cooldown_enabled or project_id is None:
            return None
        since = ensure_utc(now) - timedelta(hours=limits.renotify_cooldown_hours)
        timestamps = [
            timestamp
            for timestamp, payload in self._delivered_decision_events(
                db,
                operator_id=operator_id,
                since=since,
                until=None,
            )
            if self._payload_project_id(payload) == int(project_id)
        ]
        return max(timestamps) if timestamps else None

    def _delivered_decision_events(
        self,
        db: Session,
        *,
        operator_id: int,
        since: datetime,
        until: datetime | None,
    ) -> list[tuple[datetime, dict]]:
        """Return ``(timestamp, payload)`` for alerts that actually reached the operator."""
        query = db.query(Analytics.timestamp, Analytics.event_data).filter(
            Analytics.user_id == int(operator_id),
            Analytics.event_type == TELEGRAM_DELIVERY_EVENT_TYPE,
            Analytics.timestamp >= since,
        )
        if until is not None:
            query = query.filter(Analytics.timestamp < until)
        events: list[tuple[datetime, dict]] = []
        for timestamp, raw_payload in query.all():
            if timestamp is None:
                continue
            payload = _load_event_payload(raw_payload)
            if not bool(payload.get("sent")):
                continue
            if str(payload.get("source") or "") != DECISION_DELIVERY_SOURCE:
                continue
            events.append((ensure_utc(timestamp), payload))
        return events

    def _payload_project_id(self, payload: dict) -> int | None:
        """Read the notice id from a delivery payload, tolerating older rows."""
        raw_project_id = payload.get("project_id")
        if raw_project_id is None:
            return None
        try:
            return int(raw_project_id)
        except (TypeError, ValueError):
            return None


def record_fatigue_suppression(
    db: Session,
    *,
    operator_id: int,
    notification_id: int,
    project_id: int | None,
    source: str,
    decision: FatigueDecision,
) -> None:
    """Persist why an alert the value gate approved was withheld (§8 감사성).

    Written under its own event type so a deliberate suppression never counts as
    a failed delivery in the operations report's success rate.
    """
    event = Analytics(
        user_id=operator_id,
        event_type=TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
        event_data=json.dumps(
            {
                "operator_id": int(operator_id),
                "notification_id": int(notification_id),
                "project_id": None if project_id is None else int(project_id),
                "source": source,
                "sent": False,
                "status": decision.reason,
                **decision.as_event_payload(),
            },
            ensure_ascii=False,
        ),
    )
    db.add(event)
    db.commit()
    logger.info(
        "Telegram decision alert suppressed: operator=%s project=%s reason=%s "
        "daily_sent=%s daily_cap=%s",
        int(operator_id),
        project_id,
        decision.reason,
        decision.daily_sent_count,
        decision.daily_cap,
    )
