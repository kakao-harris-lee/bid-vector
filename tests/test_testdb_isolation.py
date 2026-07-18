"""Test-DB 격리 회귀 가드.

고정 repo-root ./test.db는 같은 checkout에서 동시 실행되는 pytest 프로세스끼리
SQLite 파일을 공유해 "attempt to write a readonly database"/missing-table
플레이크를 만들었다. conftest가 프로세스별 고유 tempdir 경로를 쓰고 앱 엔진도
같은 격리 URL을 보는지 고정한다.
"""
import os
import tempfile

import conftest


def test_test_database_url_is_process_unique_temp_file():
    prefix = "sqlite:///"
    assert conftest.TEST_DATABASE_URL.startswith(prefix)
    db_path = conftest.TEST_DATABASE_URL[len(prefix):]
    assert os.path.dirname(db_path) == tempfile.gettempdir()
    name = os.path.basename(db_path)
    assert name.startswith(f"bid-vector-test-{os.getpid()}-")
    assert name.endswith(".db")


def test_app_engine_uses_the_isolated_url():
    # 환경변수가 아니라 실제 앱 싱글턴(settings·engine)이 격리 URL을 보는지 검증한다
    # (env 우선순위 회귀 시 env 비교만으로는 통과해 버리는 동어반복 방지).
    from app.core.config import settings
    from app.core.database import engine

    assert settings.DATABASE_URL == conftest.TEST_DATABASE_URL
    assert str(engine.url) == conftest.TEST_DATABASE_URL
