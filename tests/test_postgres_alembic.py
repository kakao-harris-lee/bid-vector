"""마이그레이션을 실제 Postgres 에 적용하는 스모크 + 타입 수준 드리프트 가드.

배경
----
``tests/test_schema_drift.py`` 는 두 스키마 경로(모델 ``create_all`` vs
``alembic upgrade head``)를 **SQLite** 위에서 재현하고 (table, column) **이름**
수준으로만 비교한다. 그 파일의 주석이 밝히듯 SQLite 는 pgvector ``VECTOR(384)``
나 Postgres 타입을 충실히 반영하지 못하기 때문이다.

그래서 남는 공백이 두 가지다.

* 마이그레이션 DDL 자체가 Postgres 에서 도는지는 배포 순간에 처음 확인된다.
  프로덕션 api 컨테이너는 ``alembic upgrade head && uvicorn`` 으로 기동한다.
* 이름이 같아도 타입이 다를 수 있다(``json`` vs ``jsonb``, ``vector(384)`` vs
  다른 차원, 길이 없는 ``varchar``). 이름 비교는 그것을 통과시킨다.

이 모듈은 두 공백을 실제 Postgres 에서 메운다. 마이그레이션 소유 객체 목록은
``tests/test_schema_drift.py`` 에서 그대로 가져와, 한 곳만 갱신하면 두 가드가 함께
움직이게 한다.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, MetaData, Table, create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

import app.core.config as app_config
from app.core.database import Base
from app.models import models  # noqa: F401  -- registers all tables on Base.metadata
from tests.support.alembic_config import make_alembic_config
from tests.support.postgres import enable_pgvector
from tests.test_schema_drift import (
    ALEMBIC_INTERNAL_TABLES,
    MIGRATION_ADDED_COLUMNS,
    MIGRATION_OWNED_TABLES,
)

pytestmark = pytest.mark.postgres

COLUMN_TYPES_SQL = text(
    """
    SELECT c.relname AS table_name,
           a.attname AS column_name,
           format_type(a.atttypid, a.atttypmod) AS rendered_type
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND a.attnum > 0
      AND NOT a.attisdropped
    """
)


@contextmanager
def _database_url_pointed_at(url: str) -> Iterator[None]:
    """``alembic/env.py`` 가 읽는 ``settings.DATABASE_URL`` 을 잠시 바꾼다.

    env.py 는 로드 시점에 ``settings.DATABASE_URL`` 로 ``sqlalchemy.url`` 을 덮으므로,
    in-process 업그레이드를 특정 데이터베이스로 보내는 seam 은 이 값 하나다
    (``tests/test_schema_drift.py`` 가 SQLite 에서 쓰는 것과 같은 방식).
    """
    original = app_config.settings.DATABASE_URL
    app_config.settings.DATABASE_URL = url
    try:
        yield
    finally:
        app_config.settings.DATABASE_URL = original


def _upgrade_head(url: str) -> None:
    with _database_url_pointed_at(url):
        command.upgrade(make_alembic_config(), "head")


def _create_premigration_baseline(engine: Engine) -> None:
    """마이그레이션 이전 스키마(모델 - 마이그레이션 소유 객체)를 만든다.

    ``alembic upgrade head`` 가 create_all 이 아니라 **마이그레이션 코드로** 그
    객체들을 다시 만들게 하려는 것이다. 마이그레이션이 추가한 컬럼을 참조하는
    제약/인덱스는 baseline 에서 의도적으로 빠진다.
    """
    baseline = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in MIGRATION_OWNED_TABLES:
            continue
        dropped = MIGRATION_ADDED_COLUMNS.get(table.name, set())
        columns = [
            column._copy() for column in table.columns if column.name not in dropped
        ]
        Table(table.name, baseline, *columns)
    baseline.create_all(bind=engine)


def _column_types(engine: Engine) -> dict[tuple[str, str], str]:
    with engine.connect() as connection:
        rows = connection.execute(COLUMN_TYPES_SQL).all()
    return {
        (row.table_name, row.column_name): row.rendered_type
        for row in rows
        if row.table_name not in ALEMBIC_INTERNAL_TABLES
    }


def _prepared_engine(url: str) -> Engine:
    """pgvector 확장이 켜진 엔진 (프로덕션 db 초기화 스크립트와 같은 전제)."""
    engine = create_engine(url, poolclass=NullPool)
    enable_pgvector(engine)
    return engine


def test_alembic_upgrade_head_applies_on_postgres(blank_postgres_url):
    """마이그레이션이 실제 Postgres 에서 끝까지 돌고 소유 테이블을 만든다."""
    engine = _prepared_engine(blank_postgres_url)
    try:
        _create_premigration_baseline(engine)

        _upgrade_head(blank_postgres_url)

        with engine.connect() as connection:
            existing = {
                row.table_name
                for row in connection.execute(
                    text(
                        "SELECT tablename AS table_name FROM pg_tables"
                        " WHERE schemaname = 'public'"
                    )
                ).all()
            }
            stamped = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()

        missing = MIGRATION_OWNED_TABLES - existing
        assert not missing, f"migration path did not create {sorted(missing)}"
        assert stamped == ScriptDirectory.from_config(make_alembic_config()).get_current_head()
    finally:
        engine.dispose()


def test_blank_database_is_not_buildable_by_migrations_alone(blank_postgres_url):
    """KNOWN GAP — 마이그레이션만으로는 빈 데이터베이스를 세울 수 없다.

    이것은 바람직한 성질이 아니라 **현재 상태를 명시**하는 가드다. 마이그레이션은
    과거 ``create_all`` 베이스라인 위의 증분 패치라서, 아무것도 없는 데이터베이스에
    ``alembic upgrade head`` 를 걸면 첫 ``ALTER TABLE`` 에서 죽는다. 프로덕션 api 는
    ``alembic upgrade head && uvicorn`` 으로 기동하고 ``ENVIRONMENT=production``
    에서는 lifespan 부트스트랩이 꺼지므로, 볼륨을 새로 만든 재해복구 기동은 이
    지점에서 실패한다.

    따라서 배포 전제는 "데이터베이스는 모델 스키마로 한 번 시드돼 있어야 한다"이다.
    누군가 베이스라인 마이그레이션을 추가해 그 전제를 없애면 이 테스트가 실패하고,
    그때 운영 문서와 함께 성공 단언으로 교체해야 한다.
    """
    engine = _prepared_engine(blank_postgres_url)
    try:
        with pytest.raises(ProgrammingError) as excinfo:
            _upgrade_head(blank_postgres_url)
    finally:
        engine.dispose()

    assert "does not exist" in str(excinfo.value)


def test_model_and_migration_paths_agree_on_column_types(blank_postgres_url_factory):
    """두 스키마 경로가 Postgres **타입** 수준에서도 일치한다.

    이름 수준 비교(``tests/test_schema_drift.py``)를 통과하면서도 타입이 갈라지는
    경우 — 마이그레이션이 ``sa.JSON`` 대신 ``JSONB`` 를 쓰거나, 길이 없는
    ``String`` 을 쓰거나, 벡터 차원이 다른 경우 — 를 여기서 잡는다.
    """
    model_engine = _prepared_engine(blank_postgres_url_factory())
    migration_url = blank_postgres_url_factory()
    migration_engine = _prepared_engine(migration_url)
    try:
        Base.metadata.create_all(bind=model_engine)
        _create_premigration_baseline(migration_engine)
        _upgrade_head(migration_url)

        model_types = _column_types(model_engine)
        migration_types = _column_types(migration_engine)
    finally:
        model_engine.dispose()
        migration_engine.dispose()

    drift = {
        key: {"model": model_types[key], "migration": migration_types[key]}
        for key in set(model_types) & set(migration_types)
        if model_types[key] != migration_types[key]
    }

    assert not drift, (
        "Model <-> migration Postgres column type drift. The name-level guard "
        "in tests/test_schema_drift.py cannot see this class of divergence. "
        f"Drift: {drift}"
    )


def test_embedding_column_survives_the_migration_path(blank_postgres_url):
    """마이그레이션 경로로 세운 스키마에서도 임베딩 컬럼이 ``vector(384)`` 다."""
    engine = _prepared_engine(blank_postgres_url)
    try:
        _create_premigration_baseline(engine)
        _upgrade_head(blank_postgres_url)

        rendered = _column_types(engine)[("projects", "embedding")]
    finally:
        engine.dispose()

    assert rendered == "vector(384)"
