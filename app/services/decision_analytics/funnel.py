"""Conversion funnel analytics: summary, trend, and segment breakdowns."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.core.time import kst_now, to_kst, utc_now
from app.models.models import BidDecisionRecord, User
from app.schemas.schemas import _extract_decision_reasons
from app.services.decision_analytics.base import _DecisionAnalyticsBase


class _FunnelMixin(_DecisionAnalyticsBase):
    """Funnel progression metrics and their segment/trend decompositions."""

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

    def _build_trend(self, decisions: list[BidDecisionRecord], *, days: int, bucket_days: int) -> list[dict[str, Any]]:
        """Build conversion trend cohorts grouped by first decision date."""
        if not decisions:
            return []

        # Cohorts group by the KST calendar day so the operator's daily trend is
        # not shifted by the 9h UTC offset (a decision at 06:00 KST belongs to
        # "today", not the previous UTC day).
        now = kst_now()
        period_start = (now - timedelta(days=days)).date()
        period_end = now.date()
        buckets: dict[date, list[BidDecisionRecord]] = {}

        for decision in decisions:
            entry_date = to_kst(self._entry_datetime(decision)).date()
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
