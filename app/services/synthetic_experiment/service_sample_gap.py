"""Sample-gap planning + candidate materialization mixin."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.models.models import SyntheticExperiment, SyntheticExperimentRun

from .constants import (
    RUN_STATUS_COMPLETED,
    SAMPLE_GAP_DIMENSION_ORDER,
    SAMPLE_GAP_WARNING_LEGACY_SUMMARY,
    SYNTHETIC_EXPERIMENT_PRESETS,
)
from .sample_gap import (
    _SampleGapAccumulator,
    _candidate_params_for_action,
    _dedupe_sample_gap_warnings,
    _safe_positive_int,
    _sample_gap_candidate_description,
    _sample_gap_candidate_name,
    _sample_gap_candidate_warning_context,
    _sample_gap_run_context,
    _sample_gap_run_warnings,
    _sample_gap_source_context,
)
from .serialization import RUN_SUMMARY_COLUMN, _json_loads


class SampleGapPlanningMixin:
    """Read-only sample-gap plan + candidate build + write materialization."""

    def build_sample_gap_plan(self, *, max_runs: int = 20) -> dict[str, Any]:
        """Aggregate recent completed sample-report gaps into a backfill plan.

        This is read-only planning over persisted run summaries. It deliberately
        does not trigger DB backfills or new experiment runs; the response gives
        operators the preset/category/window/limit hints needed to decide the
        next synthetic backtest.
        """
        bounded_max_runs = min(100, max(1, int(max_runs or 20)))
        runs = (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.status == RUN_STATUS_COMPLETED)
            .order_by(
                SyntheticExperimentRun.finished_at.is_(None).asc(),
                SyntheticExperimentRun.finished_at.desc(),
                SyntheticExperimentRun.created_at.desc(),
                SyntheticExperimentRun.id.desc(),
            )
            .limit(bounded_max_runs)
            .all()
        )

        gap_groups: dict[tuple[str, str], _SampleGapAccumulator] = {}
        warnings: list[dict[str, Any]] = []
        legacy_run_ids: list[int] = []
        source_run_count = 0

        for run in runs:
            summary = _json_loads(run.summary_json, context=RUN_SUMMARY_COLUMN)
            if not isinstance(summary, dict):
                legacy_run_ids.append(run.id)
                continue
            sample_report = summary.get("sample_report")
            if not isinstance(sample_report, dict):
                legacy_run_ids.append(run.id)
                continue

            source_run_count += 1
            run_context = _sample_gap_run_context(
                run, summary=summary, sample_report=sample_report
            )
            run_warnings = _sample_gap_run_warnings(run, sample_report)
            warning_codes = [str(warning["code"]) for warning in run_warnings]
            warnings.extend(run_warnings)

            lacking_groups = sample_report.get("lacking_groups") or []
            if not isinstance(lacking_groups, list):
                continue
            for row in lacking_groups:
                if not isinstance(row, dict):
                    continue
                dimension = str(row.get("dimension") or "")
                if dimension not in SAMPLE_GAP_DIMENSION_ORDER:
                    continue
                key = str(row.get("key") or "unknown")
                missing = _safe_positive_int(row.get("missing_settled_count"))
                if missing <= 0:
                    continue
                group_key = (dimension, key)
                accumulator = gap_groups.get(group_key)
                if accumulator is None:
                    accumulator = _SampleGapAccumulator(
                        dimension=dimension,
                        key=key,
                    )
                    gap_groups[group_key] = accumulator
                accumulator.add(
                    row=row,
                    run_context=run_context,
                    warning_codes=warning_codes,
                )

        if legacy_run_ids:
            warnings.append(
                {
                    "code": SAMPLE_GAP_WARNING_LEGACY_SUMMARY,
                    "message": (
                        "Completed runs without summary.sample_report were skipped; "
                        "rerun them to include G-1 sample-gap metadata."
                    ),
                    "run_ids": sorted(legacy_run_ids),
                    "operator_slugs": [],
                }
            )

        ordered_gaps = sorted(
            gap_groups.values(),
            key=lambda item: (
                -item.total_missing_settled_count,
                -item.missing_settled_count,
                -item.source_run_count,
                SAMPLE_GAP_DIMENSION_ORDER[item.dimension],
                item.key,
            ),
        )
        gaps = [
            accumulator.to_item(priority=index + 1)
            for index, accumulator in enumerate(ordered_gaps)
        ]
        return {
            "generated_at": datetime.now(timezone.utc),
            "max_runs": bounded_max_runs,
            "scanned_completed_run_count": len(runs),
            "source_run_count": source_run_count,
            "legacy_summary_run_count": len(legacy_run_ids),
            "gap_count": len(gaps),
            "warnings": _dedupe_sample_gap_warnings(warnings),
            "gaps": gaps,
        }

    def build_sample_gap_run_candidate(
        self,
        *,
        dimension: str,
        key: str,
        max_runs: int = 20,
        action_code: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Build a read-only runnable candidate from one sample-gap item.

        This intentionally does not save an experiment or enqueue a run. It turns
        the recommendation already exposed in ``sample-gaps`` into an explicit
        experiment payload plus the next UI step.
        """
        plan = self.build_sample_gap_plan(max_runs=max_runs)
        gap = next(
            (
                item
                for item in plan["gaps"]
                if item["dimension"] == dimension and item["key"] == key
            ),
            None,
        )
        if gap is None:
            return None

        recommendation, action = self._resolve_sample_gap_candidate_action(
            gap,
            action_code=action_code,
        )
        preset_name = recommendation.get("preset_name")
        preset_name = str(preset_name) if preset_name else None
        definition = (
            SYNTHETIC_EXPERIMENT_PRESETS.get(preset_name) if preset_name else None
        )
        experiment = self._sample_gap_candidate_experiment(
            preset_name=preset_name,
            gap=gap,
        )
        base_params = dict(definition.get("params") or {}) if definition else {}
        base_params.update(recommendation.get("params") or {})
        params = _candidate_params_for_action(
            base_params,
            action_code=str(action["code"]),
            missing_settled_count=_safe_positive_int(
                gap.get("missing_settled_count")
            ),
        )
        operator_slugs = self._sample_gap_candidate_operator_slugs(
            definition=definition,
            experiment=experiment,
            gap=gap,
        )
        operator_targets = self._sample_gap_candidate_operator_targets(operator_slugs)
        operator_id_scope_ready = bool(operator_targets) and all(
            bool(target.get("operator_id_scope_ready"))
            for target in operator_targets
        )
        warnings, blocked_by_warnings, run_allowed = (
            _sample_gap_candidate_warning_context(gap)
        )
        latest_run = self._latest_experiment_run(experiment)
        action_label = str(action.get("label") or action["code"])
        experiment_payload = {
            "name": _sample_gap_candidate_name(
                preset_name=preset_name,
                dimension=str(gap["dimension"]),
                key=str(gap["key"]),
                action_code=str(action["code"]),
            ),
            "description": _sample_gap_candidate_description(
                dimension=str(gap["dimension"]),
                key=str(gap["key"]),
                action_label=action_label,
            ),
            "params": params,
            "operator_slugs": operator_slugs,
        }
        next_step = self._sample_gap_candidate_next_step(
            run_allowed=run_allowed,
            experiment=experiment,
            preset_name=preset_name,
            definition=definition,
            params=params,
            operator_slugs=operator_slugs,
        )
        source_context = _sample_gap_source_context(
            gap=gap,
            action_code=str(action["code"]),
            action_label=action_label,
            preset_name=preset_name,
            params=params,
            operator_slugs=operator_slugs,
            operator_targets=operator_targets,
            operator_id_scope_ready=operator_id_scope_ready,
            run_allowed=run_allowed,
            blocked_by_warnings=blocked_by_warnings,
            warnings=warnings,
        )
        execution_plan = self._sample_gap_candidate_execution_plan(
            next_step=next_step,
            preset_name=preset_name,
            experiment_id=experiment.id if experiment else None,
            experiment_payload=experiment_payload,
            source_context=source_context,
        )
        return self._sample_gap_candidate_response(
            gap=gap,
            action=action,
            action_label=action_label,
            preset_name=preset_name,
            params=params,
            operator_slugs=operator_slugs,
            operator_targets=operator_targets,
            operator_id_scope_ready=operator_id_scope_ready,
            experiment_payload=experiment_payload,
            experiment=experiment,
            latest_run=latest_run,
            next_step=next_step,
            execution_plan=execution_plan,
            run_allowed=run_allowed,
            blocked_by_warnings=blocked_by_warnings,
            warnings=warnings,
        )

    def _sample_gap_candidate_response(
        self,
        *,
        gap: dict[str, Any],
        action: dict[str, Any],
        action_label: str,
        preset_name: Optional[str],
        params: dict[str, Any],
        operator_slugs: list[str],
        operator_targets: list[dict[str, Any]],
        operator_id_scope_ready: bool,
        experiment_payload: dict[str, Any],
        experiment: Optional[SyntheticExperiment],
        latest_run: Optional[SyntheticExperimentRun],
        next_step: str,
        execution_plan: dict[str, Any],
        run_allowed: bool,
        blocked_by_warnings: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc),
            "gap": gap,
            "action_code": str(action["code"]),
            "action_label": action_label,
            "preset_name": preset_name,
            "params": params,
            "operator_slugs": operator_slugs,
            "operator_targets": operator_targets,
            "operator_id_scope_ready": operator_id_scope_ready,
            "experiment_payload": experiment_payload,
            "experiment_id": experiment.id if experiment else None,
            "latest_run_id": latest_run.id if latest_run else None,
            "latest_run_status": latest_run.status if latest_run else None,
            "next_step": next_step,
            "execution_plan": execution_plan,
            "run_allowed": run_allowed,
            "blocked_by_warnings": blocked_by_warnings,
            "warnings": warnings,
            "message": self._sample_gap_candidate_message(
                next_step=next_step,
                preset_name=preset_name,
                blocked_by_warnings=blocked_by_warnings,
            ),
        }

    def _resolve_sample_gap_candidate_action(
        self,
        gap: dict[str, Any],
        *,
        action_code: Optional[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        recommendation = gap.get("recommendation") or {}
        actions = recommendation.get("actions") or []
        action_lookup = {
            str(action.get("code")): action
            for action in actions
            if isinstance(action, dict) and action.get("code")
        }
        selected_action_code = action_code or self._default_sample_gap_action_code(
            gap,
            action_lookup,
        )
        action = action_lookup.get(str(selected_action_code))
        if action is None:
            available = ", ".join(sorted(action_lookup)) or "none"
            raise ValueError(
                f"Unsupported sample-gap action '{selected_action_code}'. "
                f"Available actions: {available}."
            )
        return recommendation, action

    def materialize_sample_gap_candidate_run(
        self,
        *,
        dimension: str,
        key: str,
        max_runs: int = 20,
        action_code: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Persist the selected candidate and enqueue its async evidence run.

        This is intentionally not called by the candidate API endpoint. It is
        used by explicit operator write paths (CLI or a separate approval flow)
        after the read-only candidate has been inspected.
        """
        candidate = self.build_sample_gap_run_candidate(
            dimension=dimension,
            key=key,
            max_runs=max_runs,
            action_code=action_code,
        )
        if candidate is None:
            return None
        if not candidate.get("run_allowed"):
            return {
                "status": "blocked",
                "candidate": candidate,
                "experiment": None,
                "run": None,
            }

        next_step = str(candidate.get("next_step") or "")
        experiment: Optional[SyntheticExperiment] = None
        if next_step == "run_existing_experiment":
            experiment_id = _safe_positive_int(candidate.get("experiment_id"))
            if experiment_id > 0:
                experiment = self.get_experiment(experiment_id)
        elif next_step == "save_preset":
            preset_name = candidate.get("preset_name")
            if preset_name:
                experiment = self.ensure_preset(str(preset_name))
        else:
            payload = candidate.get("experiment_payload") or {}
            experiment = self.create_experiment(
                name=str(payload.get("name") or "sample-gap-candidate"),
                description=payload.get("description"),
                params=dict(payload.get("params") or {}),
                operator_slugs=list(payload.get("operator_slugs") or []),
            )

        if experiment is None:
            raise ValueError("Could not materialize sample-gap candidate experiment.")

        source_context = (
            (candidate.get("execution_plan") or {}).get("source_context") or {}
        )
        run = self.create_run(
            experiment,
            source_sample_gap_candidate=(
                source_context if isinstance(source_context, dict) else None
            ),
        )
        return {
            "status": "queued",
            "candidate": candidate,
            "experiment": self.serialize_experiment(experiment),
            "run": self.serialize_run_detail(run),
        }
