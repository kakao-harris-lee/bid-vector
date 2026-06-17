"""Persist and evaluate decision-tuning experiments."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account, ensure_operator_strategy, ensure_operator_strategy_for
from app.core.time import ensure_utc, utc_now
from app.models.models import DecisionExperimentRun, User
from app.schemas.schemas import (
    DecisionExperimentRunCreateRequest,
    DecisionExperimentRunUpdateRequest,
    DecisionExperimentStrategyApplyRequest,
    DecisionExperimentThresholdApplyRequest,
)
from app.services.decision_analytics import DecisionAnalyticsService
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    clamp_category_priority_override,
    dump_category_priority_overrides,
    get_strategy_auto_workload_penalty_multiplier,
    get_strategy_category_priority_overrides,
)


class DecisionExperimentService:
    """Manage saved experiment plans and compare their performance against a baseline."""

    THRESHOLD_EXPERIMENT_KEYS = {
        "exp-review-threshold-tighten",
        "exp-review-threshold-relax",
        "exp-bid-now-threshold-tighten",
    }
    STRATEGY_EXPERIMENT_KEYS = {
        "exp-workload-auto-calibration",
        "exp-category-focus-shift",
    }
    RATE_SUCCESS_DELTA = 0.1
    RATE_GUARDRAIL_DROP = -0.05
    COUNT_DROP_RATIO = 0.2
    ACTIVE_PENDING_GROWTH_RATIO = 0.2
    THRESHOLD_APPLICATION_PREFIX = "Threshold 적용:"
    STRATEGY_APPLICATION_PREFIX = "Strategy 적용:"
    PARAMETER_HISTORY_LOOKBACK_DAYS = 365
    THRESHOLD_PARAMETER_DELTAS = {
        "exp-review-threshold-tighten": (0.04, 0.02, 0.06),
        "exp-review-threshold-relax": (0.03, 0.015, 0.05),
        "exp-bid-now-threshold-tighten": (0.03, 0.015, 0.05),
    }
    CATEGORY_PARAMETER_BASE_DELTA = 0.03
    CATEGORY_PARAMETER_MIN_DELTA = 0.015
    CATEGORY_PARAMETER_MAX_DELTA = 0.05

    def __init__(self) -> None:
        self.analytics = DecisionAnalyticsService()

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
        baseline_summary = self._load_json(run.baseline_summary, fallback=self._empty_snapshot(run_started_at, run_started_at))
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
        baseline_summary = self._load_json(run.baseline_summary, fallback=self._empty_snapshot(run_started_at, run_started_at))
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

        if evaluation["recommended_action"] == "rollback":
            run.status = "rolled_back"
            run.ended_at = now
        elif now >= scheduled_end:
            run.status = "completed"
            run.ended_at = scheduled_end
        elif run_started_at <= now:
            run.status = "running"
        else:
            run.status = "planned"

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
            if request.status == "rolled_back":
                run.outcome = "rollback"
                run.ended_at = ensure_utc(request.ended_at or utc_now())
            elif request.status == "completed":
                if request.outcome is not None:
                    run.outcome = request.outcome
                run.ended_at = ensure_utc(request.ended_at or utc_now())
            else:
                if request.outcome is not None:
                    run.outcome = request.outcome
                if request.status in {"planned", "running"}:
                    run.ended_at = None
        elif request.outcome is not None:
            run.outcome = request.outcome

        db.commit()
        db.refresh(run)
        return self.get_run_detail(db, run_id=int(run.id), operator=target_operator)

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

    def _build_snapshot(self, db: Session, *, operator_id: int, start_at: datetime, end_at: datetime) -> dict[str, Any]:
        """Build a compact metrics snapshot for one experiment window."""
        safe_end_at = end_at if end_at >= start_at else start_at
        decisions = self.analytics._load_decisions_in_range(
            db,
            operator_id=operator_id,
            start_at=start_at,
            end_at=safe_end_at,
        )
        summary = self.analytics._build_funnel_summary(decisions)
        category_breakdown = self.analytics._build_segment_breakdown(
            decisions,
            segment_resolver=self.analytics._resolve_category_segment,
            limit=20,
        )
        workload_breakdown = self.analytics._build_segment_breakdown(
            decisions,
            segment_resolver=lambda decision: str(decision.workload_source or self.analytics.DEFAULT_WORKLOAD_SOURCE),
            limit=10,
        )
        auto_segment = self.analytics._find_segment(workload_breakdown, "auto")
        provided_segment = self.analytics._find_segment(workload_breakdown, self.analytics.DEFAULT_WORKLOAD_SOURCE)

        category_segments = [
            segment
            for segment in category_breakdown
            if segment.get("submission_rate") is not None
        ]
        best_category = max(category_segments, key=lambda item: (float(item.get("submission_rate") or 0.0), int(item.get("decision_count") or 0))) if category_segments else None
        worst_category = min(category_segments, key=lambda item: (float(item.get("submission_rate") or 0.0), -int(item.get("decision_count") or 0))) if category_segments else None

        return {
            "window_start": start_at,
            "window_end": safe_end_at,
            "decision_count": int(summary.get("decision_count") or 0),
            "submitted_count": int(summary.get("submitted_count") or 0),
            "active_pending_count": int(summary.get("active_pending_count") or 0),
            "overall_submission_rate": summary.get("overall_submission_rate"),
            "workflow_submission_rate": summary.get("workflow_submission_rate"),
            "bid_now_submission_rate": summary.get("bid_now_submission_rate"),
            "review_submission_rate": summary.get("review_submission_rate"),
            "auto_submission_rate": auto_segment.get("submission_rate") if auto_segment is not None else None,
            "provided_submission_rate": provided_segment.get("submission_rate") if provided_segment is not None else None,
            "best_category": best_category.get("segment") if best_category is not None else None,
            "best_category_submission_rate": best_category.get("submission_rate") if best_category is not None else None,
            "worst_category": worst_category.get("segment") if worst_category is not None else None,
            "worst_category_submission_rate": worst_category.get("submission_rate") if worst_category is not None else None,
        }

    def _build_evaluation(
        self,
        run: DecisionExperimentRun,
        *,
        baseline_summary: dict[str, Any],
        current_summary: dict[str, Any],
        evaluated_at: datetime,
        scheduled_end: datetime,
    ) -> dict[str, Any]:
        """Convert metric deltas into an operator-friendly experiment verdict."""
        sample_size = int(current_summary.get("decision_count") or 0)
        minimum_sample_reached = sample_size >= int(run.minimum_decision_sample or 1)
        baseline_target_value = self._resolve_metric_value(baseline_summary, str(run.target_metric or ""))
        current_target_value = self._resolve_metric_value(current_summary, str(run.target_metric or ""))
        target_delta = self._delta(current_target_value, baseline_target_value)
        baseline_guardrail_value = self._resolve_metric_value(baseline_summary, str(run.guardrail_metric or ""))
        current_guardrail_value = self._resolve_metric_value(current_summary, str(run.guardrail_metric or ""))
        guardrail_delta = self._delta(current_guardrail_value, baseline_guardrail_value)

        if not minimum_sample_reached:
            outcome = "insufficient_data"
            recommended_action = "collect_more_data"
            summary = (
                f"현재 표본 {sample_size}건으로는 실험 판단이 이릅니다. "
                f"최소 {int(run.minimum_decision_sample or 1)}건이 쌓일 때까지 더 수집하세요."
            )
        elif self._guardrail_broken(str(run.guardrail_metric or ""), baseline_guardrail_value, current_guardrail_value):
            outcome = "rollback"
            recommended_action = "rollback"
            summary = (
                f"가드레일 지표 `{run.guardrail_metric}`가 기준 대비 악화되었습니다. "
                f"현재 변경안을 롤백하고 원인을 점검하는 편이 안전합니다."
            )
        elif self._metric_improved(str(run.expected_direction or "increase"), str(run.target_metric or ""), baseline_target_value, current_target_value):
            outcome = "success" if evaluated_at >= scheduled_end else "watch"
            recommended_action = "complete" if evaluated_at >= scheduled_end else "continue"
            summary = (
                f"목표 지표 `{run.target_metric}`가 기준 대비 개선되었습니다. "
                f"현재 추세를 유지하며 {'실험을 종료' if recommended_action == 'complete' else '추가 표본을 수집'}하세요."
            )
        elif evaluated_at >= scheduled_end:
            outcome = "inconclusive"
            recommended_action = "complete"
            summary = (
                f"예정된 실험 기간은 종료되었지만 `{run.target_metric}` 개선이 충분하지 않았습니다. "
                f"결과를 기록하고 다음 가설로 넘어가는 편이 좋습니다."
            )
        else:
            outcome = "watch"
            recommended_action = "continue"
            summary = (
                f"아직 목표 지표 `{run.target_metric}` 개선 폭이 충분하지 않습니다. "
                f"기간 종료 전까지 추이를 더 관찰하세요."
            )

        return {
            "evaluated_at": evaluated_at,
            "sample_size": sample_size,
            "minimum_sample_reached": minimum_sample_reached,
            "target_metric": run.target_metric,
            "baseline_target_value": baseline_target_value,
            "current_target_value": current_target_value,
            "target_delta": target_delta,
            "guardrail_metric": run.guardrail_metric,
            "baseline_guardrail_value": baseline_guardrail_value,
            "current_guardrail_value": current_guardrail_value,
            "guardrail_delta": guardrail_delta,
            "outcome": outcome,
            "recommended_action": recommended_action,
            "summary": summary,
            "current_summary": current_summary,
        }

    def _metric_improved(
        self,
        expected_direction: str,
        metric_name: str,
        baseline_value: float | None,
        current_value: float | None,
    ) -> bool:
        """Return whether the target metric moved in the desired direction far enough to matter."""
        if current_value is None:
            return False
        threshold = 1.0 if self._is_count_metric(metric_name) else self.RATE_SUCCESS_DELTA
        if baseline_value is None:
            if expected_direction == "decrease":
                return current_value <= 0.0
            if expected_direction == "stabilize":
                return abs(current_value) <= threshold
            return current_value >= threshold

        delta = float(current_value) - float(baseline_value)
        if expected_direction == "decrease":
            return delta <= -threshold
        if expected_direction == "stabilize":
            return abs(delta) <= threshold
        return delta >= threshold

    def _guardrail_broken(self, metric_name: str, baseline_value: float | None, current_value: float | None) -> bool:
        """Return whether the guardrail degraded enough that the experiment should be stopped."""
        if current_value is None:
            return False
        if metric_name == "active_pending_count":
            baseline = float(baseline_value or 0.0)
            if baseline <= 0:
                return float(current_value) >= 2.0
            growth_ratio = (float(current_value) - baseline) / max(baseline, 1.0)
            return growth_ratio >= self.ACTIVE_PENDING_GROWTH_RATIO

        if self._is_count_metric(metric_name):
            if baseline_value is None or baseline_value <= 0:
                return False
            drop_ratio = (float(baseline_value) - float(current_value)) / max(float(baseline_value), 1.0)
            return drop_ratio >= self.COUNT_DROP_RATIO

        if baseline_value is None:
            return False
        return (float(current_value) - float(baseline_value)) <= self.RATE_GUARDRAIL_DROP

    def _resolve_metric_value(self, snapshot: dict[str, Any], metric_name: str) -> float | None:
        """Map a named experiment metric to the correct snapshot field."""
        raw_value = snapshot.get(metric_name)
        if raw_value is None:
            return None
        try:
            return round(float(raw_value), 4)
        except (TypeError, ValueError):
            return None

    def _serialize_run(self, run: DecisionExperimentRun) -> dict[str, Any]:
        """Serialize one run row into the API response shape."""
        latest_evaluation = self._load_json(run.latest_evaluation, fallback=None)
        if latest_evaluation == {}:
            latest_evaluation = None
        notes = str(run.notes or "").strip() or None
        supported_apply_types = self._supported_apply_types(run)
        applied_apply_types = self._applied_apply_types(run)
        application_status, application_detail = self._resolve_application_state(
            run,
            supported_apply_types=supported_apply_types,
            applied_apply_types=applied_apply_types,
        )
        return {
            "id": int(run.id),
            "operator_id": int(run.operator_id),
            "experiment_key": str(run.experiment_key),
            "recommendation_key": str(run.recommendation_key),
            "status": str(run.status or "planned"),
            "outcome": str(run.outcome) if run.outcome else None,
            "priority_rank": int(run.priority_rank or 1),
            "title": str(run.title or ""),
            "hypothesis": str(run.hypothesis or ""),
            "suggested_change": str(run.suggested_change or ""),
            "target_metric": str(run.target_metric or ""),
            "expected_direction": str(run.expected_direction or "increase"),
            "success_criteria": str(run.success_criteria or ""),
            "guardrail_metric": str(run.guardrail_metric or ""),
            "minimum_decision_sample": int(run.minimum_decision_sample or 1),
            "duration_days": int(run.duration_days or 14),
            "baseline_days": int(run.baseline_days or 14),
            "rollback_trigger": str(run.rollback_trigger or ""),
            "notes": notes,
            "started_at": ensure_utc(run.started_at),
            "ended_at": ensure_utc(run.ended_at) if run.ended_at is not None else None,
            "last_evaluated_at": ensure_utc(run.last_evaluated_at) if run.last_evaluated_at is not None else None,
            "created_at": ensure_utc(run.created_at),
            "updated_at": ensure_utc(run.updated_at),
            "latest_evaluation": latest_evaluation,
            "supported_apply_types": supported_apply_types,
            "applied_apply_types": applied_apply_types,
            "application_status": application_status,
            "application_detail": application_detail,
            "application_history": self._application_history(run),
            "review_bucket": self._review_bucket(
                run,
                latest_evaluation=latest_evaluation,
                application_status=application_status,
            ),
            "review_priority": self._review_priority(
                run,
                latest_evaluation=latest_evaluation,
                application_status=application_status,
            ),
            "review_reason": self._review_reason(
                run,
                latest_evaluation=latest_evaluation,
                application_status=application_status,
            ),
            "next_actions": self._build_run_actions(
                run,
                application_status=application_status,
                supported_apply_types=supported_apply_types,
                applied_apply_types=applied_apply_types,
            ),
        }

    def _supported_apply_types(self, run: DecisionExperimentRun) -> list[str]:
        """Return apply mechanisms supported by this experiment key."""
        experiment_key = str(run.experiment_key or "")
        supported: list[str] = []
        if experiment_key in self.THRESHOLD_EXPERIMENT_KEYS:
            supported.append("thresholds")
        if experiment_key in self.STRATEGY_EXPERIMENT_KEYS:
            supported.append("strategy")
        return supported

    def _applied_apply_types(self, run: DecisionExperimentRun) -> list[str]:
        """Return apply mechanisms already written into the run notes."""
        applied: list[str] = []
        if self._run_has_applied_thresholds(run):
            applied.append("thresholds")
        if self._run_has_applied_strategy(run):
            applied.append("strategy")
        return applied

    def _resolve_application_state(
        self,
        run: DecisionExperimentRun,
        *,
        supported_apply_types: list[str],
        applied_apply_types: list[str],
    ) -> tuple[str, str]:
        """Resolve a compact dashboard status for applying experiment output."""
        if not supported_apply_types:
            return "not_supported", "이 실험 유형은 아직 자동 적용 대상이 아닙니다."

        supported_set = set(supported_apply_types)
        applied_set = set(applied_apply_types)
        if supported_set and supported_set.issubset(applied_set):
            return "applied", "지원되는 적용 작업이 모두 완료되었습니다."
        if applied_set:
            return "partially_applied", "일부 적용 작업은 완료되었고 남은 적용 작업이 있습니다."

        outcome = str(run.outcome or "")
        status = str(run.status or "planned")
        if outcome == "success":
            return "ready", "성공한 실험으로 운영 전략에 적용할 수 있습니다."
        if status == "rolled_back" or outcome in {"rollback", "inconclusive"}:
            return "blocked", "롤백 또는 결론 없음 상태라 기본 적용은 차단됩니다. 필요한 경우 force 적용만 가능합니다."
        return "not_ready", "성공 outcome이 확정되기 전까지 운영 전략 적용은 대기 상태입니다."

    def _application_history(self, run: DecisionExperimentRun) -> list[dict[str, str]]:
        """Extract audit-friendly application notes from the free-form run notes."""
        history: list[dict[str, str]] = []
        for note_line in str(run.notes or "").splitlines():
            note = note_line.strip()
            if not note:
                continue
            if self.THRESHOLD_APPLICATION_PREFIX in note:
                history.append({"apply_type": "thresholds", "note": note})
            if self.STRATEGY_APPLICATION_PREFIX in note:
                history.append({"apply_type": "strategy", "note": note})
        return history

    def _build_run_actions(
        self,
        run: DecisionExperimentRun,
        *,
        application_status: str,
        supported_apply_types: list[str],
        applied_apply_types: list[str],
    ) -> list[dict[str, Any]]:
        """Return dashboard-ready action metadata for one experiment run."""
        run_id = int(run.id)
        status = str(run.status or "planned")
        applied_set = set(applied_apply_types)
        actions = [
            {
                "action": "evaluate",
                "label": "Re-evaluate experiment",
                "method": "POST",
                "path": f"/api/v1/analytics/decision-experiments/{run_id}/evaluate",
                "enabled": status != "rolled_back",
                "reason": "현재 baseline 대비 실험 성과를 다시 계산합니다." if status != "rolled_back" else "롤백된 실험은 재평가보다 새 실험 생성이 안전합니다.",
                "payload": {},
                "dry_run_supported": False,
                "force_supported": False,
            },
            {
                "action": "mark_success",
                "label": "Mark as successful",
                "method": "PATCH",
                "path": f"/api/v1/analytics/decision-experiments/{run_id}",
                "enabled": status != "rolled_back" and str(run.outcome or "") != "success",
                "reason": "운영자가 실험 성공을 확정합니다." if status != "rolled_back" else "롤백된 실험은 성공 처리할 수 없습니다.",
                "payload": {"status": "completed", "outcome": "success"},
                "dry_run_supported": False,
                "force_supported": False,
            },
            {
                "action": "rollback",
                "label": "Rollback experiment",
                "method": "PATCH",
                "path": f"/api/v1/analytics/decision-experiments/{run_id}",
                "enabled": status != "rolled_back",
                "reason": "실험을 롤백 상태로 전환합니다." if status != "rolled_back" else "이미 롤백된 실험입니다.",
                "payload": {"status": "rolled_back"},
                "dry_run_supported": False,
                "force_supported": False,
            },
        ]

        if "thresholds" in supported_apply_types:
            already_applied = "thresholds" in applied_set
            enabled = application_status == "ready" and not already_applied
            actions.append(
                {
                    "action": "apply_thresholds",
                    "label": "Apply threshold adjustment",
                    "method": "POST",
                    "path": f"/api/v1/analytics/decision-experiments/{run_id}/apply-thresholds",
                    "enabled": enabled,
                    "reason": self._apply_action_reason(run, already_applied=already_applied, enabled=enabled),
                    "payload": {"dry_run": False, "force": False},
                    "dry_run_supported": True,
                    "force_supported": True,
                }
            )

        if "strategy" in supported_apply_types:
            already_applied = "strategy" in applied_set
            enabled = application_status == "ready" and not already_applied
            actions.append(
                {
                    "action": "apply_strategy",
                    "label": "Apply strategy tuning",
                    "method": "POST",
                    "path": f"/api/v1/analytics/decision-experiments/{run_id}/apply-strategy",
                    "enabled": enabled,
                    "reason": self._apply_action_reason(run, already_applied=already_applied, enabled=enabled),
                    "payload": {"dry_run": False, "force": False},
                    "dry_run_supported": True,
                    "force_supported": True,
                }
            )

        return actions

    def _sort_serialized_runs(self, runs: list[dict[str, Any]], *, sort: str) -> list[dict[str, Any]]:
        """Sort runs for dashboard review queues."""
        normalized_sort = self._normalize_run_sort(sort)
        if normalized_sort == "created_desc":
            return sorted(runs, key=lambda item: (self._datetime_sort_key(item.get("created_at")), int(item["id"])), reverse=True)
        if normalized_sort == "created_asc":
            return sorted(runs, key=lambda item: (self._datetime_sort_key(item.get("created_at")), int(item["id"])))
        if normalized_sort == "priority":
            return sorted(
                runs,
                key=lambda item: (
                    int(item.get("priority_rank") or 999),
                    -self._datetime_sort_key(item.get("created_at")),
                    -int(item["id"]),
                ),
            )
        if normalized_sort == "last_evaluated_desc":
            return sorted(
                runs,
                key=lambda item: (
                    self._datetime_sort_key(item.get("last_evaluated_at")),
                    self._datetime_sort_key(item.get("updated_at")),
                    int(item["id"]),
                ),
                reverse=True,
            )
        if normalized_sort == "application":
            return sorted(
                runs,
                key=lambda item: (
                    self._application_status_rank(str(item.get("application_status") or "")),
                    int(item.get("priority_rank") or 999),
                    -self._datetime_sort_key(item.get("updated_at")),
                    -int(item["id"]),
                ),
            )
        return sorted(
            runs,
            key=lambda item: (
                int(item.get("review_priority") or 999),
                int(item.get("priority_rank") or 999),
                -self._datetime_sort_key(item.get("updated_at")),
                -int(item["id"]),
            ),
        )

    def _normalize_run_sort(self, sort: str) -> str:
        """Normalize supported experiment list sort modes."""
        normalized = str(sort or "needs_attention").strip().lower()
        supported = {
            "needs_attention",
            "created_desc",
            "created_asc",
            "priority",
            "last_evaluated_desc",
            "application",
        }
        return normalized if normalized in supported else "needs_attention"

    def _review_bucket(
        self,
        run: DecisionExperimentRun,
        *,
        latest_evaluation: dict[str, Any] | None,
        application_status: str,
    ) -> str:
        """Resolve a coarse dashboard queue bucket for one run."""
        if application_status == "ready":
            return "ready_to_apply"
        if application_status == "partially_applied":
            return "partially_applied"
        if application_status == "applied":
            return "applied"
        if application_status == "blocked":
            return "blocked"
        if application_status == "not_supported":
            return "unsupported"

        status = str(run.status or "planned")
        outcome = str(run.outcome or "")
        recommended_action = str((latest_evaluation or {}).get("recommended_action") or "")
        if status == "failed":
            return "failed"
        if outcome in {"insufficient_data", "watch"} or recommended_action in {"collect_more_data", "continue"}:
            return "collecting_data"
        if status in {"running", "completed"}:
            return "needs_evaluation"
        return "scheduled"

    def _review_priority(
        self,
        run: DecisionExperimentRun,
        *,
        latest_evaluation: dict[str, Any] | None,
        application_status: str,
    ) -> int:
        """Return a stable low-is-urgent priority for dashboard sorting."""
        bucket = self._review_bucket(run, latest_evaluation=latest_evaluation, application_status=application_status)
        bucket_rank = {
            "ready_to_apply": 10,
            "blocked": 20,
            "failed": 25,
            "needs_evaluation": 30,
            "collecting_data": 40,
            "partially_applied": 50,
            "scheduled": 60,
            "applied": 70,
            "unsupported": 80,
        }
        return bucket_rank.get(bucket, 90)

    def _review_reason(
        self,
        run: DecisionExperimentRun,
        *,
        latest_evaluation: dict[str, Any] | None,
        application_status: str,
    ) -> str:
        """Explain why a run is in its dashboard review bucket."""
        bucket = self._review_bucket(run, latest_evaluation=latest_evaluation, application_status=application_status)
        if bucket == "ready_to_apply":
            return "성공 outcome이 확정되어 운영 설정 적용 검토가 필요합니다."
        if bucket == "blocked":
            return "롤백 또는 결론 없음 상태라 적용이 차단되었습니다."
        if bucket == "failed":
            return "실험 실행 또는 평가가 실패했습니다. 오류 원인 확인이 필요합니다."
        if bucket == "needs_evaluation":
            return "실험이 진행 또는 완료 상태지만 최신 평가가 없습니다."
        if bucket == "collecting_data":
            sample_size = int((latest_evaluation or {}).get("sample_size") or 0)
            minimum_sample = int(run.minimum_decision_sample or 1)
            return f"현재 표본 {sample_size}건으로 최소 기준 {minimum_sample}건까지 더 관찰해야 합니다."
        if bucket == "partially_applied":
            return "일부 적용은 끝났고 남은 적용 작업 검토가 필요합니다."
        if bucket == "scheduled":
            return "아직 시작 예정인 실험입니다."
        if bucket == "applied":
            return "지원되는 적용 작업이 완료되어 감사 이력만 확인하면 됩니다."
        return "자동 적용 대상이 아니므로 수동 검토 대상입니다."

    def _application_status_rank(self, application_status: str) -> int:
        """Return stable ordering for application-oriented sorting."""
        ranks = {
            "ready": 10,
            "partially_applied": 20,
            "blocked": 30,
            "not_ready": 40,
            "applied": 50,
            "not_supported": 60,
        }
        return ranks.get(application_status, 90)

    def _count_by_key(self, runs: list[dict[str, Any]], key: str, *, missing: str = "unknown") -> dict[str, int]:
        """Count runs by one serialized field for dashboard chips."""
        counts: dict[str, int] = {}
        for run in runs:
            value = run.get(key)
            label = str(value if value is not None else missing)
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: item[0]))

    def _datetime_sort_key(self, value: Any) -> float:
        """Return a comparable timestamp for optional datetime-like values."""
        if value is None:
            return 0.0
        if isinstance(value, datetime):
            return float(value.timestamp())
        try:
            return float(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return 0.0

    def _apply_action_reason(self, run: DecisionExperimentRun, *, already_applied: bool, enabled: bool) -> str:
        """Return a concise explanation for an apply action's enabled state."""
        if already_applied:
            return "이미 적용된 실험입니다. 재적용하려면 force가 필요합니다."
        if enabled:
            return "성공 outcome이 확정되어 적용할 수 있습니다."
        if str(run.outcome or "") != "success":
            return "성공 outcome이 확정되기 전까지 적용할 수 없습니다."
        return "현재 상태에서는 적용할 수 없습니다."

    def _resolve_operator(self, db: Session, *, operator: User | None) -> User:
        """Resolve the target operator while preserving canonical behavior by default."""
        return operator if operator is not None else ensure_operator_account(db)

    def _resolve_strategy(self, db: Session, *, operator: User | None) -> Any:
        """Resolve the strategy row for the target operator without cross-operator fallback."""
        if operator is None:
            return ensure_operator_strategy(db)
        return ensure_operator_strategy_for(db, operator)

    def _operator_context_fields(self, operator: User) -> dict[str, Any]:
        """Return standard response fields for the resolved operator context."""
        return {
            "operator_id": int(operator.id),
            "current_operator_id": int(operator.id),
            "current_operator_username": str(operator.username or ""),
        }

    def _get_run_or_raise(
        self,
        db: Session,
        *,
        run_id: int,
        operator: User | None = None,
    ) -> DecisionExperimentRun:
        """Load one run in the target operator scope or raise a clear error."""
        target_operator = self._resolve_operator(db, operator=operator)
        run = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.id == run_id,
                DecisionExperimentRun.operator_id == target_operator.id,
            )
            .first()
        )
        if run is None:
            raise ValueError(f"Decision experiment run {run_id} not found")
        return run

    def _empty_snapshot(self, window_start: datetime, window_end: datetime) -> dict[str, Any]:
        """Return an empty-but-valid snapshot shape for fallback parsing cases."""
        return {
            "window_start": window_start,
            "window_end": window_end,
            "decision_count": 0,
            "submitted_count": 0,
            "active_pending_count": 0,
            "overall_submission_rate": None,
            "workflow_submission_rate": None,
            "bid_now_submission_rate": None,
            "review_submission_rate": None,
            "auto_submission_rate": None,
            "provided_submission_rate": None,
            "best_category": None,
            "best_category_submission_rate": None,
            "worst_category": None,
            "worst_category_submission_rate": None,
        }

    def _load_json(self, raw_value: str | None, *, fallback: Any) -> Any:
        """Parse one JSON text payload and gracefully recover on bad data."""
        if raw_value in (None, ""):
            return fallback
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return fallback

    def _dump_json(self, payload: Any) -> str:
        """Serialize payloads containing datetimes into JSON text for storage."""
        return json.dumps(payload, ensure_ascii=False, default=self._json_default)

    def _json_default(self, value: Any) -> str:
        """Serialize datetimes as ISO strings for JSON persistence."""
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")

    def _is_count_metric(self, metric_name: str) -> bool:
        """Return whether a metric is count-based rather than rate-based."""
        return metric_name.endswith("_count")

    def _delta(self, current_value: float | None, baseline_value: float | None) -> float | None:
        """Return a rounded metric delta when both current and baseline are known."""
        if current_value is None or baseline_value is None:
            return None
        return round(float(current_value) - float(baseline_value), 4)

    def _current_strategy_thresholds(self, strategy) -> dict[str, float]:
        """Serialize persisted operator decision thresholds into a stable snapshot."""
        bid_now_threshold = max(0.0, min(1.0, float(getattr(strategy, "bid_now_threshold", 0.7) or 0.7)))
        review_threshold = max(0.0, min(1.0, float(getattr(strategy, "review_threshold", 0.45) or 0.45)))
        if review_threshold > bid_now_threshold:
            review_threshold = bid_now_threshold
        return {
            "bid_now_threshold": round(bid_now_threshold, 4),
            "review_threshold": round(review_threshold, 4),
        }

    def _build_run_parameter_policy(self, db: Session, run: DecisionExperimentRun) -> dict[str, Any]:
        """Summarize prior same-recommendation outcomes into a concrete parameter delta policy."""
        recommendation_key = str(run.recommendation_key or "").strip()
        if not recommendation_key:
            adjustment = self.analytics._build_history_adjustment(None)
            return self._parameter_policy_from_adjustment(adjustment, history_summary=None)

        date_from = utc_now() - timedelta(days=self.PARAMETER_HISTORY_LOOKBACK_DAYS)
        prior_runs = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.operator_id == int(run.operator_id),
                DecisionExperimentRun.recommendation_key == recommendation_key,
                DecisionExperimentRun.id != int(run.id),
                DecisionExperimentRun.updated_at >= date_from,
            )
            .order_by(DecisionExperimentRun.updated_at.desc(), DecisionExperimentRun.id.desc())
            .all()
        )
        history_summary = (
            self.analytics._summarize_experiment_runs_for_recommendation(recommendation_key, prior_runs)
            if prior_runs
            else None
        )
        adjustment = self.analytics._build_history_adjustment(history_summary)
        return self._parameter_policy_from_adjustment(adjustment, history_summary=history_summary)

    def _parameter_policy_from_adjustment(
        self,
        adjustment: dict[str, Any],
        *,
        history_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Convert a history adjustment into an apply-time parameter delta scale."""
        multiplier = self.analytics._parameter_delta_multiplier(adjustment)
        return {
            "status": str(adjustment.get("status") or "neutral"),
            "multiplier": round(multiplier, 4),
            "reason": str(adjustment.get("reason") or ""),
            "history_summary": history_summary or {},
            "history_adjustment": adjustment,
        }

    def _policy_delta(
        self,
        experiment_key: str,
        *,
        parameter_policy: dict[str, Any],
        base_delta: float | None = None,
        min_delta: float | None = None,
        max_delta: float | None = None,
    ) -> float:
        """Return the concrete parameter delta for one experiment after history scaling."""
        if base_delta is None or min_delta is None or max_delta is None:
            base_delta, min_delta, max_delta = self.THRESHOLD_PARAMETER_DELTAS.get(
                experiment_key,
                (0.03, 0.015, 0.05),
            )
        multiplier = float(parameter_policy.get("multiplier") or 1.0)
        return round(max(float(min_delta), min(float(base_delta) * multiplier, float(max_delta))), 4)

    def _parameter_rationale(self, base_rationale: str, *, parameter_policy: dict[str, Any]) -> str:
        """Append history-policy context to an apply recommendation rationale."""
        reason = str(parameter_policy.get("reason") or "").strip()
        status = str(parameter_policy.get("status") or "neutral")
        multiplier = float(parameter_policy.get("multiplier") or 1.0)
        return (
            f"{base_rationale} 장기 적용 이력 상태는 {status}이며 "
            f"추천 변화 폭 배율 {multiplier:.2f}를 적용했습니다. {reason}"
        ).strip()

    def _build_threshold_adjustments(
        self,
        run: DecisionExperimentRun,
        *,
        current_thresholds: dict[str, float],
        parameter_policy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Translate supported experiment keys into concrete threshold updates."""
        experiment_key = str(run.experiment_key or "")
        bid_now_threshold = float(current_thresholds["bid_now_threshold"])
        review_threshold = float(current_thresholds["review_threshold"])

        if experiment_key == "exp-review-threshold-tighten":
            delta = self._policy_delta(experiment_key, parameter_policy=parameter_policy)
            suggested_value = self._bounded_review_threshold(
                review_threshold + delta,
                bid_now_threshold=bid_now_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="review_threshold",
                    label="REVIEW_THRESHOLD",
                    direction="increase",
                    previous_value=review_threshold,
                    suggested_value=suggested_value,
                    rationale=self._parameter_rationale(
                        "review 진입 품질을 높여 낮은 전환율을 개선하도록 threshold를 상향합니다.",
                        parameter_policy=parameter_policy,
                    ),
                )
            ]

        if experiment_key == "exp-review-threshold-relax":
            delta = self._policy_delta(experiment_key, parameter_policy=parameter_policy)
            suggested_value = self._bounded_review_threshold(
                review_threshold - delta,
                bid_now_threshold=bid_now_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="review_threshold",
                    label="REVIEW_THRESHOLD",
                    direction="decrease",
                    previous_value=review_threshold,
                    suggested_value=suggested_value,
                    rationale=self._parameter_rationale(
                        "review 후보 풀을 넓혀 더 많은 탐색 기회를 확보하도록 threshold를 완화합니다.",
                        parameter_policy=parameter_policy,
                    ),
                )
            ]

        if experiment_key == "exp-bid-now-threshold-tighten":
            delta = self._policy_delta(experiment_key, parameter_policy=parameter_policy)
            suggested_value = self._bounded_bid_now_threshold(
                bid_now_threshold + delta,
                review_threshold=review_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="bid_now_threshold",
                    label="BID_NOW_THRESHOLD",
                    direction="increase",
                    previous_value=bid_now_threshold,
                    suggested_value=suggested_value,
                    rationale=self._parameter_rationale(
                        "즉시 투찰 후보의 질을 높이기 위해 bid_now 승격 기준을 보수적으로 조정합니다.",
                        parameter_policy=parameter_policy,
                    ),
                )
            ]

        return []

    def _threshold_update_item(
        self,
        *,
        parameter: str,
        label: str,
        direction: str,
        previous_value: float,
        suggested_value: float,
        rationale: str,
    ) -> dict[str, Any]:
        """Serialize one threshold update proposal into the API response shape."""
        return {
            "parameter": parameter,
            "label": label,
            "direction": direction,
            "previous_value": round(previous_value, 4),
            "suggested_value": round(suggested_value, 4),
            "delta": round(float(suggested_value) - float(previous_value), 4),
            "rationale": rationale,
        }

    def _apply_threshold_updates(self, strategy, threshold_updates: list[dict[str, Any]]) -> None:
        """Persist the suggested threshold values onto the operator strategy row."""
        updated_values = self._current_strategy_thresholds(strategy)
        for update in threshold_updates:
            updated_values[str(update["parameter"])] = round(float(update["suggested_value"]), 4)

        strategy.bid_now_threshold = float(updated_values["bid_now_threshold"])
        strategy.review_threshold = float(updated_values["review_threshold"])

    def _current_strategy_tuning(self, strategy) -> dict[str, Any]:
        """Serialize workload/category tuning settings into a stable snapshot."""
        return {
            "auto_workload_penalty_multiplier": get_strategy_auto_workload_penalty_multiplier(strategy),
            "category_priority_overrides": get_strategy_category_priority_overrides(strategy),
        }

    def _build_strategy_adjustments(
        self,
        run: DecisionExperimentRun,
        *,
        current_tuning: dict[str, Any],
        parameter_policy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Translate supported experiment keys into concrete strategy tuning updates."""
        experiment_key = str(run.experiment_key or "")

        if experiment_key == "exp-workload-auto-calibration":
            previous_value = float(current_tuning["auto_workload_penalty_multiplier"])
            suggested_value = clamp_auto_workload_penalty_multiplier(max(0.5, previous_value - 0.15))
            return [
                {
                    "parameter": "auto_workload_penalty_multiplier",
                    "label": "AUTO_WORKLOAD_PENALTY_MULTIPLIER",
                    "direction": "decrease",
                    "previous_value": round(previous_value, 4),
                    "suggested_value": round(suggested_value, 4),
                    "delta": round(suggested_value - previous_value, 4),
                    "rationale": "자동 산정 업무부하 후보의 제출 전환율이 낮아 감점 배율을 낮추고 후보 탐색 폭을 넓힙니다.",
                }
            ]

        if experiment_key == "exp-category-focus-shift":
            source_summary = self._strategy_source_summary(run)
            best_category = self._clean_category_name(source_summary.get("best_category"))
            worst_category = self._clean_category_name(source_summary.get("worst_category"))
            if best_category is None and worst_category is None:
                return []

            previous_overrides = dict(current_tuning["category_priority_overrides"])
            suggested_overrides = dict(previous_overrides)
            changed_deltas: dict[str, float] = {}
            category_delta = self._policy_delta(
                experiment_key,
                parameter_policy=parameter_policy,
                base_delta=self.CATEGORY_PARAMETER_BASE_DELTA,
                min_delta=self.CATEGORY_PARAMETER_MIN_DELTA,
                max_delta=self.CATEGORY_PARAMETER_MAX_DELTA,
            )

            if best_category is not None:
                previous_best_value = self._category_override_for(previous_overrides, best_category)
                suggested_best_value = clamp_category_priority_override(previous_best_value + category_delta)
                suggested_overrides[best_category] = suggested_best_value
                changed_deltas[best_category] = round(suggested_best_value - previous_best_value, 4)

            if worst_category is not None and worst_category.lower() != str(best_category or "").lower():
                previous_worst_value = self._category_override_for(previous_overrides, worst_category)
                suggested_worst_value = clamp_category_priority_override(previous_worst_value - category_delta)
                suggested_overrides[worst_category] = suggested_worst_value
                changed_deltas[worst_category] = round(suggested_worst_value - previous_worst_value, 4)

            return [
                {
                    "parameter": "category_priority_overrides",
                    "label": "CATEGORY_PRIORITY_OVERRIDES",
                    "direction": "replace",
                    "previous_value": previous_overrides,
                    "suggested_value": suggested_overrides,
                    "delta": changed_deltas,
                    "rationale": self._parameter_rationale(
                        "제출 전환이 좋은 카테고리는 우선순위를 높이고 저조한 카테고리는 보수적으로 평가합니다.",
                        parameter_policy=parameter_policy,
                    ),
                }
            ]

        return []

    def _apply_strategy_updates(self, strategy, strategy_updates: list[dict[str, Any]]) -> None:
        """Persist suggested workload/category tuning values onto the operator strategy row."""
        for update in strategy_updates:
            parameter = str(update["parameter"])
            if parameter == "auto_workload_penalty_multiplier":
                strategy.auto_workload_penalty_multiplier = clamp_auto_workload_penalty_multiplier(
                    update["suggested_value"]
                )
            elif parameter == "category_priority_overrides":
                strategy.category_priority_overrides = dump_category_priority_overrides(
                    update["suggested_value"]
                )

    def _strategy_source_summary(self, run: DecisionExperimentRun) -> dict[str, Any]:
        """Choose the best available metrics snapshot for strategy application."""
        latest_evaluation = self._load_json(run.latest_evaluation, fallback={})
        if isinstance(latest_evaluation, dict):
            current_summary = latest_evaluation.get("current_summary")
            if isinstance(current_summary, dict):
                return current_summary

        baseline_summary = self._load_json(run.baseline_summary, fallback={})
        return baseline_summary if isinstance(baseline_summary, dict) else {}

    def _clean_category_name(self, raw_value: Any) -> str | None:
        """Normalize category labels from experiment snapshots."""
        category = str(raw_value or "").strip()
        return category or None

    def _category_override_for(self, overrides: dict[str, float], category: str) -> float:
        """Read an existing category override using case-insensitive matching."""
        normalized_category = category.strip().lower()
        for key, value in overrides.items():
            if key.strip().lower() == normalized_category:
                return float(value)
        return 0.0

    def _bounded_review_threshold(self, proposed_value: float, *, bid_now_threshold: float) -> float:
        """Keep review threshold inside a sane range below the bid-now threshold."""
        upper_bound = max(0.0, min(1.0, float(bid_now_threshold) - 0.01))
        return round(max(0.0, min(float(proposed_value), upper_bound)), 4)

    def _bounded_bid_now_threshold(self, proposed_value: float, *, review_threshold: float) -> float:
        """Keep bid-now threshold above the review threshold while staying in unit range."""
        lower_bound = max(0.0, min(1.0, float(review_threshold) + 0.01))
        return round(min(1.0, max(float(proposed_value), lower_bound)), 4)

    def _merge_notes(self, current_notes: str | None, appended_note: str) -> str:
        """Append operator notes without losing the existing note body."""
        stripped_note = str(appended_note or "").strip()
        if not stripped_note:
            return str(current_notes or "").strip()
        current_text = str(current_notes or "").strip()
        if not current_text:
            return stripped_note
        return f"{current_text}\n{stripped_note}".strip()

    def _run_has_applied_thresholds(self, run: DecisionExperimentRun) -> bool:
        """Return whether this experiment run already wrote threshold updates into its notes."""
        return self.THRESHOLD_APPLICATION_PREFIX in str(run.notes or "")

    def _run_has_applied_strategy(self, run: DecisionExperimentRun) -> bool:
        """Return whether this experiment run already wrote strategy tuning updates into its notes."""
        return self.STRATEGY_APPLICATION_PREFIX in str(run.notes or "")

    def _build_threshold_application_note(
        self,
        threshold_updates: list[dict[str, Any]],
        *,
        append_note: str | None,
    ) -> str:
        """Build one audit-friendly notes line for applied threshold changes."""
        summary = ", ".join(
            f"{item['label']} {item['previous_value']:.4f}→{item['suggested_value']:.4f}"
            for item in threshold_updates
        )
        base_note = f"{self.THRESHOLD_APPLICATION_PREFIX} {summary}"
        extra_note = str(append_note or "").strip()
        if extra_note:
            return f"{base_note} | {extra_note}"
        return base_note

    def _build_strategy_application_note(
        self,
        strategy_updates: list[dict[str, Any]],
        *,
        append_note: str | None,
    ) -> str:
        """Build one audit-friendly notes line for applied strategy tuning changes."""
        summary_parts: list[str] = []
        for item in strategy_updates:
            if item["parameter"] == "auto_workload_penalty_multiplier":
                summary_parts.append(
                    f"{item['label']} {float(item['previous_value']):.4f}→{float(item['suggested_value']):.4f}"
                )
            elif item["parameter"] == "category_priority_overrides":
                changed = ", ".join(
                    f"{category} {delta:+.4f}"
                    for category, delta in dict(item.get("delta") or {}).items()
                )
                summary_parts.append(f"{item['label']} {changed or 'no-op'}")

        base_note = f"{self.STRATEGY_APPLICATION_PREFIX} {', '.join(summary_parts)}"
        extra_note = str(append_note or "").strip()
        if extra_note:
            return f"{base_note} | {extra_note}"
        return base_note
