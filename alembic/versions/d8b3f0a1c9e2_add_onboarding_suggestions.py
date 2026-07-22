"""add onboarding_suggestions audit table

온보딩 후보에 대한 운영자 결정(수락/거부/수정/보류)을 append-only 로 영속화하는
감사 테이블을 만든다(설계 §3 "수정하거나 거부한 후보도 저장해 다음 온보딩 규칙
개선에 사용"). 각 apply 결정이 1행이며 재적용은 새 행이다(멱등 강제 없음, 각
apply=이벤트). ``user_id`` 인덱스로 per-operator 스코프 조회를 지원하고, 반영과
무관하게 rejected/pending 결정도 기록한다. 이 테이블은 데이터로만 존재하며
classifier/수집/추천 동작을 바꾸지 않는다. SQLite(CI)/Postgres 양쪽에서 동작한다.

Revision ID: d8b3f0a1c9e2
Revises: c9e4a7b1f2d0
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "d8b3f0a1c9e2"
down_revision: Union[str, None] = "c9e4a7b1f2d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "onboarding_suggestions"
INDEX_NAME = "ix_onboarding_suggestions_user_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(INDEX_NAME, TABLE_NAME, ["user_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
    if INDEX_NAME in existing_indexes:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
