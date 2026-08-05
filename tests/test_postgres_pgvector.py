"""pgvector VECTOR(384) 불변식과 데이터베이스측 유사도 검색 경로.

배경
----
``pgvector 384 유지`` 는 CLAUDE.md 의 불변 원칙이지만, SQLite 스위트에서는 아무것도
그것을 검증하지 못한다. SQLite 에는 ``vector`` 타입이 없어서
:class:`app.core.vector.VECTOR` 는 컬럼 스펙 문자열만 내는 폴백으로 동작하고,
:meth:`~app.services.project_similarity.ProjectSimilarityService._can_query_pgvector`
가 항상 False 라 ``_search_with_postgres`` (프로덕션이 실제로 타는 코사인 거리
쿼리)는 **한 번도 실행되지 않는다**.

여기서는 실제 Postgres 에 대고 차원 불변식(모델 선언 / 저장 / 거부)과 그 검색
경로를 함께 고정한다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.pool import NullPool

from app.core.database import Base
from app.core.vector import PGVECTOR_SQLALCHEMY_AVAILABLE
from app.models.models import Project
from app.services.project_similarity import ProjectSimilarityService
from app.services.project_similarity_constants import PROJECT_VECTOR_DIMENSIONS
from app.services.project_similarity_schema import ensure_project_vector_schema

pytestmark = pytest.mark.postgres

EXPECTED_VECTOR_DIMENSIONS = 384
HNSW_INDEX_NAME = "ix_projects_embedding_hnsw"
# ``ensure_project_vector_schema`` 가 레거시 데이터베이스에 덧붙이는 컬럼들. 그
# 경로를 재현하려면 baseline 테이블에서 이것들을 빼고 시작해야 한다.
VECTOR_SCHEMA_COLUMNS = {
    "semantic_text",
    "embedding_payload",
    "embedding_model",
    "embedding_updated_at",
    "embedding",
}

COLUMN_TYPE_SQL = text(
    """
    SELECT format_type(a.atttypid, a.atttypmod) AS rendered_type
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = :table_name
      AND a.attname = :column_name
      AND NOT a.attisdropped
    """
)


def _rendered_column_type(session_or_connection, table: str, column: str) -> str:
    row = session_or_connection.execute(
        COLUMN_TYPE_SQL, {"table_name": table, "column_name": column}
    ).first()
    assert row is not None, f"{table}.{column} not found in the live database"
    return str(row.rendered_type)


def _project_with_embedding(session, *, title: str, category: str = "construction"):
    """공고 1건을 만들고 프로덕션 writer 로 임베딩을 채운다."""
    project = Project(
        title=title,
        description=f"{title} 설명",
        budget_estimate=100_000_000.0,
        category=category,
        status="open",
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    ProjectSimilarityService().refresh_project_embedding(session, project)
    session.commit()
    return project


def test_embedding_column_is_native_vector_384(postgres_session):
    """모델이 만든 컬럼이 실제로 ``vector(384)`` 여야 한다 (폴백 타입이 아니라)."""
    assert PGVECTOR_SQLALCHEMY_AVAILABLE, "pgvector SQLAlchemy 타입이 설치돼 있어야 한다"
    assert PROJECT_VECTOR_DIMENSIONS == EXPECTED_VECTOR_DIMENSIONS

    rendered = _rendered_column_type(postgres_session, "projects", "embedding")

    assert rendered == f"vector({EXPECTED_VECTOR_DIMENSIONS})"


def test_embedding_roundtrip_preserves_dimensions(postgres_session):
    """384차원 벡터가 네이티브 컬럼에 저장되고 그대로 돌아온다."""
    project = _project_with_embedding(postgres_session, title="항만 준설 공사")

    postgres_session.expire(project)
    stored = postgres_session.get(Project, project.id)

    assert stored.embedding is not None, "pgvector 세션에서는 네이티브 컬럼이 채워져야 한다"
    assert len(list(stored.embedding)) == EXPECTED_VECTOR_DIMENSIONS


def test_orm_write_rejects_wrong_dimension(postgres_session):
    """차원이 다른 벡터는 ORM 바인딩 단계에서 이미 막힌다 (1차 방어)."""
    project = Project(title="차원 불일치", category="construction", status="open")
    project.embedding = [0.1] * (EXPECTED_VECTOR_DIMENSIONS - 1)
    postgres_session.add(project)

    with pytest.raises(StatementError) as excinfo:
        postgres_session.flush()

    assert "expected 384 dimensions" in str(excinfo.value)
    postgres_session.rollback()


def test_database_rejects_wrong_dimension_without_the_typed_bind(postgres_session):
    """타입 바인딩을 우회해도 데이터베이스가 384 를 강제한다 (2차 방어).

    위 테스트가 잡는 것은 pgvector SQLAlchemy 타입의 클라이언트측 검사다. 그 층을
    지나치는 경로(원시 SQL·백필 스크립트·수동 DDL)에서도 불변식이 남아 있는지는
    컬럼 타입이 실제로 ``vector(384)`` 로 선언돼 있을 때만 성립한다.
    """
    short_vector = "[" + ", ".join(["0.1"] * (EXPECTED_VECTOR_DIMENSIONS - 1)) + "]"

    with pytest.raises(DBAPIError) as excinfo:
        postgres_session.execute(
            text(
                "INSERT INTO projects (title, embedding)"
                " VALUES (:title, CAST(:embedding AS vector))"
            ),
            {"title": "차원 불일치 원시 삽입", "embedding": short_vector},
        )

    assert "expected 384 dimensions" in str(excinfo.value)
    postgres_session.rollback()


def test_similarity_search_uses_the_postgres_vector_path(postgres_session):
    """유사도 검색이 파이썬 폴백이 아니라 pgvector 쿼리로 실행된다."""
    service = ProjectSimilarityService()
    assert service._can_query_pgvector(postgres_session) is True

    target = _project_with_embedding(postgres_session, title="항만 준설 공사 1공구")
    _project_with_embedding(postgres_session, title="항만 준설 공사 2공구")

    response = service.find_similar_projects(postgres_session, target, limit=5)

    assert response["search_mode"] == "postgres_vector"


def test_similarity_search_ranks_nearer_project_first(postgres_session):
    """코사인 거리 정렬이 데이터베이스측에서 의미 있는 순서를 낸다."""
    service = ProjectSimilarityService()
    target = _project_with_embedding(postgres_session, title="항만 준설 공사 1공구")
    near = _project_with_embedding(postgres_session, title="항만 준설 공사 2공구")
    far = _project_with_embedding(postgres_session, title="전산 장비 유지보수 용역")

    results = service.find_similar_projects(postgres_session, target, limit=5)["results"]

    ranked_ids = [item["project_id"] for item in results]
    assert target.id not in ranked_ids
    assert ranked_ids.index(near.id) < ranked_ids.index(far.id)


def test_similarity_search_excludes_other_embedding_models(postgres_session):
    """다른 좌표계(임베딩 모델)의 벡터는 후보에서 빠진다 — pgvector 경로 판정.

    파이썬 폴백은 resolver 에서 같은 불변식을 지키지만, 여기서는 SQL 등치
    (``Project.embedding_model == query_model``)가 실제로 거는지를 본다.
    """
    service = ProjectSimilarityService()
    target = _project_with_embedding(postgres_session, title="항만 준설 공사 1공구")
    stranger = _project_with_embedding(postgres_session, title="항만 준설 공사 2공구")
    stranger.embedding_model = "other-embedding-model-v9"
    postgres_session.commit()

    results = service.find_similar_projects(postgres_session, target, limit=5)["results"]

    assert stranger.id not in [item["project_id"] for item in results]


def test_similarity_search_excludes_null_embedding_model(postgres_session):
    """모델이 NULL 인 레거시 행은 SQL 등치에서 매칭되지 않는다.

    #349 가 레퍼런스로 선언한 계약이다: 파이썬 폴백은 ``candidate_model !=
    query_model`` 로 NULL 을 걸러내고, pgvector 경로는 SQL ``=`` 가 NULL 을 절대
    매칭하지 않는다는 성질에 의존한다. 후자는 SQLite 에서 실행되지 않으므로 그
    의존이 성립하는지는 여기서만 확인된다 — 벡터는 남아 있고 모델만 비어 있는
    행(임베딩 모델 교체 이력이 있는 레거시 형태)이 조용히 채점되면 안 된다.
    """
    service = ProjectSimilarityService()
    target = _project_with_embedding(postgres_session, title="항만 준설 공사 1공구")
    vectorless_model = _project_with_embedding(
        postgres_session, title="항만 준설 공사 2공구"
    )
    vectorless_model.embedding_model = None
    postgres_session.commit()
    assert vectorless_model.embedding is not None, "벡터는 남아 있는 상태여야 한다"

    results = service.find_similar_projects(postgres_session, target, limit=5)["results"]

    assert vectorless_model.id not in [item["project_id"] for item in results]


def test_ensure_project_vector_schema_upgrades_a_legacy_database(blank_postgres_url):
    """레거시 데이터베이스 보정 경로가 확장·컬럼·HNSW 인덱스를 실제로 만든다.

    :func:`ensure_project_vector_schema` 는 ``dialect.name != "postgresql"`` 이면
    즉시 반환하므로 SQLite 스위트에서는 본문이 한 줄도 실행되지 않는다. 실제 커버
    범위는 **로컬/개발 기동**의 lifespan 부트스트랩이다 — production/staging 에서는
    :func:`~app.core.schema_bootstrap.startup_schema_bootstrap_enabled` 가 False 라
    호출되지 않고, 확장은 데이터베이스 초기화 SQL
    (``docker/postgres/init/01-enable-pgvector.sql``)이 이미 켜 둔다.

    그래서 확장을 **미리 켜지 않은** 데이터베이스에서 시작한다. 이 함수의
    ``CREATE EXTENSION IF NOT EXISTS vector`` 가 실제로 일하는지 보려는 것이고,
    확장조차 없는 상태가 곧 진짜 레거시 시나리오다.
    """
    engine = create_engine(blank_postgres_url, poolclass=NullPool)
    try:
        _create_legacy_projects_table(engine)
        assert not _pgvector_extension_installed(engine), "사전 조건: 확장 없음"

        ensure_project_vector_schema(engine)
        # 개발 기동마다 다시 호출되므로 두 번째 호출도 무해해야 한다.
        ensure_project_vector_schema(engine)

        with engine.connect() as connection:
            rendered = _rendered_column_type(connection, "projects", "embedding")
            index_method = connection.execute(
                text(
                    "SELECT am.amname FROM pg_class i"
                    " JOIN pg_index x ON x.indexrelid = i.oid"
                    " JOIN pg_am am ON am.oid = i.relam"
                    " WHERE i.relname = :index_name"
                ),
                {"index_name": HNSW_INDEX_NAME},
            ).scalar()

        assert rendered == f"vector({EXPECTED_VECTOR_DIMENSIONS})"
        assert index_method == "hnsw"
        assert _pgvector_extension_installed(engine)
    finally:
        engine.dispose()


def _pgvector_extension_installed(engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
        )


def _create_legacy_projects_table(engine) -> None:
    """벡터 컬럼이 아직 없던 시절의 ``projects`` 를 만든다."""
    legacy = MetaData()
    Table(
        "projects",
        legacy,
        *(
            column._copy()
            for column in Project.__table__.columns
            if column.name not in VECTOR_SCHEMA_COLUMNS
        ),
    )
    legacy.create_all(bind=engine)
    assert "projects" in Base.metadata.tables
