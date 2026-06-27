"""Datetime helpers used across the application.

The app stores and computes everything in UTC (see ``utc_now``/``ensure_utc``).
``KST``/``kst_now`` exist only for the boundaries where the *Korean* clock is
the correct frame: KST day-boundary logic (daily cohorts, "today") and Korean
external systems such as KONEPS, whose dates are KST, not UTC.
"""

from datetime import UTC, datetime, timedelta, timezone

# Korea Standard Time. Korea has observed no DST since 1988, so a fixed +09:00
# offset is exact — and avoids a tzdata/zoneinfo runtime dependency.
KST = timezone(timedelta(hours=9))


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(UTC)


def kst_now() -> datetime:
    """Return the current KST-aware datetime.

    Use ONLY where the Korean clock is the correct frame — KST day boundaries
    and Korean external systems (e.g. KONEPS). For internal storage/arithmetic
    use ``utc_now``.
    """
    return datetime.now(KST)


def ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC, assuming naive values are already UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)