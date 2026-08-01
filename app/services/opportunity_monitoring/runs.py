"""Monitoring-run record lifecycle: create / transition / finalize / serialize.

All ``OperatorStrategyRun`` persistence and the rollback-safe failure recovery,
moved verbatim from the original ``opportunity_monitoring`` module.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_strategy,
    ensure_operator_strategy_for,
)
from app.core.time import utc_now
from app.models.models import OperatorStrategy, OperatorStrategyRun, User
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.opportunity_monitoring.base import _MonitoringBase
from app.services.opportunity_monitoring.serialization import (
    MONITOR_REQUEST_PAYLOAD_COLUMN,
    MONITOR_RESULT_PAYLOAD_COLUMN,
)
from app.services.realtime import realtime_event_manager


class _RunLifecycleMixin(_MonitoringBase):
    """Create, transition, finalize, and serialize monitoring run records."""

    def create_monitor_run(
        self,
        db: Session,
        *,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str,
        task_id: str | None = None,
        status: str = "queued",
        operator: User | None = None,
    ) -> OperatorStrategyRun:
        """Create a persisted monitoring run record before execution starts."""
        if operator is None:
            operator = ensure_operator_account(db)
            strategy = ensure_operator_strategy(db)
        else:
            strategy = ensure_operator_strategy_for(db, operator)
        resolved_limit, resolved_high_priority_only = self._resolve_runtime_options(
            strategy,
            limit=request.limit,
            high_priority_only=request.high_priority_only,
        )
        monitor_run = OperatorStrategyRun(
            operator_id=operator.id,
            task_id=task_id,
            trigger_source=trigger_source,
            status=status,
            high_priority_only=resolved_high_priority_only,
            limit_applied=resolved_limit,
            request_payload=request.model_dump_json(),
            started_at=utc_now() if status == "running" else None,
        )
        db.add(monitor_run)
        db.commit()
        db.refresh(monitor_run)
        return monitor_run

    def update_monitor_run_task_id(self, db: Session, *, run_id: int, task_id: str) -> OperatorStrategyRun | None:
        """Attach the async task id to an already-created monitoring run."""
        monitor_run = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.id == run_id).first()
        if monitor_run is None:
            return None
        monitor_run.task_id = task_id
        db.commit()
        db.refresh(monitor_run)
        return monitor_run

    def list_recent_runs(
        self,
        db: Session,
        *,
        limit: int = 20,
        run_status: str | None = None,
        operator: User | None = None,
    ) -> list[OperatorStrategyRun]:
        """Return recent monitoring execution history.

        Defaults to the canonical singleton operator when ``operator`` is not
        provided so legacy callers continue to behave as before. The dashboard
        endpoint passes the resolved target operator so cross-operator views
        do not leak the canonical operator's run history.
        """
        if operator is None:
            operator = ensure_operator_account(db)
        query = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.operator_id == operator.id)
        if run_status:
            query = query.filter(OperatorStrategyRun.status == run_status)
        return (
            query.order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
            .limit(limit)
            .all()
        )

    def get_run_detail(
        self,
        db: Session,
        *,
        run_id: int,
        operator: User | None = None,
    ) -> dict:
        """Return one monitoring run with stored payloads and previous-run diff details."""
        if operator is None:
            operator = ensure_operator_account(db)
        monitor_run = (
            db.query(OperatorStrategyRun)
            .filter(
                OperatorStrategyRun.id == run_id,
                OperatorStrategyRun.operator_id == operator.id,
            )
            .first()
        )
        if monitor_run is None:
            raise ValueError("Monitoring run not found")

        request_payload = self._load_json(
            monitor_run.request_payload, context=MONITOR_REQUEST_PAYLOAD_COLUMN
        )
        result_payload = self._load_json(
            monitor_run.result_payload, context=MONITOR_RESULT_PAYLOAD_COLUMN
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
        diff = self._build_run_diff(result_payload, previous_result_payload) if result_payload else {
            "new_candidate_count": 0,
            "continuing_candidate_count": 0,
            "dropped_candidate_count": 0,
            "new_candidates": [],
            "continuing_candidates": [],
            "dropped_candidates": [],
        }

        return {
            **self.serialize_run(monitor_run),
            "previous_run_id": result_payload.get("previous_run_id") or (previous_run.id if previous_run else None),
            "new_candidate_count": int(result_payload.get("new_candidate_count", diff["new_candidate_count"])),
            "continuing_candidate_count": int(result_payload.get("continuing_candidate_count", diff["continuing_candidate_count"])),
            "dropped_candidate_count": int(result_payload.get("dropped_candidate_count", diff["dropped_candidate_count"])),
            "request_payload": request_payload,
            "result": result_payload or None,
            "new_candidates": diff["new_candidates"],
            "continuing_candidates": diff["continuing_candidates"],
            "dropped_candidates": diff["dropped_candidates"],
        }

    def serialize_run(self, monitor_run: OperatorStrategyRun) -> dict:
        """Convert a stored monitoring run into the public API response shape."""
        current_operator_username = ""
        if monitor_run.operator is not None:
            current_operator_username = str(monitor_run.operator.username or "")
        return {
            "id": int(monitor_run.id),
            "operator_id": int(monitor_run.operator_id),
            "current_operator_id": int(monitor_run.operator_id),
            "current_operator_username": current_operator_username,
            "task_id": monitor_run.task_id,
            "trigger_source": str(monitor_run.trigger_source),
            "status": str(monitor_run.status),
            "high_priority_only": bool(monitor_run.high_priority_only),
            "limit_applied": int(monitor_run.limit_applied or self.DEFAULT_LIMIT),
            "evaluated_project_count": int(monitor_run.evaluated_project_count or 0),
            "selected_candidate_count": int(monitor_run.selected_candidate_count or 0),
            "persisted_candidate_count": int(monitor_run.persisted_candidate_count or 0),
            "notification_count": int(monitor_run.notification_count or 0),
            "error_message": monitor_run.error_message,
            "created_at": monitor_run.created_at,
            "started_at": monitor_run.started_at,
            "completed_at": monitor_run.completed_at,
        }

    def _resolve_runtime_options(
        self,
        strategy: OperatorStrategy,
        *,
        limit: int | None,
        high_priority_only: bool | None,
    ) -> tuple[int, bool]:
        """Resolve limit and high-priority mode from per-run overrides or stored strategy."""
        resolved_limit = max(1, min(int(limit or strategy.max_recommended_candidates or self.DEFAULT_LIMIT), 100))
        resolved_high_priority_only = strategy.notify_only_high_priority if high_priority_only is None else bool(high_priority_only)
        return resolved_limit, resolved_high_priority_only

    def _prepare_monitor_run(
        self,
        db: Session,
        *,
        operator_id: int,
        request: OperatorStrategyMonitorRequest,
        trigger_source: str,
        resolved_high_priority_only: bool,
        resolved_limit: int,
        existing_run_id: int | None,
        task_id: str | None,
        operator: User,
    ) -> OperatorStrategyRun:
        """Create or transition a monitoring run record into the running state."""
        if existing_run_id is None:
            return self.create_monitor_run(
                db,
                request=request,
                trigger_source=trigger_source,
                task_id=task_id,
                status="running",
                operator=operator,
            )

        monitor_run = db.query(OperatorStrategyRun).filter(
            OperatorStrategyRun.id == existing_run_id,
            OperatorStrategyRun.operator_id == operator_id,
        ).first()
        if monitor_run is None:
            raise ValueError("Monitoring run not found")

        monitor_run.task_id = task_id or monitor_run.task_id
        monitor_run.trigger_source = trigger_source
        monitor_run.status = "running"
        monitor_run.high_priority_only = resolved_high_priority_only
        monitor_run.limit_applied = resolved_limit
        monitor_run.request_payload = request.model_dump_json()
        monitor_run.error_message = None
        monitor_run.started_at = monitor_run.started_at or utc_now()
        monitor_run.completed_at = None
        db.commit()
        db.refresh(monitor_run)
        return monitor_run

    def _get_previous_completed_run(
        self,
        db: Session,
        *,
        operator_id: int,
        exclude_run_id: int,
        before_run_id: int | None = None,
    ) -> OperatorStrategyRun | None:
        """Return the latest completed run before the target run id."""
        query = db.query(OperatorStrategyRun).filter(
            OperatorStrategyRun.operator_id == operator_id,
            OperatorStrategyRun.status == "completed",
            OperatorStrategyRun.id != exclude_run_id,
        )
        if before_run_id is not None:
            query = query.filter(OperatorStrategyRun.id < before_run_id)
        return query.order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc()).first()

    def _mark_run_completed(self, db: Session, *, run_id: int, response: dict) -> None:
        """Persist the final summary of a completed monitoring run."""
        monitor_run = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.id == run_id).first()
        if monitor_run is None:
            return
        monitor_run.status = "completed"
        monitor_run.evaluated_project_count = int(response.get("evaluated_project_count") or 0)
        monitor_run.selected_candidate_count = int(response.get("selected_candidate_count") or 0)
        monitor_run.persisted_candidate_count = int(response.get("persisted_candidate_count") or 0)
        monitor_run.notification_count = int(response.get("notification_count") or 0)
        monitor_run.result_payload = self._dump_json(response)
        monitor_run.error_message = None
        monitor_run.completed_at = utc_now()
        db.commit()
        realtime_event_manager.publish_event(
            "strategy_monitor.completed",
            {
                "monitor_run_id": int(monitor_run.id),
                "operator_id": int(monitor_run.operator_id),
                "trigger_source": monitor_run.trigger_source,
                "evaluated_project_count": int(monitor_run.evaluated_project_count or 0),
                "selected_candidate_count": int(monitor_run.selected_candidate_count or 0),
                "persisted_candidate_count": int(monitor_run.persisted_candidate_count or 0),
                "notification_count": int(monitor_run.notification_count or 0),
                "new_candidate_count": int(response.get("new_candidate_count") or 0),
            },
        )

    def _mark_run_failed(self, db: Session, *, run_id: int, error_message: str) -> bool:
        """Persist failure metadata for a monitoring run after rollback-safe recovery.

        Best-effort and exception-safe: this runs inside the ``except`` arm of
        ``execute_monitoring`` (and the ``monitor_operator_strategy`` task), so it
        must never raise. If the original failure was a DB error, the passed-in
        session is already poisoned -- the ``db.rollback()`` / query / commit on
        it can themselves raise. We first try the live session, then fall back to
        a brand-new session, and finally swallow any secondary error so the
        original exception is always re-raised by the caller (never masked).

        Returns ``True`` when the row was transitioned to ``failed`` (so the
        reconciler does not have to mop it up later), ``False`` otherwise.
        """
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 — poisoned session must not mask original error
            pass

        try:
            if self._write_run_failure(db, run_id=run_id, error_message=error_message):
                return True
            # Live session could not persist the failure (e.g. connection
            # refused). Try once more on a fresh, independent session so the
            # orphan ``running`` row is still closed out without waiting for the
            # periodic reconciler.
            return self._write_run_failure_on_new_session(
                run_id=run_id, error_message=error_message
            )
        except Exception:  # noqa: BLE001 — finalize is best-effort; never mask original
            return False

    def _write_run_failure(self, db: Session, *, run_id: int, error_message: str) -> bool:
        """Attempt to finalize a run as failed on the given session. Never raises."""
        try:
            monitor_run = db.query(OperatorStrategyRun).filter(OperatorStrategyRun.id == run_id).first()
            if monitor_run is None:
                return False
            monitor_run.status = "failed"
            monitor_run.error_message = error_message
            monitor_run.completed_at = utc_now()
            db.commit()
        except Exception:  # noqa: BLE001 — best-effort finalize must not mask original error
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            return False

        self._publish_run_failed_event(monitor_run)
        return True

    def _write_run_failure_on_new_session(self, *, run_id: int, error_message: str) -> bool:
        """Finalize a run as failed on a fresh session when the live one is poisoned."""
        recovery_db: Session | None = None
        try:
            recovery_db = self._session_factory()
            return self._write_run_failure(recovery_db, run_id=run_id, error_message=error_message)
        except Exception:  # noqa: BLE001 — recovery is strictly best-effort
            return False
        finally:
            if recovery_db is not None:
                try:
                    recovery_db.close()
                except Exception:  # noqa: BLE001
                    pass

    def _publish_run_failed_event(self, monitor_run: OperatorStrategyRun) -> None:
        """Emit the realtime failure event without ever masking the original error."""
        try:
            realtime_event_manager.publish_event(
                "strategy_monitor.failed",
                {
                    "monitor_run_id": int(monitor_run.id),
                    "operator_id": int(monitor_run.operator_id),
                    "trigger_source": monitor_run.trigger_source,
                    "error_message": monitor_run.error_message,
                },
            )
        except Exception:  # noqa: BLE001 — event publish must not break failure finalize
            pass
