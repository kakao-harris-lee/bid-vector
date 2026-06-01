"""Synthetic experiment persistence and run-lifecycle orchestration.

Owns the "가상 회사 낙찰 실험실" (Experiment Lab) domain: saving experiment
definitions, triggering asynchronous runs, and persisting per-operator results.
ML/predictor logic is untouched here -- the underlying backtest engine is invoked
only through :class:`SyntheticBacktestService`, whose ``win_rate_on_settled`` is a
price-only estimate (NOT actual award) and is passed through unchanged.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.models import (
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
)
from app.services.synthetic_backtest import SyntheticBacktestService

logger = logging.getLogger(__name__)

RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _json_loads(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        logger.warning("Failed to decode stored synthetic-experiment JSON payload")
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class SyntheticExperimentService:
    """CRUD + run lifecycle for synthetic experiments."""

    def __init__(self, db: Session) -> None:
        self.db = db

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

    def get_experiment(self, experiment_id: int) -> Optional[SyntheticExperiment]:
        return (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.id == experiment_id)
            .first()
        )

    # --- run lifecycle ---------------------------------------------------------

    def create_run(self, experiment: SyntheticExperiment) -> SyntheticExperimentRun:
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

    def _sync_run_with_task(self, run: SyntheticExperimentRun) -> None:
        """Reconcile a still-pending run with the Celery task state.

        The task itself persists completion/failure to the DB; this only covers a
        task that failed without the lifecycle hook recording it (defensive).
        """
        from app.tasks.jobs import get_synthetic_backtest_task_status

        try:
            status_payload = get_synthetic_backtest_task_status(run.task_id)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Could not read task status for run %s", run.id)
            return
        if (status_payload.get("status") == "failed") and run.status not in (
            RUN_STATUS_COMPLETED,
            RUN_STATUS_FAILED,
        ):
            self.mark_failed(run.id, status_payload.get("error") or "Task failed")
            self.db.refresh(run)

    # --- lifecycle hooks invoked from inside the Celery task -------------------

    def mark_running(self, run_id: int) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        run.status = RUN_STATUS_RUNNING
        run.started_at = datetime.utcnow()
        self.db.commit()

    def mark_completed(self, run_id: int, result: dict[str, Any]) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        operator_results = result.get("results", []) or []
        summary = {
            "operator_count": result.get("operator_count", len(operator_results)),
            "scenario": result.get("scenario"),
            "category": result.get("category"),
            "start_at": result.get("start_at"),
            "end_at": result.get("end_at"),
            "limit": result.get("limit"),
        }
        run.status = RUN_STATUS_COMPLETED
        run.finished_at = datetime.utcnow()
        run.error = None
        run.summary_json = _json_dumps(summary)

        for item in operator_results:
            metrics = {
                key: value for key, value in item.items() if key != "settlement_items"
            }
            settlement_sample = item.get("settlement_items")
            self.db.add(
                SyntheticExperimentResult(
                    run_id=run.id,
                    operator_slug=str(item.get("slug") or "unknown"),
                    metrics_json=_json_dumps(metrics),
                    settlement_sample_json=(
                        _json_dumps(settlement_sample)
                        if settlement_sample is not None
                        else None
                    ),
                )
            )
        self.db.commit()

    def mark_failed(self, run_id: int, error: str) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        run.status = RUN_STATUS_FAILED
        run.finished_at = datetime.utcnow()
        run.error = error
        self.db.commit()

    def _fetch_run(self, run_id: int) -> Optional[SyntheticExperimentRun]:
        return (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.id == run_id)
            .first()
        )

    # --- serialization (ORM -> response dicts) --------------------------------

    def serialize_experiment(self, experiment: SyntheticExperiment) -> dict[str, Any]:
        return {
            "id": experiment.id,
            "name": experiment.name,
            "description": experiment.description,
            "params": _json_loads(experiment.params_json) or {},
            "operator_slugs": _json_loads(experiment.operator_slugs_json) or [],
            "created_at": experiment.created_at,
            "updated_at": experiment.updated_at,
            "runs": [self.serialize_run_summary(run) for run in experiment.runs],
        }

    def serialize_run_summary(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "experiment_id": run.experiment_id,
            "status": run.status,
            "task_id": run.task_id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": run.error,
            "summary": _json_loads(run.summary_json),
            "created_at": run.created_at,
        }

    def serialize_run_detail(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        payload = self.serialize_run_summary(run)
        payload["results"] = [
            {
                "operator_slug": item.operator_slug,
                "metrics": _json_loads(item.metrics_json) or {},
                "settlement_sample": _json_loads(item.settlement_sample_json),
            }
            for item in run.results
        ]
        return payload


def run_experiment_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute an experiment-scoped backtest inside a fresh DB session.

    Invoked by the Celery task. Opens its own ``SessionLocal`` so it is safe in
    both eager (memory://) and worker execution. Persists the run/result
    lifecycle when ``run_id`` is present in the payload.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    run_id = payload.get("run_id")
    service = SyntheticExperimentService(db)
    try:
        if run_id is not None:
            service.mark_running(run_id)

        backtest = SyntheticBacktestService()
        result = backtest.run_for_all(
            db,
            start_at=_parse_dt(payload.get("start_at")),
            end_at=_parse_dt(payload.get("end_at")),
            category=payload.get("category"),
            limit=int(payload.get("limit") or 100),
            scenario=str(payload.get("scenario") or "base"),
            slugs=payload.get("slugs"),
        )
        if run_id is not None:
            service.mark_completed(run_id, result)
        return result
    except Exception as exc:
        if run_id is not None:
            service.mark_failed(run_id, str(exc))
        raise
    finally:
        db.close()
