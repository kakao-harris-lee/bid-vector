"""Schema drift guard: SQLAlchemy models <-> alembic migrations.

Background (issue #45)
----------------------
The app uses BOTH ``Base.metadata.create_all`` (boot-time safety net) and alembic
migrations. Because ``create_all`` builds the *full* model schema while alembic
migrations are *incremental patches* layered on a historical create_all baseline,
the two can silently diverge: a developer adds a model column but forgets the
migration, and tests still pass (sqlite create_all always matches the model) while
production -- which is alembic-driven -- 500s with ``UndefinedColumn``.

This module reproduces the two schema paths on isolated sqlite databases and
asserts they agree at the (table, column) **name** level:

* **Model path**     -- ``Base.metadata.create_all`` (the source of truth).
* **Migration path** -- a pre-migration baseline (the model minus
  migration-owned objects) followed by ``alembic upgrade head``.

If the migration path is missing a table/column the model declares (the
``breakdown_json`` failure mode), the test fails with an explicit message.

Maintenance
-----------
When a NEW migration creates a table or adds a column, register the
migration-owned objects in ``MIGRATION_OWNED_TABLES`` /
``MIGRATION_ADDED_COLUMNS`` below so the pre-migration baseline can be
reconstructed. Forgetting to do so makes this test fail loudly, which is the
intended behaviour -- it forces the model and migrations to stay in lockstep.

Comparison is intentionally name-level only: sqlite cannot faithfully reflect
postgres-specific foreign keys or the pgvector ``VECTOR(384)`` type, so a
structural ``compare_metadata`` diff would be dominated by dialect noise.

Scope / known limitation
------------------------
The pre-migration baseline is reconstructed FROM the model metadata (minus the
registered migration-owned objects). This means the guard is strongest for
objects migrations actually own -- new tables and columns added via a migration
(exactly the ``breakdown_json`` incident). For columns on the historical
create_all baseline tables, a forgotten migration could in principle leak into
the reconstructed baseline and escape detection; those tables have never been
alembic-managed. The practical contract this test enforces: anything you add via
a migration must match the model, and migration-owned tables/columns must round
-trip through ``alembic upgrade head``.
"""
import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect

import app.core.config as app_config
from app.core.database import Base
from app.models import models  # noqa: F401  -- registers all tables on Base.metadata

# Tables created by an alembic migration (not part of the historical baseline).
MIGRATION_OWNED_TABLES = {
    "synthetic_experiments",
    "synthetic_experiment_runs",
    "synthetic_experiment_results",
    "smoke_test_runs",
    "operator_notification_channels",
    "onboarding_suggestions",
}

# Columns added to a pre-existing table by an alembic migration.
MIGRATION_ADDED_COLUMNS = {
    "projects": {"business_type_code", "business_type_label"},
    "synthetic_experiment_results": {"breakdown_json"},
    "company_profiles": {
        "construction_capacity_amount",
        "awarded_contract_limit",
        "association_memberships",
        "tech_fields",
    },
    "crawl_jobs": {"celery_task_id"},
    "paper_bid_settlements": {"estimated_price", "minimum_bid_price"},
    "tender_results": {
        "opening_rank1_company",
        "opening_rank1_business_no",
        "opening_rank1_amount",
        "opening_rank1_rate",
        "opening_participant_count",
        "opened_at",
        "opening_checked_at",
    },
}

ALEMBIC_INTERNAL_TABLES = {"alembic_version"}


def _table_column_names(engine) -> dict[str, set[str]]:
    """Reflect {table_name: {column_name, ...}} from a live engine."""
    inspector = inspect(engine)
    schema: dict[str, set[str]] = {}
    for table_name in inspector.get_table_names():
        if table_name in ALEMBIC_INTERNAL_TABLES:
            continue
        schema[table_name] = {
            column["name"] for column in inspector.get_columns(table_name)
        }
    return schema


