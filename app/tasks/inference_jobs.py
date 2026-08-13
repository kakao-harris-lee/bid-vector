"""Durable inference-projection task and dispatch helpers."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.database import task_session
from app.schemas.similarity_runtime import (
    EmbeddingRebuildDispatchInput,
    EmbeddingRebuildDispatchResult,
    SimilarityProjectionBackfillResult,
)
from app.services.project_similarity import ProjectSimilarityService
from app.services.task_singleton import singleton_lease
from app.tasks.celery_app import (
    INFERENCE_OUTBOX_PROCESS_TASK_NAME,
    SIMILARITY_PROJECTION_BACKFILL_TASK_NAME,
    celery_app,
)
from app.tasks.dispatch import MlTaskDispatch, enqueue_ml_task

logger = logging.getLogger(__name__)

SIMILARITY_PROJECTION_BACKFILL_LEASE_KEY = "similarity_projection_backfill"


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


def _warn_if_rotation_stalled(result: SimilarityProjectionBackfillResult) -> None:
    """Surface a batch that selected work and staged none of it.

    Oldest-first selection turns the batch rate into a rotation, but it also
    concentrates failures: a target that cannot be staged keeps its ``computed_at``
    and therefore returns to the head of the next batch. Under the previous id
    ordering such a target cost one slot; now enough of them fill every batch and
    the rotation stops advancing — every other projection then ages out silently.

    The counts already existed in the task result, but a Celery result payload is
    neither persisted nor alerted on, so the stall had no signal at all. Partial
    blockage stays in ``blocked_project_ids`` for diagnosis; only a fully blocked
    batch is loud, because that is the one that halts the rotation.
    """
    if result.selected_count <= 0 or result.staged_count > 0:
        return
    logger.warning(
        "similarity projection rotation stalled: selected=%d staged=0 blocked=%s",
        result.selected_count,
        result.blocked_project_ids[:20],
    )


@celery_app.task(name=SIMILARITY_PROJECTION_BACKFILL_TASK_NAME)
def stage_active_similarity_projection_backfill(
    limit: int = 100,
) -> InferenceOutboxTaskPayload:
    """Stage current active-target projections without computing them inline.

    Skips its own body when the previous tick is still running: this task is the
    single consumer of one queue, so overlapping runs do not share the work — they
    restage the same head of the candidate set and pin the worker.
    """
    resolved_limit = max(1, int(limit or 100))
    with task_session() as db:
        with singleton_lease(
            db.get_bind(), SIMILARITY_PROJECTION_BACKFILL_LEASE_KEY
        ) as acquired:
            if not acquired:
                logger.warning(
                    "similarity projection backfill still running; tick skipped"
                )
                return InferenceOutboxTaskPayload(
                    SimilarityProjectionBackfillResult(
                        selected_count=0,
                        staged_count=0,
                        limit=resolved_limit,
                        duplicate_suppressed=True,
                    ).model_dump(mode="python")
                )
            try:
                result = ProjectSimilarityService().stage_active_similarity_projection_backfill(
                    db, limit=resolved_limit
                )
                db.commit()
                _warn_if_rotation_stalled(result)
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
