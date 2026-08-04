"""Public monitoring entry points and per-candidate evaluation processing.

``preview_candidates`` / ``execute_monitoring`` and their evaluation helpers,
moved verbatim from the original ``opportunity_monitoring`` module.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    ensure_operator_profile_for,
    ensure_operator_strategy,
    ensure_operator_strategy_for,
)
from app.models.models import (
    OperatorStrategy,
    OperatorStrategyRun,
    User,
)
from app.schemas.schemas import BidDecisionSaveRequest
from app.services.opportunity_analysis import OpportunityWorkloadContext
from app.services.opportunity_monitoring.atomic_persistence import (
    MonitorAtomicPersistenceMixin,
)
from app.services.opportunity_monitoring.base import (
    CandidateDecisionInputs,
    _MonitoringBase,
)
from app.services.opportunity_monitoring.candidate_persistence import (
    MonitorCandidatePersistenceMixin,
)
from app.services.opportunity_monitoring.execution import MonitorExecutionMixin
from app.services.opportunity_monitoring.singleton import MonitorSingletonMixin


class _OrchestrationMixin(
    MonitorSingletonMixin,
    MonitorExecutionMixin,
    MonitorAtomicPersistenceMixin,
    MonitorCandidatePersistenceMixin,
    _MonitoringBase,
):
    """Preview + scheduled/manual monitoring orchestration."""

    def preview_candidates(
        self,
        db: Session,
        *,
        limit: int | None = None,
        high_priority_only: bool | None = None,
        operator: User | None = None,
    ) -> dict:
        """Return strategy-matched candidates ranked by decision priority.

        When ``operator`` is provided the preview uses that operator's profile
        and strategy rows directly (used by ``?operator_id=`` cross-operator
        context). When ``operator`` is ``None`` the canonical singleton helpers
        are used to preserve backward-compatible behavior for callers that have
        not migrated to the new context.

        이 메서드는 호출 즉시 스캔을 실행한다(캐시 없음). API 요청 경로에서 직접
        호출하지 않는다 — 소비자는 스냅샷 재계산 task
        (``jobs.recompute_preview_snapshot``)와 g2 recheck 워커
        (``evidence_jobs``), 그리고 특성화 테스트다(설계 2026-07-30 §6.2:
        preview 서빙은 ``PreviewSnapshotService.serve`` 의 순수 읽기).
        """
        if operator is None:
            operator = ensure_operator_account(db)
            ensure_operator_profile(db)
            strategy = ensure_operator_strategy(db)
        else:
            ensure_operator_profile_for(db, operator)
            strategy = ensure_operator_strategy_for(db, operator)

        resolved_limit, resolved_high_priority_only = self._resolve_runtime_options(
            strategy,
            limit=limit,
            high_priority_only=high_priority_only,
        )
        return self._build_preview_payload(
            db,
            strategy=strategy,
            operator=operator,
            resolved_limit=resolved_limit,
            resolved_high_priority_only=resolved_high_priority_only,
        )

    def _build_preview_payload(
        self,
        db: Session,
        *,
        strategy: OperatorStrategy,
        operator: User,
        resolved_limit: int,
        resolved_high_priority_only: bool,
    ) -> dict:
        """Run the preview scan and serialize it (the cached unit of work)."""
        evaluations, evaluated_project_count = self._collect_candidate_evaluations(
            db,
            strategy=strategy,
            operator=operator,
            high_priority_only=resolved_high_priority_only,
            max_active_bids=self.DEFAULT_MAX_ACTIVE_BIDS,
            current_workload_score=None,
            same_category_only=self.DEFAULT_SAME_CATEGORY_ONLY,
            similar_limit=self.DEFAULT_SIMILAR_LIMIT,
            min_similarity=self.DEFAULT_MIN_SIMILARITY,
            scan_limit=self._preview_scan_limit(resolved_limit),
        )
        candidates = [evaluation.candidate for evaluation in evaluations[:resolved_limit]]

        return {
            "operator_id": operator.id,
            "evaluated_project_count": evaluated_project_count,
            "returned_candidate_count": len(candidates),
            "high_priority_only": resolved_high_priority_only,
            "candidates": candidates,
        }

    def _build_monitor_response(
        self,
        *,
        monitor_run: OperatorStrategyRun,
        previous_run: OperatorStrategyRun | None,
        trigger_source: str,
        operator: User,
        evaluated_project_count: int,
        selected_candidate_count: int,
        results: list[dict],
        projection_deferrals: list[dict[str, int | str]],
        previous_result_payload: dict,
        high_priority_only: bool,
        limit_applied: int,
    ) -> dict:
        """Assemble one completed-run response from persisted decision results."""
        run_diff = self._build_run_diff({"results": results}, previous_result_payload)
        notification_count = sum(
            1 for item in results if item.get("notification_created")
        )
        return {
            "monitor_run_id": monitor_run.id,
            "task_id": monitor_run.task_id,
            "trigger_source": trigger_source,
            "release_sha": monitor_run.release_sha,
            "release_tag": monitor_run.release_tag,
            "previous_run_id": previous_run.id if previous_run else None,
            "operator_id": operator.id,
            "current_operator_id": int(operator.id),
            "current_operator_username": str(operator.username or ""),
            "evaluated_project_count": evaluated_project_count,
            "selected_candidate_count": selected_candidate_count,
            "persisted_candidate_count": len(results),
            "notification_count": notification_count,
            "projection_not_ready_count": len(projection_deferrals),
            "projection_not_ready_project_ids": [
                int(item["project_id"]) for item in projection_deferrals
            ],
            "new_candidate_count": run_diff["new_candidate_count"],
            "continuing_candidate_count": run_diff["continuing_candidate_count"],
            "dropped_candidate_count": run_diff["dropped_candidate_count"],
            "high_priority_only": high_priority_only,
            "limit_applied": limit_applied,
            "new_candidate_project_ids": run_diff["new_candidate_project_ids"],
            "continuing_candidate_project_ids": run_diff[
                "continuing_candidate_project_ids"
            ],
            "dropped_candidate_project_ids": run_diff["dropped_candidate_project_ids"],
            "results": results,
        }

    def _resolve_monitor_operator_context(
        self,
        db: Session,
        operator: User | None,
    ) -> tuple[User, OperatorStrategy]:
        if operator is None:
            operator = ensure_operator_account(db)
            ensure_operator_profile(db)
            return operator, ensure_operator_strategy(db)
        ensure_operator_profile_for(db, operator)
        return operator, ensure_operator_strategy_for(db, operator)

    def _previous_candidate_project_ids(self, previous_result_payload: dict) -> set[int]:
        return {
            int(item["project_id"])
            for item in self._extract_result_items(previous_result_payload)
        }

    def _monitor_scan_limit(
        self,
        *,
        trigger_source: str,
        resolved_limit: int,
    ) -> int | None:
        if trigger_source != self.SCHEDULED_TRIGGER_SOURCE:
            return None
        return self._schedule_scan_limit(resolved_limit)

    def _build_monitor_decision_request(
        self,
        inputs: CandidateDecisionInputs,
        *,
        workload_context: OpportunityWorkloadContext,
    ) -> BidDecisionSaveRequest:
        return BidDecisionSaveRequest(
            project_id=inputs.project_id,
            recommended_amount=inputs.recommended_amount,
            probability_score=inputs.probability_score,
            matched_score=inputs.matched_score,
            deadline_hours_remaining=inputs.deadline_hours_remaining,
            current_active_bids=workload_context.current_active_bids,
            max_active_bids=inputs.max_active_bids,
            current_workload_score=workload_context.current_workload_score,
            budget_estimate=inputs.budget_estimate,
            competitiveness_score=inputs.competitiveness_score,
            expected_margin_score=inputs.expected_margin_score,
            execution_complexity_score=inputs.execution_complexity_score,
            workload_source=workload_context.workload_source,
            strengths=list(inputs.strengths),
            risk_flags=list(inputs.risk_flags),
        )
