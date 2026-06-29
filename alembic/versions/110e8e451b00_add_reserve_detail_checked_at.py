"""add reserve_detail_checked_at to historical_data

Revision ID: 110e8e451b00
Revises: 6f2a8c9d0e12
Create Date: 2026-06-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "110e8e451b00"
down_revision: Union[str, None] = "6f2a8c9d0e12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "historical_data"
COLUMN_NAME = "reserve_detail_checked_at"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    if COLUMN_NAME not in existing:
        op.add_column(
            TABLE_NAME,
            sa.Column(COLUMN_NAME, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    if COLUMN_NAME in existing:
        op.drop_column(TABLE_NAME, COLUMN_NAME)
