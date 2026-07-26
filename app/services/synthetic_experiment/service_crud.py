"""Experiment + run CRUD mixin for :class:`SyntheticExperimentService`."""

from __future__ import annotations

from typing import Any, Optional

from app.models.models import SyntheticExperiment, SyntheticExperimentRun

from .constants import (
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    SYNTHETIC_EXPERIMENT_PRESETS,
)
from .serialization import _json_dumps, _json_loads


class ExperimentCrudMixin:
    """CRUD for experiments/presets and run creation/fetch."""

    # --- experiment CRUD -------------------------------------------------------

    def create_experiment(
        self,
        *,
        name: str,
        description: Optional[str],
        params: dict[str, Any],
        operator_slugs: Optional[list[str]],
    ) -> SyntheticExperiment:
        experiment = SyntheticExperiment(
            name=name,
            description=description,
            params_json=_json_dumps(params),
            operator_slugs_json=_json_dumps(operator_slugs or []),
        )
        self.db.add(experiment)
        self.db.commit()
        self.db.refresh(experiment)
        return experiment

    def list_experiments(self) -> list[SyntheticExperiment]:
        return (
            self.db.query(SyntheticExperiment)
            .order_by(
                SyntheticExperiment.created_at.desc(), SyntheticExperiment.id.desc()
            )
            .all()
        )

    def list_presets(self) -> list[dict[str, Any]]:
        """Return fixed G-1 presets with their saved experiment/run state."""
        return [
            self._serialize_preset(name, definition)
            for name, definition in SYNTHETIC_EXPERIMENT_PRESETS.items()
        ]

    def ensure_preset(self, name: str) -> Optional[SyntheticExperiment]:
        """Create or update the saved experiment for a fixed G-1 preset."""
        definition = SYNTHETIC_EXPERIMENT_PRESETS.get(name)
        if definition is None:
            return None
        experiment = (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name == name)
            .first()
        )
        if experiment is None:
            experiment = SyntheticExperiment(name=name)
            self.db.add(experiment)
        experiment.description = str(definition["description"])
        experiment.params_json = _json_dumps(definition["params"])
        experiment.operator_slugs_json = _json_dumps(definition["operator_slugs"])
        self.db.commit()
        self.db.refresh(experiment)
        return experiment

    def _serialize_preset(
        self, name: str, definition: dict[str, Any]
    ) -> dict[str, Any]:
        experiment = (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name == name)
            .first()
        )
        latest_run = experiment.runs[0] if experiment and experiment.runs else None
        return {
            "name": name,
            "description": str(definition["description"]),
            "params": definition["params"],
            "operator_slugs": list(definition["operator_slugs"]),
            "experiment_id": experiment.id if experiment else None,
            "latest_run_id": latest_run.id if latest_run else None,
            "latest_run_status": latest_run.status if latest_run else None,
        }

    def get_experiment(self, experiment_id: int) -> Optional[SyntheticExperiment]:
        return (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.id == experiment_id)
            .first()
        )

    # --- run lifecycle ---------------------------------------------------------

    def create_run(
        self,
        experiment: SyntheticExperiment,
        *,
        source_sample_gap_candidate: Optional[dict[str, Any]] = None,
    ) -> SyntheticExperimentRun:
        """Create a queued run and enqueue the Celery backtest task.

        The Celery payload carries ``experiment_id`` + ``run_id`` so the task can
        persist the run/result lifecycle, plus the saved params and slug subset.
        """
        from app.tasks.jobs import enqueue_synthetic_operator_backtest

        run = SyntheticExperimentRun(
            experiment_id=experiment.id,
            status=RUN_STATUS_QUEUED,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        params = _json_loads(experiment.params_json) or {}
        operator_slugs = _json_loads(experiment.operator_slugs_json) or []
        payload: dict[str, Any] = dict(params)
        payload["experiment_id"] = experiment.id
        payload["run_id"] = run.id
        payload["slugs"] = operator_slugs or None
        if isinstance(source_sample_gap_candidate, dict):
            payload["source_sample_gap_candidate"] = source_sample_gap_candidate

        async_result = enqueue_synthetic_operator_backtest(payload=payload)
        run.task_id = getattr(async_result, "id", None)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(
        self, experiment_id: int, run_id: int
    ) -> Optional[SyntheticExperimentRun]:
        run = (
            self.db.query(SyntheticExperimentRun)
            .filter(
                SyntheticExperimentRun.id == run_id,
                SyntheticExperimentRun.experiment_id == experiment_id,
            )
            .first()
        )
        if run is None:
            return None
        if run.status in (RUN_STATUS_QUEUED, RUN_STATUS_RUNNING) and run.task_id:
            self._sync_run_with_task(run)
        return run