def _build_model_schema() -> dict[str, set[str]]:
    """Schema produced by ``Base.metadata.create_all`` (the source of truth)."""
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    try:
        engine = create_engine(f"sqlite:///{handle.name}")
        Base.metadata.create_all(bind=engine)
        schema = _table_column_names(engine)
        engine.dispose()
        return schema
    finally:
        os.unlink(handle.name)


def _build_migration_schema() -> dict[str, set[str]]:
    """Schema produced by replaying alembic migrations on a pre-migration baseline.

    The baseline is the model metadata minus migration-owned tables and
    migration-added columns, so ``alembic upgrade head`` re-creates exactly those
    objects -- exercising the real migration code instead of create_all.
    """
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    url = f"sqlite:///{handle.name}"
    original_url = app_config.settings.DATABASE_URL
    try:
        engine = create_engine(url)

        baseline = MetaData()
        for table in Base.metadata.sorted_tables:
            if table.name in MIGRATION_OWNED_TABLES:
                continue
            dropped = MIGRATION_ADDED_COLUMNS.get(table.name, set())
            # Copy only the baseline columns; constraints/indexes that reference
            # migration-added columns are intentionally left out so the sqlite
            # baseline builds cleanly.
            columns = [
                column._copy() for column in table.columns if column.name not in dropped
            ]
            Table(table.name, baseline, *columns)
        baseline.create_all(bind=engine)

        # alembic/env.py reads settings.DATABASE_URL at env-load time; point it at
        # the isolated sqlite DB for the duration of the upgrade.
        app_config.settings.DATABASE_URL = url
        command.upgrade(Config("alembic.ini"), "head")

        schema = _table_column_names(engine)
        engine.dispose()
        return schema
    finally:
        app_config.settings.DATABASE_URL = original_url
        os.unlink(handle.name)


@pytest.fixture(scope="module")
def model_schema() -> dict[str, set[str]]:
    return _build_model_schema()


@pytest.fixture(scope="module")
def migration_schema() -> dict[str, set[str]]:
    return _build_migration_schema()


def test_migration_head_applies_cleanly(migration_schema):
    """`alembic upgrade head` must run end-to-end on a fresh baseline."""
    # Reaching here means upgrade head executed without raising; assert the
    # migration-owned tables were actually created by the migrations.
    for table in MIGRATION_OWNED_TABLES:
        assert (
            table in migration_schema
        ), f"migration path did not create expected table {table!r}"


def test_no_table_drift(model_schema, migration_schema):
    """Every model table must be reachable via migrations and vice versa."""
    model_tables = set(model_schema)
    migration_tables = set(migration_schema)

    missing_in_migrations = model_tables - migration_tables
    extra_in_migrations = migration_tables - model_tables

    assert not missing_in_migrations, (
        "Model declares tables with no migration to create them "
        f"(add a migration): {sorted(missing_in_migrations)}"
    )
    assert not extra_in_migrations, (
        "Migrations create tables the model no longer declares "
        f"(remove/adjust migration or model): {sorted(extra_in_migrations)}"
    )


def test_no_column_drift(model_schema, migration_schema):
    """Every model column must be reachable via migrations and vice versa.

    This is the guard for the issue #45 failure mode: a model column added
    without a matching migration (e.g. ``breakdown_json``).
    """
    drift: dict[str, dict[str, list[str]]] = {}
    for table in set(model_schema) & set(migration_schema):
        missing_in_migrations = model_schema[table] - migration_schema[table]
        extra_in_migrations = migration_schema[table] - model_schema[table]
        if missing_in_migrations or extra_in_migrations:
            drift[table] = {
                "missing_in_migrations": sorted(missing_in_migrations),
                "extra_in_migrations": sorted(extra_in_migrations),
            }

    assert not drift, (
        "Model <-> migration column drift detected. Columns present in the model "
        "but not produced by `alembic upgrade head` mean a migration is missing; "
        "columns only in migrations mean the model/migration disagree. "
        f"Drift: {drift}"
    )
