"""add operator-scoped similar-project refresh operations

Revision ID: f4a8c2d6e901
Revises: e6a9d4c2b7f8
Create Date: 2026-08-03 20:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a8c2d6e901"
down_revision: Union[str, None] = "e6a9d4c2b7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "similar_projects_refresh_operations",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=155), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_similar_projects_refresh_operations_expires_at",
        "similar_projects_refresh_operations",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_similar_projects_refresh_operations_operator_id",
        "similar_projects_refresh_operations",
        ["operator_id"],
        unique=False,
    )
    op.create_index(
        "ix_similar_projects_refresh_operations_project_id",
        "similar_projects_refresh_operations",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_similar_projects_refresh_operations_status",
        "similar_projects_refresh_operations",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_similar_projects_refresh_operations_task_id",
        "similar_projects_refresh_operations",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_similar_projects_refresh_operations_task_id",
        table_name="similar_projects_refresh_operations",
    )
    op.drop_index(
        "ix_similar_projects_refresh_operations_status",
        table_name="similar_projects_refresh_operations",
    )
    op.drop_index(
        "ix_similar_projects_refresh_operations_project_id",
        table_name="similar_projects_refresh_operations",
    )
    op.drop_index(
        "ix_similar_projects_refresh_operations_operator_id",
        table_name="similar_projects_refresh_operations",
    )
    op.drop_index(
        "ix_similar_projects_refresh_operations_expires_at",
        table_name="similar_projects_refresh_operations",
    )
    op.drop_table("similar_projects_refresh_operations")
