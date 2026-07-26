"""Persisted bid-decision signal summary (``build_insights``)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account
from app.models.models import User
from app.services.decision_analytics.base import _DecisionAnalyticsBase


class _InsightsMixin(_DecisionAnalyticsBase):
    """Tuning/review summary over recent decision records."""

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
