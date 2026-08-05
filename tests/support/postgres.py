"""Postgres 테스트 티어 지원 — 일회용 데이터베이스 생성/정리.

이 저장소의 유닛 스위트는 전부 SQLite 에서 돈다(루트 ``conftest.py``). 프로덕션은
``pgvector/pgvector:pg16`` 이라, SQLite 가 재현하지 못하는 dialect 계약은 어떤 자동
테스트로도 검증되지 않는다(#212 — 엔티티 ``SELECT DISTINCT`` 가 Postgres json 컬럼에서
죽는 갭을 SQLite 는 통과시키고 라이브가 잡았다).

여기 있는 헬퍼는 ``postgres`` 마커가 붙은 소수 테스트만 실제 Postgres 로 보낸다.
연결 정보는 ``TEST_POSTGRES_URL`` 환경변수로 **주입**하고, 없으면 그 테스트들은
skip 된다 — 기존 SQLite 스위트와 개발자 로컬 실행에 영향이 없어야 하기 때문이다.

.. warning::
   ``TEST_POSTGRES_URL`` 은 반드시 **일회용 인스턴스**를 가리켜야 한다. 아래 헬퍼는
   데이터베이스를 만들고 지우며 테이블을 TRUNCATE 한다. compose 의 ``db`` 서비스
   (운영 데이터)를 절대 가리키지 않는다.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from uuid import uuid4

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from app.core.constants import POSTGRES_DRIVERNAME
from app.core.database import Base

POSTGRES_URL_ENV = "TEST_POSTGRES_URL"
POSTGRES_REQUIRED_ENV = "REQUIRE_POSTGRES_TIER"
POSTGRES_MARKER = "postgres"
PGVECTOR_EXTENSION_DDL = "CREATE EXTENSION IF NOT EXISTS vector"
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "y", "on"})

POSTGRES_SKIP_REASON = (
    f"{POSTGRES_URL_ENV} is not set — the postgres tier needs a disposable "
    "PostgreSQL+pgvector instance (for example "
    "`docker run --rm -d -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test "
    "-e POSTGRES_DB=test -p 55432:5432 pgvector/pgvector:pg16`). "
    "Never point it at the compose `db` service."
)
POSTGRES_REQUIRED_FAILURE = (
    f"{POSTGRES_REQUIRED_ENV} is set but {POSTGRES_URL_ENV} is missing — the "
    "postgres tier would have skipped every test and reported success."
)


def _render(url: URL) -> str:
    """비밀번호를 살려서 문자열로 만든다.

    ``str(URL)`` 은 비밀번호를 ``***`` 로 가리므로(SQLAlchemy 2.0 기본값) 그대로
    쓰면 인증이 조용히 실패한다. 여기 값은 일회용 테스트 인스턴스 자격증명이고
    로그로 나가지 않는다.
    """
    return url.render_as_string(hide_password=False)


def normalize_postgres_url(url: str) -> str:
    """URL 을 이 저장소가 실제로 설치하는 psycopg3 드라이버로 고정한다.

    SQLAlchemy 2.0 은 드라이버 없는 ``postgresql://`` 를 psycopg2 로 해석하는데
    이 저장소는 ``psycopg[binary]`` 만 설치한다(``requirements/runtime.txt``). CI 든
    로컬이든 어떤 표기로 넘겨도 같은 드라이버로 붙게 해서, 티어 전체가 드라이버
    표기 하나로 갈라지지 않게 한다.
    """
    return _render(make_url(url).set(drivername=POSTGRES_DRIVERNAME))


def configured_postgres_url() -> str | None:
    """``TEST_POSTGRES_URL`` 이 있으면 정규화해서, 없으면 ``None``."""
    raw = (os.environ.get(POSTGRES_URL_ENV) or "").strip()
    return normalize_postgres_url(raw) if raw else None


def postgres_tier_required() -> bool:
    """CI 처럼 이 티어가 **반드시** 실행돼야 하는 환경인지.

    연결 정보를 잘못 준 CI 잡은 전부 skip 하고도 초록이 된다 — 이 저장소가 겪은
    escape 와 같은 종류의 침묵이다. 이 플래그가 켜지면 skip 대신 실패시킨다.
    """
    return (os.environ.get(POSTGRES_REQUIRED_ENV) or "").strip().lower() in (
        TRUTHY_ENV_VALUES
    )


@contextmanager
def disposable_database(admin_url: str, *, prefix: str) -> Iterator[str]:
    """고유 이름의 빈 데이터베이스를 만들고, 끝나면 지운다.

    이름에 pid 와 uuid 를 함께 넣어 같은 인스턴스를 공유하는 동시 실행(로컬 병렬
    pytest, CI 매트릭스)이 서로의 데이터베이스를 밟지 않게 한다. ``WITH (FORCE)``
    는 남은 세션이 있어도 정리가 실패하지 않게 한다(PostgreSQL 13+).
    """
    name = f"{prefix}_{os.getpid()}_{uuid4().hex[:8]}"
    admin_engine = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        yield _render(make_url(admin_url).set(database=name))
    finally:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin_engine.dispose()


def enable_pgvector(engine: Engine) -> None:
    """``VECTOR(384)`` 컬럼을 만들 수 있도록 확장을 켠다.

    프로덕션에서 이 전제를 세우는 것은 데이터베이스 초기화 시 1회 실행되는
    ``docker/postgres/init/01-enable-pgvector.sql`` 이다.
    :func:`app.services.project_similarity_schema.ensure_project_vector_schema`
    도 같은 DDL 을 내지만, 그 호출부(lifespan 부트스트랩)는
    ``ENVIRONMENT`` 가 production/staging 이면 아예 실행되지 않는다
    (:func:`app.core.schema_bootstrap.startup_schema_bootstrap_enabled`).
    여기서는 그 두 경로 **밖**에서 스키마를 세우는 픽스처가 쓴다.
    """
    with engine.begin() as connection:
        connection.exec_driver_sql(PGVECTOR_EXTENSION_DDL)


def truncate_all_tables(engine: Engine) -> None:
    """모델이 선언한 모든 테이블을 비운다(테스트 간 격리).

    DROP/CREATE 반복보다 훨씬 싸고, 세션을 여러 개 커밋하는 동시성 테스트에도
    쓸 수 있다(외부 트랜잭션 롤백 방식은 그 경우 성립하지 않는다).
    """
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if not table_names:
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"
        )
