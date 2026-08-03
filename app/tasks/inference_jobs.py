"""Durable inference-projection task and dispatch helpers."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.database import task_session
from app.schemas.similarity_runtime import (
    EmbeddingRebuildDispatchInput,
    EmbeddingRebuildDispatchResult,
)
from app.services.project_similarity import ProjectSimilarityService
from app.tasks.celery_app import INFERENCE_OUTBOX_PROCESS_TASK_NAME, celery_app
from app.tasks.dispatch import MlTaskDispatch, enqueue_ml_task

logger = logging.getLogger(__name__)


class InferenceOutboxTaskPayload(dict):
    """JSON-serializable Celery payload with a named boundary contract."""


@celery_app.task(name=INFERENCE_OUTBOX_PROCESS_TASK_NAME)
def process_inference_outbox(limit: int = 50) -> InferenceOutboxTaskPayload:
    """Recover stale claims and project pending inference outbox events."""
    with task_session() as db:
        try:
            result = ProjectSimilarityService().process_inference_outbox_events(
                db, limit=max(1, int(limit or 50))
            )
            return InferenceOutboxTaskPayload(result.model_dump(mode="python"))
        except Exception:
            db.rollback()
            raise


def enqueue_inference_outbox_processing(*, limit: int = 50):
    """Queue inference outbox processing on the online inference queue."""
    return enqueue_ml_task(
        MlTaskDispatch(
            task=process_inference_outbox,
            kwargs={"limit": max(1, int(limit or 50))},
            queue=settings.CELERY_ML_INFERENCE_QUEUE,
        )
    )


def notify_embedding_rebuild_committed(
    dispatch: EmbeddingRebuildDispatchInput,
) -> EmbeddingRebuildDispatchResult:
    """Best-effort fast dispatch; periodic sweep remains the delivery guarantee."""
    return notify_inference_outbox_committed(dispatch.outbox_event_ids)


def notify_inference_outbox_committed(
    outbox_event_ids: list[int],
) -> EmbeddingRebuildDispatchResult:
    """Fast-dispatch any committed outbox batch without weakening durability."""
    if not outbox_event_ids:
        return EmbeddingRebuildDispatchResult()
    try:
        result = enqueue_inference_outbox_processing(
            limit=max(50, len(outbox_event_ids))
        )
        return EmbeddingRebuildDispatchResult(
            task_id=str(result.id), queue=settings.CELERY_ML_INFERENCE_QUEUE
        )
    except Exception:  # noqa: BLE001 - durable rows remain for the periodic sweep
        logger.exception("failed to enqueue inference outbox processor")
        return EmbeddingRebuildDispatchResult()
