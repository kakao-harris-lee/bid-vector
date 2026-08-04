"""Per-operator monitor singleton entry boundary."""

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import OperatorStrategyRun, User
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.task_singleton import AdvisorySingletonLease


class MonitorSingletonMixin:
    """Suppress overlapping monitor runs and retain an auditable cancelled run."""

    def execute_monitoring(
        self,
        db: Session,
        *,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str = "manual_sync",
        existing_run_id: int | None = None,
        task_id: str | None = None,
        operator: User | None = None,
    ):
        operator, _ = self._resolve_monitor_operator_context(db, operator)
        lease = AdvisorySingletonLease(
            db.get_bind(), f"operator_strategy_monitor:{int(operator.id)}"
        )
        if not lease.acquire():
            return self._record_duplicate_suppressed_run(
                db,
                request=request,
                trigger_source=trigger_source,
                existing_run_id=existing_run_id,
                task_id=task_id,
                operator=operator,
            )
        try:
            return self._execute_monitoring_under_lock(
                db,
                request=request,
                trigger_source=trigger_source,
                existing_run_id=existing_run_id,
                task_id=task_id,
                operator=operator,
            )
        finally:
            lease.release()

    def _record_duplicate_suppressed_run(
        self, db, *, request, trigger_source, existing_run_id, task_id, operator
    ):
        monitor_run = (
            db.get(OperatorStrategyRun, int(existing_run_id))
            if existing_run_id is not None
            else self.create_monitor_run(
                db,
                request=request,
                trigger_source=trigger_source,
                task_id=task_id,
                status="queued",
                operator=operator,
            )
        )
        if monitor_run is None:
            raise ValueError("Monitoring run not found")
        response = self._duplicate_suppressed_response(
            monitor_run, operator, trigger_source, task_id
        )
        monitor_run.status = "cancelled"
        monitor_run.error_message = "singleton lock busy; duplicate run suppressed"
        monitor_run.result_payload = self._dump_json(response)
        monitor_run.completed_at = utc_now()
        db.commit()
        return response

    def _duplicate_suppressed_response(
        self, monitor_run, operator, trigger_source, task_id
    ):
        return {
            "monitor_run_id": int(monitor_run.id),
            "task_id": task_id or monitor_run.task_id,
            "trigger_source": trigger_source,
            "release_sha": monitor_run.release_sha,
            "release_tag": monitor_run.release_tag,
            "previous_run_id": None,
            "operator_id": int(operator.id),
            "current_operator_id": int(operator.id),
            "current_operator_username": str(operator.username or ""),
            "evaluated_project_count": 0,
            "selected_candidate_count": 0,
            "persisted_candidate_count": 0,
            "notification_count": 0,
            "projection_not_ready_count": 0,
            "projection_not_ready_project_ids": [],
            "new_candidate_count": 0,
            "continuing_candidate_count": 0,
            "dropped_candidate_count": 0,
            "high_priority_only": bool(monitor_run.high_priority_only),
            "limit_applied": int(monitor_run.limit_applied or self.DEFAULT_LIMIT),
            "new_candidate_project_ids": [],
            "continuing_candidate_project_ids": [],
            "dropped_candidate_project_ids": [],
            "duplicate_suppressed": True,
            "results": [],
        }
