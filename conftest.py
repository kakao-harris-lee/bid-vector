"""Test configuration and fixtures"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = "sqlite:///./test.db"

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

from app.core.database import Base


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
