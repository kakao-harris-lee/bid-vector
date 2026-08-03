"""Persisted application seam for user-facing similar-project refreshes."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import ensure_utc, utc_now
from app.models.models import Project, SimilarProjectsRefreshOperation, User
from app.schemas.project import (
    ProjectEmbeddingBatchRefreshTaskStatusResponse,
    SimilarProjectsRefreshOperationResponse,
    SimilarProjectsRefreshOperationStatusResponse,
)
from app.tasks.jobs import (
    enqueue_project_embedding_refresh,
    get_project_embedding_rebuild_task_status,
)


class RefreshTaskHandle(Protocol):
    id: str


EnqueueRefresh = Callable[..., RefreshTaskHandle]
ReadTaskStatus = Callable[[str], dict[str, Any]]
Now = Callable[[], datetime]

_OPERATION_NAME = "refresh_similar_projects"
_TASK_TO_DOMAIN_STATUS = {
    "queued": "accepted",
    "running": "in_progress",
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}
_MESSAGES = {
    "accepted": "유사 공고 갱신을 요청했습니다.",
    "in_progress": "유사 공고를 갱신하고 있습니다.",
    "succeeded": "유사 공고 갱신이 완료되었습니다.",
    "failed": "유사 공고 갱신에 실패했습니다.",
    "cancelled": "유사 공고 갱신이 취소되었습니다.",
}
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class SimilarProjectsRefreshOperationNotFound(LookupError):
    """The requested operation is absent or outside the actor's scope."""


class SimilarProjectsRefreshService:
    """Create and poll opaque operations bound to one project and operator."""

    def __init__(
        self,
        *,
        enqueue_refresh: EnqueueRefresh | None = None,
        read_task_status: ReadTaskStatus | None = None,
        now: Now = utc_now,
    ) -> None:
        self._enqueue_refresh = (
            enqueue_refresh or enqueue_project_embedding_refresh
        )
        self._read_task_status = (
            read_task_status or get_project_embedding_rebuild_task_status
        )
        self._now = now

    def start(
        self,
        db: Session,
        *,
        project: Project,
        operator: User,
        force: bool,
    ) -> SimilarProjectsRefreshOperationResponse:
        operation = SimilarProjectsRefreshOperation(
            operation_id=secrets.token_urlsafe(24),
            project_id=int(project.id),
            operator_id=int(operator.id),
            status="accepted",
            expires_at=self._now()
            + timedelta(seconds=settings.CELERY_RESULT_EXPIRES_SECONDS),
        )
        db.add(operation)
        db.commit()

        try:
            task = self._enqueue_refresh(project_id=int(project.id), force=force)
            operation.task_id = str(task.id)
            db.commit()
        except Exception:
            db.rollback()
            operation = self._load_owned(
                db,
                operation_id=operation.operation_id,
                project_id=int(project.id),
                operator_id=int(operator.id),
            )
            self._mark_failed(db, operation)

        return self._start_response(operation)

    def get_status(
        self,
        db: Session,
        *,
        operation_id: str,
        project_id: int,
        operator: User,
    ) -> SimilarProjectsRefreshOperationStatusResponse:
        operation = self._load_owned(
            db,
            operation_id=operation_id,
            project_id=project_id,
            operator_id=int(operator.id),
        )
        if operation.status in _TERMINAL_STATUSES:
            return self._status_response(operation)
        if ensure_utc(operation.expires_at) <= self._now():
            self._mark_failed(db, operation)
            return self._status_response(operation)
        if not operation.task_id:
            self._mark_failed(db, operation)
            return self._status_response(operation)

        task_status = ProjectEmbeddingBatchRefreshTaskStatusResponse.model_validate(
            self._read_task_status(operation.task_id)
        )
        domain_status = _TASK_TO_DOMAIN_STATUS.get(task_status.status, "accepted")
        if domain_status != operation.status:
            operation.status = domain_status
            operation.error_message = (
                _MESSAGES["failed"] if domain_status == "failed" else None
            )
            db.commit()
        return self._status_response(operation)

    @staticmethod
    def _load_owned(
        db: Session,
        *,
        operation_id: str,
        project_id: int,
        operator_id: int,
    ) -> SimilarProjectsRefreshOperation:
        operation = (
            db.query(SimilarProjectsRefreshOperation)
            .filter(
                SimilarProjectsRefreshOperation.operation_id == operation_id,
                SimilarProjectsRefreshOperation.project_id == int(project_id),
                SimilarProjectsRefreshOperation.operator_id == int(operator_id),
            )
            .first()
        )
        if operation is None:
            raise SimilarProjectsRefreshOperationNotFound
        return operation

    @staticmethod
    def _mark_failed(
        db: Session,
        operation: SimilarProjectsRefreshOperation,
    ) -> None:
        operation.status = "failed"
        operation.error_message = _MESSAGES["failed"]
        db.commit()

    @staticmethod
    def _start_response(
        operation: SimilarProjectsRefreshOperation,
    ) -> SimilarProjectsRefreshOperationResponse:
        return SimilarProjectsRefreshOperationResponse(
            operation_id=operation.operation_id,
            operation=_OPERATION_NAME,
            project_id=int(operation.project_id),
            status=operation.status,
            message=_MESSAGES[operation.status],
            poll_url=(
                f"/api/v1/projects/{operation.project_id}/similar/refresh/operations/"
                f"{operation.operation_id}"
            ),
        )

    @staticmethod
    def _status_response(
        operation: SimilarProjectsRefreshOperation,
    ) -> SimilarProjectsRefreshOperationStatusResponse:
        return SimilarProjectsRefreshOperationStatusResponse(
            operation_id=operation.operation_id,
            operation=_OPERATION_NAME,
            project_id=int(operation.project_id),
            status=operation.status,
            is_terminal=operation.status in _TERMINAL_STATUSES,
            succeeded=operation.status == "succeeded",
            message=_MESSAGES[operation.status],
            error=operation.error_message,
        )


def get_similar_projects_refresh_service() -> SimilarProjectsRefreshService:
    """Build the request-scoped refresh application service."""
    return SimilarProjectsRefreshService()
