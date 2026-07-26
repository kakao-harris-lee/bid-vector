"""Operator strategy-run reporting mixin."""

from __future__ import annotations

from typing import Any

from app.models.models import OperatorStrategyRun


class _StrategyMixin:
    """Operator strategy-run summary and dashboard cards."""

    def _build_strategy_summary(self, strategy_runs: list[OperatorStrategyRun], *, recent_limit: int) -> dict[str, Any]:
        """Aggregate strategy monitoring performance metrics."""
        total_count = len(strategy_runs)
        completed_count = sum(1 for run in strategy_runs if str(run.status) == "completed")
        failed_count = sum(1 for run in strategy_runs if str(run.status) == "failed")
        running_count = sum(1 for run in strategy_runs if str(run.status) in {"queued", "running"})
        evaluated_count = sum(int(run.evaluated_project_count or 0) for run in strategy_runs)
        selected_count = sum(int(run.selected_candidate_count or 0) for run in strategy_runs)
        persisted_count = sum(int(run.persisted_candidate_count or 0) for run in strategy_runs)
        notification_count = sum(int(run.notification_count or 0) for run in strategy_runs)
        failed_runs = [run for run in strategy_runs if str(run.status) == "failed"]
        completed_runs = [run for run in strategy_runs if str(run.status) == "completed"]
        return {
            "run_count": total_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "running_count": running_count,
            "completion_rate": self._rate(completed_count, total_count),
            "failure_rate": self._rate(failed_count, total_count),
            "evaluated_project_count": evaluated_count,
            "selected_candidate_count": selected_count,
            "persisted_candidate_count": persisted_count,
            "notification_count": notification_count,
            "selection_rate": self._rate(selected_count, evaluated_count),
            "persistence_rate": self._rate(persisted_count, selected_count),
            "notification_rate": self._rate(notification_count, persisted_count),
            "average_selected_candidates": self._average([int(run.selected_candidate_count or 0) for run in completed_runs]),
            "last_completed_at": self._latest_completed_at(completed_runs),
            "last_failure_at": self._latest_completed_at(failed_runs),
            "failure_reason_breakdown": self._reason_breakdown(
                [str(run.error_message or "") for run in failed_runs if run.error_message]
            ),
            "recent_failures": [
                {
                    "run_id": int(run.id),
                    "operator_id": int(run.operator_id),
                    "source_run_type": "operator_strategy_monitor",
                    "source_run_id": int(run.id),
                    "trigger_source": run.trigger_source,
                    "status": run.status,
                    "error_message": run.error_message,
                    "created_at": run.created_at,
                    "completed_at": run.completed_at,
                }
                for run in failed_runs[:recent_limit]
            ],
        }

    def _strategy_dashboard_cards(
        self,
        strategy_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        completion_status = (
            "info"
            if strategy_summary["run_count"] == 0
            else self._status_for_rate(
                strategy_summary["completion_rate"],
                warning=0.85,
                critical=0.65,
            )
        )
        selection_status = (
            "info"
            if strategy_summary["evaluated_project_count"] == 0
            else "healthy"
            if strategy_summary["selected_candidate_count"] > 0
            else "watch"
        )
        return [
            {
                "key": "strategy_completion_rate",
                "label": "Strategy run completion",
                "value": strategy_summary["completion_rate"],
                "unit": "ratio",
                "status": completion_status,
                "detail": f"{strategy_summary['run_count']} run(s), {strategy_summary['failed_count']} failed.",
            },
            {
                "key": "strategy_selection_rate",
                "label": "Candidate selection rate",
                "value": strategy_summary["selection_rate"],
                "unit": "ratio",
                "status": selection_status,
                "detail": (
                    f"{strategy_summary['selected_candidate_count']} selected from "
                    f"{strategy_summary['evaluated_project_count']} evaluated project(s)."
                ),
            },
            {
                "key": "strategy_notifications",
                "label": "Notifications created",
                "value": strategy_summary["notification_count"],
                "unit": "count",
                "status": "healthy" if strategy_summary["notification_count"] > 0 else "info",
                "detail": f"{strategy_summary['persisted_candidate_count']} persisted candidate(s).",
            },
        ]
