"""Persist and evaluate decision-tuning experiments."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_account, ensure_operator_strategy
from app.core.time import ensure_utc, utc_now
from app.models.models import DecisionExperimentRun
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

    RATE_SUCCESS_DELTA = 0.1
    RATE_GUARDRAIL_DROP = -0.05
    COUNT_DROP_RATIO = 0.2
    ACTIVE_PENDING_GROWTH_RATIO = 0.2
    THRESHOLD_APPLICATION_PREFIX = "Threshold 적용:"
    STRATEGY_APPLICATION_PREFIX = "Strategy 적용:"

    def __init__(self) -> None:
        self.analytics = DecisionAnalyticsService()

    def create_run(self, db: Session, *, request: DecisionExperimentRunCreateRequest) -> dict[str, Any]:
        """Persist one experiment definition with a baseline snapshot collected at creation time."""
        operator = ensure_operator_account(db)
        now = utc_now()
        started_at = ensure_utc(request.started_at or now)
        baseline_start = started_at - timedelta(days=int(request.baseline_days))
        baseline_summary = self._build_snapshot(
            db,
            operator_id=operator.id,
            start_at=baseline_start,
            end_at=started_at,
        )

        run = DecisionExperimentRun(
            operator_id=operator.id,
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
        return self.get_run_detail(db, run_id=int(run.id))

    def list_runs(self, db: Session, *, limit: int = 20, run_status: str | None = None) -> dict[str, Any]:
        """Return recent experiment runs for dashboard and operator review."""
        operator = ensure_operator_account(db)
        query = db.query(DecisionExperimentRun).filter(DecisionExperimentRun.operator_id == operator.id)
        if run_status:
            query = query.filter(DecisionExperimentRun.status == run_status)

        runs = (
            query
            .order_by(DecisionExperimentRun.created_at.desc(), DecisionExperimentRun.id.desc())
            .limit(limit)
            .all()
        )
        serialized_runs = [self._serialize_run(run) for run in runs]
        return {
            "operator_id": operator.id,
            "result_count": len(serialized_runs),
            "active_count": sum(1 for run in serialized_runs if run["status"] in {"planned", "running"}),
            "completed_count": sum(1 for run in serialized_runs if run["status"] == "completed"),
            "rolled_back_count": sum(1 for run in serialized_runs if run["status"] == "rolled_back"),
            "runs": serialized_runs,
        }

    def get_run_detail(self, db: Session, *, run_id: int) -> dict[str, Any]:
        """Return one run including its baseline metrics and latest evaluation, if available."""
        run = self._get_run_or_raise(db, run_id=run_id)
        run_started_at = ensure_utc(run.started_at)
        baseline_summary = self._load_json(run.baseline_summary, fallback=self._empty_snapshot(run_started_at, run_started_at))
        return {
            "run": self._serialize_run(run),
            "baseline_summary": baseline_summary,
        }

    def evaluate_run(self, db: Session, *, run_id: int) -> dict[str, Any]:
        """Compare the experiment window against the saved baseline and persist the latest evaluation."""
        run = self._get_run_or_raise(db, run_id=run_id)
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
        return self.get_run_detail(db, run_id=int(run.id))

    def update_run(
        self,
        db: Session,
        *,
        run_id: int,
        request: DecisionExperimentRunUpdateRequest,
    ) -> dict[str, Any]:
        """Manually update run notes or lifecycle state for operator workflow control."""
        run = self._get_run_or_raise(db, run_id=run_id)

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
        return self.get_run_detail(db, run_id=int(run.id))

    def apply_threshold_adjustments(
        self,
        db: Session,
        *,
        run_id: int,
        request: DecisionExperimentThresholdApplyRequest,
    ) -> dict[str, Any]:
        """Convert one successful experiment into persisted operator decision-threshold updates."""
        run = self._get_run_or_raise(db, run_id=run_id)
        strategy = ensure_operator_strategy(db)
        current_thresholds = self._current_strategy_thresholds(strategy)
        threshold_updates = self._build_threshold_adjustments(run, current_thresholds=current_thresholds)
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
    ) -> dict[str, Any]:
        """Convert workload/category experiments into persisted operator strategy tuning."""
        run = self._get_run_or_raise(db, run_id=run_id)
        strategy = ensure_operator_strategy(db)
        current_tuning = self._current_strategy_tuning(strategy)
        strategy_updates = self._build_strategy_adjustments(run, current_tuning=current_tuning)
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
        }

    def _get_run_or_raise(self, db: Session, *, run_id: int) -> DecisionExperimentRun:
        """Load one run that belongs to the singleton operator or raise a clear error."""
        operator = ensure_operator_account(db)
        run = (
            db.query(DecisionExperimentRun)
            .filter(
                DecisionExperimentRun.id == run_id,
                DecisionExperimentRun.operator_id == operator.id,
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

    def _build_threshold_adjustments(
        self,
        run: DecisionExperimentRun,
        *,
        current_thresholds: dict[str, float],
    ) -> list[dict[str, Any]]:
        """Translate supported experiment keys into concrete threshold updates."""
        experiment_key = str(run.experiment_key or "")
        bid_now_threshold = float(current_thresholds["bid_now_threshold"])
        review_threshold = float(current_thresholds["review_threshold"])

        if experiment_key == "exp-review-threshold-tighten":
            suggested_value = self._bounded_review_threshold(
                review_threshold + 0.04,
                bid_now_threshold=bid_now_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="review_threshold",
                    label="REVIEW_THRESHOLD",
                    direction="increase",
                    previous_value=review_threshold,
                    suggested_value=suggested_value,
                    rationale="review 진입 품질을 높여 낮은 전환율을 개선하도록 threshold를 상향합니다.",
                )
            ]

        if experiment_key == "exp-review-threshold-relax":
            suggested_value = self._bounded_review_threshold(
                review_threshold - 0.03,
                bid_now_threshold=bid_now_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="review_threshold",
                    label="REVIEW_THRESHOLD",
                    direction="decrease",
                    previous_value=review_threshold,
                    suggested_value=suggested_value,
                    rationale="review 후보 풀을 넓혀 더 많은 탐색 기회를 확보하도록 threshold를 완화합니다.",
                )
            ]

        if experiment_key == "exp-bid-now-threshold-tighten":
            suggested_value = self._bounded_bid_now_threshold(
                bid_now_threshold + 0.03,
                review_threshold=review_threshold,
            )
            return [
                self._threshold_update_item(
                    parameter="bid_now_threshold",
                    label="BID_NOW_THRESHOLD",
                    direction="increase",
                    previous_value=bid_now_threshold,
                    suggested_value=suggested_value,
                    rationale="즉시 투찰 후보의 질을 높이기 위해 bid_now 승격 기준을 보수적으로 조정합니다.",
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

            if best_category is not None:
                previous_best_value = self._category_override_for(previous_overrides, best_category)
                suggested_best_value = clamp_category_priority_override(previous_best_value + 0.03)
                suggested_overrides[best_category] = suggested_best_value
                changed_deltas[best_category] = round(suggested_best_value - previous_best_value, 4)

            if worst_category is not None and worst_category.lower() != str(best_category or "").lower():
                previous_worst_value = self._category_override_for(previous_overrides, worst_category)
                suggested_worst_value = clamp_category_priority_override(previous_worst_value - 0.03)
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
                    "rationale": "제출 전환이 좋은 카테고리는 우선순위를 높이고 저조한 카테고리는 보수적으로 평가합니다.",
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
