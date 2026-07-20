"""add notice_eligibility_labels table

공고 참가자격 식별 라벨(협회/엔지니어링 조건)을 소스별 1라벨로 영속화한다
(ML 실증 데이터셋 · G-3 식별 precision/recall 리포트의 원천). ``(project_id,
source)`` 유니크로 소스별 1라벨을 강제하고 재생성은 upsert 한다. 라벨은 이
단계에선 데이터로만 존재하며 classifier/수집/추천 동작은 불변이다.

Revision ID: f6b1d40a9c37
Revises: e2f4b6a8c1d3
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "f6b1d40a9c37"
down_revision: Union[str, None] = "e2f4b6a8c1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "notice_eligibility_labels"
UNIQUE_NAME = "uq_notice_eligibility_labels_project_source"
INDEX_NAME = "ix_notice_eligibility_labels_project_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("labeler_version", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "source", name=UNIQUE_NAME),
    )
    op.create_index(
        INDEX_NAME, TABLE_NAME, ["project_id"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    if INDEX_NAME in existing_indexes:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
