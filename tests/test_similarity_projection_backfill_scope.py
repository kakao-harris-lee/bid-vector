"""Candidate-scope regression guard for the similarity projection backfill.

The backfill decides *which* active targets get restaged. Two independent things
can silently break that decision:

1. The corpus watermark is scoped to ``(embedding_model, category)``, and a target
   with no usable category is scoped to its embedding model alone. Collapsing
   those two scopes makes unrelated categories invalidate each other (endless
   restaging) or makes real corpus growth invisible (never restaged).
2. The staleness rule itself (see ``app.core.constants`` materiality policy).

These tests pin (1) — the scope — so a query rewrite cannot move the candidate
set. They build snapshot rows directly instead of going through the embedding
model, because the scope question is pure SQL and does not need real vectors.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.time import utc_now
from app.models.models import (
    Project,
    ProjectSimilarityEdge,
    ProjectSimilaritySnapshot,
)
from app.services.similarity_projection_backfill import _backfill_candidates

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
OTHER_EMBEDDING_MODEL = "some-other-model-v2"


class _StubReadModel:
    """Only ``min_similarity_bucket`` participates in candidate selection."""

    def min_similarity_bucket(self, min_similarity: float) -> float:
        return round(float(min_similarity), 4)


def _make_project(
    db,
    *,
    title: str,
    category: str | None = "construction",
    embedding_model: str | None = EMBEDDING_MODEL,
    embedding_updated_at=None,
    status: str = "open",
) -> Project:
    """Create an active, embedding-ready project without loading a real model."""
    project = Project(
        title=title,
        description=f"{title} 설명",
        requirements="",
        budget_estimate=0.0,
        category=category,
        status=status,
        embedding_model=embedding_model,
        embedding_updated_at=embedding_updated_at or utc_now(),
        embedding_payload="[0.1, 0.2, 0.3]",
        semantic_text=title,
    )
    db.add(project)
    db.flush()
    return project


def _make_snapshot(
    db,
    project: Project,
    *,
    corpus_embedding_count: int,
    corpus_embedding_updated_at,
    edge_count: int = 0,
    computed_at=None,
) -> ProjectSimilaritySnapshot:
    """Attach a snapshot plus the matching number of real edge rows."""
    snapshot = ProjectSimilaritySnapshot(
        target_project_id=int(project.id),
        embedding_model=str(project.embedding_model),
        target_embedding_updated_at=project.embedding_updated_at,
        same_category_only=True,
        min_similarity_bucket=0.15,
        corpus_embedding_count=corpus_embedding_count,
        corpus_embedding_updated_at=corpus_embedding_updated_at,
        edge_count=edge_count,
        source="pgvector_hnsw",
        computed_at=computed_at or utc_now(),
    )
    db.add(snapshot)
    db.flush()
    for rank in range(1, edge_count + 1):
        db.add(
            ProjectSimilarityEdge(
                snapshot_id=int(snapshot.id),
                candidate_project_id=int(project.id),
                rank=rank,
                similarity_score=0.9,
            )
        )
    db.flush()
    return snapshot


def _corpus_watermark(db, *, embedding_model: str, category: str | None):
    """Recompute the watermark the same way the read model scopes it."""
    from sqlalchemy import func

    query = db.query(
        func.count(Project.embedding_updated_at),
        func.max(Project.embedding_updated_at),
    ).filter(Project.embedding_model == embedding_model)
    if category:
        query = query.filter(Project.category == category)
    count, updated_at = query.one()
    return int(count or 0), updated_at


def _candidate_ids(db, limit: int = 50) -> list[int]:
    return [int(p.id) for p in _backfill_candidates(db, _StubReadModel(), limit)]


def _is_candidate(db, project: Project) -> bool:
    """Membership of one target. Corpus rows added by a test are candidates too."""
    return int(project.id) in _candidate_ids(db)


def _snapshot_current(db, project: Project, *, edge_count: int = 0) -> None:
    """Give ``project`` a snapshot that exactly matches the live watermark."""
    count, updated_at = _corpus_watermark(
        db,
        embedding_model=str(project.embedding_model),
        category=project.category,
    )
    _make_snapshot(
        db,
        project,
        corpus_embedding_count=count,
        corpus_embedding_updated_at=updated_at,
        edge_count=edge_count,
    )


def test_target_without_snapshot_is_a_candidate(test_db):
    project = _make_project(test_db, title="스냅샷 없음")

    assert _is_candidate(test_db, project)


def test_target_with_current_snapshot_is_not_a_candidate(test_db):
    project = _make_project(test_db, title="현행 스냅샷")
    _snapshot_current(test_db, project)

    assert not _is_candidate(test_db, project)


def test_same_category_corpus_growth_makes_target_a_candidate(test_db):
    project = _make_project(test_db, title="같은 카테고리 증가")
    _snapshot_current(test_db, project)
    _make_project(test_db, title="같은 카테고리 신규", category="construction")

    assert _is_candidate(test_db, project)


def test_other_category_corpus_growth_leaves_target_alone(test_db):
    project = _make_project(test_db, title="다른 카테고리 무관")
    _snapshot_current(test_db, project)
    _make_project(test_db, title="용역 신규", category="service")

    assert not _is_candidate(test_db, project)


def test_other_embedding_model_corpus_growth_leaves_target_alone(test_db):
    project = _make_project(test_db, title="다른 모델 무관")
    _snapshot_current(test_db, project)
    _make_project(
        test_db,
        title="다른 모델 신규",
        category="construction",
        embedding_model=OTHER_EMBEDDING_MODEL,
    )

    assert not _is_candidate(test_db, project)


def test_uncategorized_target_is_scoped_to_the_whole_model_corpus(test_db):
    """A NULL category drops the category filter, so any category counts."""
    project = _make_project(test_db, title="카테고리 없음", category=None)
    _snapshot_current(test_db, project)
    _make_project(test_db, title="용역 신규", category="service")

    assert _is_candidate(test_db, project)


def test_empty_category_target_is_scoped_like_a_null_category(test_db):
    """``corpus_watermark`` treats "" as falsy, so it must be model-wide here too."""
    project = _make_project(test_db, title="빈 카테고리", category="")
    _snapshot_current(test_db, project)
    _make_project(test_db, title="용역 신규", category="service")

    assert _is_candidate(test_db, project)


def test_uncategorized_target_ignores_other_model_growth(test_db):
    project = _make_project(test_db, title="카테고리 없음 모델 스코프", category=None)
    _snapshot_current(test_db, project)
    _make_project(
        test_db,
        title="다른 모델 신규",
        category="service",
        embedding_model=OTHER_EMBEDDING_MODEL,
    )

    assert not _is_candidate(test_db, project)


def test_missing_edges_make_target_a_candidate(test_db):
    """A snapshot claiming edges it no longer has is not a usable projection."""
    project = _make_project(test_db, title="엣지 유실")
    _snapshot_current(test_db, project, edge_count=3)
    test_db.query(ProjectSimilarityEdge).delete()
    test_db.flush()

    assert _is_candidate(test_db, project)


def test_edge_count_matching_stored_count_is_not_a_candidate(test_db):
    project = _make_project(test_db, title="엣지 일치")
    _snapshot_current(test_db, project, edge_count=3)

    assert not _is_candidate(test_db, project)


def test_snapshot_for_a_different_embedding_version_does_not_count(test_db):
    """Re-embedding the target moves ``embedding_updated_at`` past the snapshot."""
    project = _make_project(test_db, title="타겟 재임베딩")
    _snapshot_current(test_db, project)
    project.embedding_updated_at = utc_now() + timedelta(seconds=1)
    test_db.flush()

    assert _is_candidate(test_db, project)


def test_closed_and_expired_targets_are_never_candidates(test_db):
    closed = _make_project(test_db, title="마감 상태", status="closed")
    expired = _make_project(test_db, title="마감 기한 지남")
    expired.deadline = utc_now() - timedelta(days=1)
    test_db.flush()

    assert _candidate_ids(test_db) == []
    assert closed.id is not None and expired.id is not None


def test_candidates_are_ordered_and_bounded_by_limit(test_db):
    projects = [_make_project(test_db, title=f"공고 {i}") for i in range(5)]
    expected = sorted(int(p.id) for p in projects)[:3]

    assert _candidate_ids(test_db, limit=3) == expected
