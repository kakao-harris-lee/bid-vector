"""투영 신선도 SQL 술어의 Postgres 판정 — SQLite 스위트가 볼 수 없는 축.

배경
----
신선도 규칙은 한 벌(:mod:`app.domain.projection_freshness`)이지만 렌더링은 두 벌이다:
백필은 SQL 술어로, read model 은 Python 으로 묻는다. SQL 쪽에는 방언이 갈릴 수 있는
조각이 모여 있다 — ``abs()``, ``CASE``, 정수 × float 비교(``drift >= 0.005 * count``),
그리고 카테고리 스코프를 고르는 ``case`` 의 NULL·빈 문자열 처리.

이 저장소는 같은 함정을 이미 밟았다: 엔티티 DISTINCT 가 Postgres json 컬럼에서만
죽었고 SQLite 스위트는 그것을 잡지 못했다(#212, 라이브에서야 드러남). 그래서 이 값표는
프로덕션 방언에서도 같은 답을 내는지 별도로 고정한다.

여기서 틀리면 증상은 예외가 아니라 **후보 집합의 조용한 이동**이다: 너무 넓으면
러닝머신이 돌아오고, 너무 좁으면 투영이 영원히 낡는다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.constants import SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS
from app.core.time import utc_now
from app.domain.projection_freshness import projection_is_fresh
from app.models.models import Project, ProjectSimilaritySnapshot
from app.services.similarity_projection_backfill import _backfill_candidates

pytestmark = pytest.mark.postgres

EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


class _StubReadModel:
    def min_similarity_bucket(self, min_similarity: float) -> float:
        return round(float(min_similarity), 4)


def _make_project(session, *, title: str, category: str | None) -> Project:
    project = Project(
        title=title,
        description=f"{title} 설명",
        category=category,
        status="open",
        embedding_model=EMBEDDING_MODEL,
        embedding_updated_at=utc_now(),
        embedding_payload="[0.1, 0.2, 0.3]",
        semantic_text=title,
    )
    session.add(project)
    session.flush()
    return project


def _snapshot(session, project: Project, *, corpus_count: int, age_seconds: float):
    snapshot = ProjectSimilaritySnapshot(
        target_project_id=int(project.id),
        embedding_model=str(project.embedding_model),
        target_embedding_updated_at=project.embedding_updated_at,
        same_category_only=True,
        min_similarity_bucket=0.15,
        corpus_embedding_count=corpus_count,
        corpus_embedding_updated_at=project.embedding_updated_at,
        edge_count=0,
        source="pgvector_hnsw",
        computed_at=utc_now() - timedelta(seconds=age_seconds),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


# (snapshot_corpus_count, live corpus rows to create, age_seconds)
CASES = [
    (50, 50, 60.0),
    (50, 51, 60.0),  # +1 row: must NOT be material
    (50, 49, 60.0),
    (200, 200, 60.0),
    # N=200 is where the ratio overtakes the one-row floor (0.005 × 200 = 1.0),
    # so a single row is already material here but not at N=50 above.
    (200, 201, 60.0),
    (200, 202, 60.0),
    (1, 1, 60.0),
    (50, 50, float(SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS) + 60),
    (50, 50, float(SIMILARITY_PROJECTION_MAX_SNAPSHOT_AGE_SECONDS) - 60),
]


@pytest.mark.parametrize("snapshot_count,corpus_rows,age_seconds", CASES)
def test_postgres_sql_predicate_matches_the_python_kernel(
    postgres_session_factory, snapshot_count: int, corpus_rows: int, age_seconds: float
) -> None:
    session = postgres_session_factory()
    try:
        target = _make_project(session, title="교차 검증 대상", category="construction")
        for index in range(corpus_rows - 1):
            _make_project(session, title=f"코퍼스 {index}", category="construction")
        _snapshot(
            session, target, corpus_count=snapshot_count, age_seconds=age_seconds
        )
        session.flush()

        sql_says_fresh = int(target.id) not in {
            int(p.id)
            for p in _backfill_candidates(session, _StubReadModel(), 1000)
        }
        python_says_fresh = projection_is_fresh(
            snapshot_corpus_count=snapshot_count,
            corpus_count=corpus_rows,
            snapshot_age_seconds=age_seconds,
        )

        assert sql_says_fresh is python_says_fresh
    finally:
        session.rollback()
        session.close()


def test_postgres_model_wide_scope_handles_null_and_empty_category(
    postgres_session_factory,
) -> None:
    """``NULL``/``''`` 카테고리는 모델 전체가 스코프다 — CASE 분기가 방언을 타지 않는지."""
    session = postgres_session_factory()
    try:
        null_target = _make_project(session, title="카테고리 없음", category=None)
        empty_target = _make_project(session, title="빈 카테고리", category="")
        for index in range(30):
            _make_project(session, title=f"용역 {index}", category="service")
        session.flush()
        model_wide_count = session.query(Project).count()
        for target in (null_target, empty_target):
            _snapshot(session, target, corpus_count=model_wide_count, age_seconds=60.0)
        session.flush()

        fresh = {
            int(p.id) for p in _backfill_candidates(session, _StubReadModel(), 1000)
        }

        # Both are scoped to the whole model corpus, which they match exactly.
        assert int(null_target.id) not in fresh
        assert int(empty_target.id) not in fresh
    finally:
        session.rollback()
        session.close()
