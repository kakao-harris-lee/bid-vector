"""Public monitoring entry points and per-candidate evaluation processing.

``preview_candidates`` / ``execute_monitoring`` and their evaluation helpers,
moved verbatim from the original ``opportunity_monitoring`` module.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    ensure_operator_profile_for,
    ensure_operator_strategy,
    ensure_operator_strategy_for,
)
from app.models.models import OperatorStrategy, Project, User
from app.schemas.schemas import BidDecisionSaveRequest, OperatorStrategyMonitorRequest
from app.services.opportunity_monitoring.base import (
    StrategyCandidateEvaluation,
    _MonitoringBase,
)
from app.services.opportunity_monitoring.preview_cache import (
    PreviewCacheKey,
    preview_cache,
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

        The scan itself goes through ``preview_cache`` (single-flight + short
        TTL): a reload or re-click while a scan is running shares that scan
        instead of starting a duplicate, and a repeat read inside the TTL is
        served from the stored payload. The returned shape is unchanged, and a
        strategy edit invalidates the operator's entries.
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
        cache_key = PreviewCacheKey(
            operator_id=int(operator.id),
            limit=int(resolved_limit),
            high_priority_only=bool(resolved_high_priority_only),
        )
        return preview_cache.get_or_compute(
            cache_key,
            lambda: self._build_preview_payload(
                db,
                strategy=strategy,
                operator=operator,
                resolved_limit=resolved_limit,
                resolved_high_priority_only=resolved_high_priority_only,
            ),
            float(settings.OPERATOR_STRATEGY_PREVIEW_CACHE_TTL_SECONDS),
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
        candidates = [self._serialize_candidate(evaluation) for evaluation in evaluations[:resolved_limit]]

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
        previous_result_payload = self._load_json(previous_run.result_payload if previous_run else None)
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
                strategy=strategy,
                operator=operator,
                request=request,
                high_priority_only=resolved_high_priority_only,
                previous_candidate_project_ids=previous_candidate_project_ids,
            )

            run_diff = self._build_run_diff({"results": results}, previous_result_payload)
            notification_count = sum(1 for item in results if item.get("notification_created"))

            response = {
                "monitor_run_id": monitor_run.id,
                "task_id": monitor_run.task_id,
                "trigger_source": trigger_source,
                "previous_run_id": previous_run.id if previous_run else None,
                "operator_id": operator.id,
                "current_operator_id": int(operator.id),
                "current_operator_username": str(operator.username or ""),
                "evaluated_project_count": evaluated_project_count,
                "selected_candidate_count": len(selected_evaluations),
                "persisted_candidate_count": len(results),
                "notification_count": notification_count,
                "new_candidate_count": run_diff["new_candidate_count"],
                "continuing_candidate_count": run_diff["continuing_candidate_count"],
                "dropped_candidate_count": run_diff["dropped_candidate_count"],
                "high_priority_only": resolved_high_priority_only,
                "limit_applied": resolved_limit,
                "new_candidate_project_ids": run_diff["new_candidate_project_ids"],
                "continuing_candidate_project_ids": run_diff["continuing_candidate_project_ids"],
                "dropped_candidate_project_ids": run_diff["dropped_candidate_project_ids"],
                "results": results,
            }
            self._mark_run_completed(db, run_id=monitor_run.id, response=response)
            return response
        except Exception as exc:
            self._mark_run_failed(db, run_id=monitor_run.id, error_message=str(exc))
            raise

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
        strategy: OperatorStrategy,
        operator: User,
        request: OperatorStrategyMonitorRequest,
        high_priority_only: bool,
        previous_candidate_project_ids: set[int],
    ) -> list[dict]:
        results: list[dict] = []
        for evaluation in selected_evaluations:
            result = self._process_monitor_evaluation(
                db,
                evaluation=evaluation,
                strategy=strategy,
                operator=operator,
                request=request,
                high_priority_only=high_priority_only,
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
        strategy: OperatorStrategy,
        operator: User,
        request: OperatorStrategyMonitorRequest,
        high_priority_only: bool,
        previous_candidate_project_ids: set[int],
    ) -> dict | None:
        project = evaluation.project
        refreshed_analysis = self._analyze_project(
            db,
            project,
            operator=operator,
            max_active_bids=request.max_active_bids,
            current_workload_score=request.current_workload_score,
            same_category_only=request.same_category_only,
            similar_limit=request.similar_limit,
            min_similarity=request.min_similarity,
        )
        if not self._passes_monitor_thresholds(
            refreshed_analysis,
            strategy=strategy,
            high_priority_only=high_priority_only,
        ):
            return None

        decision_record = self.decision_service.save_decision(
            db,
            self._build_monitor_decision_request(
                project,
                refreshed_analysis,
                request=request,
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
            decision_record=decision_record,
            notification=notification,
            refreshed_analysis=refreshed_analysis,
            is_new_candidate=is_new_candidate,
        )

    def _passes_monitor_thresholds(
        self,
        refreshed_analysis: dict,
        *,
        strategy: OperatorStrategy,
        high_priority_only: bool,
    ) -> bool:
        if float(refreshed_analysis["matched_score"]) < float(strategy.minimum_match_score or 0.0):
            return False
        if float(refreshed_analysis["probability_score"]) < float(strategy.minimum_probability_score or 0.0):
            return False
        if high_priority_only and not self._is_high_priority_candidate(refreshed_analysis):
            return False
        return True

    def _build_monitor_decision_request(
        self,
        project: Project,
        refreshed_analysis: dict,
        *,
        request: OperatorStrategyMonitorRequest,
    ) -> BidDecisionSaveRequest:
        return BidDecisionSaveRequest(
            project_id=project.id,
            recommended_amount=float(refreshed_analysis["recommended_amount"]),
            probability_score=float(refreshed_analysis["probability_score"]),
            matched_score=float(refreshed_analysis["matched_score"]),
            deadline_hours_remaining=refreshed_analysis.get("deadline_hours_remaining"),
            current_active_bids=int(refreshed_analysis.get("current_active_bids") or 0),
            max_active_bids=int(refreshed_analysis.get("max_active_bids") or request.max_active_bids),
            current_workload_score=self._resolve_current_workload_score(
                refreshed_analysis,
                fallback=request.current_workload_score,
            ),
            budget_estimate=float(project.budget_estimate or 0.0),
            competitiveness_score=self._resolve_competitiveness_score(refreshed_analysis),
            expected_margin_score=self._resolve_expected_margin_score(refreshed_analysis),
            execution_complexity_score=self._resolve_execution_complexity_score(refreshed_analysis),
            workload_source=str(
                refreshed_analysis.get("workload_source")
                or ("provided" if request.current_workload_score is not None else "auto")
            ),
            strengths=list(refreshed_analysis.get("strengths") or []),
            risk_flags=list(refreshed_analysis.get("risk_flags") or []),
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
        decision_record,
        notification,
        refreshed_analysis: dict,
        is_new_candidate: bool,
    ) -> dict:
        return {
            "project_id": evaluation.project.id,
            "title": evaluation.project.title,
            "decision_record_id": int(decision_record.id),
            "notification_id": int(notification.id) if notification is not None else None,
            "action": str(decision_record.action),
            "decision_status": str(decision_record.decision_status),
            "priority_score": float(decision_record.priority_score or 0.0),
            "probability_score": float(decision_record.probability_score or 0.0),
            "matched_score": float(decision_record.matched_score or 0.0),
            "recommended_amount": float(decision_record.recommended_amount or 0.0),
            "analysis_summary": str(refreshed_analysis.get("analysis_summary") or ""),
            "is_new_candidate": is_new_candidate,
            "notification_created": notification is not None,
            "strategy_reasons": evaluation.strategy_reasons,
        }
