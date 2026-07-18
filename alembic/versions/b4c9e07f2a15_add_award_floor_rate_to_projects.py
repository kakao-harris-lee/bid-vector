"""add award_floor_rate column to projects

Persists the notice's 낙찰하한율 (KONEPS ``sucsfbidLwltRate``) as a fraction so
the post-개찰 낙찰 적격 검증(``verify_one``)can fall back to the공고 값 when the
operator did not supply a floor rate.

Revision ID: b4c9e07f2a15
Revises: a3f8d21c9b47
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "b4c9e07f2a15"
down_revision: Union[str, None] = "a3f8d21c9b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "projects"


def _new_columns() -> tuple[sa.Column, ...]:
    """Build fresh Column objects (op.add_column attaches them, so no reuse)."""
    return (sa.Column("award_floor_rate", sa.Float(), nullable=True),)


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
