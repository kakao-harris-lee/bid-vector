"""Window snapshot building and the metric-delta verdict evaluation.

Turns a bounded decision window into a compact metrics snapshot
(``_build_snapshot``) and compares baseline vs. current snapshots through the
declarative verdict machine (``_build_evaluation`` plus the ``_metric_improved``
/ ``_guardrail_broken`` numeric predicates). Method bodies are the original
``DecisionExperimentService`` methods, moved verbatim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import DecisionExperimentRun
from app.services.decision_experiments.base import _DecisionExperimentBase
from app.services.decision_experiments.verdict_machine import _VERDICT_RULES, _VerdictContext


class _EvaluationMixin(_DecisionExperimentBase):
    """Build experiment window snapshots and turn metric deltas into verdicts."""

    def _build_snapshot(self, db: Session, *, operator_id: int, start_at: datetime, end_at: datetime) -> dict[str, Any]:
        """Build a compact metrics snapshot for one experiment window."""
        safe_end_at = end_at if end_at >= start_at else start_at
        decisions = self.analytics._load_decisions_in_range(
            db,
            operator_id=operator_id,
            start_at=start_at,
            end_at=safe_end_at,
        )
        summary = self.analytics._build_funnel_summary(decisions)
        category_breakdown = self.analytics._build_segment_breakdown(
            decisions,
            segment_resolver=self.analytics._resolve_category_segment,
            limit=20,
        )
        workload_breakdown = self.analytics._build_segment_breakdown(
            decisions,
            segment_resolver=lambda decision: str(decision.workload_source or self.analytics.DEFAULT_WORKLOAD_SOURCE),
            limit=10,
        )
        auto_segment = self.analytics._find_segment(workload_breakdown, "auto")
        provided_segment = self.analytics._find_segment(workload_breakdown, self.analytics.DEFAULT_WORKLOAD_SOURCE)

        category_segments = [
            segment
            for segment in category_breakdown
            if segment.get("submission_rate") is not None
        ]
        best_category = max(category_segments, key=lambda item: (float(item.get("submission_rate") or 0.0), int(item.get("decision_count") or 0))) if category_segments else None
        worst_category = min(category_segments, key=lambda item: (float(item.get("submission_rate") or 0.0), -int(item.get("decision_count") or 0))) if category_segments else None

        return {
            "window_start": start_at,
            "window_end": safe_end_at,
            "decision_count": int(summary.get("decision_count") or 0),
            "submitted_count": int(summary.get("submitted_count") or 0),
            "active_pending_count": int(summary.get("active_pending_count") or 0),
            "overall_submission_rate": summary.get("overall_submission_rate"),
            "workflow_submission_rate": summary.get("workflow_submission_rate"),
            "bid_now_submission_rate": summary.get("bid_now_submission_rate"),
            "review_submission_rate": summary.get("review_submission_rate"),
            "auto_submission_rate": auto_segment.get("submission_rate") if auto_segment is not None else None,
            "provided_submission_rate": provided_segment.get("submission_rate") if provided_segment is not None else None,
            "best_category": best_category.get("segment") if best_category is not None else None,
            "best_category_submission_rate": best_category.get("submission_rate") if best_category is not None else None,
            "worst_category": worst_category.get("segment") if worst_category is not None else None,
            "worst_category_submission_rate": worst_category.get("submission_rate") if worst_category is not None else None,
        }

    def _build_evaluation(
        self,
        run: DecisionExperimentRun,
        *,
        baseline_summary: dict[str, Any],
        current_summary: dict[str, Any],
        evaluated_at: datetime,
        scheduled_end: datetime,
    ) -> dict[str, Any]:
        """Convert metric deltas into an operator-friendly experiment verdict."""
        sample_size = int(current_summary.get("decision_count") or 0)
        minimum_decision_sample = int(run.minimum_decision_sample or 1)
        minimum_sample_reached = sample_size >= minimum_decision_sample
        baseline_target_value = self._resolve_metric_value(baseline_summary, str(run.target_metric or ""))
        current_target_value = self._resolve_metric_value(current_summary, str(run.target_metric or ""))
        target_delta = self._delta(current_target_value, baseline_target_value)
        baseline_guardrail_value = self._resolve_metric_value(baseline_summary, str(run.guardrail_metric or ""))
        current_guardrail_value = self._resolve_metric_value(current_summary, str(run.guardrail_metric or ""))
        guardrail_delta = self._delta(current_guardrail_value, baseline_guardrail_value)

        context = _VerdictContext(
            run=run,
            sample_size=sample_size,
            minimum_decision_sample=minimum_decision_sample,
            minimum_sample_reached=minimum_sample_reached,
            guardrail_broken=self._guardrail_broken(
                str(run.guardrail_metric or ""),
                baseline_guardrail_value,
                current_guardrail_value,
            ),
            metric_improved=self._metric_improved(
                str(run.expected_direction or "increase"),
                str(run.target_metric or ""),
                baseline_target_value,
                current_target_value,
            ),
            period_elapsed=evaluated_at >= scheduled_end,
        )
        verdict = next(rule for rule in _VERDICT_RULES if rule.predicate(context))

        return {
            "evaluated_at": evaluated_at,
            "sample_size": sample_size,
            "minimum_sample_reached": minimum_sample_reached,
            "target_metric": run.target_metric,
            "baseline_target_value": baseline_target_value,
            "current_target_value": current_target_value,
            "target_delta": target_delta,
            "guardrail_metric": run.guardrail_metric,
            "baseline_guardrail_value": baseline_guardrail_value,
            "current_guardrail_value": current_guardrail_value,
            "guardrail_delta": guardrail_delta,
            "outcome": verdict.outcome,
            "recommended_action": verdict.recommended_action,
            "summary": verdict.summary_builder(context),
            "current_summary": current_summary,
        }

    def _metric_improved(
        self,
        expected_direction: str,
        metric_name: str,
        baseline_value: float | None,
        current_value: float | None,
    ) -> bool:
        """Return whether the target metric moved in the desired direction far enough to matter."""
        if current_value is None:
            return False
        threshold = 1.0 if self._is_count_metric(metric_name) else self.RATE_SUCCESS_DELTA
        if baseline_value is None:
            if expected_direction == "decrease":
                return current_value <= 0.0
            if expected_direction == "stabilize":
                return abs(current_value) <= threshold
            return current_value >= threshold

        delta = float(current_value) - float(baseline_value)
        if expected_direction == "decrease":
            return delta <= -threshold
        if expected_direction == "stabilize":
            return abs(delta) <= threshold
        return delta >= threshold

    def _guardrail_broken(self, metric_name: str, baseline_value: float | None, current_value: float | None) -> bool:
        """Return whether the guardrail degraded enough that the experiment should be stopped."""
        if current_value is None:
            return False
        if metric_name == "active_pending_count":
            baseline = float(baseline_value or 0.0)
            if baseline <= 0:
                return float(current_value) >= 2.0
            growth_ratio = (float(current_value) - baseline) / max(baseline, 1.0)
            return growth_ratio >= self.ACTIVE_PENDING_GROWTH_RATIO

        if self._is_count_metric(metric_name):
            if baseline_value is None or baseline_value <= 0:
                return False
            drop_ratio = (float(baseline_value) - float(current_value)) / max(float(baseline_value), 1.0)
            return drop_ratio >= self.COUNT_DROP_RATIO

        if baseline_value is None:
            return False
        return (float(current_value) - float(baseline_value)) <= self.RATE_GUARDRAIL_DROP

    def _resolve_metric_value(self, snapshot: dict[str, Any], metric_name: str) -> float | None:
        """Map a named experiment metric to the correct snapshot field."""
        raw_value = snapshot.get(metric_name)
        if raw_value is None:
            return None
        try:
            return round(float(raw_value), 4)
        except (TypeError, ValueError):
            return None
