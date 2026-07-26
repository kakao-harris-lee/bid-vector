"""Run lifecycle hooks (running/completed/failed) mixin."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.time import utc_now
from app.models.models import SyntheticExperimentResult, SyntheticExperimentRun

from .breakdown import compute_breakdown
from .constants import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
)
from .sample_report import build_sample_report
from .sample_status import aggregate_sample_status, sample_status_for_settled_count
from .serialization import _json_dumps

logger = logging.getLogger(__name__)


class RunLifecycleMixin:
    """Persist run status transitions and per-operator result rows."""

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
        run.started_at = utc_now()
        self.db.commit()

    def mark_completed(
        self,
        run_id: int,
        result: dict[str, Any],
        *,
        source_sample_gap_candidate: Optional[dict[str, Any]] = None,
    ) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        operator_results = result.get("results", []) or []
        normalized_results: list[dict[str, Any]] = []
        for item in operator_results:
            settlement_sample = item.get("settlement_items")
            # Prefer the engine-supplied breakdown (computed over the full,
            # non-truncated settlement set). Fall back to computing from the
            # sampled ``settlement_items`` for stubbed/legacy payloads.
            breakdown = item.get("breakdown")
            if breakdown is None:
                breakdown = compute_breakdown(settlement_sample)
            normalized_results.append({**item, "breakdown": breakdown})

        preset_name = run.experiment.name if run.experiment else None
        summary = {
            "operator_count": result.get("operator_count", len(normalized_results)),
            "scenario": result.get("scenario"),
            "category": result.get("category"),
            "start_at": result.get("start_at"),
            "end_at": result.get("end_at"),
            "limit": result.get("limit"),
            **aggregate_sample_status(normalized_results),
            "sample_report": build_sample_report(
                preset_name=preset_name,
                operator_results=normalized_results,
            ),
        }
        if isinstance(source_sample_gap_candidate, dict):
            summary["source_sample_gap_candidate"] = source_sample_gap_candidate
        run.status = RUN_STATUS_COMPLETED
        run.finished_at = utc_now()
        run.error = None
        run.summary_json = _json_dumps(summary)

        excluded_metric_keys = {"settlement_items", "breakdown"}
        for item in normalized_results:
            metrics = {
                key: value
                for key, value in item.items()
                if key not in excluded_metric_keys
            }
            # Bind the per-operator result to ``operator_id`` so the G-2 evidence
            # ledger (analytics_reporting._build_g2_synthetic_experiment_summary)
            # can attribute it to a specific operator. The upstream backtest item
            # carries ``user_id`` (synthetic_backtest.list_operators) but not
            # ``operator_id``; mirror it here. ``user_id`` is preserved alongside
            # so existing consumers are unaffected.
            operator_id_value = item.get("operator_id")
            if operator_id_value is None:
                operator_id_value = item.get("user_id")
            if operator_id_value is not None:
                metrics["operator_id"] = operator_id_value
            metrics.update(
                sample_status_for_settled_count(int(item.get("settled_count") or 0))
            )
            settlement_sample = item.get("settlement_items")
            breakdown = item.get("breakdown")
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
                    breakdown_json=_json_dumps(breakdown),
                )
            )
        self.db.commit()

    def mark_failed(self, run_id: int, error: str) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        run.status = RUN_STATUS_FAILED
        run.finished_at = utc_now()
        run.error = error
        self.db.commit()

    def _fetch_run(self, run_id: int) -> Optional[SyntheticExperimentRun]:
        return (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.id == run_id)
            .first()
        )
