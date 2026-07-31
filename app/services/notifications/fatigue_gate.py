"""Delivery-history boundary for the notification fatigue core (§4.7.1/3).

The pure rules live in :mod:`app.services.notifications.fatigue`; this module is
the thin I/O half that answers the two questions those rules need: how many
decision alerts this operator actually received today (KST), and when this
notice was last announced.

Both answers come from the ``analytics`` rows the delivery path already writes
(``telegram.delivery``), so no schema change is involved. Only deliveries with
``sent: true`` count — a dry-run or route-blocked attempt puts no message in
front of the operator, so it must not consume the operator's alert budget.

The payload is read back through its declared contract
(:class:`~app.schemas.analytics_events.PersistedTelegramDeliveryEvent`) instead of
``json.loads`` + ``.get()``. The three fields this gate branches on are the write
path's contract, so a rename on either side must not silently degrade into "no
deliveries today" (which would quietly disable the cap).

This gate is **fail-closed**: it exists to stop alert floods, so an unusable
delivery row errs toward suppression rather than toward sending one more message.
See :meth:`NotificationFatigueGate._delivered_decision_events`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    TELEGRAM_DELIVERY_EVENT_TYPE,
    TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
)
from app.core.time import ensure_utc, kst_day_bounds_utc, utc_now
from app.models.models import Analytics
from app.schemas.analytics_events import (
    PersistedTelegramDeliveryEvent,
    TelegramDeliverySuppressedEvent,
)
from app.services.analytics_event_payload import (
    dump_analytics_event,
    load_analytics_event_as,
)
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


@dataclass(frozen=True)
class DeliveryBudgetRow:
    """One delivery row that consumes the operator's alert budget.

    ``payload`` is ``None`` when the stored ``event_data`` could not be restored.
    Such a row still counts against the daily cap (fail-closed), but it cannot
    take part in the per-notice cooldown match because the notice is unknown.
    """

    delivered_at: datetime
    payload: PersistedTelegramDeliveryEvent | None = None

    def matches_project(self, project_id: int) -> bool:
        """Whether this row is a known delivery for ``project_id``."""
        return self.payload is not None and self.payload.project_id == project_id


class NotificationFatigueGate:
    """Resolve the fatigue verdict for one pending decision alert."""

    def __init__(
        self,
        *,
        limits: NotificationFatigueLimits | None = None,
        now_provider: Callable[[], datetime] = utc_now,
    ) -> None:
        # ``limits=None`` reads the settings singleton per evaluation; a
        # ``.env`` change still needs ``docker compose up -d`` recreation to
        # reach the process (settings load once at import).
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
        """Count today's (KST) budget-consuming rows, unreadable ones included."""
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
            row.delivered_at
            for row in self._delivered_decision_events(
                db,
                operator_id=operator_id,
                since=since,
                until=None,
            )
            if row.matches_project(int(project_id))
        ]
        return max(timestamps) if timestamps else None

    def _delivered_decision_events(
        self,
        db: Session,
        *,
        operator_id: int,
        since: datetime,
        until: datetime | None,
    ) -> list[DeliveryBudgetRow]:
        """Return the rows that consume this operator's alert budget.

        The gate is **fail-closed** because it is a flood-prevention device: a row
        whose payload cannot be restored is NOT assumed to be "nothing was
        delivered". It is counted against the daily cap (the conservative,
        suppressing direction), and only **excluded from the per-notice cooldown
        match** — an unreadable row cannot tell us which notice it announced, and
        guessing a notice id would suppress the wrong alert.

        Readable rows keep the original filters: only ``sent: true`` decision
        alerts consume the budget (a dry-run puts no message in front of the
        operator, and a bid-submission receipt confirms the operator's own action).
        """
        query = db.query(Analytics.timestamp, Analytics.event_data).filter(
            Analytics.user_id == int(operator_id),
            Analytics.event_type == TELEGRAM_DELIVERY_EVENT_TYPE,
            Analytics.timestamp >= since,
        )
        if until is not None:
            query = query.filter(Analytics.timestamp < until)
        rows: list[DeliveryBudgetRow] = []
        for timestamp, raw_payload in query.all():
            if timestamp is None:
                continue
            payload = load_analytics_event_as(
                raw_payload,
                model=PersistedTelegramDeliveryEvent,
                event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
            )
            if payload is None:
                rows.append(DeliveryBudgetRow(delivered_at=ensure_utc(timestamp)))
                continue
            if not payload.sent:
                continue
            if (payload.source or "") != DECISION_DELIVERY_SOURCE:
                continue
            rows.append(
                DeliveryBudgetRow(delivered_at=ensure_utc(timestamp), payload=payload)
            )
        return rows


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
        event_data=dump_analytics_event(
            TelegramDeliverySuppressedEvent(
                operator_id=int(operator_id),
                notification_id=int(notification_id),
                project_id=None if project_id is None else int(project_id),
                source=source,
                sent=False,
                status=decision.reason,
                allowed=decision.allowed,
                reason=decision.reason,
                detail=decision.detail,
                daily_sent_count=decision.daily_sent_count,
                daily_cap=decision.daily_cap,
                hours_since_project_send=decision.hours_since_project_send,
                renotify_cooldown_hours=decision.renotify_cooldown_hours,
            )
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
