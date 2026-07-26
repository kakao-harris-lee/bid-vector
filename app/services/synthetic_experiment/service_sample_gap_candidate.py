"""Sample-gap candidate resolution helpers mixin."""

from __future__ import annotations

from typing import Any, Optional

from app.models.models import SyntheticExperiment, SyntheticExperimentRun, User
from app.services.synthetic_backtest import SYNTHETIC_USERNAME_PREFIX

from .constants import SAMPLE_GAP_WARNING_MIXED_DATA
from .sample_gap import (
    _safe_positive_int,
    _sample_gap_cli_command,
    _sample_gap_matches_fixed_preset,
    _sample_gap_operator_slugs_match,
    _sample_gap_params_match,
)
from .serialization import _json_loads


class SampleGapCandidateMixin:
    """Action/experiment/operator resolution for a single sample-gap item."""

    def _default_sample_gap_action_code(
        self,
        gap: dict[str, Any],
        action_lookup: dict[str, dict[str, Any]],
    ) -> str:
        warnings = set(gap.get("warnings", []) or [])
        if (
            SAMPLE_GAP_WARNING_MIXED_DATA in warnings
            and "rerun_synthetic_only" in action_lookup
        ):
            return "rerun_synthetic_only"
        if action_lookup:
            return next(iter(action_lookup))
        raise ValueError("Sample gap has no recommended actions.")

    def _sample_gap_candidate_experiment(
        self,
        *,
        preset_name: Optional[str],
        gap: dict[str, Any],
    ) -> Optional[SyntheticExperiment]:
        if preset_name:
            experiment = (
                self.db.query(SyntheticExperiment)
                .filter(SyntheticExperiment.name == preset_name)
                .first()
            )
            if experiment is not None:
                return experiment

        related_runs = gap.get("related_runs") or []
        source_experiment_id = 0
        if related_runs and isinstance(related_runs[0], dict):
            source_experiment_id = _safe_positive_int(
                related_runs[0].get("experiment_id")
            )
        if source_experiment_id <= 0:
            return None
        return self.get_experiment(source_experiment_id)

    def _sample_gap_candidate_operator_slugs(
        self,
        *,
        definition: Optional[dict[str, Any]],
        experiment: Optional[SyntheticExperiment],
        gap: dict[str, Any],
    ) -> list[str]:
        if definition is not None:
            return [str(slug) for slug in definition.get("operator_slugs") or []]
        if experiment is not None:
            operator_slugs = _json_loads(experiment.operator_slugs_json) or []
            if isinstance(operator_slugs, list):
                return [str(slug) for slug in operator_slugs]
        related_runs = gap.get("related_runs") or []
        if related_runs and isinstance(related_runs[0], dict):
            operator_slugs = related_runs[0].get("operator_slugs") or []
            if isinstance(operator_slugs, list):
                return [str(slug) for slug in operator_slugs]
        return []

    def _sample_gap_candidate_operator_targets(
        self,
        operator_slugs: list[str],
    ) -> list[dict[str, Any]]:
        pairs = [
            (
                str(slug),
                (
                    str(slug)
                    if str(slug).startswith(SYNTHETIC_USERNAME_PREFIX)
                    else f"{SYNTHETIC_USERNAME_PREFIX}{slug}"
                ),
            )
            for slug in operator_slugs
        ]
        usernames = [username for _, username in pairs]
        users_by_username: dict[str, User] = {}
        if usernames:
            users = (
                self.db.query(User)
                .filter(User.username.in_(usernames))
                .filter(User.is_active.is_(True))
                .all()
            )
            users_by_username = {str(user.username): user for user in users}

        targets: list[dict[str, Any]] = []
        for slug, username in pairs:
            user = users_by_username.get(username)
            user_id = int(user.id) if user is not None else None
            resolved = user_id is not None
            targets.append(
                {
                    "slug": slug,
                    "username": username,
                    "operator_id": user_id,
                    "user_id": user_id,
                    "resolved": resolved,
                    "operator_id_scope_ready": resolved,
                }
            )
        return targets

    def _latest_experiment_run(
        self, experiment: Optional[SyntheticExperiment]
    ) -> Optional[SyntheticExperimentRun]:
        if experiment is None or not experiment.runs:
            return None
        return max(experiment.runs, key=lambda run: run.id)

    def _sample_gap_candidate_next_step(
        self,
        *,
        run_allowed: bool,
        experiment: Optional[SyntheticExperiment],
        preset_name: Optional[str],
        definition: Optional[dict[str, Any]],
        params: dict[str, Any],
        operator_slugs: list[str],
    ) -> str:
        if not run_allowed:
            return "resolve_mixed_data"
        if (
            experiment is not None
            and _sample_gap_params_match(experiment, params)
            and _sample_gap_operator_slugs_match(experiment, operator_slugs)
        ):
            return "run_existing_experiment"
        if (
            preset_name
            and definition is not None
            and _sample_gap_matches_fixed_preset(
                definition=definition,
                params=params,
                operator_slugs=operator_slugs,
            )
        ):
            return "save_preset"
        return "create_experiment"

    def _sample_gap_candidate_execution_plan(
        self,
        *,
        next_step: str,
        preset_name: Optional[str],
        experiment_id: Optional[int],
        experiment_payload: dict[str, Any],
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        dimension = str(source_context.get("dimension") or "")
        key = str(source_context.get("key") or "")
        action_code = str(source_context.get("action_code") or "")
        dry_run_command = _sample_gap_cli_command(
            preset_name=preset_name,
            dimension=dimension,
            key=key,
            action_code=action_code,
            write=False,
        )
        write_command = _sample_gap_cli_command(
            preset_name=preset_name,
            dimension=dimension,
            key=key,
            action_code=action_code,
            write=True,
        )
        base = {
            "approval_required": True,
            "dry_run_default": True,
            "source_context": source_context,
            "cli_command": dry_run_command,
            "write_cli_command": None,
        }
        run_body = {"source_sample_gap_candidate": source_context}
        if next_step == "resolve_mixed_data":
            return {
                **base,
                "mode": "blocked",
                "preset_request": None,
                "experiment_request": None,
                "run_request": None,
                "instructions": [
                    "Do not enqueue a run from this candidate.",
                    "Resolve mixed canonical/synthetic data and rerun synthetic-only first.",
                ],
            }
        if next_step == "run_existing_experiment" and experiment_id is not None:
            return {
                **base,
                "mode": "run_existing_experiment",
                "preset_request": None,
                "experiment_request": None,
                "run_request": {
                    "method": "POST",
                    "path": f"/api/v1/synthetic/experiments/{experiment_id}/runs",
                    "body": run_body,
                },
                "write_cli_command": write_command,
                "instructions": [
                    "Dry-run this plan first; --write enqueues the asynchronous evidence run.",
                    "The API request only queues the run and does not run the backtest inline.",
                ],
            }
        if next_step == "save_preset" and preset_name:
            return {
                **base,
                "mode": "save_preset_then_run",
                "preset_request": {
                    "method": "POST",
                    "path": f"/api/v1/synthetic/experiments/presets/{preset_name}",
                    "body": {},
                },
                "experiment_request": None,
                "run_request": {
                    "method": "POST",
                    "path": "/api/v1/synthetic/experiments/{experiment_id}/runs",
                    "body": run_body,
                },
                "write_cli_command": write_command,
                "instructions": [
                    "Save the preset, then enqueue a run using the returned experiment id.",
                    "--write performs both DB write steps and requires operator approval.",
                ],
            }
        return {
            **base,
            "mode": "create_experiment_then_run",
            "preset_request": None,
            "experiment_request": {
                "method": "POST",
                "path": "/api/v1/synthetic/experiments",
                "body": experiment_payload,
            },
            "run_request": {
                "method": "POST",
                "path": "/api/v1/synthetic/experiments/{experiment_id}/runs",
                "body": run_body,
            },
            "write_cli_command": write_command,
            "instructions": [
                "Create the candidate experiment, then enqueue a run using the returned experiment id.",
                "--write performs both DB write steps and requires operator approval.",
            ],
        }

    def _sample_gap_candidate_message(
        self,
        *,
        next_step: str,
        preset_name: Optional[str],
        blocked_by_warnings: list[str],
    ) -> str:
        if SAMPLE_GAP_WARNING_MIXED_DATA in blocked_by_warnings:
            return (
                "Related runs include canonical/operator data mixed with synthetic "
                "results. Review the source run and create a synthetic-only rerun "
                "candidate before treating it as reporting-ready."
            )
        if next_step == "run_existing_experiment":
            return "Existing experiment is ready to select and run asynchronously."
        if next_step == "save_preset":
            return f"Save preset {preset_name} before starting a run."
        return "Create this experiment candidate before starting a run."
