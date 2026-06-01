"""add breakdown_json to synthetic_experiment_results

Revision ID: 254b8c3c5cef
Revises: 3c5e7b1a9d2f
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "254b8c3c5cef"
down_revision: Union[str, None] = "3c5e7b1a9d2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "synthetic_experiment_results",
        sa.Column("breakdown_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("synthetic_experiment_results", "breakdown_json")
