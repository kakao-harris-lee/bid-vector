"""add award_outcome columns to bid_decision_records

Persists the win/loss outcome of an operator real bid so the post-개찰 낙찰결과
파이프라인 leaves a durable 과거 입찰 기록 (몇 위였는지가 아니라 낙찰/패찰 여부):

  * ``award_outcome``    — String(20), indexed: "won" / "lost"; NULL = 미확정.
    Set by operator company-name 매칭 vs ``TenderResult.winning_company`` only
    after the winner is public (§2 정직 명세 — 추정으로 미리 채우지 않음).
  * ``award_outcome_at`` — DateTime(tz): stamp of when the outcome was recorded.

Nullable add-column (harmless on SQLite/Postgres); no consumer depends on a
default value. Distinct from ``award_notified_at`` (알림 발송 멱등 마커) —
outcome is recorded when the winner is confirmed, independent of send success.

Revision ID: d5f1a2c8b3e9
Revises: c7d2f3a9b8e1
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "d5f1a2c8b3e9"
down_revision: Union[str, None] = "c7d2f3a9b8e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "bid_decision_records"
INDEX_NAME = "ix_bid_decision_records_award_outcome"
INDEXED_COLUMN = "award_outcome"


def _new_columns() -> tuple[sa.Column, ...]:
    """Build fresh Column objects (op.add_column attaches them, so no reuse)."""
    return (
        sa.Column("award_outcome", sa.String(length=20), nullable=True),
        sa.Column("award_outcome_at", sa.DateTime(timezone=True), nullable=True),
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
