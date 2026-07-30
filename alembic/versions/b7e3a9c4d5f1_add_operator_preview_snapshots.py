"""add operator_preview_snapshots table

preview 스냅샷 + 온디맨드 갱신(설계 2026-07-30 §6.1)의 유일한 마이그레이션.
키는 UNIQUE(operator_id, high_priority_only) — limit 은 키 차원이 아니며 상한
예산으로 1회 계산한 top-100(payload_json)을 서빙 시 슬라이스한다. status 가
DB 단일비행 가드, task_id 는 crawl_jobs celery_task_id 패턴(String(155)).
additive-only: 롤백 = 테이블 drop. SQLite(CI)/Postgres 양쪽에서 동작한다.

Revision ID: b7e3a9c4d5f1
Revises: a1f4c8e7b2d9
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "b7e3a9c4d5f1"
down_revision: Union[str, None] = "a1f4c8e7b2d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "operator_preview_snapshots"
INDEXES = (
    ("ix_operator_preview_snapshots_operator_id", ["operator_id"]),
    ("ix_operator_preview_snapshots_status", ["status"]),
    ("ix_operator_preview_snapshots_task_id", ["task_id"]),
)
UNIQUE_NAME = "uq_operator_preview_snapshots_key"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("high_priority_only", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("task_id", sa.String(length=155), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operator_id", "high_priority_only", name=UNIQUE_NAME),
    )
    for index_name, columns in INDEXES:
        op.create_index(index_name, TABLE_NAME, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    for index_name, _ in INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
