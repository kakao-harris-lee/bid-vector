"""Roadmap operating KPIs (override / missed / review-time / feedback / settlement)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.constants import (
    PROJECT_VIEW_EVENT_TYPE,
    RECOMMENDATION_FEEDBACK_EVENT_TYPE,
)
from app.core.single_user import ensure_operator_account
from app.core.time import ensure_utc, utc_now
from app.models.models import (
    Analytics,
    BidDecisionRecord,
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    User,
)
from app.services.decision_analytics.base import _DecisionAnalyticsBase
from app.services.decision_analytics.events import parse_analytics_event_data
from app.services.prediction_feedback import PredictionFeedbackService


class _OperationsKpiMixin(_DecisionAnalyticsBase):
    """Operating KPI aggregations over the current-period decision set."""

    def build_operations_kpi(
        self,
        db: Session,
        *,
        days: int = 30,
        missed_limit: int = 10,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Aggregate roadmap operating KPIs (d/e/f/b) from existing data in one call.

        Conversion (e) and prediction accuracy (f) reuse ``build_funnel`` and
        ``build_feedback`` verbatim so the conversion/accuracy logic stays single-sourced.
        Manual override (d) and missed opportunities (b) are computed here over the same
        current-period decision set that ``build_funnel`` analyzes.
        """
        if operator is None:
            operator = ensure_operator_account(db)
        now = utc_now()
        current_period_start = now - timedelta(days=days)
        decisions = self._load_decisions_in_range(
            db,
            operator_id=operator.id,
            start_at=current_period_start,
            end_at=None,
        )

        funnel = self.build_funnel(db, days=days, operator=operator)
        feedback = PredictionFeedbackService().build_feedback(
            db, days=days, limit=100, operator=operator
        )

        manual_override = self._build_manual_override_kpi(decisions)
        missed_opportunities = self._build_missed_opportunities_kpi(
            decisions,
            now=now,
            limit=missed_limit,
        )
        review_time = self._build_review_time_kpi(
            db,
            operator_id=operator.id,
            decisions=decisions,
            start_at=current_period_start,
        )
        recommendation_feedback = self._build_recommendation_feedback_kpi(
            db,
            operator_id=operator.id,
            start_at=current_period_start,
        )
        settlement_coverage = self._build_settlement_coverage_kpi(
            db,
            operator_id=operator.id,
            start_at=current_period_start,
        )

        return {
            "operator_id": operator.id,
            "period_days": days,
            "manual_override": manual_override,
            "conversion": {
                "decision_count": int(funnel.get("decision_count") or 0),
                "submitted_count": int(funnel.get("submitted_count") or 0),
                "overall_submission_rate": funnel.get("overall_submission_rate"),
                "bid_now_submission_rate": funnel.get("bid_now_submission_rate"),
                "review_submission_rate": funnel.get("review_submission_rate"),
                "average_hours_to_submit": funnel.get("average_hours_to_submit"),
            },
            "prediction_accuracy": {
                "result_count": int(feedback.get("result_count") or 0),
                "prediction_sample_count": int(feedback.get("prediction_sample_count") or 0),
                "recommendation_sample_count": int(feedback.get("recommendation_sample_count") or 0),
                "average_prediction_error_rate": feedback.get("average_prediction_error_rate"),
                "average_recommendation_error_rate": feedback.get("average_recommendation_error_rate"),
                "prediction_within_1_percent_count": int(feedback.get("prediction_within_1_percent_count") or 0),
                "prediction_within_3_percent_count": int(feedback.get("prediction_within_3_percent_count") or 0),
                "recommendation_within_1_percent_count": int(feedback.get("recommendation_within_1_percent_count") or 0),
                "recommendation_within_3_percent_count": int(feedback.get("recommendation_within_3_percent_count") or 0),
            },
            "missed_opportunities": missed_opportunities,
            "review_time": review_time,
            "recommendation_feedback": recommendation_feedback,
            "settlement_coverage": settlement_coverage,
        }

    def _build_settlement_coverage_kpi(
        self,
        db: Session,
        *,
        operator_id: int,
        start_at,
    ) -> dict[str, Any]:
        """KPI: how far paper-bid settlement has progressed in the window.

        Counts the operator's :class:`PaperBid` rows created since ``start_at`` and,
        via an outer join to :class:`PaperBidSettlement`, how many already carry a
        settlement. The ``forward_paper`` subset (joined through
        :class:`PaperBidRun`) is reported separately because forward paper bids are
        the ones the automated forward settlement job is responsible for closing.

        Aggregation runs as two grouped ``count`` queries (overall + per-mode) so
        no paper bid rows are loaded into Python. Coverage rates return ``None``
        when their denominator is zero.
        """
        settled_flag = case(
            (PaperBidSettlement.id.isnot(None), 1),
            else_=0,
        )

        overall_total, overall_settled = (
            db.query(
                func.count(PaperBid.id),
                func.coalesce(func.sum(settled_flag), 0),
            )
            .outerjoin(
                PaperBidSettlement,
                PaperBidSettlement.paper_bid_id == PaperBid.id,
            )
            .filter(
                PaperBid.operator_id == operator_id,
                PaperBid.created_at >= start_at,
            )
            .one()
        )

        forward_total, forward_settled = (
            db.query(
                func.count(PaperBid.id),
                func.coalesce(func.sum(settled_flag), 0),
            )
            .join(PaperBidRun, PaperBidRun.id == PaperBid.run_id)
            .outerjoin(
                PaperBidSettlement,
                PaperBidSettlement.paper_bid_id == PaperBid.id,
            )
            .filter(
                PaperBid.operator_id == operator_id,
                PaperBid.created_at >= start_at,
                PaperBidRun.mode == "forward_paper",
            )
            .one()
        )

        total_paper_bids = int(overall_total or 0)
        settled_count = int(overall_settled or 0)
        forward_paper_bids = int(forward_total or 0)
        forward_settled_count = int(forward_settled or 0)

        return {
            "total_paper_bids": total_paper_bids,
            "settled_count": settled_count,
            "coverage_rate": self._rate(settled_count, total_paper_bids),
            "forward_paper_bids": forward_paper_bids,
            "forward_settled_count": forward_settled_count,
            "forward_coverage_rate": self._rate(
                forward_settled_count, forward_paper_bids
            ),
        }

    def _build_review_time_kpi(
        self,
        db: Session,
        *,
        operator_id: int,
        decisions: list[BidDecisionRecord],
        start_at,
    ) -> dict[str, Any]:
        """KPI (a): time between first opening a tender and first deciding on it.

        Joins ``project_view`` events (with ``event_data.project_id``) to the
        period's decisions on project id, requiring the view to precede the
        decision's ``first_decided_at``. Only matched decisions contribute to the
        average; the earliest qualifying view is used per decision.
        """
        view_events = self._load_events_in_range(
            db,
            operator_id=operator_id,
            event_type=PROJECT_VIEW_EVENT_TYPE,
            start_at=start_at,
        )
        earliest_view_by_project: dict[int, Any] = {}
        for event in view_events:
            payload = parse_analytics_event_data(event.event_data)
            project_id = self._coerce_int(payload.get("project_id"))
            if project_id is None or event.timestamp is None:
                continue
            viewed_at = ensure_utc(event.timestamp)
            existing = earliest_view_by_project.get(project_id)
            if existing is None or viewed_at < existing:
                earliest_view_by_project[project_id] = viewed_at

        review_seconds: list[float] = []
        for decision in decisions:
            if decision.project_id is None:
                continue
            decided_at = decision.first_decided_at
            if decided_at is None:
                continue
            first_view = earliest_view_by_project.get(int(decision.project_id))
            if first_view is None:
                continue
            decided_at = ensure_utc(decided_at)
            if first_view > decided_at:
                continue
            review_seconds.append((decided_at - first_view).total_seconds())

        average_seconds = self._average(review_seconds)
        average_minutes = (
            round(average_seconds / 60, 4) if average_seconds is not None else None
        )
        return {
            "average_review_minutes": average_minutes,
            "sample_count": len(review_seconds),
        }

    def _dedupe_latest_feedback_verdicts(
        self,
        feedback_events: list[Analytics],
    ) -> dict[int, dict[str, Any]]:
        """Reduce ``recommendation_feedback`` events to the latest verdict per decision.

        Returns a mapping of ``decision_record_id`` to ``{verdict, project_id,
        feedback_at}`` for the most recent qualifying event. Events with malformed
        payloads or verdict values outside ``{useful, not_useful}`` are skipped.
        Since callers load events oldest-first, the last write wins.
        """
        latest_by_decision: dict[int, dict[str, Any]] = {}
        for event in feedback_events:
            payload = parse_analytics_event_data(event.event_data)
            decision_record_id = self._coerce_int(payload.get("decision_record_id"))
            verdict = str(payload.get("verdict") or "").strip().lower()
            if decision_record_id is None or verdict not in {"useful", "not_useful"}:
                continue
            latest_by_decision[decision_record_id] = {
                "verdict": verdict,
                "project_id": self._coerce_int(payload.get("project_id")),
                "feedback_at": ensure_utc(event.timestamp) if event.timestamp is not None else None,
            }
        return latest_by_decision

    def _build_recommendation_feedback_kpi(
        self,
        db: Session,
        *,
        operator_id: int,
        start_at,
    ) -> dict[str, Any]:
        """KPI (c): operator usefulness votes on recommendations.

        Aggregates ``recommendation_feedback`` events, keeping only the latest
        verdict per ``decision_record_id`` via :meth:`_dedupe_latest_feedback_verdicts`.
        ``review_value_rate`` is the share of useful verdicts among rated decisions.
        """
        feedback_events = self._load_events_in_range(
            db,
            operator_id=operator_id,
            event_type=RECOMMENDATION_FEEDBACK_EVENT_TYPE,
            start_at=start_at,
        )
        latest_by_decision = self._dedupe_latest_feedback_verdicts(feedback_events)

        useful_count = sum(
            1 for entry in latest_by_decision.values() if entry["verdict"] == "useful"
        )
        not_useful_count = sum(
            1 for entry in latest_by_decision.values() if entry["verdict"] == "not_useful"
        )
        return {
            "useful_count": useful_count,
            "not_useful_count": not_useful_count,
            "review_value_rate": self._rate(useful_count, useful_count + not_useful_count),
            "feedback_count": len(latest_by_decision),
        }

    def _build_manual_override_kpi(self, decisions: list[BidDecisionRecord]) -> dict[str, Any]:
        """KPI (d): share of decisions the operator changed from the initial recommendation."""
        modified_count = sum(1 for decision in decisions if self._is_manually_overridden(decision))
        return {
            "decision_count": len(decisions),
            "modified_count": modified_count,
            "modification_rate": self._rate(modified_count, len(decisions)),
        }

    def _is_manually_overridden(self, decision: BidDecisionRecord) -> bool:
        """Return whether the current action/status diverged from the captured initial values."""
        initial_action = decision.initial_action
        initial_status = decision.initial_decision_status
        action_changed = initial_action is not None and str(decision.action or "") != str(initial_action)
        status_changed = (
            initial_status is not None
            and str(decision.decision_status or "") != str(initial_status)
        )
        return action_changed or status_changed

    def _build_missed_opportunities_kpi(
        self,
        decisions: list[BidDecisionRecord],
        *,
        now,
        limit: int,
    ) -> dict[str, Any]:
        """KPI (b): bid_now/review recommendations still pending past the project deadline."""
        missed: list[BidDecisionRecord] = [
            decision for decision in decisions if self._is_missed_opportunity(decision, now=now)
        ]
        missed.sort(
            key=lambda decision: (
                -float(decision.priority_score or 0.0),
                self._missed_deadline_sort_key(decision),
            )
        )
        items = [
            {
                "decision_record_id": int(decision.id),
                "project_id": int(decision.project_id),
                "project_title": (
                    str(decision.project.title)
                    if decision.project is not None and decision.project.title is not None
                    else f"Project {decision.project_id}"
                ),
                "deadline": decision.project.deadline if decision.project is not None else None,
                "initial_action": self._entry_action(decision),
                "decision_status": str(decision.decision_status or "planned"),
                "priority_score": float(decision.priority_score or 0.0),
            }
            for decision in missed[: max(limit, 0)]
        ]
        return {
            "missed_count": len(missed),
            "items": items,
        }

    def _is_missed_opportunity(self, decision: BidDecisionRecord, *, now) -> bool:
        """Return whether a recommended decision is still pending after its deadline passed."""
        if self._entry_action(decision) not in {"bid_now", "review"}:
            return False
        if str(decision.decision_status or "") not in self.ACTIVE_DECISION_STATUSES:
            return False
        if decision.project is None:
            return False
        deadline = decision.project.deadline
        if deadline is None:
            return False
        return ensure_utc(deadline) < ensure_utc(now)

    def _missed_deadline_sort_key(self, decision: BidDecisionRecord):
        """Order missed items by earliest deadline after priority, tolerating null deadlines."""
        deadline = decision.project.deadline if decision.project is not None else None
        if deadline is None:
            return (1, 0.0)
        return (0, ensure_utc(deadline).timestamp())
