"""``postgres`` 마커 전용 픽스처.

루트 ``conftest.py`` 의 SQLite 픽스처(``test_db``/``client``)는 건드리지 않는다.
여기 있는 것들은 ``@pytest.mark.postgres`` 가 붙은 테스트만 쓰고, 그 테스트들은
``TEST_POSTGRES_URL`` 이 없으면 :func:`pytest_runtest_setup` 이 skip 한다. 마커가
없는 기존 3,900+ 테스트의 실행 경로에는 아무 영향이 없다.

격리 전략: 세션당 일회용 데이터베이스 1개(확장 + ``create_all``), 테스트마다
TRUNCATE. 세션을 팩토리로 주입하는 이유는 CAS 경합처럼 **독립 커밋 세션 2개**가
필요한 테스트 때문이다(외부 트랜잭션 롤백 격리로는 표현할 수 없다).
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Callable, Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import Base
from app.models import models  # noqa: F401  -- registers all tables on Base.metadata
from tests.support.postgres import (
    POSTGRES_MARKER,
    POSTGRES_REQUIRED_FAILURE,
    POSTGRES_SKIP_REASON,
    configured_postgres_url,
    disposable_database,
    enable_pgvector,
    postgres_tier_required,
    truncate_all_tables,
)

POSTGRES_DATABASE_PREFIX = "bidvector_pgtier"


def pytest_runtest_setup(item: pytest.Item) -> None:
    """마커 테스트는 Postgres 연결이 주입되지 않았으면 이유를 밝히고 skip 한다.

    단 ``REQUIRE_POSTGRES_TIER`` 가 켜진 환경(CI)에서는 skip 이 곧 침묵이므로
    실패시킨다 — 잡이 아무것도 검증하지 않고 초록이 되는 것을 막는다.
    """
    if POSTGRES_MARKER not in item.keywords or configured_postgres_url() is not None:
        return
    if postgres_tier_required():
        pytest.fail(POSTGRES_REQUIRED_FAILURE, pytrace=False)
    pytest.skip(POSTGRES_SKIP_REASON)


@pytest.fixture(scope="session")
def postgres_admin_url() -> str:
    url = configured_postgres_url()
    if url is None:  # pragma: no cover - pytest_runtest_setup skips first
        pytest.skip(POSTGRES_SKIP_REASON)
    return url


@pytest.fixture
def blank_postgres_url_factory(
    postgres_admin_url: str,
) -> Iterator[Callable[[], str]]:
    """빈 데이터베이스를 필요한 만큼 만드는 팩토리 (테스트 종료 시 전부 삭제).

    팩토리인 이유: 스키마 경로 두 개(모델 ``create_all`` vs 마이그레이션 재생)를
    나란히 세워 비교하려면 한 테스트에 빈 데이터베이스가 둘 필요하다.
    """
    with ExitStack() as stack:

        def new_database() -> str:
            return stack.enter_context(
                disposable_database(
                    postgres_admin_url, prefix=f"{POSTGRES_DATABASE_PREFIX}_blank"
                )
            )

        yield new_database


@pytest.fixture
def blank_postgres_url(blank_postgres_url_factory: Callable[[], str]) -> str:
    """스키마가 하나도 없는 새 데이터베이스 URL (마이그레이션/부트스트랩 스모크용)."""
    return blank_postgres_url_factory()


@pytest.fixture(scope="session")
def postgres_engine(postgres_admin_url: str) -> Iterator[Engine]:
    """모델 스키마가 올라간 일회용 데이터베이스 엔진(세션 스코프)."""
    with disposable_database(
        postgres_admin_url, prefix=POSTGRES_DATABASE_PREFIX
    ) as url:
        engine = create_engine(url, poolclass=NullPool)
        enable_pgvector(engine)
        Base.metadata.create_all(bind=engine)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def postgres_session_factory(
    postgres_engine: Engine,
) -> Iterator[Callable[[], Session]]:
    """독립 세션을 여는 팩토리. 테스트가 끝나면 전부 닫고 테이블을 비운다."""
    factory = sessionmaker(bind=postgres_engine, autocommit=False, autoflush=False)
    opened: list[Session] = []

    def open_session() -> Session:
        session = factory()
        opened.append(session)
        return session

    try:
        yield open_session
    finally:
        for session in opened:
            session.close()
        truncate_all_tables(postgres_engine)


@pytest.fixture
def postgres_session(postgres_session_factory: Callable[[], Session]) -> Session:
    """단일 세션 편의 픽스처 (SQLite 스위트의 ``test_db`` 에 대응)."""
    return postgres_session_factory()
