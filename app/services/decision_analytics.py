"""Decision analytics summaries for operator workflow tuning."""

from __future__ import annotations

import ast
import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session, selectinload

from app.core.single_user import ensure_operator_account
from app.core.time import ensure_utc, utc_now
from app.models.models import (
    Analytics,
    BidDecisionRecord,
    DecisionExperimentRun,
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    User,
)
from app.schemas.schemas import _extract_decision_reasons
from app.services.prediction_feedback import PredictionFeedbackService


def parse_analytics_event_data(raw: str | None) -> dict[str, Any]:
    """Decode a persisted ``Analytics.event_data`` string back into a dict.

    Events written after the JSON round-trip fix store valid JSON. Legacy rows
    persisted with ``str(dict)`` (Python repr, single quotes) are recovered via
    ``ast.literal_eval``. Any unparseable or non-mapping payload yields ``{}``.
    """
    if not raw:
        return {}
    text = raw.strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (ValueError, TypeError):
        try:
            decoded = ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError):
            return {}
    return decoded if isinstance(decoded, dict) else {}


class DecisionAnalyticsService:
    """Build operator-facing analytics from persisted bid decision records."""

    ACTIVE_DECISION_STATUSES = {"planned", "reviewing"}
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

    def build_insights(
        self,
        db: Session,
        *,
        days: int = 30,
        limit: int = 10,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Summarize persisted bid-decision signals for tuning and operator review."""
        if operator is None:
            operator = ensure_operator_account(db)
        decisions = self._load_recent_decisions(db, operator_id=operator.id, days=days)

        status_breakdown: dict[str, int] = {}
        action_breakdown: dict[str, int] = {}
        for decision in decisions:
            status_breakdown[str(decision.decision_status)] = status_breakdown.get(str(decision.decision_status), 0) + 1
            action_breakdown[str(decision.action)] = action_breakdown.get(str(decision.action), 0) + 1

        return {
            "operator_id": operator.id,
            "period_days": days,
            "result_count": len(decisions),
            "high_priority_count": sum(1 for decision in decisions if float(decision.priority_score or 0.0) >= 0.7),
            "bid_now_count": action_breakdown.get("bid_now", 0),
            "review_count": action_breakdown.get("review", 0),
            "skip_count": action_breakdown.get("skip", 0),
            "submitted_count": status_breakdown.get("submitted", 0),
            "auto_workload_count": sum(1 for decision in decisions if str(decision.workload_source or "provided") == "auto"),
            "provided_workload_count": sum(1 for decision in decisions if str(decision.workload_source or "provided") == "provided"),
            "average_priority_score": self._average([float(decision.priority_score or 0.0) for decision in decisions]),
            "average_expected_margin_score": self._average([float(decision.expected_margin_score or 0.0) for decision in decisions]),
            "average_execution_complexity_score": self._average([float(decision.execution_complexity_score or 0.0) for decision in decisions]),
            "average_competitiveness_score": self._average([float(decision.competitiveness_score or 0.0) for decision in decisions]),
            "average_budget_capture_score": self._average([float(decision.budget_capture_score or 0.0) for decision in decisions]),
            "status_breakdown": status_breakdown,
            "action_breakdown": action_breakdown,
            "recent_decisions": [
                {
                    "decision_record_id": int(decision.id),
                    "project_id": int(decision.project_id),
                    "action": str(decision.action),
                    "decision_status": str(decision.decision_status),
                    "priority_score": float(decision.priority_score or 0.0),
                    "expected_margin_score": float(decision.expected_margin_score or 0.0),
                    "execution_complexity_score": float(decision.execution_complexity_score or 0.0),
                    "competitiveness_score": float(decision.competitiveness_score or 0.0),
                    "budget_capture_score": float(decision.budget_capture_score or 0.0),
                    "updated_at": decision.updated_at,
                }
                for decision in decisions[:limit]
            ],
        }

    def build_funnel(
        self,
        db: Session,
        *,
        days: int = 30,
        limit: int = 10,
        breakdown_limit: int = 5,
        trend_bucket_days: int = 7,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Summarize how initial bid decisions progress toward submitted workflow states."""
        if operator is None:
            operator = ensure_operator_account(db)
        now = utc_now()
        current_period_start = now - timedelta(days=days)
        previous_period_start = current_period_start - timedelta(days=days)
        current_decisions = self._load_decisions_in_range(
            db,
            operator_id=operator.id,
            start_at=current_period_start,
            end_at=None,
        )
        previous_decisions = self._load_decisions_in_range(
            db,
            operator_id=operator.id,
            start_at=previous_period_start,
            end_at=current_period_start,
        )
        current_summary = self._build_funnel_summary(current_decisions)
        previous_summary = self._build_funnel_summary(previous_decisions)
        recent_submissions = self._build_recent_submissions(current_decisions, limit=limit)

        return {
            "operator_id": operator.id,
            "period_days": days,
            **current_summary,
            "current_period_start": current_period_start,
            "current_period_end": now,
            "previous_period": {
                "period_start": previous_period_start,
                "period_end": current_period_start,
                **previous_summary,
            },
            "comparison": self._build_funnel_comparison(
                current_summary,
                previous_summary,
                current_period_start=current_period_start,
                current_period_end=now,
                previous_period_start=previous_period_start,
                previous_period_end=current_period_start,
            ),
            "trend_bucket_days": trend_bucket_days,
            "breakdown_limit_applied": breakdown_limit,
            "trend": self._build_trend(current_decisions, days=days, bucket_days=trend_bucket_days),
            "category_breakdown": self._build_segment_breakdown(
                current_decisions,
                segment_resolver=self._resolve_category_segment,
                limit=breakdown_limit,
            ),
            "workload_source_breakdown": self._build_segment_breakdown(
                current_decisions,
                segment_resolver=lambda decision: str(decision.workload_source or self.DEFAULT_WORKLOAD_SOURCE),
                limit=breakdown_limit,
            ),
            "agency_breakdown": self._build_segment_breakdown(
                current_decisions,
                segment_resolver=self._resolve_agency_segment,
                limit=breakdown_limit,
            ),
            "recent_submissions": recent_submissions,
        }

    def build_recommendations(
        self,
        db: Session,
        *,
        days: int = 30,
        breakdown_limit: int = 5,
        trend_bucket_days: int = 7,
        recommendation_limit: int = 5,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Convert funnel analytics into actionable tuning recommendations."""
        if operator is None:
            operator = ensure_operator_account(db)
        funnel = self.build_funnel(
            db,
            days=days,
            limit=max(3, recommendation_limit),
            breakdown_limit=breakdown_limit,
            trend_bucket_days=trend_bucket_days,
            operator=operator,
        )

        recommendations = [
            recommendation
            for recommendation in [
                self._recommend_review_threshold(funnel),
                self._recommend_bid_now_threshold(funnel),
                self._recommend_workload_calibration(funnel),
                self._recommend_category_focus(funnel),
            ]
            if recommendation is not None
        ]
        experiment_history = self._build_recommendation_experiment_history(
            db,
            operator_id=int(funnel["operator_id"]),
            days=days,
        )
        recommendations = self._apply_experiment_history_to_recommendations(
            recommendations,
            experiment_history=experiment_history,
        )

        if not recommendations:
            recommendations.append(self._with_default_recommendation_priority({
                "key": "stable-funnel",
                "severity": "info",
                "title": "전환 퍼널이 비교적 안정적입니다.",
                "summary": "현재 기간에는 임계값을 즉시 조정해야 할 강한 하락 신호가 보이지 않습니다. 현재 추세를 유지하며 추가 표본을 모으는 편이 안전합니다.",
                "suggested_adjustment": "기존 `BID_NOW_THRESHOLD`/`REVIEW_THRESHOLD`를 유지하고 다음 기간 비교를 확인하세요.",
                "supporting_metrics": {
                    "overall_submission_rate": funnel.get("overall_submission_rate"),
                    "review_submission_rate": funnel.get("review_submission_rate"),
                    "bid_now_submission_rate": funnel.get("bid_now_submission_rate"),
                },
            }))

        recommendations = recommendations[:recommendation_limit]
        experiments: list[dict[str, Any]] = []
        enriched_recommendations: list[dict[str, Any]] = []
        for priority_rank, recommendation in enumerate(recommendations, start=1):
            experiment_plan = self._build_experiment_plan(recommendation, priority_rank=priority_rank)
            enriched_recommendations.append({
                **recommendation,
                "experiment_plan": experiment_plan,
            })
            if experiment_plan is not None:
                experiments.append(experiment_plan)

        return {
            "operator_id": funnel["operator_id"],
            "period_days": funnel["period_days"],
            "decision_count": funnel["decision_count"],
            "submitted_count": funnel["submitted_count"],
            "active_pending_count": funnel["active_pending_count"],
            "overall_submission_rate": funnel.get("overall_submission_rate"),
            "workflow_submission_rate": funnel.get("workflow_submission_rate"),
            "bid_now_submission_rate": funnel.get("bid_now_submission_rate"),
            "review_submission_rate": funnel.get("review_submission_rate"),
            "recommendation_count": len(enriched_recommendations),
            "recommendation_limit_applied": recommendation_limit,
            "experiment_count": len(experiments),
            "headline": self._build_recommendation_headline(funnel, recommendations),
            "comparison": funnel["comparison"],
            "experiment_history": experiment_history,
            "recommended_next_experiment": experiments[0] if experiments else None,
            "experiments": experiments,
            "recommendations": enriched_recommendations,
        }

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
            event_type="project_view",
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
            event_type="recommendation_feedback",
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

    def build_recommendation_feedback_labels(
        self,
        db: Session,
        *,
        days: int = 90,
        limit: int = 100,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Return false-positive/negative labels collected via recommendation feedback.

        Joins ``recommendation_feedback`` analytics events to their persisted
        ``BidDecisionRecord`` (and ``Project``) so the operator's verdicts become
        analyzable training data. Verdicts are deduped per ``decision_record_id``
        via the shared :meth:`_dedupe_latest_feedback_verdicts` helper. Orphan
        verdicts whose decision record is missing are skipped.
        """
        if operator is None:
            operator = ensure_operator_account(db)
        start_at = utc_now() - timedelta(days=days)
        feedback_events = self._load_events_in_range(
            db,
            operator_id=operator.id,
            event_type="recommendation_feedback",
            start_at=start_at,
        )
        latest_by_decision = self._dedupe_latest_feedback_verdicts(feedback_events)

        if not latest_by_decision:
            return {
                "operator_id": operator.id,
                "period_days": days,
                "label_count": 0,
                "useful_count": 0,
                "not_useful_count": 0,
                "review_value_rate": None,
                "by_category": {},
                "by_action": {},
                "items": [],
            }

        decision_ids = list(latest_by_decision.keys())
        decisions = (
            db.query(BidDecisionRecord)
            .options(selectinload(BidDecisionRecord.project))
            .filter(
                BidDecisionRecord.operator_id == operator.id,
                BidDecisionRecord.id.in_(decision_ids),
            )
            .all()
        )
        decisions_by_id = {int(record.id): record for record in decisions}

        items: list[dict[str, Any]] = []
        useful_count = 0
        not_useful_count = 0
        category_counters: dict[str, dict[str, int]] = {}
        action_counters: dict[str, dict[str, int]] = {}

        for decision_id, entry in latest_by_decision.items():
            decision = decisions_by_id.get(decision_id)
            if decision is None:
                # Orphan label: feedback pointing to a missing/foreign decision record.
                continue
            verdict = entry["verdict"]
            if verdict == "useful":
                useful_count += 1
            else:
                not_useful_count += 1

            project = decision.project
            project_category = (
                str(project.category) if project is not None and project.category else None
            )
            project_business_type_code = (
                str(project.business_type_code)
                if project is not None and project.business_type_code
                else None
            )
            project_title = (
                str(project.title)
                if project is not None and project.title
                else f"Project {decision.project_id}"
            )

            self._increment_label_breakdown(
                category_counters,
                key=project_category or self.UNKNOWN_CATEGORY,
                verdict=verdict,
            )
            self._increment_label_breakdown(
                action_counters,
                key=str(decision.action or "skip"),
                verdict=verdict,
            )

            strengths, risk_flags = _extract_decision_reasons(decision.score_breakdown)
            reasoning_excerpt = self._reasoning_excerpt(decision.reasoning)
            items.append(
                {
                    "decision_record_id": int(decision.id),
                    "project_id": int(decision.project_id) if decision.project_id is not None else 0,
                    "project_title": project_title,
                    "project_category": project_category,
                    "project_business_type_code": project_business_type_code,
                    "action": str(decision.action or "skip"),
                    "decision_status": str(decision.decision_status or "planned"),
                    "priority_score": float(decision.priority_score or 0.0),
                    "verdict": verdict,
                    "feedback_at": entry["feedback_at"],
                    "reasoning": reasoning_excerpt,
                    "strengths": strengths,
                    "risk_flags": risk_flags,
                }
            )

        # Newest feedback first; missing timestamps sort last deterministically by id.
        items.sort(
            key=lambda item: (
                item["feedback_at"] is None,
                -(item["feedback_at"].timestamp() if item["feedback_at"] is not None else 0.0),
                -int(item["decision_record_id"]),
            )
        )
        clamped_items = items[: max(limit, 0)]

        label_count = useful_count + not_useful_count
        return {
            "operator_id": operator.id,
            "period_days": days,
            "label_count": label_count,
            "useful_count": useful_count,
            "not_useful_count": not_useful_count,
            "review_value_rate": self._rate(useful_count, label_count),
            "by_category": self._finalize_label_breakdown(category_counters),
            "by_action": self._finalize_label_breakdown(action_counters),
            "items": clamped_items,
        }

    def _increment_label_breakdown(
        self,
        counters: dict[str, dict[str, int]],
        *,
        key: str,
        verdict: str,
    ) -> None:
        """Accumulate useful/not_useful counts for one breakdown key."""
        bucket = counters.setdefault(key, {"useful": 0, "not_useful": 0, "total": 0})
        bucket[verdict] = bucket.get(verdict, 0) + 1
        bucket["total"] = bucket.get("total", 0) + 1

    def _finalize_label_breakdown(
        self, counters: dict[str, dict[str, int]]
    ) -> dict[str, dict[str, Any]]:
        """Attach the useful-share rate to each breakdown bucket."""
        finalized: dict[str, dict[str, Any]] = {}
        for key, bucket in counters.items():
            total = int(bucket.get("total", 0))
            useful = int(bucket.get("useful", 0))
            not_useful = int(bucket.get("not_useful", 0))
            finalized[key] = {
                "useful": useful,
                "not_useful": not_useful,
                "total": total,
                "rate": self._rate(useful, total),
            }
        return finalized

    def _reasoning_excerpt(self, reasoning: Any, *, limit: int = 200) -> str:
        """Return a bounded excerpt of the persisted reasoning text."""
        if reasoning is None:
            return ""
        text = str(reasoning)
        if len(text) <= limit:
            return text
        return text[:limit]

    def _coerce_int(self, value: Any) -> int | None:
        """Best-effort coercion of event payload identifiers to int."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if text.lstrip("-").isdigit():
                return int(text)
        return None

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

    def _build_recommendation_experiment_history(
        self,
        db: Session,
        *,
        operator_id: int,
        days: int,
    ) -> dict[str, Any]:
        """Summarize prior experiment outcomes for recommendation ranking."""
        lookback_days = max(self.EXPERIMENT_HISTORY_MIN_LOOKBACK_DAYS, int(days) * 6)
        lookback_days = min(self.EXPERIMENT_HISTORY_MAX_LOOKBACK_DAYS, lookback_days)
        date_from = utc_now() - timedelta(days=lookback_days)
        runs = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.operator_id == operator_id,
                DecisionExperimentRun.updated_at >= date_from,
            )
            .order_by(DecisionExperimentRun.updated_at.desc(), DecisionExperimentRun.id.desc())
            .all()
        )
        by_recommendation: dict[str, list[DecisionExperimentRun]] = {}
        for run in runs:
            key = str(run.recommendation_key or "").strip()
            if not key:
                continue
            by_recommendation.setdefault(key, []).append(run)

        recommendation_summaries = {
            key: self._summarize_experiment_runs_for_recommendation(key, key_runs)
            for key, key_runs in sorted(by_recommendation.items())
        }
        return {
            "operator_id": operator_id,
            "lookback_days": lookback_days,
            "run_count": len(runs),
            "evaluated_count": sum(1 for run in runs if str(run.outcome or "") in {"success", "rollback", "inconclusive"}),
            "success_count": sum(1 for run in runs if str(run.outcome or "") == "success"),
            "rollback_count": sum(1 for run in runs if str(run.outcome or "") == "rollback"),
            "inconclusive_count": sum(1 for run in runs if str(run.outcome or "") == "inconclusive"),
            "pending_count": sum(1 for run in runs if str(run.outcome or "") in {"", "watch", "insufficient_data"}),
            "failed_count": sum(1 for run in runs if str(run.status or "") == "failed"),
            "applied_count": sum(1 for run in runs if self._experiment_run_applied(run)),
            "recommendation_summaries": recommendation_summaries,
        }

    def _apply_experiment_history_to_recommendations(
        self,
        recommendations: list[dict[str, Any]],
        *,
        experiment_history: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Attach prior-run context and sort recommendations by adjusted priority."""
        summaries = experiment_history.get("recommendation_summaries")
        if not isinstance(summaries, dict):
            summaries = {}

        enriched: list[dict[str, Any]] = []
        for original_index, recommendation in enumerate(recommendations):
            key = str(recommendation.get("key") or "")
            history_summary = summaries.get(key) if isinstance(summaries.get(key), dict) else None
            base_score = self._base_recommendation_priority_score(recommendation, original_index=original_index)
            adjustment = self._build_history_adjustment(history_summary)
            priority_score = max(0.0, round(base_score + float(adjustment["priority_delta"]), 2))
            adjusted_recommendation = {
                **recommendation,
                "severity": self._adjust_recommendation_severity(
                    str(recommendation.get("severity") or "info"),
                    adjustment=adjustment,
                ),
                "priority_score": priority_score,
                "history_adjustment": adjustment,
                "_original_index": original_index,
            }
            adjusted_recommendation["supporting_metrics"] = {
                **(recommendation.get("supporting_metrics") or {}),
                "experiment_history": history_summary or {},
            }
            adjusted_recommendation = self._attach_parameter_recommendation(
                adjusted_recommendation,
                adjustment=adjustment,
                history_summary=history_summary,
            )
            enriched.append(adjusted_recommendation)

        ordered = sorted(
            enriched,
            key=lambda item: (
                -float(item.get("priority_score") or 0.0),
                int(item.get("_original_index", 0)),
                str(item.get("key") or ""),
            ),
        )
        for item in ordered:
            item.pop("_original_index", None)
        return ordered

    def _with_default_recommendation_priority(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        """Attach neutral ranking metadata to fallback recommendations."""
        return {
            **recommendation,
            "priority_score": self._base_recommendation_priority_score(recommendation, original_index=0),
            "history_adjustment": self._build_history_adjustment(None),
            "parameter_recommendation": {},
        }

    def _summarize_experiment_runs_for_recommendation(
        self,
        recommendation_key: str,
        runs: list[DecisionExperimentRun],
    ) -> dict[str, Any]:
        """Build one recommendation-key experiment history summary."""
        success_runs = [run for run in runs if str(run.outcome or "") == "success"]
        rollback_runs = [run for run in runs if str(run.outcome or "") == "rollback"]
        failed_runs = [run for run in runs if str(run.status or "") == "failed"]
        pending_runs = [run for run in runs if str(run.outcome or "") in {"", "watch", "insufficient_data"}]
        applied_runs = [run for run in runs if self._experiment_run_applied(run)]
        evaluated_count = len(success_runs) + len(rollback_runs) + sum(
            1 for run in runs if str(run.outcome or "") == "inconclusive"
        )
        latest_run = max(runs, key=lambda run: (run.updated_at, int(run.id or 0))) if runs else None
        return {
            "recommendation_key": recommendation_key,
            "run_count": len(runs),
            "evaluated_count": evaluated_count,
            "success_count": len(success_runs),
            "rollback_count": len(rollback_runs),
            "failed_count": len(failed_runs),
            "pending_count": len(pending_runs),
            "applied_count": len(applied_runs),
            "success_rate": self._rate(len(success_runs), evaluated_count),
            "latest_run_id": int(latest_run.id) if latest_run is not None else None,
            "latest_status": str(latest_run.status or "") if latest_run is not None else None,
            "latest_outcome": str(latest_run.outcome) if latest_run is not None and latest_run.outcome else None,
            "latest_updated_at": latest_run.updated_at if latest_run is not None else None,
        }

    def _build_history_adjustment(self, history_summary: dict[str, Any] | None) -> dict[str, Any]:
        """Translate experiment history into a priority adjustment."""
        if not history_summary:
            return {
                "status": "neutral",
                "priority_delta": 0.0,
                "reason": "아직 이 추천 유형의 실험 이력이 없습니다.",
                "recent_run_count": 0,
                "success_count": 0,
                "rollback_count": 0,
                "failed_count": 0,
                "pending_count": 0,
                "applied_count": 0,
            }

        run_count = int(history_summary.get("run_count") or 0)
        success_count = int(history_summary.get("success_count") or 0)
        rollback_count = int(history_summary.get("rollback_count") or 0)
        failed_count = int(history_summary.get("failed_count") or 0)
        pending_count = int(history_summary.get("pending_count") or 0)
        applied_count = int(history_summary.get("applied_count") or 0)
        negative_count = rollback_count + failed_count
        if applied_count > 0 and success_count > 0:
            status = "promoted"
            priority_delta = 14.0
            reason = "성공 후 운영 전략에 적용된 실험 이력이 있어 후속 실험 우선순위를 높였습니다."
        elif negative_count >= 2 and success_count == 0:
            status = "deprioritized"
            priority_delta = -55.0
            reason = "반복 실패 또는 롤백 이력이 있어 같은 유형의 실험을 뒤로 미뤘습니다."
        elif negative_count > success_count:
            status = "deprioritized"
            priority_delta = -35.0
            reason = "실패/롤백 이력이 성공 이력보다 많아 우선순위를 낮췄습니다."
        elif pending_count >= 2 and success_count == 0:
            status = "deprioritized"
            priority_delta = -20.0
            reason = "보류 또는 표본 부족 이력이 반복되어 추가 추천 강도를 낮췄습니다."
        elif success_count > 0:
            status = "promoted"
            priority_delta = 8.0
            reason = "성공한 실험 이력이 있어 같은 계열의 후속 실험 신뢰도를 높였습니다."
        else:
            status = "neutral"
            priority_delta = 0.0
            reason = "이력상 우선순위를 조정할 충분한 신호가 없습니다."

        return {
            "status": status,
            "priority_delta": priority_delta,
            "reason": reason,
            "recent_run_count": run_count,
            "success_count": success_count,
            "rollback_count": rollback_count,
            "failed_count": failed_count,
            "pending_count": pending_count,
            "applied_count": applied_count,
        }

    def _base_recommendation_priority_score(self, recommendation: dict[str, Any], *, original_index: int) -> float:
        """Return a stable base score before historical adjustment."""
        severity_score = {
            "action": 100.0,
            "watch": 70.0,
            "info": 40.0,
        }.get(str(recommendation.get("severity") or "info"), 40.0)
        return severity_score - (float(original_index) * 0.01)

    def _adjust_recommendation_severity(self, severity: str, *, adjustment: dict[str, Any]) -> str:
        """Downgrade visible urgency for heavily penalized recommendations."""
        if adjustment.get("status") != "deprioritized":
            return severity if severity in {"info", "watch", "action"} else "info"
        priority_delta = float(adjustment.get("priority_delta") or 0.0)
        if priority_delta <= -45.0 and severity == "action":
            return "watch"
        if priority_delta <= -45.0 and severity == "watch":
            return "info"
        return severity if severity in {"info", "watch", "action"} else "info"

    def _attach_parameter_recommendation(
        self,
        recommendation: dict[str, Any],
        *,
        adjustment: dict[str, Any],
        history_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Attach concrete parameter deltas adjusted by long-running experiment history."""
        parameter_recommendation = self._build_parameter_recommendation(
            recommendation,
            adjustment=adjustment,
            history_summary=history_summary,
        )
        if not parameter_recommendation:
            return {
                **recommendation,
                "parameter_recommendation": {},
            }

        supporting_metrics = {
            **(recommendation.get("supporting_metrics") or {}),
            "parameter_recommendation": parameter_recommendation,
        }
        return {
            **recommendation,
            "suggested_adjustment": self._render_parameter_adjustment(
                recommendation,
                parameter_recommendation=parameter_recommendation,
            ),
            "supporting_metrics": supporting_metrics,
            "parameter_recommendation": parameter_recommendation,
        }

    def _build_parameter_recommendation(
        self,
        recommendation: dict[str, Any],
        *,
        adjustment: dict[str, Any],
        history_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build concrete threshold/category parameter suggestions from historical outcomes."""
        key = str(recommendation.get("key") or "")
        multiplier = self._parameter_delta_multiplier(adjustment)
        confidence = self._parameter_confidence(adjustment)
        history_counts = self._parameter_history_counts(history_summary)
        history_reason = str(adjustment.get("reason") or "")

        threshold_spec = self.THRESHOLD_PARAMETER_DELTAS.get(key)
        if threshold_spec is not None:
            base_delta = float(threshold_spec["base_delta"])
            recommended_delta = self._bounded_parameter_delta(
                base_delta * multiplier,
                min_delta=float(threshold_spec["min_delta"]),
                max_delta=float(threshold_spec["max_delta"]),
            )
            return {
                "type": "threshold",
                "parameter": threshold_spec["parameter"],
                "label": threshold_spec["label"],
                "direction": threshold_spec["direction"],
                "base_delta": base_delta,
                "recommended_delta": recommended_delta,
                "min_delta": float(threshold_spec["min_delta"]),
                "max_delta": float(threshold_spec["max_delta"]),
                "delta_multiplier": round(multiplier, 4),
                "confidence": confidence,
                "history_status": str(adjustment.get("status") or "neutral"),
                "history_reason": history_reason,
                "history_counts": history_counts,
            }

        if key == "category-focus-shift":
            metrics = recommendation.get("supporting_metrics") or {}
            base_delta = self.CATEGORY_PARAMETER_BASE_DELTA
            recommended_delta = self._bounded_parameter_delta(
                base_delta * multiplier,
                min_delta=self.CATEGORY_PARAMETER_MIN_DELTA,
                max_delta=self.CATEGORY_PARAMETER_MAX_DELTA,
            )
            return {
                "type": "category_priority",
                "parameter": "category_priority_overrides",
                "label": "CATEGORY_PRIORITY_OVERRIDES",
                "direction": "replace",
                "base_delta": base_delta,
                "best_category": metrics.get("best_category"),
                "worst_category": metrics.get("worst_category"),
                "best_category_delta": recommended_delta,
                "worst_category_delta": -recommended_delta,
                "min_delta": self.CATEGORY_PARAMETER_MIN_DELTA,
                "max_delta": self.CATEGORY_PARAMETER_MAX_DELTA,
                "delta_multiplier": round(multiplier, 4),
                "confidence": confidence,
                "history_status": str(adjustment.get("status") or "neutral"),
                "history_reason": history_reason,
                "history_counts": history_counts,
            }

        return {}

    def _parameter_delta_multiplier(self, adjustment: dict[str, Any]) -> float:
        """Return the parameter-change scale implied by historical outcomes."""
        status = str(adjustment.get("status") or "neutral")
        priority_delta = float(adjustment.get("priority_delta") or 0.0)
        applied_count = int(adjustment.get("applied_count") or 0)
        success_count = int(adjustment.get("success_count") or 0)
        if status == "promoted" and applied_count > 0:
            return 1.25
        if status == "promoted" and success_count > 0:
            return 1.15
        if status == "deprioritized":
            if priority_delta <= -45.0:
                return 0.5
            if priority_delta <= -30.0:
                return 0.65
            return 0.75
        return 1.0

    def _parameter_confidence(self, adjustment: dict[str, Any]) -> float:
        """Return a compact confidence score for the adjusted parameter suggestion."""
        status = str(adjustment.get("status") or "neutral")
        priority_delta = float(adjustment.get("priority_delta") or 0.0)
        applied_count = int(adjustment.get("applied_count") or 0)
        if status == "promoted" and applied_count > 0:
            return 0.78
        if status == "promoted":
            return 0.68
        if status == "deprioritized" and priority_delta <= -45.0:
            return 0.35
        if status == "deprioritized":
            return 0.42
        return 0.55

    def _parameter_history_counts(self, history_summary: dict[str, Any] | None) -> dict[str, int]:
        """Serialize history counts used by parameter recommendation formulas."""
        if not history_summary:
            return {
                "run_count": 0,
                "success_count": 0,
                "rollback_count": 0,
                "failed_count": 0,
                "pending_count": 0,
                "applied_count": 0,
            }
        return {
            "run_count": int(history_summary.get("run_count") or 0),
            "success_count": int(history_summary.get("success_count") or 0),
            "rollback_count": int(history_summary.get("rollback_count") or 0),
            "failed_count": int(history_summary.get("failed_count") or 0),
            "pending_count": int(history_summary.get("pending_count") or 0),
            "applied_count": int(history_summary.get("applied_count") or 0),
        }

    def _bounded_parameter_delta(self, value: float, *, min_delta: float, max_delta: float) -> float:
        """Clamp parameter deltas into a small operationally safe range."""
        return round(max(float(min_delta), min(float(value), float(max_delta))), 4)

    def _render_parameter_adjustment(
        self,
        recommendation: dict[str, Any],
        *,
        parameter_recommendation: dict[str, Any],
    ) -> str:
        """Render operator-facing text from the concrete parameter recommendation."""
        parameter_type = str(parameter_recommendation.get("type") or "")
        history_reason = str(parameter_recommendation.get("history_reason") or "").strip()
        if parameter_type == "threshold":
            label = str(parameter_recommendation.get("label") or "")
            direction = str(parameter_recommendation.get("direction") or "increase")
            direction_text = "상향" if direction == "increase" else "하향"
            delta = float(parameter_recommendation.get("recommended_delta") or 0.0)
            return (
                f"`{label}`를 {delta:.3f} {direction_text}하는 실험을 권장합니다. "
                f"{history_reason}"
            ).strip()

        if parameter_type == "category_priority":
            best_category = parameter_recommendation.get("best_category") or "고성과 카테고리"
            worst_category = parameter_recommendation.get("worst_category") or "저성과 카테고리"
            best_delta = float(parameter_recommendation.get("best_category_delta") or 0.0)
            worst_delta = float(parameter_recommendation.get("worst_category_delta") or 0.0)
            return (
                f"`{best_category}` 우선순위 override를 {best_delta:+.3f}, "
                f"`{worst_category}` override를 {worst_delta:+.3f} 조정하는 실험을 권장합니다. "
                f"{history_reason}"
            ).strip()

        return str(recommendation.get("suggested_adjustment") or "")

    def _experiment_run_applied(self, run: DecisionExperimentRun) -> bool:
        """Return whether an experiment run has been applied to operator settings."""
        notes = str(run.notes or "")
        return any(marker in notes for marker in self.EXPERIMENT_APPLICATION_MARKERS)

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

    def _build_funnel_summary(self, decisions: list[BidDecisionRecord]) -> dict[str, Any]:
        """Compute reusable top-level funnel metrics for one period."""
        submitted_decisions = [decision for decision in decisions if self._is_submitted(decision)]
        active_pending_decisions = [decision for decision in decisions if self._is_active_pending(decision)]
        skipped_decisions = [decision for decision in decisions if self._is_skipped(decision)]
        workflow_entries = [decision for decision in decisions if self._entry_status(decision) != "submitted"]
        bid_now_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "bid_now"]
        review_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "review"]
        skip_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "skip"]
        direct_submitted = [decision for decision in submitted_decisions if self._entry_status(decision) == "submitted"]
        submitted_after_bid_now = [
            decision
            for decision in submitted_decisions
            if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "bid_now"
        ]
        submitted_after_review = [
            decision
            for decision in submitted_decisions
            if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "review"
        ]
        submitted_after_skip = [
            decision
            for decision in submitted_decisions
            if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "skip"
        ]
        submission_durations = [
            hours_to_submit
            for hours_to_submit in (self._compute_hours_to_submit(decision) for decision in submitted_decisions)
            if hours_to_submit is not None
        ]

        return {
            "decision_count": len(decisions),
            "project_count": len({int(decision.project_id) for decision in decisions if decision.project_id is not None}),
            "active_pending_count": len(active_pending_decisions),
            "submitted_count": len(submitted_decisions),
            "skipped_count": len(skipped_decisions),
            "entry_bid_now_count": len(bid_now_entries),
            "entry_review_count": len(review_entries),
            "entry_skip_count": len(skip_entries),
            "direct_submitted_count": len(direct_submitted),
            "submitted_after_bid_now_count": len(submitted_after_bid_now),
            "submitted_after_review_count": len(submitted_after_review),
            "submitted_after_skip_count": len(submitted_after_skip),
            "overall_submission_rate": self._rate(len(submitted_decisions), len(decisions)),
            "workflow_submission_rate": self._rate(
                len(submitted_after_bid_now) + len(submitted_after_review) + len(submitted_after_skip),
                len(workflow_entries),
            ),
            "bid_now_submission_rate": self._rate(len(submitted_after_bid_now), len(bid_now_entries)),
            "review_submission_rate": self._rate(len(submitted_after_review), len(review_entries)),
            "average_hours_to_submit": self._average(submission_durations),
        }

    def _build_recent_submissions(self, decisions: list[BidDecisionRecord], *, limit: int) -> list[dict[str, Any]]:
        """Serialize recent submitted decisions for the current analysis window."""
        submitted_decisions = [decision for decision in decisions if self._is_submitted(decision)]
        items: list[dict[str, Any]] = []
        for decision in submitted_decisions[:limit]:
            strengths, risk_flags = _extract_decision_reasons(decision.score_breakdown)
            items.append(
                {
                    "decision_record_id": int(decision.id),
                    "project_id": int(decision.project_id),
                    "project_title": str(decision.project.title) if decision.project is not None else f"Project {decision.project_id}",
                    "initial_action": self._entry_action(decision),
                    "initial_decision_status": self._entry_status(decision),
                    "current_action": str(decision.action or self._entry_action(decision)),
                    "current_decision_status": str(decision.decision_status or "submitted"),
                    "priority_score": float(decision.priority_score or 0.0),
                    "recommended_amount": float(decision.recommended_amount or 0.0),
                    "first_decided_at": self._entry_datetime(decision),
                    "submitted_at": decision.updated_at,
                    "hours_to_submit": self._compute_hours_to_submit(decision),
                    "strengths": strengths,
                    "risk_flags": risk_flags,
                }
            )
        return items

    def _build_funnel_comparison(
        self,
        current_summary: dict[str, Any],
        previous_summary: dict[str, Any],
        *,
        current_period_start,
        current_period_end,
        previous_period_start,
        previous_period_end,
    ) -> dict[str, Any]:
        """Compute period-over-period deltas for the most actionable funnel metrics."""
        return {
            "current_period_start": current_period_start,
            "current_period_end": current_period_end,
            "previous_period_start": previous_period_start,
            "previous_period_end": previous_period_end,
            "decision_count_delta": int(current_summary["decision_count"] - previous_summary["decision_count"]),
            "project_count_delta": int(current_summary["project_count"] - previous_summary["project_count"]),
            "submitted_count_delta": int(current_summary["submitted_count"] - previous_summary["submitted_count"]),
            "active_pending_count_delta": int(current_summary["active_pending_count"] - previous_summary["active_pending_count"]),
            "skipped_count_delta": int(current_summary["skipped_count"] - previous_summary["skipped_count"]),
            "overall_submission_rate_delta": self._delta(current_summary["overall_submission_rate"], previous_summary["overall_submission_rate"]),
            "workflow_submission_rate_delta": self._delta(current_summary["workflow_submission_rate"], previous_summary["workflow_submission_rate"]),
            "bid_now_submission_rate_delta": self._delta(current_summary["bid_now_submission_rate"], previous_summary["bid_now_submission_rate"]),
            "review_submission_rate_delta": self._delta(current_summary["review_submission_rate"], previous_summary["review_submission_rate"]),
            "average_hours_to_submit_delta": self._delta(current_summary["average_hours_to_submit"], previous_summary["average_hours_to_submit"]),
        }

    def _recommend_review_threshold(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend tightening or relaxing the review threshold based on review conversion."""
        review_entry_count = int(funnel.get("entry_review_count") or 0)
        current_rate = funnel.get("review_submission_rate")
        delta = (funnel.get("comparison") or {}).get("review_submission_rate_delta")
        if review_entry_count < 3 or current_rate is None:
            return None

        if current_rate < self.REVIEW_RATE_TIGHTEN_THRESHOLD:
            severity = "action" if delta is not None and delta <= -0.1 else "watch"
            return {
                "key": "review-threshold-tighten",
                "severity": severity,
                "title": "검토 대기열 품질을 더 엄격하게 관리하세요.",
                "summary": f"현재 기간의 review 진입 {review_entry_count}건 중 제출 전환율이 {current_rate:.2f}로 낮습니다. 약한 후보가 review 큐에 과하게 남아 있을 가능성이 큽니다.",
                "suggested_adjustment": "`REVIEW_THRESHOLD`를 0.03~0.07 상향하거나 review 후보 생성 시 예상 수익성/복잡도 조건을 더 엄격하게 두는 실험을 권장합니다.",
                "supporting_metrics": {
                    "entry_review_count": review_entry_count,
                    "current_review_submission_rate": current_rate,
                    "review_submission_rate_delta": delta,
                },
            }

        if current_rate > self.REVIEW_RATE_RELAX_THRESHOLD and review_entry_count >= 4:
            return {
                "key": "review-threshold-relax",
                "severity": "info",
                "title": "검토 후보를 조금 더 넓게 받아볼 여지가 있습니다.",
                "summary": f"review 진입 {review_entry_count}건의 제출 전환율이 {current_rate:.2f}로 높습니다. 현재 검토 기준이 다소 보수적일 수 있습니다.",
                "suggested_adjustment": "`REVIEW_THRESHOLD`를 0.02~0.04 낮추고 추가 후보 유입 시 품질이 유지되는지 확인해 보세요.",
                "supporting_metrics": {
                    "entry_review_count": review_entry_count,
                    "current_review_submission_rate": current_rate,
                    "review_submission_rate_delta": delta,
                },
            }
        return None

    def _recommend_bid_now_threshold(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend tightening the immediate-bid threshold when direct submissions underperform."""
        bid_now_entry_count = int(funnel.get("entry_bid_now_count") or 0)
        current_rate = funnel.get("bid_now_submission_rate")
        delta = (funnel.get("comparison") or {}).get("bid_now_submission_rate_delta")
        if bid_now_entry_count < 3 or current_rate is None:
            return None
        if current_rate >= self.BID_NOW_RATE_TIGHTEN_THRESHOLD:
            return None

        return {
            "key": "bid-now-threshold-tighten",
            "severity": "action" if delta is not None and delta < 0 else "watch",
            "title": "즉시 투찰 기준을 다소 보수적으로 조정하세요.",
            "summary": f"bid_now 진입 {bid_now_entry_count}건의 제출 전환율이 {current_rate:.2f}입니다. 즉시 추진으로 분류되는 후보의 질이 낮아졌을 수 있습니다.",
            "suggested_adjustment": "`BID_NOW_THRESHOLD`를 0.02~0.05 상향하고, 높은 실행 복잡도 케이스의 즉시 승격을 줄이는 편이 안전합니다.",
            "supporting_metrics": {
                "entry_bid_now_count": bid_now_entry_count,
                "current_bid_now_submission_rate": current_rate,
                "bid_now_submission_rate_delta": delta,
            },
        }

    def _recommend_workload_calibration(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend reviewing auto workload calibration when auto-tagged cases underperform."""
        auto_segment = self._find_segment(funnel.get("workload_source_breakdown", []), "auto")
        provided_segment = self._find_segment(funnel.get("workload_source_breakdown", []), self.DEFAULT_WORKLOAD_SOURCE)
        if auto_segment is None or provided_segment is None:
            return None
        if int(auto_segment.get("decision_count") or 0) < 2 or int(provided_segment.get("decision_count") or 0) < 2:
            return None

        auto_rate = auto_segment.get("submission_rate")
        provided_rate = provided_segment.get("submission_rate")
        if auto_rate is None or provided_rate is None:
            return None

        gap = round(float(provided_rate) - float(auto_rate), 4)
        if gap < 0.2:
            return None

        return {
            "key": "workload-auto-calibration",
            "severity": "action" if gap >= self.WORKLOAD_GAP_ACTION_THRESHOLD else "watch",
            "title": "자동 업무부하 산정 로직을 재점검하세요.",
            "summary": f"auto workload 케이스의 제출 전환율이 {auto_rate:.2f}, provided 케이스는 {provided_rate:.2f}입니다. 자동 부하 점수가 후보를 과하게 눌러서 전환을 저해할 수 있습니다.",
            "suggested_adjustment": "auto workload 계산식의 상한을 낮추거나 `load_penalty` 가중치를 축소하는 실험을 권장합니다.",
            "supporting_metrics": {
                "auto_decision_count": auto_segment.get("decision_count"),
                "auto_submission_rate": auto_rate,
                "provided_decision_count": provided_segment.get("decision_count"),
                "provided_submission_rate": provided_rate,
                "submission_rate_gap": gap,
            },
        }

    def _recommend_category_focus(self, funnel: dict[str, Any]) -> dict[str, Any] | None:
        """Recommend shifting category focus when one category clearly outperforms another."""
        segments = [
            segment
            for segment in funnel.get("category_breakdown", [])
            if int(segment.get("decision_count") or 0) >= 2 and segment.get("submission_rate") is not None
        ]
        if len(segments) < 2:
            return None

        best_segment = max(segments, key=lambda item: (float(item.get("submission_rate") or 0.0), int(item.get("decision_count") or 0)))
        worst_segment = min(segments, key=lambda item: (float(item.get("submission_rate") or 0.0), -int(item.get("decision_count") or 0)))
        gap = round(float(best_segment.get("submission_rate") or 0.0) - float(worst_segment.get("submission_rate") or 0.0), 4)
        if gap < 0.25:
            return None

        return {
            "key": "category-focus-shift",
            "severity": "action" if gap >= self.CATEGORY_GAP_ACTION_THRESHOLD else "watch",
            "title": "카테고리별 우선순위를 다시 배분하세요.",
            "summary": f"`{best_segment['segment']}` 카테고리 제출 전환율은 {best_segment['submission_rate']:.2f}인데 반해 `{worst_segment['segment']}`는 {worst_segment['submission_rate']:.2f}입니다. 카테고리별 필터 강도를 다르게 가져갈 필요가 있습니다.",
            "suggested_adjustment": f"`{worst_segment['segment']}` 카테고리에는 더 높은 확률/적합도 기준을 적용하고, `{best_segment['segment']}`에는 모니터링 우선순위를 높이는 편이 유리합니다.",
            "supporting_metrics": {
                "best_category": best_segment.get("segment"),
                "best_category_submission_rate": best_segment.get("submission_rate"),
                "worst_category": worst_segment.get("segment"),
                "worst_category_submission_rate": worst_segment.get("submission_rate"),
                "submission_rate_gap": gap,
            },
        }

    def _find_segment(self, segments: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
        """Return one breakdown segment by exact label."""
        for segment in segments:
            if str(segment.get("segment")) == label:
                return segment
        return None

    def _build_experiment_plan(self, recommendation: dict[str, Any], *, priority_rank: int) -> dict[str, Any] | None:
        """Translate one tuning recommendation into a lightweight experiment plan."""
        key = str(recommendation.get("key") or "")
        metrics = recommendation.get("supporting_metrics") or {}
        parameter_recommendation = recommendation.get("parameter_recommendation")
        if not isinstance(parameter_recommendation, dict):
            parameter_recommendation = {}

        if key == "review-threshold-tighten":
            delta = float(parameter_recommendation.get("recommended_delta") or 0.04)
            return {
                "experiment_key": "exp-review-threshold-tighten",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "Review threshold 상향 실험",
                "hypothesis": "review 후보 진입 기준을 높이면 review 대기열 품질이 개선되어 제출 전환율이 올라갑니다.",
                "suggested_change": f"`REVIEW_THRESHOLD`를 {delta:.3f} 높이고, 복잡도 상위 케이스는 review 진입에서 한 단계 더 엄격하게 거릅니다.",
                "target_metric": "review_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "review_submission_rate가 최소 +0.10p 개선되고 active_pending_count가 증가하지 않으면 성공으로 간주합니다.",
                "guardrail_metric": "overall_submission_rate",
                "minimum_decision_sample": max(4, int(metrics.get("entry_review_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "overall_submission_rate가 0.05p 이상 하락하거나 review 후보 수가 절반 이하로 줄면 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
            }

        if key == "review-threshold-relax":
            delta = float(parameter_recommendation.get("recommended_delta") or 0.03)
            return {
                "experiment_key": "exp-review-threshold-relax",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "Review threshold 완화 실험",
                "hypothesis": "review 기준을 조금 낮추면 제출 품질을 크게 해치지 않으면서 더 많은 유의미 후보를 검토 큐에 올릴 수 있습니다.",
                "suggested_change": f"`REVIEW_THRESHOLD`를 {delta:.3f} 낮추고 review 후보 수 변화와 전환율을 함께 추적합니다.",
                "target_metric": "decision_count",
                "expected_direction": "increase",
                "success_criteria": "review 후보 수가 늘면서 review_submission_rate가 0.60 이상 유지되면 성공으로 간주합니다.",
                "guardrail_metric": "review_submission_rate",
                "minimum_decision_sample": max(4, int(metrics.get("entry_review_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "review_submission_rate가 0.15p 이상 하락하면 즉시 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
            }

        if key == "bid-now-threshold-tighten":
            delta = float(parameter_recommendation.get("recommended_delta") or 0.03)
            return {
                "experiment_key": "exp-bid-now-threshold-tighten",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "즉시 투찰 기준 상향 실험",
                "hypothesis": "bid_now 기준을 높이면 즉시 추진 후보의 질이 개선되어 제출 완료 비율이 높아집니다.",
                "suggested_change": f"`BID_NOW_THRESHOLD`를 {delta:.3f} 높이고 복잡도 상위 케이스는 review로 유도합니다.",
                "target_metric": "bid_now_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "bid_now_submission_rate가 +0.10p 이상 개선되고 submitted_count가 유지되면 성공으로 봅니다.",
                "guardrail_metric": "submitted_count",
                "minimum_decision_sample": max(4, int(metrics.get("entry_bid_now_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "submitted_count가 20% 이상 감소하면 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
            }

        if key == "workload-auto-calibration":
            return {
                "experiment_key": "exp-workload-auto-calibration",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "자동 workload 보정 실험",
                "hypothesis": "auto workload 계산 상한과 감점 강도를 낮추면 자동 추정 케이스의 과도한 감점이 줄어 제출 전환율이 개선됩니다.",
                "suggested_change": "auto workload 상한을 0.10~0.15 낮추거나 `load_penalty` 가중치를 축소해 auto 케이스를 재평가합니다.",
                "target_metric": "auto_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "auto_submission_rate가 +0.10p 이상 개선되고 active_pending_count가 20% 이상 늘지 않으면 성공으로 간주합니다.",
                "guardrail_metric": "active_pending_count",
                "minimum_decision_sample": max(4, int(metrics.get("auto_decision_count") or 4)),
                "duration_days": 14,
                "rollback_trigger": "active_pending_count가 20% 이상 늘거나 overall_submission_rate가 하락하면 롤백합니다.",
            }

        if key == "category-focus-shift":
            best_delta = float(parameter_recommendation.get("best_category_delta") or 0.03)
            worst_delta = float(parameter_recommendation.get("worst_category_delta") or -0.03)
            best_category = parameter_recommendation.get("best_category") or metrics.get("best_category") or "고성과 카테고리"
            worst_category = parameter_recommendation.get("worst_category") or metrics.get("worst_category") or "저성과 카테고리"
            return {
                "experiment_key": "exp-category-focus-shift",
                "recommendation_key": key,
                "priority_rank": priority_rank,
                "title": "카테고리별 기준 차등화 실험",
                "hypothesis": "전환이 약한 카테고리에 더 엄격한 기준을 적용하고 강한 카테고리에 더 많은 탐색 용량을 주면 전체 제출 효율이 개선됩니다.",
                "suggested_change": f"`{best_category}` category override를 {best_delta:+.3f}, `{worst_category}` override를 {worst_delta:+.3f} 조정합니다.",
                "target_metric": "worst_category_submission_rate",
                "expected_direction": "increase",
                "success_criteria": "저성과 카테고리 제출 전환율이 +0.10p 이상 개선되거나 전체 submission_rate가 유지되면 성공으로 봅니다.",
                "guardrail_metric": "best_category_submission_rate",
                "minimum_decision_sample": 4,
                "duration_days": 21,
                "rollback_trigger": "고성과 카테고리 제출 전환율이 0.10p 이상 하락하면 롤백합니다.",
                "parameter_recommendation": parameter_recommendation,
            }

        return None

    def _build_recommendation_headline(self, funnel: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
        """Create a compact narrative headline for dashboard summaries."""
        action_count = sum(1 for item in recommendations if item.get("severity") == "action")
        watch_count = sum(1 for item in recommendations if item.get("severity") == "watch")
        overall_delta = (funnel.get("comparison") or {}).get("overall_submission_rate_delta")
        if action_count:
            if overall_delta is not None:
                return f"즉시 점검이 필요한 항목 {action_count}건이 있습니다. 전체 제출 전환율은 직전 기간 대비 {overall_delta:+.2f}p 변화했습니다."
            return f"즉시 점검이 필요한 항목 {action_count}건이 있습니다."
        if watch_count:
            if overall_delta is not None:
                return f"주의 깊게 볼 최적화 포인트 {watch_count}건이 있습니다. 전체 제출 전환율은 직전 기간 대비 {overall_delta:+.2f}p 변화했습니다."
            return f"주의 깊게 볼 최적화 포인트 {watch_count}건이 있습니다."
        return "현재 기간에는 강한 하락 신호가 없어 기존 임계값을 유지해도 괜찮아 보입니다."

    def _build_trend(self, decisions: list[BidDecisionRecord], *, days: int, bucket_days: int) -> list[dict[str, Any]]:
        """Build conversion trend cohorts grouped by first decision date."""
        if not decisions:
            return []

        now = utc_now()
        period_start = (now - timedelta(days=days)).date()
        period_end = now.date()
        buckets: dict[date, list[BidDecisionRecord]] = {}

        for decision in decisions:
            entry_date = self._entry_datetime(decision).date()
            offset_days = max((entry_date - period_start).days, 0)
            bucket_index = offset_days // max(bucket_days, 1)
            bucket_start = period_start + timedelta(days=bucket_index * max(bucket_days, 1))
            buckets.setdefault(bucket_start, []).append(decision)

        trend: list[dict[str, Any]] = []
        for bucket_start in sorted(buckets.keys()):
            bucket_decisions = buckets[bucket_start]
            trend.append({
                "bucket_start": bucket_start,
                "bucket_end": min(bucket_start + timedelta(days=max(bucket_days, 1) - 1), period_end),
                **self._build_segment_metrics(bucket_decisions),
            })
        return trend

    def _build_segment_breakdown(
        self,
        decisions: list[BidDecisionRecord],
        *,
        segment_resolver,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Build top-N conversion summaries for a chosen segmentation dimension."""
        grouped: dict[str, list[BidDecisionRecord]] = {}
        for decision in decisions:
            label = str(segment_resolver(decision) or self.UNKNOWN_AGENCY)
            grouped.setdefault(label, []).append(decision)

        summaries = [
            {
                "segment": label,
                **self._build_segment_metrics(items),
            }
            for label, items in grouped.items()
        ]
        summaries.sort(key=lambda item: (-int(item["decision_count"]), -int(item["submitted_count"]), str(item["segment"])))
        return summaries[:limit]

    def _build_segment_metrics(self, decisions: list[BidDecisionRecord]) -> dict[str, Any]:
        """Compute reusable funnel metrics for a subset of decision records."""
        submitted_decisions = [decision for decision in decisions if self._is_submitted(decision)]
        active_pending_decisions = [decision for decision in decisions if self._is_active_pending(decision)]
        skipped_decisions = [decision for decision in decisions if self._is_skipped(decision)]

        workflow_entries = [decision for decision in decisions if self._entry_status(decision) != "submitted"]
        bid_now_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "bid_now"]
        review_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "review"]
        skip_entries = [decision for decision in workflow_entries if self._entry_action(decision) == "skip"]

        submitted_after_bid_now = [decision for decision in submitted_decisions if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "bid_now"]
        submitted_after_review = [decision for decision in submitted_decisions if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "review"]
        submitted_after_skip = [decision for decision in submitted_decisions if self._entry_status(decision) != "submitted" and self._entry_action(decision) == "skip"]

        submission_durations = [
            hours_to_submit
            for hours_to_submit in (self._compute_hours_to_submit(decision) for decision in submitted_decisions)
            if hours_to_submit is not None
        ]

        return {
            "decision_count": len(decisions),
            "project_count": len({int(decision.project_id) for decision in decisions if decision.project_id is not None}),
            "submitted_count": len(submitted_decisions),
            "active_pending_count": len(active_pending_decisions),
            "skipped_count": len(skipped_decisions),
            "entry_bid_now_count": len(bid_now_entries),
            "entry_review_count": len(review_entries),
            "entry_skip_count": len(skip_entries),
            "submitted_after_bid_now_count": len(submitted_after_bid_now),
            "submitted_after_review_count": len(submitted_after_review),
            "submitted_after_skip_count": len(submitted_after_skip),
            "submission_rate": self._rate(len(submitted_decisions), len(decisions)),
            "bid_now_submission_rate": self._rate(len(submitted_after_bid_now), len(bid_now_entries)),
            "review_submission_rate": self._rate(len(submitted_after_review), len(review_entries)),
            "average_priority_score": self._average([float(decision.priority_score or 0.0) for decision in decisions]),
            "average_expected_margin_score": self._average([float(decision.expected_margin_score or 0.0) for decision in decisions]),
            "average_hours_to_submit": self._average(submission_durations),
        }

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
        delta_seconds = max((submitted_at - first_decided_at).total_seconds(), 0.0)
        return round(delta_seconds / 3600, 4)

    def _average(self, values: list[float]) -> float | None:
        """Return a rounded average for summary metrics."""
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    def _rate(self, numerator: int, denominator: int) -> float | None:
        """Return a safe rounded ratio for funnel conversion metrics."""
        if denominator <= 0:
            return None
        return round(numerator / denominator, 4)

    def _delta(self, current_value: float | None, previous_value: float | None) -> float | None:
        """Return a rounded period-over-period delta when both values exist."""
        if current_value is None or previous_value is None:
            return None
        return round(float(current_value) - float(previous_value), 4)
