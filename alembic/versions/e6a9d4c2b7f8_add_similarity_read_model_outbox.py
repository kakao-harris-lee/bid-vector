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

SIMILARITY_TABLE = "project_similarity_edges"
OUTBOX_TABLE = "inference_outbox_events"

SIMILARITY_INDEXES = (
    ("ix_project_similarity_edges_target_project_id", ["target_project_id"]),
    ("ix_project_similarity_edges_candidate_project_id", ["candidate_project_id"]),
    (
        "ix_project_similarity_edges_lookup",
        [
            "target_project_id",
            "embedding_model",
            "target_embedding_updated_at",
            "same_category_only",
            "min_similarity_bucket",
            "rank",
        ],
    ),
)
OUTBOX_INDEXES = (
    ("ix_inference_outbox_events_event_type", ["event_type"]),
    ("ix_inference_outbox_events_aggregate_type", ["aggregate_type"]),
    ("ix_inference_outbox_events_aggregate_id", ["aggregate_id"]),
    ("ix_inference_outbox_events_status", ["status"]),
    ("ix_inference_outbox_events_task_id", ["task_id"]),
    ("ix_inference_outbox_events_available_at", ["available_at"]),
    ("ix_inference_outbox_events_claim", ["status", "available_at", "id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if SIMILARITY_TABLE not in existing_tables:
        op.create_table(
            SIMILARITY_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("target_project_id", sa.Integer(), nullable=False),
            sa.Column("candidate_project_id", sa.Integer(), nullable=False),
            sa.Column("embedding_model", sa.String(length=255), nullable=False),
            sa.Column("target_embedding_updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("same_category_only", sa.Boolean(), nullable=False),
            sa.Column("min_similarity_bucket", sa.Float(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("similarity_score", sa.Float(), nullable=False),
            sa.Column("source", sa.String(length=50), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["candidate_project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["target_project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "target_project_id",
                "embedding_model",
                "target_embedding_updated_at",
                "same_category_only",
                "min_similarity_bucket",
                "rank",
                name="uq_project_similarity_edges_version_rank",
            ),
        )
        for index_name, columns in SIMILARITY_INDEXES:
            op.create_index(index_name, SIMILARITY_TABLE, columns, unique=False)

    if OUTBOX_TABLE not in existing_tables:
        op.create_table(
            OUTBOX_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=100), nullable=False),
            sa.Column("aggregate_type", sa.String(length=50), nullable=False),
            sa.Column("aggregate_id", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("task_id", sa.String(length=155), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
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

    if SIMILARITY_TABLE in existing_tables:
        existing_indexes = {
            ix["name"] for ix in inspector.get_indexes(SIMILARITY_TABLE)
        }
        for index_name, _ in SIMILARITY_INDEXES:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=SIMILARITY_TABLE)
        op.drop_table(SIMILARITY_TABLE)
