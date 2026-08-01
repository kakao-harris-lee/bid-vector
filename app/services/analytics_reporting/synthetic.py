"""Canonical G-1 synthetic validation reporting mixin."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import (
    SyntheticExperiment,
    SyntheticExperimentRun,
)
from app.services.synthetic_experiment import (
    RUN_SUMMARY_COLUMN,
    SAMPLE_STATUS_SUFFICIENT,
    SYNTHETIC_EXPERIMENT_PRESETS,
    SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
)


class _SyntheticValidationMixin:
    """Canonical G-1 synthetic validation summary and cards."""

    def _build_synthetic_validation_summary(
        self,
        db: Session,
        *,
        date_from: datetime,
        recent_limit: int,
    ) -> dict[str, Any]:
        """Summarize G-1 synthetic experiment health for the operations report."""
        preset_names = tuple(SYNTHETIC_EXPERIMENT_PRESETS)
        experiment_by_name = self._synthetic_experiments_by_name(db, preset_names)
        recent_runs = self._synthetic_preset_runs(
            db,
            preset_names,
            date_from=date_from,
        )
        all_preset_runs = self._synthetic_preset_runs(db, preset_names)
        latest_run_by_name = self._latest_synthetic_run_by_name(all_preset_runs)
        latest_run = all_preset_runs[0] if all_preset_runs else None
        preset_rows = [
            self._synthetic_preset_row(
                name,
                experiment=experiment_by_name.get(name),
                latest_preset_run=latest_run_by_name.get(name),
            )
            for name in preset_names
        ]
        counts = self._synthetic_validation_counts(preset_rows, recent_runs)
        status, detail = self._synthetic_validation_status(
            preset_count=len(preset_names),
            saved_preset_count=counts["saved_preset_count"],
            completed_preset_count=counts["completed_preset_count"],
            failed_preset_count=counts["failed_preset_count"],
            sufficient_preset_count=counts["sufficient_preset_count"],
            recent_run_count=counts["recent_run_count"],
        )
        detail = self._with_synthetic_scope_detail(detail)
        return {
            "preset_count": len(preset_names),
            "saved_preset_count": counts["saved_preset_count"],
            "completed_preset_count": counts["completed_preset_count"],
            "failed_preset_count": counts["failed_preset_count"],
            "sufficient_preset_count": counts["sufficient_preset_count"],
            "sample_target": SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
            "recent_run_count": counts["recent_run_count"],
            "recent_completed_count": counts["recent_completed_count"],
            "recent_failed_count": counts["recent_failed_count"],
            "status": status,
            "detail": detail,
            "latest": self._synthetic_latest_run_summary(latest_run),
            "presets": preset_rows,
        }

    def _synthetic_experiments_by_name(
        self,
        db: Session,
        preset_names: tuple[str, ...],
    ) -> dict[str, SyntheticExperiment]:
        if not preset_names:
            return {}
        experiments = (
            db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name.in_(preset_names))
            .order_by(
                SyntheticExperiment.created_at.desc(),
                SyntheticExperiment.id.desc(),
            )
            .all()
        )
        experiment_by_name: dict[str, SyntheticExperiment] = {}
        for experiment in experiments:
            experiment_by_name.setdefault(str(experiment.name), experiment)
        return experiment_by_name

    def _synthetic_preset_runs(
        self,
        db: Session,
        preset_names: tuple[str, ...],
        *,
        date_from: datetime | None = None,
    ) -> list[SyntheticExperimentRun]:
        if not preset_names:
            return []
        query = (
            db.query(SyntheticExperimentRun)
            .join(SyntheticExperiment)
            .filter(SyntheticExperiment.name.in_(preset_names))
        )
        if date_from is not None:
            query = query.filter(SyntheticExperimentRun.created_at >= date_from)
        return query.order_by(
            SyntheticExperimentRun.created_at.desc(),
            SyntheticExperimentRun.id.desc(),
        ).all()

    @staticmethod
    def _latest_synthetic_run_by_name(
        runs: list[SyntheticExperimentRun],
    ) -> dict[str, SyntheticExperimentRun]:
        latest_run_by_name: dict[str, SyntheticExperimentRun] = {}
        for run in runs:
            if run.experiment is None:
                continue
            latest_run_by_name.setdefault(str(run.experiment.name), run)
        return latest_run_by_name

    def _synthetic_preset_row(
        self,
        name: str,
        *,
        experiment: SyntheticExperiment | None,
        latest_preset_run: SyntheticExperimentRun | None,
    ) -> dict[str, Any]:
        summary = self._load_json_object(
            latest_preset_run.summary_json if latest_preset_run else None,
            context=RUN_SUMMARY_COLUMN,
        )
        row_experiment_id = (
            int(latest_preset_run.experiment_id)
            if latest_preset_run
            else int(experiment.id)
            if experiment
            else None
        )
        return {
            "name": name,
            "experiment_id": row_experiment_id,
            "latest_run_id": int(latest_preset_run.id) if latest_preset_run else None,
            "latest_run_status": latest_preset_run.status if latest_preset_run else None,
            "latest_finished_at": latest_preset_run.finished_at if latest_preset_run else None,
            "sample_status": summary.get("sample_status"),
            "total_settled_count": int(summary.get("total_settled_count") or 0),
            "missing_total_settled_count": int(
                summary.get("missing_total_settled_count") or 0
            ),
            "insufficient_operator_count": len(summary.get("insufficient_operators") or []),
            "evidence_scope": self.SYNTHETIC_EVIDENCE_SCOPE,
            "canonical_only_reason": self.SYNTHETIC_CANONICAL_ONLY_REASON,
        }

    def _synthetic_validation_counts(
        self,
        preset_rows: list[dict[str, Any]],
        recent_runs: list[SyntheticExperimentRun],
    ) -> dict[str, int]:
        return {
            "saved_preset_count": sum(
                1 for item in preset_rows if item["experiment_id"] is not None
            ),
            "completed_preset_count": sum(
                1 for item in preset_rows if item["latest_run_status"] == "completed"
            ),
            "failed_preset_count": sum(
                1 for item in preset_rows if item["latest_run_status"] == "failed"
            ),
            "sufficient_preset_count": sum(
                1 for item in preset_rows if item["sample_status"] == SAMPLE_STATUS_SUFFICIENT
            ),
            "recent_run_count": len(recent_runs),
            "recent_completed_count": sum(
                1 for run in recent_runs if str(run.status) == "completed"
            ),
            "recent_failed_count": sum(
                1 for run in recent_runs if str(run.status) == "failed"
            ),
        }

    def _synthetic_latest_run_summary(
        self,
        latest_run: SyntheticExperimentRun | None,
    ) -> dict[str, Any] | None:
        if latest_run is None:
            return None
        latest_summary = self._load_json_object(
            latest_run.summary_json,
            context=RUN_SUMMARY_COLUMN,
        )
        return {
            "experiment_id": int(latest_run.experiment_id),
            "experiment_name": latest_run.experiment.name if latest_run.experiment else None,
            "run_id": int(latest_run.id),
            "status": latest_run.status,
            "created_at": latest_run.created_at,
            "finished_at": latest_run.finished_at,
            "sample_status": latest_summary.get("sample_status"),
            "total_settled_count": int(latest_summary.get("total_settled_count") or 0),
            "missing_total_settled_count": int(
                latest_summary.get("missing_total_settled_count") or 0
            ),
            "evidence_scope": self.SYNTHETIC_EVIDENCE_SCOPE,
            "canonical_only_reason": self.SYNTHETIC_CANONICAL_ONLY_REASON,
        }

    def _synthetic_validation_status(
        self,
        *,
        preset_count: int,
        saved_preset_count: int,
        completed_preset_count: int,
        failed_preset_count: int,
        sufficient_preset_count: int,
        recent_run_count: int,
    ) -> tuple[str, str]:
        """Convert G-1 synthetic run state into dashboard status/detail."""
        if preset_count == 0:
            return "info", "G-1 synthetic preset is not configured."
        if failed_preset_count > 0:
            return "critical", f"{failed_preset_count} G-1 preset run(s) failed."
        if saved_preset_count == 0:
            return "info", "No G-1 synthetic preset has been saved yet."
        if sufficient_preset_count >= preset_count:
            return "healthy", "All G-1 presets have sufficient settled samples."
        if completed_preset_count > 0:
            return (
                "watch",
                f"{sufficient_preset_count}/{preset_count} G-1 preset(s) reached the sample target.",
            )
        if recent_run_count > 0:
            return "watch", "G-1 synthetic runs exist, but no preset has completed yet."
        return (
            "watch",
            f"{saved_preset_count}/{preset_count} G-1 preset(s) saved; run experiments to collect samples.",
        )

    @staticmethod
    def _synthetic_validation_dashboard_cards(
        synthetic_validation_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            {
                "key": "synthetic_g1_presets",
                "label": "G-1 preset 준비",
                "value": synthetic_validation_summary["saved_preset_count"],
                "unit": "count",
                "status": synthetic_validation_summary["status"],
                "detail": (
                    f"{synthetic_validation_summary['saved_preset_count']}/"
                    f"{synthetic_validation_summary['preset_count']} preset saved."
                ),
            },
            {
                "key": "synthetic_g1_samples",
                "label": "G-1 충분 표본 preset",
                "value": synthetic_validation_summary["sufficient_preset_count"],
                "unit": "count",
                "status": synthetic_validation_summary["status"],
                "detail": synthetic_validation_summary["detail"],
            },
        ]
