"""Freshness policy: the rule itself, and that SQL and Python render it alike.

The 2026-08-13 runaway happened because "is this projection still usable?" was
answered by *exact* corpus-watermark equality. One embedding landing in a
category invalidated every snapshot in that category at once, so the candidate
set never drained and the backfill restaged the same targets forever.

Two failure modes are pinned here:

1. The policy itself — a single row must not invalidate a large corpus, a real
   drift must, and a snapshot must not outlive its maximum age.
2. Divergence — the backfill decides in SQL and the read model decides in
   Python. If those two answers differ, targets the backfill calls fresh render
   as ``stale`` forever (or vice versa: an endless restage loop).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.constants import (
    SIMILARITY_PROJECTION_CORPUS_DRIFT_MATERIALITY_RATIO,
    SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS,
)
from app.core.time import utc_now
from app.domain.projection_freshness import (
    DEFAULT_PROJECTION_FRESHNESS_POLICY,
    ProjectionFreshnessPolicy,
    corpus_drift_is_material,
    corpus_drift_threshold_rows,
    projection_is_fresh,
    snapshot_age_exceeds_max,
)
from app.models.models import Project, ProjectSimilaritySnapshot
from app.services.similarity_projection_backfill import _backfill_candidates

from tests.test_similarity_projection_backfill_scope import (
    _StubReadModel,
    _make_project,
    _make_snapshot,
)

# (snapshot_corpus_count, corpus_count, age_seconds, expected_fresh)
FRESHNESS_CASES = [
    # A single row must not invalidate a large corpus — this is the runaway.
    (35310, 35311, 60.0, True),
    (35310, 35135, 60.0, True),  # 175 rows drift, threshold is 176.55
    (35310, 35133, 60.0, False),  # 177 rows drift clears the threshold
    (35310, 35487, 60.0, False),  # growth is material in either direction
    # Small corpora: the ratio floors at one row, so any change is material.
    # That floor governs everything up to N=200 (0.005 × 200 = 1.0 exactly); the
    # ratio only starts absorbing a single row from N=201 up. The live embedding
    # model sits well past that (corpus ~3,593 → 18 rows), the retired one further
    # still (68,827 → 344 rows).
    (10, 11, 60.0, False),
    (10, 10, 60.0, True),
    (199, 200, 60.0, False),
    (200, 201, 60.0, False),  # last point where the one-row floor governs
    (201, 202, 60.0, True),  # first point where the ratio absorbs one row
    (3593, 3594, 60.0, True),  # live model: +1 of ~18 rows is not material
    (3593, 3611, 60.0, False),  # live model: 18 rows clears the threshold
    # An unchanged corpus still expires at the maximum age.
    (35310, 35310, float(SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS) - 1, True),
    (35310, 35310, float(SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS) + 1, False),
    # An empty corpus that stays empty is fresh.
    (0, 0, 60.0, True),
    (0, 1, 60.0, False),
]


@pytest.mark.parametrize(
    "snapshot_count,corpus_count,age_seconds,expected", FRESHNESS_CASES
)
def test_freshness_policy_value_table(
    snapshot_count: int, corpus_count: int, age_seconds: float, expected: bool
) -> None:
    assert (
        projection_is_fresh(
            snapshot_corpus_count=snapshot_count,
            corpus_count=corpus_count,
            snapshot_age_seconds=age_seconds,
        )
        is expected
    )


def test_one_new_embedding_does_not_invalidate_a_large_corpus() -> None:
    """The exact regression: +1 row in a 35k corpus is not a reason to recompute."""
    assert not corpus_drift_is_material(
        snapshot_corpus_count=35310, corpus_count=35311
    )


def test_threshold_floors_at_one_row() -> None:
    assert corpus_drift_threshold_rows(0) == 1.0
    assert corpus_drift_threshold_rows(100) == 1.0
    assert corpus_drift_threshold_rows(35310) == pytest.approx(
        35310 * SIMILARITY_PROJECTION_CORPUS_DRIFT_MATERIALITY_RATIO
    )


def test_age_bound_catches_rechurn_that_keeps_the_count_identical() -> None:
    """Re-embedding an existing row leaves the count alone; only age catches it."""
    assert not corpus_drift_is_material(snapshot_corpus_count=500, corpus_count=500)
    assert snapshot_age_exceeds_max(
        float(SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS) + 1
    )


def test_policy_is_injectable() -> None:
    strict = ProjectionFreshnessPolicy(
        corpus_drift_materiality_ratio=0.0, max_snapshot_age_seconds=0
    )
    assert not projection_is_fresh(
        snapshot_corpus_count=100,
        corpus_count=101,
        snapshot_age_seconds=1.0,
        policy=strict,
    )
    assert projection_is_fresh(
        snapshot_corpus_count=100,
        corpus_count=100,
        snapshot_age_seconds=1.0,
        policy=DEFAULT_PROJECTION_FRESHNESS_POLICY,
    )


def _sql_says_fresh(
    db, *, snapshot_corpus_count: int, corpus_count: int, age_seconds: float
) -> bool:
    """Run the backfill's SQL predicate for one target with a built corpus.

    The corpus is created as real rows so the query's own grouped aggregate
    produces ``corpus_count``; the target is one of them.
    """
    target = _make_project(db, title="교차 검증 대상", category="construction")
    for index in range(corpus_count - 1):
        _make_project(db, title=f"코퍼스 {index}", category="construction")
    _make_snapshot(
        db,
        target,
        corpus_embedding_count=snapshot_corpus_count,
        corpus_embedding_updated_at=target.embedding_updated_at,
        computed_at=utc_now() - timedelta(seconds=age_seconds),
    )
    candidates = _backfill_candidates(db, _StubReadModel(), 500)
    return int(target.id) not in {int(p.id) for p in candidates}


# Small corpora only: the SQL side has to materialize every corpus row.
SQL_CROSS_CHECK_CASES = [
    (50, 50, 60.0),
    (50, 51, 60.0),
    (50, 49, 60.0),
    (1, 1, 60.0),
    (200, 200, 60.0),
    (200, 201, 60.0),
    (50, 50, float(SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS) + 60),
    (50, 50, float(SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS) - 60),
]


@pytest.mark.parametrize("snapshot_count,corpus_count,age_seconds", SQL_CROSS_CHECK_CASES)
def test_sql_predicate_matches_the_python_kernel(
    test_db, snapshot_count: int, corpus_count: int, age_seconds: float
) -> None:
    """One rule, two renderings — they must answer identically."""
    python_answer = projection_is_fresh(
        snapshot_corpus_count=snapshot_count,
        corpus_count=corpus_count,
        snapshot_age_seconds=age_seconds,
    )
    sql_answer = _sql_says_fresh(
        test_db,
        snapshot_corpus_count=snapshot_count,
        corpus_count=corpus_count,
        age_seconds=age_seconds,
    )

    assert sql_answer is python_answer


def test_backfill_leaves_a_target_alone_after_one_new_embedding(test_db):
    """End to end: the treadmill step that used to restage the whole category."""
    target = _make_project(test_db, title="러닝머신 회귀", category="construction")
    for index in range(400):
        _make_project(test_db, title=f"코퍼스 {index}", category="construction")
    corpus_count = (
        test_db.query(Project)
        .filter(Project.category == "construction")
        .count()
    )
    _make_snapshot(
        test_db,
        target,
        corpus_embedding_count=corpus_count,
        corpus_embedding_updated_at=target.embedding_updated_at,
    )
    assert int(target.id) not in {
        int(p.id) for p in _backfill_candidates(test_db, _StubReadModel(), 500)
    }

    _make_project(test_db, title="신규 임베딩 1건", category="construction")

    assert int(target.id) not in {
        int(p.id) for p in _backfill_candidates(test_db, _StubReadModel(), 500)
    }


def test_backfill_restages_a_target_after_material_corpus_growth(test_db):
    target = _make_project(test_db, title="물질적 증가", category="construction")
    for index in range(400):
        _make_project(test_db, title=f"코퍼스 {index}", category="construction")
    corpus_count = (
        test_db.query(Project)
        .filter(Project.category == "construction")
        .count()
    )
    _make_snapshot(
        test_db,
        target,
        corpus_embedding_count=corpus_count,
        corpus_embedding_updated_at=target.embedding_updated_at,
    )
    material_rows = int(corpus_drift_threshold_rows(corpus_count)) + 1
    for index in range(material_rows):
        _make_project(test_db, title=f"신규 {index}", category="construction")

    assert int(target.id) in {
        int(p.id) for p in _backfill_candidates(test_db, _StubReadModel(), 500)
    }


def test_backfill_restages_a_target_past_the_maximum_age(test_db):
    target = _make_project(test_db, title="수명 초과", category="construction")
    _make_snapshot(
        test_db,
        target,
        corpus_embedding_count=1,
        corpus_embedding_updated_at=target.embedding_updated_at,
        computed_at=utc_now()
        - timedelta(seconds=SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS + 60),
    )

    assert int(target.id) in {
        int(p.id) for p in _backfill_candidates(test_db, _StubReadModel(), 500)
    }


def test_snapshot_integrity_still_beats_freshness(test_db):
    """A snapshot missing the edges it claims is restaged no matter how fresh."""
    target = _make_project(test_db, title="엣지 불일치", category="construction")
    snapshot = _make_snapshot(
        test_db,
        target,
        corpus_embedding_count=1,
        corpus_embedding_updated_at=target.embedding_updated_at,
        edge_count=0,
    )
    snapshot.edge_count = 5
    test_db.flush()

    assert int(target.id) in {
        int(p.id) for p in _backfill_candidates(test_db, _StubReadModel(), 500)
    }


def test_read_model_serves_a_snapshot_the_backfill_considers_fresh(test_db):
    """Read and drain must agree, or the UI shows an emptiness nothing repairs."""
    from app.services.project_similarity import ProjectSimilarityService

    target = _make_project(test_db, title="읽기 정합", category="construction")
    for index in range(400):
        _make_project(test_db, title=f"코퍼스 {index}", category="construction")
    corpus_count = (
        test_db.query(Project)
        .filter(Project.category == "construction")
        .count()
    )
    _make_snapshot(
        test_db,
        target,
        corpus_embedding_count=corpus_count,
        corpus_embedding_updated_at=target.embedding_updated_at,
        edge_count=0,
    )
    _make_project(test_db, title="신규 임베딩 1건", category="construction")
    read_model = ProjectSimilarityService()._read_model
    snapshot = (
        test_db.query(ProjectSimilaritySnapshot)
        .filter(ProjectSimilaritySnapshot.target_project_id == int(target.id))
        .one()
    )

    backfill_wants_recompute = int(target.id) in {
        int(p.id) for p in _backfill_candidates(test_db, _StubReadModel(), 500)
    }

    assert read_model._snapshot_is_current(test_db, target, snapshot)
    assert not backfill_wants_recompute
