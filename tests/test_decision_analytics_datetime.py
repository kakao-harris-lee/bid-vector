"""Regression tests for naive/aware datetime handling in decision analytics.

``BidDecisionRecord`` datetime columns are read back naive on some backends
(e.g. SQLite) and aware on others. Subtracting a naive ``updated_at`` from the
UTC-aware entry timestamp previously raised ``TypeError: can't subtract
offset-naive and offset-aware datetimes`` whenever a baseline/funnel snapshot
included submitted decisions (e.g. the decision funnel/experiment endpoints for
an operator with submitted records). Both operands must be normalized to UTC.
"""

from datetime import UTC, datetime

from app.models.models import BidDecisionRecord
from app.services.decision_analytics import DecisionAnalyticsService


def test_entry_datetime_normalizes_naive_first_decided_at():
    """``_entry_datetime`` must return a UTC-aware value for a naive column."""
    naive_entry = datetime(2026, 6, 15, 4, 18, 48)  # naive, as stored in DB
    decision = BidDecisionRecord(
        first_decided_at=naive_entry,
        updated_at=datetime(2026, 6, 15, 6, 21, 44, tzinfo=UTC),
    )

    entry = DecisionAnalyticsService()._entry_datetime(decision)

    assert entry.tzinfo is not None
    assert entry == naive_entry.replace(tzinfo=UTC)


def test_compute_hours_to_submit_with_naive_entry_and_aware_submit():
    """Mixed naive entry + aware submit must not raise and must yield hours."""
    decision = BidDecisionRecord(
        first_decided_at=datetime(2026, 6, 15, 4, 0, 0),  # naive
        updated_at=datetime(2026, 6, 15, 6, 30, 0, tzinfo=UTC),  # aware, +2.5h
    )

    hours = DecisionAnalyticsService()._compute_hours_to_submit(decision)

    assert hours == 2.5


def test_compute_hours_to_submit_with_naive_submit_does_not_raise():
    """Naive ``updated_at`` (as SQLite reads it back) must not raise.

    The earlier fix normalized only ``first_decided_at``; a naive ``updated_at``
    still mixed naive/aware at subtraction time. Both operands must be UTC-aware.
    """
    decision = BidDecisionRecord(
        first_decided_at=datetime(2026, 6, 15, 4, 0, 0),  # naive
        updated_at=datetime(2026, 6, 15, 6, 30, 0),  # naive, as SQLite returns
    )

    hours = DecisionAnalyticsService()._compute_hours_to_submit(decision)

    assert hours == 2.5
