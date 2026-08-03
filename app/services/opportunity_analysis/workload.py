"""Workload / active-bid capacity resolution for opportunity analysis.

Counts the operator's currently active bid decisions and derives (or accepts) a
workload score plus the remaining deadline hours. Methods are moved verbatim
from the original ``OpportunityAnalysisService`` body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utc_now
from app.models.models import BidDecisionRecord, Project
from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.opportunity_analysis.base import _OpportunityAnalysisBase
from app.services.opportunity_analysis.score_tables import _WORKLOAD_COMPOSITE_WEIGHTS


@dataclass(frozen=True, slots=True)
class OpportunityWorkloadContext:
    """Current capacity inputs for one opportunity decision boundary."""

    current_active_bids: int
    current_workload_score: float
    workload_source: Literal["provided", "auto"]


class _WorkloadMixin(_OpportunityAnalysisBase):
    """Active-bid count, workload score, and deadline-hours helpers."""

    def _count_current_active_bids(self, db: Session, operator_id: int, *, exclude_project_id: int | None = None) -> int:
        """Count other active bid decisions already on the operator's plate."""
        return len(self._load_current_active_bid_records(db, operator_id, exclude_project_id=exclude_project_id))

    def _load_current_active_bid_records(
        self,
        db: Session,
        operator_id: int,
        *,
        exclude_project_id: int | None = None,
    ) -> list[BidDecisionRecord]:
        """Return active bid-decision records currently occupying operator capacity."""
        query = db.query(BidDecisionRecord).filter(
            BidDecisionRecord.operator_id == operator_id,
            BidDecisionRecord.decision_status.in_(self.ACTIVE_DECISION_STATUSES),
        )
        if exclude_project_id is not None:
            query = query.filter(BidDecisionRecord.project_id != exclude_project_id)
        return query.all()

    def resolve_workload_context(
        self,
        db: Session,
        *,
        operator_id: int,
        max_active_bids: int,
        current_active_bids: int | None = None,
        current_workload_score: float | None = None,
        exclude_project_id: int | None = None,
    ) -> OpportunityWorkloadContext:
        """Resolve live capacity plus either provided or stored-decision workload.

        This public, narrow contract lets persistence paths refresh the cheap
        dynamic decision context without re-running classifiers, predictors, or
        similarity lookup.
        """
        active_records = self._load_current_active_bid_records(
            db,
            operator_id,
            exclude_project_id=exclude_project_id,
        )
        resolved_active_bids = (
            int(current_active_bids)
            if current_active_bids is not None
            else len(active_records)
        )

        if current_workload_score is not None:
            normalized_workload = max(0.0, min(1.0, float(current_workload_score)))
            return OpportunityWorkloadContext(
                current_active_bids=resolved_active_bids,
                current_workload_score=round(normalized_workload, 2),
                workload_source="provided",
            )

        auto_workload_score = self._estimate_current_workload_score(
            active_records=active_records,
            current_active_bids=resolved_active_bids,
            max_active_bids=max_active_bids,
        )
        return OpportunityWorkloadContext(
            current_active_bids=resolved_active_bids,
            current_workload_score=auto_workload_score,
            workload_source="auto",
        )

    def _resolve_workload_context(
        self,
        db: Session,
        *,
        operator_id: int,
        request: OpportunityAnalysisRequest,
        exclude_project_id: int | None = None,
    ) -> tuple[int, float, str]:
        """Preserve the analysis-internal tuple contract over the public value object."""
        context = self.resolve_workload_context(
            db,
            operator_id=operator_id,
            max_active_bids=request.max_active_bids,
            current_active_bids=request.current_active_bids,
            current_workload_score=request.current_workload_score,
            exclude_project_id=exclude_project_id,
        )
        return (
            context.current_active_bids,
            context.current_workload_score,
            context.workload_source,
        )

    def _estimate_current_workload_score(
        self,
        *,
        active_records: list[BidDecisionRecord],
        current_active_bids: int,
        max_active_bids: int,
    ) -> float:
        """Estimate current workload from persisted active bid decisions when the caller omits it."""
        if current_active_bids <= 0:
            return 0.0

        safe_max = max(1, max_active_bids)
        active_load_ratio = min(1.0, current_active_bids / safe_max)

        if not active_records:
            return round(active_load_ratio * 0.65, 2)

        average_priority = sum(float(record.priority_score or 0.0) for record in active_records) / len(active_records)
        urgent_ratio = sum(
            1 for record in active_records
            if record.deadline_hours_remaining is not None and int(record.deadline_hours_remaining) <= 24
        ) / len(active_records)
        review_ratio = sum(1 for record in active_records if str(record.decision_status) == "reviewing") / len(active_records)

        weights = _WORKLOAD_COMPOSITE_WEIGHTS
        workload_score = (
            active_load_ratio * weights["active_load_ratio"]
            + average_priority * weights["average_priority"]
            + urgent_ratio * weights["urgent_ratio"]
            + review_ratio * weights["review_ratio"]
        )
        return round(max(0.0, min(1.0, workload_score)), 2)

    def _compute_deadline_hours_remaining(self, project: Project) -> int | None:
        """Convert a project deadline into remaining whole hours."""
        if not project.deadline:
            return None

        remaining_seconds = (ensure_utc(project.deadline) - utc_now()).total_seconds()
        if remaining_seconds <= 0:
            return 0
        return int(remaining_seconds // 3600)
