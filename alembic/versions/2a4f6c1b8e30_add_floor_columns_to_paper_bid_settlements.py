"""add estimated_price and minimum_bid_price to paper_bid_settlements

The settlement eligibility gate scores a paper bid against the category
낙찰하한가 (minimum eligible bid amount) instead of a pure price-proximity
proxy. To make that judgement auditable and reusable downstream (probability
calibration labels, reporting gate aggregates) the two derived values are
persisted on each settlement row:

* ``estimated_price`` — 예정가격 derived at settlement (scoring) time from the
  award's reserve-price detail (mean of the selected 복수예비가격) or the
  collected planned price. ``NULL`` when no reserve/planned data exists for the
  project (honest absence — the gate then falls back to ``"unknown"``).
* ``minimum_bid_price`` — 낙찰하한가 = ``estimated_price`` × the category
  minimum-bid-rate floor (``settings.PREDICTION_CATEGORY_MINIMUM_BID_RATES``).
  ``NULL`` when ``estimated_price`` is ``NULL``.

Both columns are ``nullable`` (no server default) — a missing value is a
first-class "not enough data" signal, distinct from ``0``. This is a
zero-data-loss ADD COLUMN: existing settlement rows keep ``NULL`` and the gate
treats them as ``"unknown"`` until re-settled.

Revision ID: 2a4f6c1b8e30
Revises: 1b7d9c4e2a55
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2a4f6c1b8e30"
down_revision: Union[str, None] = "1b7d9c4e2a55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "paper_bid_settlements",
        sa.Column("estimated_price", sa.Float(), nullable=True),
    )
    op.add_column(
        "paper_bid_settlements",
        sa.Column("minimum_bid_price", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paper_bid_settlements", "minimum_bid_price")
    op.drop_column("paper_bid_settlements", "estimated_price")
