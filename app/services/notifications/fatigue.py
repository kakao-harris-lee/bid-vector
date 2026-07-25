"""Pure decision core for Telegram notification fatigue caps (§4.7.4).

Telegram decision alerts already pass a *value* gate
(``OperatorNotificationService.should_deliver_bid_decision_to_telegram``:
action / priority / probability). This module adds the orthogonal *volume* gate
asked for by the roadmap's "알림 품질 조정" step: how many alerts one operator
may receive per Korean calendar day, and how soon the same notice may be
re-announced.

Everything the rules read is passed in — the already-counted deliveries, the
last send timestamp for the notice, the limits, and ``now``. No DB session, no
settings lookup, no clock call happens here, so the whole gate is testable as a
value table.

The rules are declared in an ordered tuple (:data:`_FATIGUE_RULES`); adding a
new fatigue rule is one entry, not another branch in a growing if-ladder (§4.5).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.time import ensure_utc

# Suppression reasons. These strings land in the audit event payload and in the
# suppression log line, so they are part of the operations contract.
FATIGUE_ALLOWED = "allowed"
FATIGUE_SUPPRESSED_RENOTIFY_COOLDOWN = "suppressed_renotify_cooldown"
FATIGUE_SUPPRESSED_DAILY_CAP = "suppressed_daily_cap"


@dataclass(frozen=True)
class NotificationFatigueLimits:
    """Boundary snapshot of the configured caps.

    ``0`` disables a limit — that is the shipped default for both keys, so the
    gate is inert until an operator opts in through ``.env``.
    """

    daily_cap: int = 0
    renotify_cooldown_hours: float = 0.0

    @classmethod
    def from_settings(cls, settings: Any) -> "NotificationFatigueLimits":
        return cls(
            daily_cap=int(settings.TELEGRAM_DECISION_DAILY_CAP or 0),
            renotify_cooldown_hours=float(
                settings.TELEGRAM_DECISION_RENOTIFY_COOLDOWN_HOURS or 0.0
            ),
        )

    @property
    def daily_cap_enabled(self) -> bool:
        return self.daily_cap > 0

    @property
    def renotify_cooldown_enabled(self) -> bool:
        return self.renotify_cooldown_hours > 0.0

    @property
    def is_active(self) -> bool:
        """Whether any limit is on. Callers skip evidence queries when False."""
        return self.daily_cap_enabled or self.renotify_cooldown_enabled


@dataclass(frozen=True)
class FatigueSignals:
    """Delivery evidence gathered at the boundary.

    ``daily_sent_count`` counts alerts actually delivered to the operator in the
    current KST day (dry-run and blocked attempts deliver nothing, so they do
    not consume the cap). ``last_project_sent_at`` is the newest such delivery
    for this notice, or ``None`` when there is none in the lookback window.
    """

    daily_sent_count: int = 0
    last_project_sent_at: datetime | None = None


@dataclass(frozen=True)
class FatigueDecision:
    """Gate verdict plus the numbers that justified it, for the audit trail."""

    allowed: bool
    reason: str
    detail: str
    daily_sent_count: int
    daily_cap: int
    hours_since_project_send: float | None
    renotify_cooldown_hours: float

    def as_event_payload(self) -> dict[str, object]:
        """Return the audit-safe payload (no message text, no target)."""
        return {
            "allowed": bool(self.allowed),
            "reason": str(self.reason),
            "detail": str(self.detail),
            "daily_sent_count": int(self.daily_sent_count),
            "daily_cap": int(self.daily_cap),
            "hours_since_project_send": self.hours_since_project_send,
            "renotify_cooldown_hours": float(self.renotify_cooldown_hours),
        }


def hours_between(earlier: datetime, later: datetime) -> float:
    """Return the elapsed hours between two instants, tolerating naive values."""
    return (ensure_utc(later) - ensure_utc(earlier)).total_seconds() / 3600.0


def _renotify_cooldown_violation(
    limits: NotificationFatigueLimits,
    signals: FatigueSignals,
    now: datetime,
) -> tuple[str, str] | None:
    """Suppress a repeat alert for a notice already announced recently."""
    if not limits.renotify_cooldown_enabled or signals.last_project_sent_at is None:
        return None
    elapsed_hours = hours_between(signals.last_project_sent_at, now)
    if elapsed_hours >= limits.renotify_cooldown_hours:
        return None
    return (
        FATIGUE_SUPPRESSED_RENOTIFY_COOLDOWN,
        "Telegram delivery suppressed because this notice was already announced "
        f"{elapsed_hours:.2f}h ago (cooldown {limits.renotify_cooldown_hours:g}h).",
    )


def _daily_cap_violation(
    limits: NotificationFatigueLimits,
    signals: FatigueSignals,
    now: datetime,
) -> tuple[str, str] | None:
    """Suppress once the operator hit the KST-day alert budget."""
    if not limits.daily_cap_enabled or signals.daily_sent_count < limits.daily_cap:
        return None
    return (
        FATIGUE_SUPPRESSED_DAILY_CAP,
        "Telegram delivery suppressed because the operator already received "
        f"{signals.daily_sent_count} decision alert(s) today (KST cap {limits.daily_cap}).",
    )


# A rule reads the limits, the gathered signals and ``now``, and returns either
# ``None`` (no violation) or the ``(reason, detail)`` pair to report.
FatigueRule = Callable[
    [NotificationFatigueLimits, FatigueSignals, datetime], tuple[str, str] | None
]

# Ordered rule table. The first violating rule wins, so the most specific reason
# (same notice repeated) is reported ahead of the broader daily budget.
_FATIGUE_RULES: tuple[FatigueRule, ...] = (
    _renotify_cooldown_violation,
    _daily_cap_violation,
)


def evaluate_notification_fatigue(
    *,
    limits: NotificationFatigueLimits,
    signals: FatigueSignals,
    now: datetime,
) -> FatigueDecision:
    """Decide whether one more Telegram decision alert may be delivered."""
    hours_since_project_send = (
        None
        if signals.last_project_sent_at is None
        else hours_between(signals.last_project_sent_at, now)
    )
    for rule in _FATIGUE_RULES:
        violation = rule(limits, signals, now)
        if violation is None:
            continue
        reason, detail = violation
        return FatigueDecision(
            allowed=False,
            reason=reason,
            detail=detail,
            daily_sent_count=signals.daily_sent_count,
            daily_cap=limits.daily_cap,
            hours_since_project_send=hours_since_project_send,
            renotify_cooldown_hours=limits.renotify_cooldown_hours,
        )
    return FatigueDecision(
        allowed=True,
        reason=FATIGUE_ALLOWED,
        detail="Telegram delivery is within the configured notification budget.",
        daily_sent_count=signals.daily_sent_count,
        daily_cap=limits.daily_cap,
        hours_since_project_send=hours_since_project_send,
        renotify_cooldown_hours=limits.renotify_cooldown_hours,
    )
