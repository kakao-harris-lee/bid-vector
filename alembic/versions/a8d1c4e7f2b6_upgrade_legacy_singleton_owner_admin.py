"""upgrade legacy singleton owner admin privilege

Revision ID: a8d1c4e7f2b6
Revises: f4a8c2d6e901
Create Date: 2026-08-03 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8d1c4e7f2b6"
down_revision: Union[str, None] = "f4a8c2d6e901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CANONICAL_OPERATOR_USERNAME = "operator"
SYNTHETIC_OPERATOR_USERNAME_PREFIX = "synthetic-"


def upgrade() -> None:
    bind = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("username", sa.String()),
        sa.column("is_admin", sa.Boolean()),
    )
    existing_users = bind.execute(
        sa.select(users.c.id, users.c.username).order_by(users.c.id).limit(2)
    ).fetchall()

    bind.execute(
        users.update()
        .where(users.c.username == CANONICAL_OPERATOR_USERNAME)
        .values(is_admin=True)
    )
    if len(existing_users) != 1:
        return

    operator_id, username = existing_users[0]
    if not username or str(username).startswith(SYNTHETIC_OPERATOR_USERNAME_PREFIX):
        return
    bind.execute(
        users.update().where(users.c.id == int(operator_id)).values(is_admin=True)
    )


def downgrade() -> None:
    # This data repair is intentionally one-way: a prior legitimate admin row
    # cannot be distinguished safely from a row promoted by this migration.
    pass
