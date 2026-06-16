"""add smoke_test_runs table

Persists each daily KONEPS + Telegram end-to-end smoke cycle so the operations
dashboard can surface a consolidated PASS/FAIL history and consecutive green
streak (the "0단계 운영 검증" signal) alongside crawl and notification health.

Each row stores the cycle outcome plus a trimmed ``phases`` JSON
(``[{name, passed, detail}, ...]`` — the bulky per-phase ``data`` dict is
dropped before persistence to keep rows small). ``created_at`` is the
window-query key; ``overall_passed`` is indexed for streak/pass-rate scans.

Revision ID: 4b8e2f1c6a90
Revises: 2a4f6c1b8e30
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "4b8e2f1c6a90"
down_revision: Union[str, None] = "2a4f6c1b8e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    # The app also runs ``Base.metadata.create_all`` as a boot-time safety net,
    # which can create this table from the model before the migration runs.
    # Guard against that so ``alembic upgrade head`` applies cleanly either way.
    if inspector.has_table("smoke_test_runs"):
        return
    op.create_table(
        "smoke_test_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overall_passed", sa.Boolean(), nullable=True),
        sa.Column("phases", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("telegram_status", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_smoke_test_runs_overall_passed"),
        "smoke_test_runs",
        ["overall_passed"],
        unique=False,
    )
    op.create_index(
        op.f("ix_smoke_test_runs_created_at"),
        "smoke_test_runs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_smoke_test_runs_created_at"),
        table_name="smoke_test_runs",
    )
    op.drop_index(
        op.f("ix_smoke_test_runs_overall_passed"),
        table_name="smoke_test_runs",
    )
    op.drop_table("smoke_test_runs")
