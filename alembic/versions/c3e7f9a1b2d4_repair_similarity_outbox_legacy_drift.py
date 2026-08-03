"""repair legacy similarity and outbox schema drift without deleting data

Revision ID: c3e7f9a1b2d4
Revises: a8d1c4e7f2b6
Create Date: 2026-08-04 01:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "c3e7f9a1b2d4"
down_revision: Union[str, None] = "a8d1c4e7f2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SNAPSHOT_TABLE = "project_similarity_snapshots"
EDGE_TABLE = "project_similarity_edges"
LEGACY_EDGE_TABLE = "project_similarity_edges_legacy_pre_snapshot"
OUTBOX_TABLE = "inference_outbox_events"

NORMALIZED_EDGE_COLUMNS = {
    "id",
    "snapshot_id",
    "candidate_project_id",
    "rank",
    "similarity_score",
}
LEGACY_EDGE_COLUMNS = {
    "id",
    "target_project_id",
    "candidate_project_id",
    "embedding_model",
    "target_embedding_updated_at",
    "same_category_only",
    "min_similarity_bucket",
    "rank",
    "similarity_score",
    "source",
    "computed_at",
}


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _repair_outbox(bind) -> None:
    inspector = inspect(bind)
    if OUTBOX_TABLE not in set(inspector.get_table_names()):
        return
    columns = _column_names(inspector, OUTBOX_TABLE)
    if "dedupe_key" not in columns:
        op.add_column(
            OUTBOX_TABLE,
            sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE inference_outbox_events "
                "SET dedupe_key = 'legacy:' || CAST(id AS VARCHAR) "
                "WHERE dedupe_key IS NULL"
            )
        )
        op.alter_column(
            OUTBOX_TABLE,
            "dedupe_key",
            existing_type=sa.String(length=255),
            nullable=False,
        )

    inspector = inspect(bind)
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(OUTBOX_TABLE)
    }
    if "uq_inference_outbox_event_dedupe" not in unique_names:
        op.create_unique_constraint(
            "uq_inference_outbox_event_dedupe",
            OUTBOX_TABLE,
            ["event_type", "aggregate_type", "aggregate_id", "dedupe_key"],
        )


def _rename_postgres_legacy_objects(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    primary_key_name = inspect(bind).get_pk_constraint(LEGACY_EDGE_TABLE).get("name")
    if primary_key_name == "project_similarity_edges_pkey":
        op.execute(
            "ALTER TABLE project_similarity_edges_legacy_pre_snapshot "
            "RENAME CONSTRAINT project_similarity_edges_pkey TO "
            "project_similarity_edges_legacy_pre_snapshot_pkey"
        )
    op.execute(
        "ALTER SEQUENCE IF EXISTS project_similarity_edges_id_seq RENAME TO "
        "project_similarity_edges_legacy_pre_snapshot_id_seq"
    )
    legacy_index_renames = {
        "ix_project_similarity_edges_candidate_project_id": (
            "ix_project_similarity_edges_legacy_candidate"
        ),
        "ix_project_similarity_edges_lookup": (
            "ix_project_similarity_edges_legacy_lookup"
        ),
        "ix_project_similarity_edges_target_project_id": (
            "ix_project_similarity_edges_legacy_target"
        ),
    }
    existing_indexes = {
        index["name"] for index in inspect(bind).get_indexes(LEGACY_EDGE_TABLE)
    }
    for old_name, new_name in legacy_index_renames.items():
        if old_name in existing_indexes:
            op.execute(f"ALTER INDEX {old_name} RENAME TO {new_name}")


def _create_normalized_edge_table() -> None:
    op.create_table(
        EDGE_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("candidate_project_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], [f"{SNAPSHOT_TABLE}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "rank",
            name="uq_project_similarity_edges_snapshot_rank",
        ),
    )
    op.create_index(
        "ix_project_similarity_edges_snapshot_id",
        EDGE_TABLE,
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_similarity_edges_candidate_project_id",
        EDGE_TABLE,
        ["candidate_project_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_similarity_edges_snapshot_rank",
        EDGE_TABLE,
        ["snapshot_id", "rank"],
        unique=False,
    )


def _repair_similarity_edges(bind) -> None:
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if EDGE_TABLE not in tables:
        return
    columns = _column_names(inspector, EDGE_TABLE)
    if NORMALIZED_EDGE_COLUMNS.issubset(columns):
        return
    if not LEGACY_EDGE_COLUMNS.issubset(columns):
        raise RuntimeError(
            "project_similarity_edges has an unsupported intermediate schema: "
            f"{sorted(columns)}"
        )
    if LEGACY_EDGE_TABLE in tables:
        raise RuntimeError(
            f"cannot quarantine legacy edges: {LEGACY_EDGE_TABLE} already exists"
        )

    # These legacy rows have no corpus watermark. Promoting them into the active
    # read model could serve a stale projection as current, so preserve the exact
    # rows in a quarantined table and let new outbox work build fresh snapshots.
    op.rename_table(EDGE_TABLE, LEGACY_EDGE_TABLE)
    _rename_postgres_legacy_objects(bind)
    _create_normalized_edge_table()


def upgrade() -> None:
    bind = op.get_bind()
    _repair_outbox(bind)
    _repair_similarity_edges(bind)


def downgrade() -> None:
    # The production repair preserves pre-snapshot rows in a quarantine table.
    # Replacing a subsequently rebuilt active projection would discard new data,
    # so this data-preserving repair is intentionally one-way.
    pass
