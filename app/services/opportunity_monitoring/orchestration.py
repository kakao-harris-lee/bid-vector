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
from app.models.models import OperatorStrategy, OperatorStrategyRun, Project, User
from app.schemas.schemas import BidDecisionSaveRequest, OperatorStrategyMonitorRequest
from app.services.opportunity_analysis import OpportunityWorkloadContext
from app.services.opportunity_monitoring.base import (
    CandidateDecisionInputs,
    StrategyCandidateEvaluation,
    _MonitoringBase,
)
from app.services.opportunity_monitoring.serialization import (
    MONITOR_RESULT_PAYLOAD_COLUMN,
)


class _OrchestrationMixin(_MonitoringBase):
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

    def execute_monitoring(
        self,
        db: Session,
        *,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str = _MonitoringBase.SYNC_TRIGGER_SOURCE,
        existing_run_id: int | None = None,
        task_id: str | None = None,
        operator: User | None = None,
    ) -> dict:
        """Run the stored strategy, persist bid decisions, and create notifications."""
        operator, strategy = self._resolve_monitor_operator_context(db, operator)
        resolved_limit, resolved_high_priority_only = self._resolve_runtime_options(
            strategy,
            limit=request.limit,
            high_priority_only=request.high_priority_only,
        )
        monitor_run = self._prepare_monitor_run(
            db,
            operator_id=operator.id,
            request=request,
            trigger_source=trigger_source,
            resolved_high_priority_only=resolved_high_priority_only,
            resolved_limit=resolved_limit,
            existing_run_id=existing_run_id,
            task_id=task_id,
            operator=operator,
        )
        previous_run = self._get_previous_completed_run(
            db,
            operator_id=operator.id,
            exclude_run_id=monitor_run.id,
            before_run_id=monitor_run.id,
        )
        previous_result_payload = self._load_json(
            previous_run.result_payload if previous_run else None,
            context=MONITOR_RESULT_PAYLOAD_COLUMN,
        )
        previous_candidate_project_ids = self._previous_candidate_project_ids(
            previous_result_payload
        )
        scan_limit = self._monitor_scan_limit(
            trigger_source=trigger_source,
            resolved_limit=resolved_limit,
        )

        try:
            evaluations, evaluated_project_count = self._collect_candidate_evaluations(
                db,
                strategy=strategy,
                operator=operator,
                high_priority_only=resolved_high_priority_only,
                max_active_bids=request.max_active_bids,
                current_workload_score=request.current_workload_score,
                same_category_only=request.same_category_only,
                similar_limit=request.similar_limit,
                min_similarity=request.min_similarity,
                scan_limit=scan_limit,
            )
            selected_evaluations = evaluations[:resolved_limit]
            results = self._process_monitor_evaluations(
                db,
                selected_evaluations=selected_evaluations,
                operator=operator,
                previous_candidate_project_ids=previous_candidate_project_ids,
            )

            response = self._build_monitor_response(
                monitor_run=monitor_run,
                previous_run=previous_run,
                trigger_source=trigger_source,
                operator=operator,
                evaluated_project_count=evaluated_project_count,
                selected_candidate_count=len(selected_evaluations),
                results=results,
                previous_result_payload=previous_result_payload,
                high_priority_only=resolved_high_priority_only,
                limit_applied=resolved_limit,
            )
            self._mark_run_completed(db, run_id=monitor_run.id, response=response)
            return response
        except Exception as exc:
            self._mark_run_failed(db, run_id=monitor_run.id, error_message=str(exc))
            raise

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
            "previous_run_id": previous_run.id if previous_run else None,
            "operator_id": operator.id,
            "current_operator_id": int(operator.id),
            "current_operator_username": str(operator.username or ""),
            "evaluated_project_count": evaluated_project_count,
            "selected_candidate_count": selected_candidate_count,
            "persisted_candidate_count": len(results),
            "notification_count": notification_count,
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

    def _process_monitor_evaluations(
        self,
        db: Session,
        *,
        selected_evaluations: list[StrategyCandidateEvaluation],
        operator: User,
        previous_candidate_project_ids: set[int],
    ) -> list[dict]:
        results: list[dict] = []
        for evaluation in selected_evaluations:
            result = self._process_monitor_evaluation(
                db,
                evaluation=evaluation,
                operator=operator,
                previous_candidate_project_ids=previous_candidate_project_ids,
            )
            if result is not None:
                results.append(result)
        return results

    def _process_monitor_evaluation(
        self,
        db: Session,
        *,
        evaluation: StrategyCandidateEvaluation,
        operator: User,
        previous_candidate_project_ids: set[int],
    ) -> dict | None:
        # Only selected top-N rows are rehydrated, for notification rendering.
        # Decision inputs and result summary come from the first scan.
        project = db.get(Project, evaluation.project_id)
        if project is None:  # pragma: no cover - 동일 트랜잭션에서 행 소실 불가
            return None
        try:
            workload_context = self.analysis_service.resolve_workload_context(
                db,
                operator_id=int(operator.id),
                max_active_bids=evaluation.decision_inputs.max_active_bids,
                current_workload_score=(
                    evaluation.decision_inputs.provided_workload_score
                ),
                exclude_project_id=evaluation.project_id,
            )
            decision_record = self.decision_service.save_decision(
                db,
                self._build_monitor_decision_request(
                    evaluation.decision_inputs,
                    workload_context=workload_context,
                ),
                operator=operator,
            )
            is_new_candidate = project.id not in previous_candidate_project_ids
            notification = self._maybe_create_monitor_notification(
                db,
                operator=operator,
                project=project,
                decision_record=decision_record,
                is_new_candidate=is_new_candidate,
            )
            return self._serialize_monitor_result(
                evaluation=evaluation,
                project=project,
                decision_record=decision_record,
                notification=notification,
                is_new_candidate=is_new_candidate,
            )
        finally:
            if project in db:
                db.expunge(project)

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

    def _maybe_create_monitor_notification(
        self,
        db: Session,
        *,
        operator: User,
        project: Project,
        decision_record,
        is_new_candidate: bool,
    ):
        if not is_new_candidate:
            return None
        return self.notification_service.create_bid_decision_notification(
            db,
            operator_id=operator.id,
            project=project,
            decision_record=decision_record,
        )

    def _serialize_monitor_result(
        self,
        *,
        evaluation: StrategyCandidateEvaluation,
        project: Project,
        decision_record,
        notification,
        is_new_candidate: bool,
    ) -> dict:
        return {
            "project_id": project.id,
            "title": project.title,
            "decision_record_id": int(decision_record.id),
            "notification_id": int(notification.id) if notification is not None else None,
            "action": str(decision_record.action),
            "decision_status": str(decision_record.decision_status),
            "priority_score": float(decision_record.priority_score or 0.0),
            "probability_score": float(decision_record.probability_score or 0.0),
            "matched_score": float(decision_record.matched_score or 0.0),
            "recommended_amount": float(decision_record.recommended_amount or 0.0),
            "analysis_summary": evaluation.decision_inputs.analysis_summary,
            "is_new_candidate": is_new_candidate,
            "notification_created": notification is not None,
            "strategy_reasons": evaluation.strategy_reasons,
        }
