"""Shared constants, decision-record predicates, and row loaders.

The foundation every ``DecisionAnalyticsService`` mixin builds on: the tuning
constants plus the leaf helpers (rate/average/delta math, entry-state
predicates, segment resolvers, and the DB row/event loaders). Method bodies
are the original ``DecisionAnalyticsService`` methods, moved verbatim.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.core.constants import ACTIVE_DECISION_STATUSES as _ACTIVE_DECISION_STATUSES
from app.core.time import ensure_utc, utc_now
from app.domain.aggregates import average, delta
from app.models.models import Analytics, BidDecisionRecord
from app.schemas.analytics_events import coerce_payload_int


class _DecisionAnalyticsBase:
    """Constants and stateless leaf helpers shared by all analytics mixins."""

    ACTIVE_DECISION_STATUSES = _ACTIVE_DECISION_STATUSES
    UNKNOWN_CATEGORY = "uncategorized"
    UNKNOWN_AGENCY = "unknown"
    DEFAULT_WORKLOAD_SOURCE = "provided"
    REVIEW_RATE_TIGHTEN_THRESHOLD = 0.35
    REVIEW_RATE_RELAX_THRESHOLD = 0.75
    BID_NOW_RATE_TIGHTEN_THRESHOLD = 0.5
    WORKLOAD_GAP_ACTION_THRESHOLD = 0.35
    CATEGORY_GAP_ACTION_THRESHOLD = 0.3
    EXPERIMENT_HISTORY_MIN_LOOKBACK_DAYS = 90
    EXPERIMENT_HISTORY_MAX_LOOKBACK_DAYS = 365
    EXPERIMENT_APPLICATION_MARKERS = ("Threshold 적용:", "Strategy 적용:")
    THRESHOLD_PARAMETER_DELTAS = {
        "review-threshold-tighten": {
            "parameter": "review_threshold",
            "label": "REVIEW_THRESHOLD",
            "direction": "increase",
            "base_delta": 0.04,
            "min_delta": 0.02,
            "max_delta": 0.06,
        },
        "review-threshold-relax": {
            "parameter": "review_threshold",
            "label": "REVIEW_THRESHOLD",
            "direction": "decrease",
            "base_delta": 0.03,
            "min_delta": 0.015,
            "max_delta": 0.05,
        },
        "bid-now-threshold-tighten": {
            "parameter": "bid_now_threshold",
            "label": "BID_NOW_THRESHOLD",
            "direction": "increase",
            "base_delta": 0.03,
            "min_delta": 0.015,
            "max_delta": 0.05,
        },
    }
    CATEGORY_PARAMETER_BASE_DELTA = 0.03
    CATEGORY_PARAMETER_MIN_DELTA = 0.015
    CATEGORY_PARAMETER_MAX_DELTA = 0.05

    def _coerce_int(self, value: Any) -> int | None:
        """Best-effort coercion of event payload identifiers to int.

        Thin delegator: the rule itself is declared once in
        :func:`app.schemas.analytics_events.coerce_payload_int`, which the
        ``Persisted*`` restore models apply as a lenient field validator. Keeping
        two copies would let the same stored row resolve differently depending on
        which consumer read it.
        """
        return coerce_payload_int(value)

    def _reasoning_excerpt(self, reasoning: Any, *, limit: int = 200) -> str:
        """Return a bounded excerpt of the persisted reasoning text."""
        if reasoning is None:
            return ""
        text = str(reasoning)
        if len(text) <= limit:
            return text
        return text[:limit]

    def _average(self, values: list[float]) -> float | None:
        """Return a rounded average for summary metrics."""
        return average(values, digits=4)

    def _rate(self, numerator: int, denominator: int) -> float | None:
        """Return a safe rounded ratio for funnel conversion metrics."""
        if denominator <= 0:
            return None
        return round(numerator / denominator, 4)

    def _delta(self, current_value: float | None, previous_value: float | None) -> float | None:
        """Return a rounded period-over-period delta when both values exist."""
        return delta(current_value, previous_value, digits=4)

    def _entry_datetime(self, decision: BidDecisionRecord):
        """Resolve the timestamp that represents entry into the decision workflow.

        ``first_decided_at`` is persisted as a naive timestamp while
        ``created_at`` / ``updated_at`` are timezone-aware; normalize to a
        UTC-aware value so downstream arithmetic (e.g. ``_compute_hours_to_submit``)
        never mixes naive and aware datetimes.
        """
        return ensure_utc(
            decision.first_decided_at
            or decision.created_at
            or decision.updated_at
            or utc_now()
        )

    def _is_submitted(self, decision: BidDecisionRecord) -> bool:
        """Return whether the current decision has reached submitted state."""
        return str(decision.decision_status or "") == "submitted"

    def _is_active_pending(self, decision: BidDecisionRecord) -> bool:
        """Return whether the current decision is still pending active handling."""
        return str(decision.decision_status or "") in self.ACTIVE_DECISION_STATUSES

    def _is_skipped(self, decision: BidDecisionRecord) -> bool:
        """Return whether the current decision has been skipped."""
        return str(decision.decision_status or "") == "skipped"

    def _entry_action(self, decision: BidDecisionRecord) -> str:
        """Resolve the original action that first introduced the record into the workflow."""
        return str(decision.initial_action or decision.action or "skip")

    def _entry_status(self, decision: BidDecisionRecord) -> str:
        """Resolve the original workflow status captured when the record was first created."""
        return str(decision.initial_decision_status or decision.decision_status or "planned")

    def _compute_hours_to_submit(self, decision: BidDecisionRecord) -> float | None:
        """Measure elapsed hours from first decision creation to submitted state."""
        first_decided_at = self._entry_datetime(decision)
        submitted_at = decision.updated_at
        if first_decided_at is None or submitted_at is None:
            return None
        # ``first_decided_at`` is already UTC-aware (normalized by
        # ``_entry_datetime``), but ``updated_at`` is read back naive on some
        # backends (e.g. SQLite), so normalize it too before subtracting to
        # avoid mixing naive and aware datetimes.
        delta_seconds = max(
            (ensure_utc(submitted_at) - first_decided_at).total_seconds(), 0.0
        )
        return round(delta_seconds / 3600, 4)

    def _resolve_category_segment(self, decision: BidDecisionRecord) -> str:
        """Return a stable category label for segment analysis."""
        if decision.project is None or not decision.project.category:
            return self.UNKNOWN_CATEGORY
        return str(decision.project.category)

    def _resolve_agency_segment(self, decision: BidDecisionRecord) -> str:
        """Use demand agency first, then issuing agency, for segment analysis."""
        if decision.project is None:
            return self.UNKNOWN_AGENCY
        return str(decision.project.demand_agency or decision.project.issuing_agency or self.UNKNOWN_AGENCY)

    def _find_segment(self, segments: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
        """Return one breakdown segment by exact label."""
        for segment in segments:
            if str(segment.get("segment")) == label:
                return segment
        return None

    def _load_recent_decisions(self, db: Session, *, operator_id: int, days: int) -> list[BidDecisionRecord]:
        """Return recent decision rows ordered newest first."""
        date_from = utc_now() - timedelta(days=days)
        return self._load_decisions_in_range(db, operator_id=operator_id, start_at=date_from, end_at=None)

    def _load_decisions_in_range(
        self,
        db: Session,
        *,
        operator_id: int,
        start_at,
        end_at,
    ) -> list[BidDecisionRecord]:
        """Return decision rows for one operator within a bounded updated-at window."""
        query = (
            db.query(BidDecisionRecord)
            .options(selectinload(BidDecisionRecord.project))
            .filter(
                BidDecisionRecord.operator_id == operator_id,
                BidDecisionRecord.updated_at >= start_at,
            )
        )
        if end_at is not None:
            query = query.filter(BidDecisionRecord.updated_at < end_at)

        return (
            query
            .order_by(BidDecisionRecord.updated_at.desc(), BidDecisionRecord.id.desc())
            .all()
        )

    def _load_events_in_range(
        self,
        db: Session,
        *,
        operator_id: int,
        event_type: str,
        start_at,
    ) -> list[Analytics]:
        """Return one operator's analytics events of a single type since ``start_at``."""
        return (
            db.query(Analytics)
            .filter(
                Analytics.user_id == operator_id,
                Analytics.event_type == event_type,
                Analytics.timestamp >= start_at,
            )
            .order_by(Analytics.timestamp.asc(), Analytics.id.asc())
            .all()
        )
