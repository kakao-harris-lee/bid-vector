"""Bounded staging for missing or stale active similarity projections."""

from datetime import timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from app.core.constants import ACTIVE_PROJECT_STATUSES
from app.core.time import utc_now
from app.domain.projection_freshness import (
    DEFAULT_PROJECTION_FRESHNESS_POLICY,
    ProjectionFreshnessPolicy,
)
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


def _corpus_size_aggregates() -> tuple[Subquery, Subquery]:
    """Group the corpus size once per scope instead of once per candidate row.

    The corpus size is a property of a ``(embedding_model, category)`` scope, not of
    the individual target, so recomputing it per row multiplies one grouped scan by
    the number of active targets. Two grouped passes cover the two scope levels:
    category-scoped targets join the first, model-wide targets the second.
    ``embedding_updated_at IS NOT NULL`` is what ``count`` already means here, so
    the filter only narrows the scan without changing the value.
    """
    by_category_corpus = aliased(Project)
    by_category = (
        select(
            by_category_corpus.embedding_model.label("embedding_model"),
            by_category_corpus.category.label("category"),
            func.count(by_category_corpus.embedding_updated_at).label(
                "embedding_count"
            ),
        )
        .where(
            by_category_corpus.embedding_model.isnot(None),
            by_category_corpus.embedding_updated_at.isnot(None),
        )
        .group_by(by_category_corpus.embedding_model, by_category_corpus.category)
        .subquery("corpus_size_by_category")
    )
    by_model_corpus = aliased(Project)
    by_model = (
        select(
            by_model_corpus.embedding_model.label("embedding_model"),
            func.count(by_model_corpus.embedding_updated_at).label("embedding_count"),
        )
        .where(
            by_model_corpus.embedding_model.isnot(None),
            by_model_corpus.embedding_updated_at.isnot(None),
        )
        .group_by(by_model_corpus.embedding_model)
        .subquery("corpus_size_by_model")
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


def _scoped_corpus_count(
    by_category: Subquery, by_model: Subquery
) -> ColumnElement[int]:
    """Take the corpus size of the scope this target actually projects against."""
    return case(
        (_model_wide_scope(), func.coalesce(by_model.c.embedding_count, 0)),
        else_=func.coalesce(by_category.c.embedding_count, 0),
    )


def _corpus_drift_is_material(
    snapshot,
    corpus_embedding_count: ColumnElement[int],
    policy: ProjectionFreshnessPolicy,
) -> ColumnElement[bool]:
    """SQL rendering of :func:`app.domain.projection_freshness.corpus_drift_is_material`.

    ``abs(delta) >= max(1, ratio * count)`` is expressed as the two comparisons it
    decomposes into, because ``GREATEST`` (PostgreSQL) and the two-argument
    ``max`` (SQLite) are not the same function. The row counts are integers, so
    ``abs(delta) >= 1`` is exactly ``delta != 0``.
    """
    drift = func.abs(corpus_embedding_count - snapshot.corpus_embedding_count)
    return and_(
        drift != 0,
        drift
        >= policy.corpus_drift_materiality_ratio * snapshot.corpus_embedding_count,
    )


def _snapshot_age_exceeds_max(
    snapshot, policy: ProjectionFreshnessPolicy
) -> ColumnElement[bool]:
    """SQL rendering of :func:`app.domain.projection_freshness.snapshot_age_exceeds_max`."""
    expires_before = utc_now() - timedelta(seconds=policy.max_snapshot_age_seconds)
    return snapshot.computed_at < expires_before


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
    policy: ProjectionFreshnessPolicy,
) -> ColumnElement[bool]:
    """A target needs work when it has no current projection, when that projection
    does not hold the edges it claims, or when it is no longer fresh enough.

    The first two are integrity questions and stay exact. Only the third is a
    freshness policy, and it is the shared one — see
    :mod:`app.domain.projection_freshness` for why exact watermark equality is not
    usable as a freshness rule.
    """
    return or_(
        snapshot.id.is_(None),
        snapshot.edge_count != func.coalesce(edge_counts.c.edge_count, 0),
        _corpus_drift_is_material(snapshot, corpus_embedding_count, policy),
        _snapshot_age_exceeds_max(snapshot, policy),
    )


def _staleness_first_order(snapshot) -> tuple[ColumnElement, ColumnElement]:
    """Oldest projection first, so a bounded batch rate bounds staleness.

    Ordering by ``Project.id`` starves instead. The candidate set is larger than
    one refresh cycle's throughput, and a target that was just refreshed becomes a
    candidate again long before the pointer would reach the end — so an id-ordered
    scan keeps re-serving the same low-id head and never reaches the tail. Those
    tail rows are the *newest* notices: they get one projection when their
    embedding lands, go stale at the max age, and no pipeline ever returns to
    them. That is the permanently-empty panel this policy exists to prevent, and
    the operator hits it on exactly the notices they are still able to bid on.

    Oldest-first turns the batch rate into a rotation: every target is revisited
    every ``active_targets / staging_rate`` hours, which is what makes the maximum
    snapshot age a bound rather than an aspiration.

    Targets with no current projection sort first (``NULLS FIRST``) — they have no
    projection at all, so they are strictly more urgent than any stale one.
    PostgreSQL orders NULLs last in ``ASC`` by default, so this must be explicit.
    """
    return (snapshot.computed_at.asc().nullsfirst(), Project.id.asc())


def _backfill_candidates(
    db,
    read_model,
    limit: int,
    policy: ProjectionFreshnessPolicy = DEFAULT_PROJECTION_FRESHNESS_POLICY,
):
    snapshot = aliased(ProjectSimilaritySnapshot)
    edge_counts = _snapshot_edge_counts()
    by_category, by_model = _corpus_size_aggregates()
    corpus_embedding_count = _scoped_corpus_count(by_category, by_model)
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
                snapshot, edge_counts, corpus_embedding_count, policy
            ),
        )
        .order_by(*_staleness_first_order(snapshot))
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
    blocked_project_ids: list[int] = []
    for project in projects:
        # A target that cannot be staged keeps its ``computed_at``, so under
        # oldest-first ordering it stays at the head of the next batch too. Record
        # it: enough of these and the rotation stops advancing at all.
        if read_model.embedding_state(project).status != "ready":
            blocked_project_ids.append(int(project.id))
            continue
        event = outbox.append_embedding_ready_event(
            db, project, reactivate_completed=True
        )
        if event is None:
            blocked_project_ids.append(int(project.id))
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
        blocked_project_ids=blocked_project_ids,
    )
