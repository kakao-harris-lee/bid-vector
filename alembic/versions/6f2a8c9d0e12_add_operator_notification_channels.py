"""add operator notification channels

Revision ID: 6f2a8c9d0e12
Revises: 4b8e2f1c6a90
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "6f2a8c9d0e12"
down_revision: Union[str, None] = "4b8e2f1c6a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "operator_notification_channels"
LEGACY_TELEGRAM_ROUTE_KEY = "telegram:legacy-configured-chat"


def _backfill_canonical_telegram_channel() -> None:
    bind = op.get_bind()
    canonical_rows = bind.execute(
        sa.text("SELECT id FROM users WHERE username = :username"),
        {"username": "operator"},
    ).fetchall()
    for row in canonical_rows:
        operator_id = int(row[0])
        existing = bind.execute(
            sa.text(
                """
                SELECT id
                FROM operator_notification_channels
                WHERE operator_id = :operator_id
                  AND channel_type = 'telegram'
                  AND route_key = :route_key
                LIMIT 1
                """
            ),
            {"operator_id": operator_id, "route_key": LEGACY_TELEGRAM_ROUTE_KEY},
        ).fetchone()
        if existing is not None:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO operator_notification_channels (
                    operator_id,
                    channel_type,
                    route_key,
                    target_label,
                    is_active,
                    dry_run_only,
                    verified_at,
                    created_at,
                    updated_at
                ) VALUES (
                    :operator_id,
                    'telegram',
                    :route_key,
                    :target_label,
                    :is_active,
                    :dry_run_only,
                    NULL,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "operator_id": operator_id,
                "route_key": LEGACY_TELEGRAM_ROUTE_KEY,
                "target_label": "legacy configured Telegram chat",
                "is_active": True,
                "dry_run_only": False,
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("operator_id", sa.Integer(), nullable=False),
            sa.Column("channel_type", sa.String(length=50), nullable=False),
            sa.Column("route_key", sa.String(length=120), nullable=False),
            sa.Column("target_label", sa.String(length=120), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("dry_run_only", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "operator_id",
                "channel_type",
                "route_key",
                name="uq_operator_notification_channels_operator_channel_route",
            ),
        )
        op.create_index(
            op.f("ix_operator_notification_channels_operator_id"),
            TABLE_NAME,
            ["operator_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_operator_notification_channels_channel_type"),
            TABLE_NAME,
            ["channel_type"],
            unique=False,
        )
        op.create_index(
            op.f("ix_operator_notification_channels_route_key"),
            TABLE_NAME,
            ["route_key"],
            unique=False,
        )

    _backfill_canonical_telegram_channel()


def downgrade() -> None:
    op.drop_index(
        op.f("ix_operator_notification_channels_route_key"),
        table_name=TABLE_NAME,
    )
    op.drop_index(
        op.f("ix_operator_notification_channels_channel_type"),
        table_name=TABLE_NAME,
    )
    op.drop_index(
        op.f("ix_operator_notification_channels_operator_id"),
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)
