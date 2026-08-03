"""Shared task-dispatch guards for ML workloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.config import settings


class QueuedOnlyTaskHandle:
    """Task handle used when ML work must not execute inside the API process."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


@dataclass(frozen=True)
class MlTaskDispatch:
    task: Any
    kwargs: dict[str, Any]
    queue: str


def enqueue_ml_task(dispatch: MlTaskDispatch):
    """Queue ML work, refusing eager API-process execution unless opted in."""
    if settings.uses_in_memory_celery and not settings.CELERY_ALLOW_INLINE_ML_TASKS:
        return QueuedOnlyTaskHandle(str(uuid4()))
    return dispatch.task.apply_async(kwargs=dispatch.kwargs, queue=dispatch.queue)
