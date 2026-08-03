"""Serialized schema bootstrap for non-production first boots."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.database import Base
from app.services.bid_decision_schema import ensure_bid_decision_schema
from app.services.operator_strategy_schema import ensure_operator_strategy_schema
from app.services.prediction_schema import ensure_price_prediction_metadata_schema
from app.services.project_similarity import (
    ensure_project_metadata_schema,
    ensure_project_vector_schema,
)


_PRODUCTION_ENVIRONMENTS = frozenset({"production", "staging"})
_POSTGRES_SCHEMA_BOOTSTRAP_LOCK_ID = 7_338_231_946_092_024_001


def startup_schema_bootstrap_enabled(environment: str) -> bool:
    """Return whether lifespan may provide the local first-boot safety net."""
    return str(environment or "").strip().lower() not in _PRODUCTION_ENVIRONMENTS


@contextmanager
def _serialized_schema_bootstrap(engine: Engine) -> Iterator[None]:
    """Serialize bootstrap DDL across PostgreSQL application processes."""
    if engine.dialect.name != "postgresql":
        yield
        return

    with engine.connect() as lock_connection:
        lock_connection.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": _POSTGRES_SCHEMA_BOOTSTRAP_LOCK_ID},
        )
        try:
            yield
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _POSTGRES_SCHEMA_BOOTSTRAP_LOCK_ID},
            )


def bootstrap_application_schema(engine: Engine) -> None:
    """Create/repair a development schema under one cross-process lock."""
    with _serialized_schema_bootstrap(engine):
        Base.metadata.create_all(bind=engine)
        ensure_bid_decision_schema(engine)
        ensure_operator_strategy_schema(engine)
        ensure_price_prediction_metadata_schema(engine)
        ensure_project_metadata_schema(engine)
        ensure_project_vector_schema(engine)
