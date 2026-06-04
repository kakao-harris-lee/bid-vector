"""add construction_capacity_amount and awarded_contract_limit to company_profiles

Adds two operator-maintained construction-domain financial fields to
``company_profiles``:

* ``construction_capacity_amount`` — 시공능력평가액 (시공능력 평가액, 원 단위).
  Primary budget-fit indicator for the ``construction`` category once
  populated by the operator.
* ``awarded_contract_limit`` — 도급한도 (원 단위). Maximum simultaneously-held
  award amount. Persisted for future capacity tracking; the matcher does not
  yet consume it.

Both columns are ``NOT NULL`` with a ``0`` server-side default so the migration
is safe to apply to a populated table (zero-data-loss ADD COLUMN).

Revision ID: 8f1c4a2d7b9e
Revises: 254b8c3c5cef
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8f1c4a2d7b9e"
down_revision: Union[str, None] = "254b8c3c5cef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_profiles",
        sa.Column(
            "construction_capacity_amount",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "company_profiles",
        sa.Column(
            "awarded_contract_limit",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("company_profiles", "awarded_contract_limit")
    op.drop_column("company_profiles", "construction_capacity_amount")
