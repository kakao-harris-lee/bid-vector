"""add eligibility_raw column to projects

Persists the notice's 참가자격 관련 raw 필드(한글 원문 — 면허제한/업종/참가제한지역
등)를 ``Project.eligibility_raw`` (JSON) 에 보존한다. 수집 시점에 소실되던 원문을
PR-B 라벨 추출의 원천으로 남기기 위한 순수 데이터 보존 컬럼이며, 이 단계에는
소비자가 없다(추천 동작 불변).

Revision ID: c7d2f3a9b8e1
Revises: b4c9e07f2a15
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "c7d2f3a9b8e1"
down_revision: Union[str, None] = "b4c9e07f2a15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "projects"


def _new_columns() -> tuple[sa.Column, ...]:
    """Build fresh Column objects (op.add_column attaches them, so no reuse)."""
    return (sa.Column("eligibility_raw", sa.JSON(none_as_null=True), nullable=True),)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    for column in _new_columns():
        if column.name not in existing:
            op.add_column(TABLE_NAME, column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    for column in reversed(_new_columns()):
        if column.name in existing:
            op.drop_column(TABLE_NAME, column.name)
