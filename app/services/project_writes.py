"""Atomic manual-project writes backed by the durable inference outbox."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from app.models.models import InferenceOutboxEvent, Project
from app.schemas.project import ProjectCreate
from app.services.inference_outbox import InferenceOutboxService
from app.services.similarity_read_model import (
    invalidate_project_embedding,
    project_embedding_input_state,
)

logger = logging.getLogger(__name__)


class SemanticInputOutbox(Protocol):
    """Minimal durable-write contract needed by manual project mutations."""

    def ensure_semantic_input_changed_event(
        self,
        db: Session,
        project: Project,
        *,
        semantic_input_changed: bool,
    ) -> InferenceOutboxEvent | None: ...


NotifyCommitted = Callable[[list[int]], object]


class ProjectWriteNotFound(LookupError):
    """The requested project does not exist."""


class ProjectWriteService:
    """Commit project facts and recoverable inference intent as one unit."""

    def __init__(
        self,
        *,
        notify_committed: NotifyCommitted,
        outbox: SemanticInputOutbox | None = None,
    ) -> None:
        self._notify_committed = notify_committed
        self._outbox = outbox or InferenceOutboxService()

    def create(self, db: Session, project_input: ProjectCreate) -> Project:
        """Create a project and its current-input event in one transaction."""
        project = Project(**project_input.model_dump())
        try:
            db.add(project)
            db.flush()
            invalidate_project_embedding(project)
            event = self._outbox.ensure_semantic_input_changed_event(
                db,
                project,
                semantic_input_changed=True,
            )
            event_ids = self._event_ids(event)
            db.commit()
        except Exception:
            db.rollback()
            raise

        self._notify_best_effort(event_ids)
        db.refresh(project)
        return project

    def update(
        self,
        db: Session,
        *,
        project_id: int,
        project_update: ProjectCreate,
    ) -> Project:
        """Update facts while atomically invalidating and recording changed input."""
        project = db.get(Project, project_id)
        if project is None:
            raise ProjectWriteNotFound(project_id)

        try:
            previous_input = project_embedding_input_state(project)
            for field, value in project_update.model_dump(exclude_unset=True).items():
                setattr(project, field, value)
            semantic_input_changed = (
                project_embedding_input_state(project) != previous_input
            )
            event: InferenceOutboxEvent | None = None
            if semantic_input_changed:
                invalidate_project_embedding(project)
                event = self._outbox.ensure_semantic_input_changed_event(
                    db,
                    project,
                    semantic_input_changed=True,
                )
            event_ids = self._event_ids(event)
            db.commit()
        except Exception:
            db.rollback()
            raise

        self._notify_best_effort(event_ids)
        db.refresh(project)
        return project

    def _notify_best_effort(self, event_ids: list[int]) -> None:
        if not event_ids:
            return
        try:
            self._notify_committed(event_ids)
        except Exception:  # noqa: BLE001 - periodic sweep owns durable recovery
            logger.exception(
                "manual project inference notification failed for %d event(s)",
                len(event_ids),
            )

    @staticmethod
    def _event_ids(event: InferenceOutboxEvent | None) -> list[int]:
        return [] if event is None else [int(event.id)]


def get_project_write_service() -> ProjectWriteService:
    """Compose the request-scoped writer with the narrow inference notifier."""
    from app.tasks.inference_jobs import notify_inference_outbox_committed

    return ProjectWriteService(
        notify_committed=notify_inference_outbox_committed,
    )
