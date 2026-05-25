"""add business_type to project

Revision ID: 7e85ae125de7
Revises: 
Create Date: 2026-05-25 23:12:23.705440

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e85ae125de7'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("business_type_code", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("business_type_label", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_projects_business_type_code",
        "projects",
        ["business_type_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_business_type_code", table_name="projects")
    op.drop_column("projects", "business_type_label")
    op.drop_column("projects", "business_type_code")
