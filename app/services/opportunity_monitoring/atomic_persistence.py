"""Atomic monitor persistence configuration and failure evidence."""

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.models import OperatorStrategyRunItem


class MonitorAtomicPersistenceMixin:
    def _configure_atomic_monitor_persistence(self, monitor_run_id: int) -> None:
        self.decision_service.defer_commit = True
        self.decision_service.monitor_run_id = monitor_run_id
        self.notification_service.defer_commit = True
        self.notification_service.defer_delivery = True
        self.notification_service.monitor_run_id = monitor_run_id

    def _stage_projection_deferral_items(
        self, db: Session, *, monitor_run_id: int
    ) -> None:
        for deferred in self._projection_deferrals:
            db.add(
                OperatorStrategyRunItem(
                    run_id=monitor_run_id,
                    project_id=int(deferred["project_id"]),
                    status="deferred",
                    stage="similarity_projection",
                    error_message=(
                        "similarity projection " f"{deferred['projection_status']}"
                    ),
                    completed_at=utc_now(),
                )
            )
        db.flush()

    def _reset_monitor_persistence(self) -> None:
        self.decision_service.defer_commit = False
        self.decision_service.monitor_run_id = None
        self.notification_service.defer_commit = False
        self.notification_service.defer_delivery = False
        self.notification_service.monitor_run_id = None

    def _record_failed_monitor_item(
        self,
        db: Session,
        *,
        monitor_run_id: int,
        project_id: int,
        stage: str,
        error_message: str,
    ) -> None:
        try:
            item = (
                db.query(OperatorStrategyRunItem)
                .filter(
                    OperatorStrategyRunItem.run_id == monitor_run_id,
                    OperatorStrategyRunItem.project_id == project_id,
                )
                .first()
            )
            if item is None:
                item = OperatorStrategyRunItem(
                    run_id=monitor_run_id, project_id=project_id
                )
                db.add(item)
            item.status = "failed"
            item.stage = stage
            item.error_message = error_message[:4000]
            item.completed_at = utc_now()
            db.commit()
        except Exception:
            db.rollback()
