"""add similarity read model and inference outbox

Revision ID: e6a9d4c2b7f8
Revises: b7e3a9c4d5f1
Create Date: 2026-08-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "e6a9d4c2b7f8"
down_revision: Union[str, None] = "b7e3a9c4d5f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SNAPSHOT_TABLE = "project_similarity_snapshots"
EDGE_TABLE = "project_similarity_edges"
OUTBOX_TABLE = "inference_outbox_events"

SNAPSHOT_INDEXES = (
    ("ix_project_similarity_snapshots_target_project_id", ["target_project_id"]),
    (
        "ix_project_similarity_snapshots_lookup",
        [
            "target_project_id",
            "embedding_model",
            "target_embedding_updated_at",
            "same_category_only",
            "min_similarity_bucket",
        ],
    ),
)
EDGE_INDEXES = (
    ("ix_project_similarity_edges_snapshot_id", ["snapshot_id"]),
    ("ix_project_similarity_edges_candidate_project_id", ["candidate_project_id"]),
    ("ix_project_similarity_edges_snapshot_rank", ["snapshot_id", "rank"]),
)
OUTBOX_INDEXES = (
    ("ix_inference_outbox_events_event_type", ["event_type"]),
    ("ix_inference_outbox_events_aggregate_type", ["aggregate_type"]),
    ("ix_inference_outbox_events_aggregate_id", ["aggregate_id"]),
    ("ix_inference_outbox_events_status", ["status"]),
    ("ix_inference_outbox_events_available_at", ["available_at"]),
    ("ix_inference_outbox_events_claim", ["status", "available_at", "id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if SNAPSHOT_TABLE not in existing_tables:
        op.create_table(
            SNAPSHOT_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("target_project_id", sa.Integer(), nullable=False),
            sa.Column("embedding_model", sa.String(length=255), nullable=False),
            sa.Column(
                "target_embedding_updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column("same_category_only", sa.Boolean(), nullable=False),
            sa.Column("min_similarity_bucket", sa.Float(), nullable=False),
            sa.Column("corpus_embedding_count", sa.Integer(), nullable=False),
            sa.Column(
                "corpus_embedding_updated_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("edge_count", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["target_project_id"], ["projects.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "target_project_id",
                "embedding_model",
                "target_embedding_updated_at",
                "same_category_only",
                "min_similarity_bucket",
                name="uq_project_similarity_snapshot_version",
            ),
        )
        for index_name, columns in SNAPSHOT_INDEXES:
            op.create_index(index_name, SNAPSHOT_TABLE, columns, unique=False)

    if EDGE_TABLE not in existing_tables:
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
        for index_name, columns in EDGE_INDEXES:
            op.create_index(index_name, EDGE_TABLE, columns, unique=False)

    if OUTBOX_TABLE not in existing_tables:
        op.create_table(
            OUTBOX_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=100), nullable=False),
            sa.Column("aggregate_type", sa.String(length=50), nullable=False),
            sa.Column("aggregate_id", sa.Integer(), nullable=False),
            sa.Column("dedupe_key", sa.String(length=255), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "event_type",
                "aggregate_type",
                "aggregate_id",
                "dedupe_key",
                name="uq_inference_outbox_event_dedupe",
            ),
        )
        for index_name, columns in OUTBOX_INDEXES:
            op.create_index(index_name, OUTBOX_TABLE, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if OUTBOX_TABLE in existing_tables:
        existing_indexes = {ix["name"] for ix in inspector.get_indexes(OUTBOX_TABLE)}
        for index_name, _ in OUTBOX_INDEXES:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=OUTBOX_TABLE)
        op.drop_table(OUTBOX_TABLE)

    if EDGE_TABLE in existing_tables:
        existing_indexes = {ix["name"] for ix in inspector.get_indexes(EDGE_TABLE)}
        for index_name, _ in EDGE_INDEXES:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=EDGE_TABLE)
        op.drop_table(EDGE_TABLE)

    if SNAPSHOT_TABLE in existing_tables:
        existing_indexes = {ix["name"] for ix in inspector.get_indexes(SNAPSHOT_TABLE)}
        for index_name, _ in SNAPSHOT_INDEXES:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=SNAPSHOT_TABLE)
        op.drop_table(SNAPSHOT_TABLE)
