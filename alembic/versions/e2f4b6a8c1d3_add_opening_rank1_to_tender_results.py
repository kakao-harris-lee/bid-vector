"""add 개찰 1위(잠정) columns to tender_results

Persists the 개찰 직후 1위 업체 정보 + 참가자 수 for tracked real bids so the
operator sees "개찰 1위 = 우리 여부" as a **잠정 신호**, kept distinct from the
낙찰 확정 (winning_*) fields (§2 정직 명세 — 수의계약은 1위=사실상 확정이나
적격심사는 1위부터 캐스케이드라 1위≠낙찰 가능):

  * ``opening_rank1_company``      — String(255): 개찰 1위 상호.
  * ``opening_rank1_business_no``  — String(20): 사업자번호(제로패딩 보존 문자열, #210).
  * ``opening_rank1_amount``       — Float: 1위 투찰금액.
  * ``opening_rank1_rate``         — Float: 1위 투찰률(비율).
  * ``opening_participant_count``  — Integer: 참가자 수.
  * ``opened_at``                  — DateTime(tz): 개찰 시각.
  * ``opening_checked_at``         — DateTime(tz): 수집 패스 recheck backoff 마커
    (NULL = 미조회). 매칭 실패도 스탬프해 다음 backoff 후 재시도한다.

Nullable add-column (harmless on SQLite/Postgres); no consumer depends on a
default value. Distinct from ``winning_*`` (낙찰 확정) — the opening pass only
ever writes ``opening_*`` and never touches the winner fields.

Revision ID: e2f4b6a8c1d3
Revises: d5f1a2c8b3e9
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "e2f4b6a8c1d3"
down_revision: Union[str, None] = "d5f1a2c8b3e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "tender_results"


def _new_columns() -> tuple[sa.Column, ...]:
    """Build fresh Column objects (op.add_column attaches them, so no reuse)."""
    return (
        sa.Column("opening_rank1_company", sa.String(length=255), nullable=True),
        sa.Column("opening_rank1_business_no", sa.String(length=20), nullable=True),
        sa.Column("opening_rank1_amount", sa.Float(), nullable=True),
        sa.Column("opening_rank1_rate", sa.Float(), nullable=True),
        sa.Column("opening_participant_count", sa.Integer(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opening_checked_at", sa.DateTime(timezone=True), nullable=True),
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
