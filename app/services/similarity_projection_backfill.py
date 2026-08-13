"""Bounded staging for missing or stale active similarity projections."""

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from app.core.constants import ACTIVE_PROJECT_STATUSES
from app.core.time import utc_now
from app.models.models import (
    Project,
    ProjectSimilarityEdge,
    ProjectSimilaritySnapshot,
)
from app.schemas.similarity_runtime import SimilarityProjectionBackfillResult
from app.services.similarity_read_model import SIMILARITY_READ_MODEL_MIN_SIMILARITY


def _model_wide_scope() -> ColumnElement[bool]:
    """Targets without a usable category compare against their model corpus only.

    This mirrors :meth:`ProjectSimilarityReadModelService.corpus_watermark`, which
    drops the category filter for a falsy category — so an empty string is
    model-wide exactly like ``NULL`` is, and the two must not diverge here.
    """
    return or_(Project.category.is_(None), Project.category == "")


def _corpus_watermark_aggregates() -> tuple[Subquery, Subquery]:
    """Group the corpus watermark once per scope instead of once per candidate row.

    The watermark is a property of a ``(embedding_model, category)`` scope, not of
    the individual target, so recomputing it per row multiplies one grouped scan by
    the number of active targets. Two grouped passes cover the two scope levels:
    category-scoped targets join the first, model-wide targets the second.
    ``embedding_updated_at IS NOT NULL`` is redundant with ``count``/``max``
    ignoring NULLs, so it only narrows the scan without changing either value.
    """
    by_category_corpus = aliased(Project)
    by_category = (
        select(
            by_category_corpus.embedding_model.label("embedding_model"),
            by_category_corpus.category.label("category"),
            func.count(by_category_corpus.embedding_updated_at).label(
                "embedding_count"
            ),
            func.max(by_category_corpus.embedding_updated_at).label(
                "embedding_updated_at"
            ),
        )
        .where(
            by_category_corpus.embedding_model.isnot(None),
            by_category_corpus.embedding_updated_at.isnot(None),
        )
        .group_by(by_category_corpus.embedding_model, by_category_corpus.category)
        .subquery("corpus_watermark_by_category")
    )
    by_model_corpus = aliased(Project)
    by_model = (
        select(
            by_model_corpus.embedding_model.label("embedding_model"),
            func.count(by_model_corpus.embedding_updated_at).label("embedding_count"),
            func.max(by_model_corpus.embedding_updated_at).label(
                "embedding_updated_at"
            ),
        )
        .where(
            by_model_corpus.embedding_model.isnot(None),
            by_model_corpus.embedding_updated_at.isnot(None),
        )
        .group_by(by_model_corpus.embedding_model)
        .subquery("corpus_watermark_by_model")
    )
    return by_category, by_model


def _snapshot_edge_counts() -> Subquery:
    """Edges per snapshot in one grouped pass rather than a per-snapshot count."""
    return (
        select(
            ProjectSimilarityEdge.snapshot_id.label("snapshot_id"),
            func.count(ProjectSimilarityEdge.id).label("edge_count"),
        )
        .group_by(ProjectSimilarityEdge.snapshot_id)
        .subquery("snapshot_edge_counts")
    )


def _scoped_watermark_columns(
    by_category: Subquery, by_model: Subquery
) -> tuple[ColumnElement[int], ColumnElement]:
    """Select the watermark of the scope this target actually projects against."""
    model_wide = _model_wide_scope()
    count = case(
        (model_wide, func.coalesce(by_model.c.embedding_count, 0)),
        else_=func.coalesce(by_category.c.embedding_count, 0),
    )
    updated_at = case(
        (model_wide, by_model.c.embedding_updated_at),
        else_=by_category.c.embedding_updated_at,
    )
    return count, updated_at


def _watermark_time_mismatch(snapshot, corpus_updated_at) -> ColumnElement[bool]:
    return or_(
        and_(corpus_updated_at.is_(None), snapshot.corpus_embedding_updated_at.isnot(None)),
        and_(
            corpus_updated_at.isnot(None),
            or_(snapshot.corpus_embedding_updated_at.is_(None),
                snapshot.corpus_embedding_updated_at != corpus_updated_at),
        ),
    )


def _active_target_filters() -> tuple[ColumnElement[bool], ...]:
    """Targets the projection pipeline is allowed to stage work for at all."""
    return (
        Project.status.in_(ACTIVE_PROJECT_STATUSES),
        or_(Project.deadline.is_(None), Project.deadline > utc_now()),
        Project.embedding_model.isnot(None),
        Project.embedding_updated_at.isnot(None),
        Project.embedding_payload.isnot(None),
        Project.embedding_payload != "[]",
    )


def _current_version_snapshot_join(snapshot, read_model) -> ColumnElement[bool]:
    """Attach only the snapshot version keyed to this target's current embedding."""
    return and_(
        snapshot.target_project_id == Project.id,
        snapshot.embedding_model == Project.embedding_model,
        snapshot.target_embedding_updated_at == Project.embedding_updated_at,
        snapshot.same_category_only.is_(True),
        snapshot.min_similarity_bucket
        == read_model.min_similarity_bucket(SIMILARITY_READ_MODEL_MIN_SIMILARITY),
    )


def _needs_projection(
    snapshot,
    edge_counts: Subquery,
    corpus_embedding_count: ColumnElement[int],
    corpus_embedding_updated_at: ColumnElement,
) -> ColumnElement[bool]:
    """A target needs work when it has no current projection, or that projection
    no longer describes the corpus or the edges it claims to summarize."""
    return or_(
        snapshot.id.is_(None),
        snapshot.corpus_embedding_count != corpus_embedding_count,
        _watermark_time_mismatch(snapshot, corpus_embedding_updated_at),
        snapshot.edge_count != func.coalesce(edge_counts.c.edge_count, 0),
    )


def _backfill_candidates(db, read_model, limit: int):
    snapshot = aliased(ProjectSimilaritySnapshot)
    edge_counts = _snapshot_edge_counts()
    by_category, by_model = _corpus_watermark_aggregates()
    corpus_embedding_count, corpus_embedding_updated_at = _scoped_watermark_columns(
        by_category, by_model
    )
    return (
        db.query(Project)
        .outerjoin(snapshot, _current_version_snapshot_join(snapshot, read_model))
        .outerjoin(edge_counts, edge_counts.c.snapshot_id == snapshot.id)
        .outerjoin(
            by_category,
            and_(
                by_category.c.embedding_model == Project.embedding_model,
                by_category.c.category == Project.category,
            ),
        )
        .outerjoin(by_model, by_model.c.embedding_model == Project.embedding_model)
        .filter(
            *_active_target_filters(),
            _needs_projection(
                snapshot,
                edge_counts,
                corpus_embedding_count,
                corpus_embedding_updated_at,
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
