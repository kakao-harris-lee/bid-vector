"""Experiment run lifecycle: create, list, evaluate, update, and apply.

The operator-facing service entry points. Persists new experiment definitions,
serves the dashboard list/detail, runs auto-evaluation through the lifecycle
transition machine, applies manual status updates through the status-effect
table, and converts successful experiments into persisted threshold/strategy
tuning. Method bodies are the original ``DecisionExperimentService`` methods,
moved verbatim.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utc_now
from app.models.models import DecisionExperimentRun, User
from app.schemas.schemas import (
    DecisionExperimentRunCreateRequest,
    DecisionExperimentRunUpdateRequest,
    DecisionExperimentStrategyApplyRequest,
    DecisionExperimentThresholdApplyRequest,
)
from app.services.decision_experiments.base import (
    BASELINE_SUMMARY_COLUMN,
    _DecisionExperimentBase,
)
from app.services.decision_experiments.verdict_machine import (
    _DEFAULT_STATUS_EFFECT,
    _EVALUATION_LIFECYCLE_RULES,
    _UPDATE_STATUS_EFFECTS,
    _LifecycleContext,
)
from app.services.preview_snapshot import PreviewSnapshotService


class _LifecycleMixin(_DecisionExperimentBase):
    """Persist, evaluate, update, and apply decision-experiment runs."""

    def create_run(
        self,
        db: Session,
        *,
        request: DecisionExperimentRunCreateRequest,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Persist one experiment definition with a baseline snapshot collected at creation time."""
        target_operator = self._resolve_operator(db, operator=operator)
        now = utc_now()
        started_at = ensure_utc(request.started_at or now)
        baseline_start = started_at - timedelta(days=int(request.baseline_days))
        baseline_summary = self._build_snapshot(
            db,
            operator_id=target_operator.id,
            start_at=baseline_start,
            end_at=started_at,
        )

        run = DecisionExperimentRun(
            operator_id=target_operator.id,
            experiment_key=request.experiment_key,
            recommendation_key=request.recommendation_key,
            status="planned" if started_at > now else "running",
            priority_rank=request.priority_rank,
            title=request.title,
            hypothesis=request.hypothesis,
            suggested_change=request.suggested_change,
            target_metric=request.target_metric,
            expected_direction=request.expected_direction,
            success_criteria=request.success_criteria,
            guardrail_metric=request.guardrail_metric,
            minimum_decision_sample=request.minimum_decision_sample,
            duration_days=request.duration_days,
            baseline_days=request.baseline_days,
            rollback_trigger=request.rollback_trigger,
            notes=request.notes or "",
            baseline_summary=self._dump_json(baseline_summary),
            latest_evaluation="{}",
            started_at=started_at,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return self.get_run_detail(db, run_id=int(run.id), operator=target_operator)

    def list_runs(
        self,
        db: Session,
        *,
        limit: int = 20,
        run_status: str | None = None,
        outcome: str | None = None,
        application_status: str | None = None,
        sort: str = "needs_attention",
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Return recent experiment runs for dashboard and operator review."""
        target_operator = self._resolve_operator(db, operator=operator)
        query = db.query(DecisionExperimentRun).filter(DecisionExperimentRun.operator_id == target_operator.id)
        if run_status:
            query = query.filter(DecisionExperimentRun.status == run_status)
        if outcome:
            query = query.filter(DecisionExperimentRun.outcome == outcome)

        runs = query.order_by(DecisionExperimentRun.created_at.desc(), DecisionExperimentRun.id.desc()).all()
        serialized_runs = [self._serialize_run(run) for run in runs]
        if application_status:
            serialized_runs = [
                run for run in serialized_runs
                if str(run["application_status"]) == application_status
            ]
        serialized_runs = self._sort_serialized_runs(serialized_runs, sort=sort)
        limited_runs = serialized_runs[:limit]
        return {
            **self._operator_context_fields(target_operator),
            "result_count": len(limited_runs),
            "total_match_count": len(serialized_runs),
            "sort": self._normalize_run_sort(sort),
            "active_count": sum(1 for run in limited_runs if run["status"] in {"planned", "running"}),
            "completed_count": sum(1 for run in limited_runs if run["status"] == "completed"),
            "rolled_back_count": sum(1 for run in limited_runs if run["status"] == "rolled_back"),
            "failed_count": sum(1 for run in limited_runs if run["status"] == "failed"),
            "success_count": sum(1 for run in limited_runs if run.get("outcome") == "success"),
            "pending_count": sum(1 for run in limited_runs if run.get("outcome") in {None, "watch", "insufficient_data"}),
            "inconclusive_count": sum(1 for run in limited_runs if run.get("outcome") == "inconclusive"),
            "rollback_count": sum(1 for run in limited_runs if run.get("outcome") == "rollback"),
            "applicable_count": sum(1 for run in limited_runs if run["application_status"] != "not_supported"),
            "ready_to_apply_count": sum(1 for run in limited_runs if run["application_status"] == "ready"),
            "applied_count": sum(1 for run in limited_runs if run["application_status"] == "applied"),
            "partially_applied_count": sum(1 for run in limited_runs if run["application_status"] == "partially_applied"),
            "blocked_count": sum(1 for run in limited_runs if run["application_status"] == "blocked"),
            "not_ready_count": sum(1 for run in limited_runs if run["application_status"] == "not_ready"),
            "not_supported_count": sum(1 for run in limited_runs if run["application_status"] == "not_supported"),
            "application_status_counts": self._count_by_key(limited_runs, "application_status"),
            "outcome_counts": self._count_by_key(limited_runs, "outcome", missing="pending"),
            "review_bucket_counts": self._count_by_key(limited_runs, "review_bucket"),
            "runs": limited_runs,
        }

    def get_run_detail(
        self,
        db: Session,
        *,
        run_id: int,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Return one run including its baseline metrics and latest evaluation, if available."""
        target_operator = self._resolve_operator(db, operator=operator)
        run = self._get_run_or_raise(db, run_id=run_id, operator=target_operator)
        run_started_at = ensure_utc(run.started_at)
        baseline_summary = self._load_json(run.baseline_summary, fallback=self._empty_snapshot(run_started_at, run_started_at), context=BASELINE_SUMMARY_COLUMN)
        return {
            **self._operator_context_fields(target_operator),
            "run": self._serialize_run(run),
            "baseline_summary": baseline_summary,
        }

    def evaluate_run(
        self,
        db: Session,
        *,
        run_id: int,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Compare the experiment window against the saved baseline and persist the latest evaluation."""
        target_operator = self._resolve_operator(db, operator=operator)
        run = self._get_run_or_raise(db, run_id=run_id, operator=target_operator)
        now = utc_now()
        run_started_at = ensure_utc(run.started_at)
        scheduled_end = run_started_at + timedelta(days=int(run.duration_days or 0))
        evaluation_window_end = run_started_at if now < run_started_at else min(now, scheduled_end)
        baseline_summary = self._load_json(run.baseline_summary, fallback=self._empty_snapshot(run_started_at, run_started_at), context=BASELINE_SUMMARY_COLUMN)
        current_summary = self._build_snapshot(
            db,
            operator_id=int(run.operator_id),
            start_at=run_started_at,
            end_at=evaluation_window_end,
        )
        evaluation = self._build_evaluation(
            run,
            baseline_summary=baseline_summary,
            current_summary=current_summary,
            evaluated_at=now,
            scheduled_end=scheduled_end,
        )

        run.latest_evaluation = self._dump_json(evaluation)
        run.last_evaluated_at = now
        run.outcome = evaluation["outcome"]

        lifecycle_context = _LifecycleContext(
            recommended_action=evaluation["recommended_action"],
            now=now,
            run_started_at=run_started_at,
            scheduled_end=scheduled_end,
        )
        lifecycle = next(
            rule for rule in _EVALUATION_LIFECYCLE_RULES if rule.predicate(lifecycle_context)
        )
        run.status = lifecycle.status
        if lifecycle.ended_at is not None:
            run.ended_at = lifecycle.ended_at(lifecycle_context)

        db.commit()
        db.refresh(run)
        return self.get_run_detail(db, run_id=int(run.id), operator=target_operator)

    def update_run(
        self,
        db: Session,
        *,
        run_id: int,
        request: DecisionExperimentRunUpdateRequest,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Manually update run notes or lifecycle state for operator workflow control."""
        target_operator = self._resolve_operator(db, operator=operator)
        run = self._get_run_or_raise(db, run_id=run_id, operator=target_operator)

        if request.replace_notes is not None:
            run.notes = str(request.replace_notes).strip()
        if request.append_note:
            run.notes = self._merge_notes(run.notes, str(request.append_note))

        if request.status is not None:
            run.status = request.status
            effect = _UPDATE_STATUS_EFFECTS.get(request.status, _DEFAULT_STATUS_EFFECT)
            resolved_outcome = effect.forced_outcome if effect.forced_outcome is not None else request.outcome
            if resolved_outcome is not None:
                run.outcome = resolved_outcome
            self._apply_status_ended_at(run, effect.ended_at, requested_ended_at=request.ended_at)
        elif request.outcome is not None:
            run.outcome = request.outcome

        db.commit()
        db.refresh(run)
        return self.get_run_detail(db, run_id=int(run.id), operator=target_operator)

    def _apply_status_ended_at(
        self,
        run: DecisionExperimentRun,
        mode: str,
        *,
        requested_ended_at: datetime | None,
    ) -> None:
        """Apply the ``ended_at`` side-effect for one manual status transition."""
        ended_at_setters: dict[str, Callable[[], datetime | None]] = {
            "set": lambda: ensure_utc(requested_ended_at or utc_now()),
            "clear": lambda: None,
        }
        setter = ended_at_setters.get(mode)
        if setter is not None:
            run.ended_at = setter()

    def apply_threshold_adjustments(
        self,
        db: Session,
        *,
        run_id: int,
        request: DecisionExperimentThresholdApplyRequest,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Convert one successful experiment into persisted operator decision-threshold updates."""
        target_operator = self._resolve_operator(db, operator=operator)
        run = self._get_run_or_raise(db, run_id=run_id, operator=target_operator)
        strategy = self._resolve_strategy(db, operator=target_operator)
        current_thresholds = self._current_strategy_thresholds(strategy)
        parameter_policy = self._build_run_parameter_policy(db, run)
        threshold_updates = self._build_threshold_adjustments(
            run,
            current_thresholds=current_thresholds,
            parameter_policy=parameter_policy,
        )
        if not threshold_updates:
            raise ValueError("This experiment does not map to a supported threshold adjustment yet")

        if not request.dry_run:
            if self._run_has_applied_thresholds(run) and not request.force:
                raise ValueError("Threshold adjustments were already applied for this experiment run")
            if str(run.outcome or "") != "success" and not request.force:
                raise ValueError("Threshold adjustments can only be applied after a successful experiment outcome")

            self._apply_threshold_updates(strategy, threshold_updates)
            run.notes = self._merge_notes(
                run.notes,
                self._build_threshold_application_note(
                    threshold_updates,
                    append_note=request.append_note,
                ),
            )
            db.commit()
            db.refresh(run)
            db.refresh(strategy)
            # 적용된 임계/튜닝은 preview 후보를 바꾼다: 사용 중인 스냅샷 키를
            # 재계산 디스패치한다 (설계 §6.3 — 구 preview_cache.invalidate 대체).
            PreviewSnapshotService().dispatch_for_strategy_write(
                db, operator_id=int(target_operator.id)
            )

        strategy_thresholds = self._current_strategy_thresholds(strategy)
        return {
            **self._operator_context_fields(target_operator),
            "operator_id": int(run.operator_id),
            "run_id": int(run.id),
            "experiment_key": str(run.experiment_key),
            "recommendation_key": str(run.recommendation_key),
            "applied": not request.dry_run,
            "dry_run": bool(request.dry_run),
            "latest_outcome": str(run.outcome) if run.outcome else None,
            "threshold_updates": threshold_updates,
            "strategy_thresholds": strategy_thresholds,
            "detail": (
                "Threshold adjustment preview generated."
                if request.dry_run
                else f"Applied {len(threshold_updates)} threshold adjustment(s) to the operator strategy."
            ),
        }

    def apply_strategy_adjustments(
        self,
        db: Session,
        *,
        run_id: int,
        request: DecisionExperimentStrategyApplyRequest,
        operator: User | None = None,
    ) -> dict[str, Any]:
        """Convert workload/category experiments into persisted operator strategy tuning."""
        target_operator = self._resolve_operator(db, operator=operator)
        run = self._get_run_or_raise(db, run_id=run_id, operator=target_operator)
        strategy = self._resolve_strategy(db, operator=target_operator)
        current_tuning = self._current_strategy_tuning(strategy)
        parameter_policy = self._build_run_parameter_policy(db, run)
        strategy_updates = self._build_strategy_adjustments(
            run,
            current_tuning=current_tuning,
            parameter_policy=parameter_policy,
        )
        if not strategy_updates:
            raise ValueError("This experiment does not map to a supported strategy adjustment yet")

        if not request.dry_run:
            if self._run_has_applied_strategy(run) and not request.force:
                raise ValueError("Strategy adjustments were already applied for this experiment run")
            if str(run.outcome or "") != "success" and not request.force:
                raise ValueError("Strategy adjustments can only be applied after a successful experiment outcome")

            self._apply_strategy_updates(strategy, strategy_updates)
            run.notes = self._merge_notes(
                run.notes,
                self._build_strategy_application_note(
                    strategy_updates,
                    append_note=request.append_note,
                ),
            )
            db.commit()
            db.refresh(run)
            db.refresh(strategy)
            # 적용된 임계/튜닝은 preview 후보를 바꾼다: 사용 중인 스냅샷 키를
            # 재계산 디스패치한다 (설계 §6.3 — 구 preview_cache.invalidate 대체).
            PreviewSnapshotService().dispatch_for_strategy_write(
                db, operator_id=int(target_operator.id)
            )

        strategy_tuning = self._current_strategy_tuning(strategy)
        return {
            **self._operator_context_fields(target_operator),
            "operator_id": int(run.operator_id),
            "run_id": int(run.id),
            "experiment_key": str(run.experiment_key),
            "recommendation_key": str(run.recommendation_key),
            "applied": not request.dry_run,
            "dry_run": bool(request.dry_run),
            "latest_outcome": str(run.outcome) if run.outcome else None,
            "strategy_updates": strategy_updates,
            "strategy_tuning": strategy_tuning,
            "detail": (
                "Strategy adjustment preview generated."
                if request.dry_run
                else f"Applied {len(strategy_updates)} strategy adjustment(s) to the operator strategy."
            ),
        }
