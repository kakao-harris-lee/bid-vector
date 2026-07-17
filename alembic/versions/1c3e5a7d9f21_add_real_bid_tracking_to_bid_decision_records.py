"""add real-bid tracking columns to bid_decision_records

Adds the operator's actual KONEPS submission fields plus the award-result
notification idempotency marker so the post-개찰 낙찰결과 알림 파이프라인can
track real bids without overwriting the system recommendation.

Revision ID: 1c3e5a7d9f21
Revises: 110e8e451b00
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "1c3e5a7d9f21"
down_revision: Union[str, None] = "110e8e451b00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "bid_decision_records"


def _new_columns() -> tuple[sa.Column, ...]:
    """Build fresh Column objects (op.add_column attaches them, so no reuse)."""
    return (
        sa.Column("submitted_bid_amount", sa.Float(), nullable=True),
        sa.Column("submitted_floor_rate", sa.Float(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("award_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


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
