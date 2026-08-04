"""Test configuration and fixtures"""
import os
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Per-process isolated SQLite file. A fixed repo-root ./test.db lets concurrent
# pytest processes in the same checkout corrupt each other ("attempt to write a
# readonly database" / missing-table flakes); the uuid also prevents reusing a
# stale file left behind by a crashed earlier run.
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(), f"bid-vector-test-{os.getpid()}-{uuid.uuid4().hex[:8]}.db"
)
TEST_DATABASE_URL = f"sqlite:///{_TEST_DB_PATH}"


def pytest_sessionfinish(session, exitstatus):
    """Remove this process's test database (and SQLite journal) after the run."""
    for path in (_TEST_DB_PATH, f"{_TEST_DB_PATH}-journal"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

# Force test processes to use the lightweight SQLite database even when Docker
# Compose injects PostgreSQL split env vars for the application runtime or they
# are present in the local `.env` file loaded by pydantic-settings.
os.environ["DATABASE_USER"] = ""
os.environ["DATABASE_PASSWORD"] = ""
os.environ["DATABASE_HOST"] = ""
os.environ["DATABASE_PORT"] = "0"
os.environ["DATABASE_NAME"] = ""

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["CELERY_ALLOW_INLINE_ML_TASKS"] = "false"
os.environ["ML_RELEASE_OBJECT_STORAGE_URL"] = ""
os.environ["ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH"] = "false"
os.environ["PRICE_PREDICTION_PREFERRED_PREDICTOR"] = "historical"
os.environ["PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS"] = "false"
os.environ["PRICE_PREDICTION_LSTM_MODEL_PATH"] = ""
os.environ["PRICE_PREDICTION_ENSEMBLE_MODEL_PATH"] = ""
os.environ["OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED"] = "false"
os.environ["OPERATOR_STRATEGY_MONITOR_RUN_ON_STARTUP"] = "false"
os.environ["PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED"] = "false"
os.environ["PAPER_BIDDING_FORWARD_RUN_ON_STARTUP"] = "false"
os.environ["TELEGRAM_DECISION_DAILY_CAP"] = "0"
os.environ["TELEGRAM_DECISION_RENOTIFY_COOLDOWN_HOURS"] = "0"

from app.core.database import Base  # noqa: E402 - test env must precede app import


@pytest.fixture
def test_db():
    """Create test database session"""
    # Use SQLite for tests
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = TestingSessionLocal()

    yield db

    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    """Create test client"""
    from fastapi.testclient import TestClient
    from app.core.database import get_db
    from app.main import app

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
