"""ORM -> response-dict serialization, CSV export, and A/B compare mixin."""

from __future__ import annotations

import csv
import io
from typing import Any, Optional

from app.models.models import SyntheticExperiment, SyntheticExperimentRun

from .breakdown import _empty_breakdown
from .constants import (
    EXPORT_CSV_COLUMNS,
    _COMPARE_DELTA_KEYS,
    _COMPARE_METRIC_KEYS,
)
from .sample_status import sample_status_for_settled_count
from .serialization import _json_loads


class RunSerializationMixin:
    """Serialize experiments/runs and diff two runs by operator slug."""

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
                **sample_status_for_settled_count(
                    int((_json_loads(item.metrics_json) or {}).get("settled_count") or 0)
                ),
                "operator_slug": item.operator_slug,
                "metrics": _json_loads(item.metrics_json) or {},
                "settlement_sample": _json_loads(item.settlement_sample_json),
                "breakdown": _json_loads(item.breakdown_json) or _empty_breakdown(),
            }
            for item in run.results
        ]
        return payload

    # --- Phase 4: CSV export ---------------------------------------------------

    def export_run_csv(self, run: SyntheticExperimentRun) -> str:
        """Render a run's per-operator metrics as a CSV document (string).

        Columns mirror the CLI comparison CSV (``EXPORT_CSV_COLUMNS``) with
        ``operator_slug`` prepended. Missing metric keys serialize to an empty
        cell. A run with no results yields a header-only CSV (still HTTP 200), so
        not-yet-completed runs export cleanly rather than erroring. ``win_rate_*``
        columns stay price-only estimates -- the engine values pass through
        unchanged.
        """
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=list(EXPORT_CSV_COLUMNS), extrasaction="ignore"
        )
        writer.writeheader()
        for item in run.results:
            metrics = _json_loads(item.metrics_json) or {}
            row: dict[str, Any] = {}
            for column in EXPORT_CSV_COLUMNS:
                if column == "operator_slug":
                    value: Any = item.operator_slug
                else:
                    value = metrics.get(column)
                row[column] = "" if value is None else value
            writer.writerow(row)
        return buffer.getvalue()

    # --- Phase 4: A/B run comparison -------------------------------------------

    def _fetch_run_by_id(self, run_id: int) -> Optional[SyntheticExperimentRun]:
        """Fetch a run by id alone (cross-experiment; for A/B comparison)."""
        return (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.id == run_id)
            .first()
        )

    def _run_compact_header(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        """Minimal run header (id + experiment_id + summary) for the compare payload."""
        return {
            "id": run.id,
            "experiment_id": run.experiment_id,
            "summary": _json_loads(run.summary_json),
        }

    @staticmethod
    def _compare_side_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        """Extract the compare metric subset from a stored per-operator metrics dict."""
        return {key: metrics.get(key) for key in _COMPARE_METRIC_KEYS}

    @staticmethod
    def _compare_delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        """Signed (b - a) deltas; ``None`` when either operand is missing/None."""
        delta: dict[str, Any] = {}
        for key in _COMPARE_DELTA_KEYS:
            a_value = a.get(key)
            b_value = b.get(key)
            if a_value is None or b_value is None:
                delta[key] = None
            else:
                delta[key] = round(float(b_value) - float(a_value), 6)
        return delta

    def compare_runs(self, run_a_id: int, run_b_id: int) -> Optional[dict[str, Any]]:
        """Join two runs' per-operator metrics by ``operator_slug`` and diff them.

        Returns ``None`` when either run is missing (router maps to 404). The two
        runs may belong to different experiments -- the join is purely on the
        operator-slug intersection. ``delta`` is ``b - a`` (positive => B higher);
        ``win_rate_*`` deltas are ``None`` when either side has no settled rows.
        """
        run_a = self._fetch_run_by_id(run_a_id)
        run_b = self._fetch_run_by_id(run_b_id)
        if run_a is None or run_b is None:
            return None

        metrics_a = {
            item.operator_slug: (_json_loads(item.metrics_json) or {})
            for item in run_a.results
        }
        metrics_b = {
            item.operator_slug: (_json_loads(item.metrics_json) or {})
            for item in run_b.results
        }

        shared = sorted(set(metrics_a) & set(metrics_b))
        operators = []
        for slug in shared:
            side_a = self._compare_side_metrics(metrics_a[slug])
            side_b = self._compare_side_metrics(metrics_b[slug])
            operators.append(
                {
                    "operator_slug": slug,
                    "a": side_a,
                    "b": side_b,
                    "delta": self._compare_delta(side_a, side_b),
                }
            )

        only_in_a = sorted(set(metrics_a) - set(metrics_b))
        only_in_b = sorted(set(metrics_b) - set(metrics_a))
        return {
            "run_a": self._run_compact_header(run_a),
            "run_b": self._run_compact_header(run_b),
            "operators": operators,
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
        }
