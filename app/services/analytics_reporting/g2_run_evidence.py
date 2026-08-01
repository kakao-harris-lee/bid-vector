"""Per-run G-2 evidence serializer mixin."""

from __future__ import annotations

from typing import Any

from app.models.models import (
    DecisionExperimentRun,
    OperatorStrategyRun,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
)
from app.services.decision_experiments.base import LATEST_EVALUATION_COLUMN
from app.services.synthetic_experiment import sample_status_for_settled_count


class _G2RunEvidenceMixin:
    """Per-run G-2 evidence serializers (strategy / experiment / synthetic)."""

    def _g2_strategy_run_evidence(self, run: OperatorStrategyRun) -> dict[str, Any]:
        return {
            "run_id": int(run.id),
            "operator_id": int(run.operator_id),
            "source_run_type": "operator_strategy_monitor",
            "source_run_id": int(run.id),
            "trigger_source": run.trigger_source,
            "status": run.status,
            "evaluated_project_count": int(run.evaluated_project_count or 0),
            "selected_candidate_count": int(run.selected_candidate_count or 0),
            "persisted_candidate_count": int(run.persisted_candidate_count or 0),
            "notification_count": int(run.notification_count or 0),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }

    def _g2_decision_experiment_run_evidence(self, run: DecisionExperimentRun) -> dict[str, Any]:
        latest_evaluation = self._load_json_object(
            run.latest_evaluation,
            context=LATEST_EVALUATION_COLUMN,
        )
        return {
            "run_id": int(run.id),
            "operator_id": int(run.operator_id),
            "source_run_type": "decision_experiment_run",
            "source_run_id": int(run.id),
            "experiment_key": run.experiment_key,
            "recommendation_key": run.recommendation_key,
            "status": run.status,
            "outcome": run.outcome,
            "title": run.title,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "last_evaluated_at": run.last_evaluated_at,
            "created_at": run.created_at,
            "latest_evaluation": latest_evaluation or None,
        }

    def _g2_synthetic_result_evidence(
        self,
        run: SyntheticExperimentRun,
        result: SyntheticExperimentResult,
        *,
        metrics: dict[str, Any],
        result_operator_id: int | None,
    ) -> dict[str, Any]:
        sample_status = str(
            metrics.get("sample_status")
            or sample_status_for_settled_count(int(metrics.get("settled_count") or 0))["sample_status"]
        )
        return {
            "run_id": int(run.id),
            "experiment_id": int(run.experiment_id),
            "experiment_name": run.experiment.name if run.experiment else None,
            "source_run_type": "synthetic_experiment_run",
            "source_run_id": int(run.id),
            "result_id": int(result.id),
            "operator_id": result_operator_id,
            "operator_slug": result.operator_slug,
            "run_status": run.status,
            "sample_status": sample_status,
            "settled_count": int(metrics.get("settled_count") or 0),
            "missing_settled_count": int(metrics.get("missing_settled_count") or 0),
            "created_at": run.created_at,
            "finished_at": run.finished_at,
        }
