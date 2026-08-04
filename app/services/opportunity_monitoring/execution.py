"""Prepared monitor-run execution and all-or-nothing completion."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.models import OperatorStrategy, OperatorStrategyRun, User
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.opportunity_monitoring.serialization import (
    MONITOR_RESULT_PAYLOAD_COLUMN,
)


@dataclass(slots=True)
class MonitorExecutionContext:
    operator: User
    strategy: OperatorStrategy
    run: OperatorStrategyRun
    previous_run: OperatorStrategyRun | None
    previous_payload: dict
    previous_project_ids: set[int]
    resolved_limit: int
    high_priority_only: bool
    scan_limit: int | None
    trigger_source: str


class MonitorExecutionMixin:
    """Execute one monitor run while its operator singleton lease is held."""

    def _execute_monitoring_under_lock(
        self,
        db: Session,
        *,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str,
        existing_run_id: int | None,
        task_id: str | None,
        operator: User | None,
    ):
        operator, strategy = self._resolve_monitor_operator_context(db, operator)
        limit, high_priority = self._resolve_runtime_options(
            strategy, limit=request.limit, high_priority_only=request.high_priority_only
        )
        monitor_run = self._prepare_monitor_run(
            db,
            operator_id=operator.id,
            request=request,
            trigger_source=trigger_source,
            resolved_high_priority_only=high_priority,
            resolved_limit=limit,
            existing_run_id=existing_run_id,
            task_id=task_id,
            operator=operator,
        )
        try:
            context = self._monitor_execution_context(
                db, operator, strategy, monitor_run, trigger_source, limit, high_priority
            )
            return self._run_monitor_context(db, request, context)
        except Exception as exc:
            self._handle_monitor_failure(db, monitor_run, exc)
            raise

    def _monitor_execution_context(
        self, db, operator, strategy, monitor_run, trigger_source, limit, high_priority
    ) -> MonitorExecutionContext:
        previous_run = self._get_previous_completed_run(
            db,
            operator_id=operator.id,
            exclude_run_id=monitor_run.id,
            before_run_id=monitor_run.id,
        )
        previous_payload = self._load_json(
            previous_run.result_payload if previous_run else None,
            context=MONITOR_RESULT_PAYLOAD_COLUMN,
        )
        return MonitorExecutionContext(
            operator=operator,
            strategy=strategy,
            run=monitor_run,
            previous_run=previous_run,
            previous_payload=previous_payload,
            previous_project_ids=self._previous_candidate_project_ids(previous_payload),
            resolved_limit=limit,
            high_priority_only=high_priority,
            scan_limit=self._monitor_scan_limit(
                trigger_source=trigger_source, resolved_limit=limit
            ),
            trigger_source=trigger_source,
        )

    def _run_monitor_context(self, db, request, context: MonitorExecutionContext):
        self._projection_deferrals = []
        evaluations, evaluated_count = self._collect_candidate_evaluations(
            db,
            strategy=context.strategy,
            operator=context.operator,
            high_priority_only=context.high_priority_only,
            max_active_bids=request.max_active_bids,
            current_workload_score=request.current_workload_score,
            same_category_only=request.same_category_only,
            similar_limit=request.similar_limit,
            min_similarity=request.min_similarity,
            scan_limit=context.scan_limit,
        )
        selected = evaluations[: context.resolved_limit]
        self._configure_atomic_monitor_persistence(int(context.run.id))
        try:
            self._stage_projection_deferral_items(db, monitor_run_id=int(context.run.id))
            results = self._process_monitor_evaluations(
                db,
                monitor_run_id=int(context.run.id),
                selected_evaluations=selected,
                operator=context.operator,
                previous_candidate_project_ids=context.previous_project_ids,
            )
        finally:
            self._reset_monitor_persistence()
        response = self._build_monitor_response(
            monitor_run=context.run,
            previous_run=context.previous_run,
            trigger_source=context.trigger_source,
            operator=context.operator,
            evaluated_project_count=evaluated_count,
            selected_candidate_count=len(selected),
            results=results,
            projection_deferrals=list(self._projection_deferrals),
            previous_result_payload=context.previous_payload,
            high_priority_only=context.high_priority_only,
            limit_applied=context.resolved_limit,
        )
        self._mark_run_completed(db, run_id=context.run.id, response=response)
        return response

    def _handle_monitor_failure(self, db, monitor_run, exc: Exception) -> None:
        db.rollback()
        project_id = getattr(exc, "monitor_project_id", None)
        if project_id is not None:
            self._record_failed_monitor_item(
                db,
                monitor_run_id=int(monitor_run.id),
                project_id=int(project_id),
                stage=str(getattr(exc, "monitor_stage", "persistence")),
                error_message=str(exc),
            )
        self._mark_run_failed(db, run_id=monitor_run.id, error_message=str(exc))
