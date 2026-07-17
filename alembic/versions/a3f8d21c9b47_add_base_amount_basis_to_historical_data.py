"""add base_amount provenance classification columns to historical_data

Adds three nullable columns that tag the provenance of ``base_amount`` without
ever overwriting it (정직 명세 §2 — 원본 불변, 추정은 추정으로 표기):

  * ``base_amount_basis``     — String(30), indexed: classifier verdict
    ('clean' / 'derived-yega' / 'derived-vat' / 'suspect-fractional').
  * ``base_amount_estimated`` — Float: estimated real 기초금액 for non-clean
    rows (복수예비가격 midpoint); NULL when unrecoverable or already clean.
  * ``basis_checked_at``      — DateTime(tz): backfill idempotency marker.

Revision ID: a3f8d21c9b47
Revises: 1c3e5a7d9f21
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "a3f8d21c9b47"
down_revision: Union[str, None] = "1c3e5a7d9f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "historical_data"
INDEX_NAME = "ix_historical_data_base_amount_basis"
INDEXED_COLUMN = "base_amount_basis"


def _new_columns() -> tuple[sa.Column, ...]:
    """Build fresh Column objects (op.add_column attaches them, so no reuse)."""
    return (
        sa.Column("base_amount_basis", sa.String(length=30), nullable=True),
        sa.Column("base_amount_estimated", sa.Float(), nullable=True),
        sa.Column("basis_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    for column in _new_columns():
        if column.name not in existing:
            op.add_column(TABLE_NAME, column)

    indexes = {idx["name"] for idx in inspect(bind).get_indexes(TABLE_NAME)}
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, TABLE_NAME, [INDEXED_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {idx["name"] for idx in inspect(bind).get_indexes(TABLE_NAME)}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)

    inspector = inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
    for column in reversed(_new_columns()):
        if column.name in existing:
            op.drop_column(TABLE_NAME, column.name)
