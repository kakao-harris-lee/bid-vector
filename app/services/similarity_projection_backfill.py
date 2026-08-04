"""Bounded staging for missing or stale active similarity projections."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.constants import ACTIVE_PROJECT_STATUSES
from app.core.time import utc_now
from app.models.models import (
    Project,
    ProjectSimilarityEdge,
    ProjectSimilaritySnapshot,
)
from app.schemas.similarity_runtime import SimilarityProjectionBackfillResult


def _backfill_candidates(db, read_model, limit: int):
    watermark = read_model.corpus_watermark(db)
    snapshot = aliased(ProjectSimilaritySnapshot)
    edge_count = (
        select(func.count(ProjectSimilarityEdge.id))
        .where(ProjectSimilarityEdge.snapshot_id == snapshot.id)
        .correlate(snapshot)
        .scalar_subquery()
    )
    time_mismatch = (
        snapshot.corpus_embedding_updated_at.isnot(None)
        if watermark.embedding_updated_at is None
        else or_(
            snapshot.corpus_embedding_updated_at.is_(None),
            snapshot.corpus_embedding_updated_at != watermark.embedding_updated_at,
        )
    )
    return (
        db.query(Project)
        .outerjoin(
            snapshot,
            and_(
                snapshot.target_project_id == Project.id,
                snapshot.embedding_model == Project.embedding_model,
                snapshot.target_embedding_updated_at == Project.embedding_updated_at,
                snapshot.same_category_only.is_(True),
                snapshot.min_similarity_bucket
                == read_model.min_similarity_bucket(0.15),
            ),
        )
        .filter(
            Project.status.in_(ACTIVE_PROJECT_STATUSES),
            or_(Project.deadline.is_(None), Project.deadline > utc_now()),
            Project.embedding_model.isnot(None),
            Project.embedding_updated_at.isnot(None),
            Project.embedding_payload.isnot(None),
            Project.embedding_payload != "[]",
            or_(
                snapshot.id.is_(None),
                snapshot.corpus_embedding_count != watermark.embedding_count,
                time_mismatch,
                snapshot.edge_count != edge_count,
            ),
        )
        .order_by(Project.id.asc())
        .limit(limit)
        .all()
    )


def stage_active_similarity_projection_backfill(
    db: Session, *, read_model, outbox, limit: int = 100
) -> SimilarityProjectionBackfillResult:
    resolved_limit = max(1, int(limit or 100))
    projects = _backfill_candidates(db, read_model, resolved_limit)
    project_ids: list[int] = []
    event_ids: list[int] = []
    for project in projects:
        if read_model.embedding_state(project).status != "ready":
            continue
        event = outbox.append_embedding_ready_event(
            db, project, reactivate_completed=True
        )
        if event is None:
            continue
        project_ids.append(int(project.id))
        event_ids.append(int(event.id))
    db.flush()
    return SimilarityProjectionBackfillResult(
        selected_count=len(projects),
        staged_count=len(event_ids),
        limit=resolved_limit,
        project_ids=project_ids,
        event_ids=event_ids,
    )
