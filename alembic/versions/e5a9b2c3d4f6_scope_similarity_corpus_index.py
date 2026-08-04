"""scope similarity corpus watermark queries

Revision ID: e5a9b2c3d4f6
Revises: d4f8a1b2c3e5
Create Date: 2026-08-04 14:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "e5a9b2c3d4f6"
down_revision: Union[str, None] = "d4f8a1b2c3e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_projects_embedding_model_category_updated_at",
        "projects",
        ["embedding_model", "category", "embedding_updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_projects_embedding_model_category_updated_at",
        table_name="projects",
    )
