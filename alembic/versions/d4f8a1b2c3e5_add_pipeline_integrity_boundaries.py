"""add pipeline integrity boundaries and lineage

Revision ID: d4f8a1b2c3e5
Revises: c3e7f9a1b2d4
Create Date: 2026-08-04 09:30:00.000000

"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f8a1b2c3e5"
down_revision: Union[str, None] = "c3e7f9a1b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_crawl_accounting() -> None:
    with op.batch_alter_table("crawl_jobs") as batch:
        batch.add_column(sa.Column("category", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("execution_mode", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("max_items", sa.Integer(), nullable=True))
        for name in (
            "received_count",
            "normalized_count",
            "duplicate_count",
            "dropped_count",
            "persisted_count",
        ):
            batch.add_column(
                sa.Column(name, sa.Integer(), nullable=False, server_default="0")
            )
        batch.add_column(sa.Column("source_total_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pages_fetched", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("release_sha", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("release_tag", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("drop_reasons", sa.JSON(), nullable=True))
        batch.create_index("ix_crawl_jobs_category", ["category"])
        batch.create_index("ix_crawl_jobs_release_sha", ["release_sha"])


def _add_monitor_lineage() -> None:
    with op.batch_alter_table("operator_strategy_runs") as batch:
        batch.add_column(
            sa.Column(
                "projection_not_ready_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("release_sha", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("release_tag", sa.String(length=255), nullable=True))
        batch.create_index("ix_operator_strategy_runs_release_sha", ["release_sha"])
    with op.batch_alter_table("bid_decision_records") as batch:
        batch.add_column(sa.Column("monitor_run_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_bid_decision_records_monitor_run_id",
            "operator_strategy_runs",
            ["monitor_run_id"],
            ["id"],
        )
        batch.create_index(
            "ix_bid_decision_records_monitor_run_id", ["monitor_run_id"]
        )
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("monitor_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("decision_record_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notifications_monitor_run_id",
            "operator_strategy_runs",
            ["monitor_run_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_notifications_project_id", "projects", ["project_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_notifications_decision_record_id",
            "bid_decision_records",
            ["decision_record_id"],
            ["id"],
        )
        batch.create_index("ix_notifications_monitor_run_id", ["monitor_run_id"])
        batch.create_index("ix_notifications_project_id", ["project_id"])
        batch.create_index(
            "ix_notifications_decision_record_id", ["decision_record_id"]
        )
        batch.create_unique_constraint(
            "uq_notifications_monitor_run_project_type",
            ["monitor_run_id", "project_id", "type"],
        )

    op.create_table(
        "operator_strategy_run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("operator_strategy_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column(
            "decision_record_id",
            sa.Integer(),
            sa.ForeignKey("bid_decision_records.id"),
            nullable=True,
        ),
        sa.Column(
            "notification_id",
            sa.Integer(),
            sa.ForeignKey("notifications.id"),
            nullable=True,
        ),
        sa.Column("is_new_candidate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "run_id", "project_id", name="uq_operator_strategy_run_items_run_project"
        ),
    )
    for name, columns in (
        ("ix_operator_strategy_run_items_run_id", ["run_id"]),
        ("ix_operator_strategy_run_items_project_id", ["project_id"]),
        ("ix_operator_strategy_run_items_status", ["status"]),
        ("ix_operator_strategy_run_items_decision_record_id", ["decision_record_id"]),
        ("ix_operator_strategy_run_items_notification_id", ["notification_id"]),
        ("ix_operator_strategy_run_items_run_status", ["run_id", "status"]),
    ):
        op.create_index(name, "operator_strategy_run_items", columns)

    op.create_table(
        "notification_delivery_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "notification_id",
            sa.Integer(),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("monitor_run_id", sa.Integer(), sa.ForeignKey("operator_strategy_runs.id"), nullable=True),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("decision_record_id", sa.Integer(), sa.ForeignKey("bid_decision_records.id"), nullable=True),
        sa.Column("channel", sa.String(length=30), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "notification_id", "channel", name="uq_notification_delivery_channel"
        ),
    )
    for name, columns in (
        ("ix_notification_delivery_outbox_monitor_run_id", ["monitor_run_id"]),
        ("ix_notification_delivery_outbox_operator_id", ["operator_id"]),
        ("ix_notification_delivery_outbox_project_id", ["project_id"]),
        ("ix_notification_delivery_outbox_decision_record_id", ["decision_record_id"]),
        ("ix_notification_delivery_outbox_status", ["status"]),
        ("ix_notification_delivery_outbox_claim", ["status", "available_at", "id"]),
    ):
        op.create_index(name, "notification_delivery_outbox", columns)


def _add_tender_result_grain(bind) -> None:
    with op.batch_alter_table("tender_results") as batch:
        batch.add_column(
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.create_index("ix_tender_results_is_current", ["is_current"])

    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY project_id
                           ORDER BY
                               CASE
                                   WHEN COALESCE(winning_amount, 0) > 0
                                     OR COALESCE(winning_rate, 0) > 0
                                     OR COALESCE(winning_company, '') <> '' THEN 0
                                   WHEN COALESCE(opening_rank1_company, '') <> '' THEN 1
                                   ELSE 2
                               END,
                               COALESCE(announced_at, opened_at, created_at) DESC,
                               id DESC
                       ) AS rn
                FROM tender_results
                WHERE project_id IS NOT NULL
            )
            UPDATE tender_results
               SET is_current = CASE
                   WHEN id IN (SELECT id FROM ranked WHERE rn = 1) THEN TRUE
                   ELSE FALSE
               END
             WHERE project_id IS NOT NULL
            """
        )
    )
    op.create_index(
        "uq_tender_results_current_project",
        "tender_results",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_current IS TRUE"),
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "tender_result_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tender_result_id",
            sa.Integer(),
            sa.ForeignKey("tender_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("event_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tender_result_events_project_id", "tender_result_events", ["project_id"])

    rows = bind.execute(
        sa.text(
            "SELECT id, project_id, winning_company, winning_amount, winning_rate, "
            "result_status, announced_at, opening_rank1_company, "
            "opening_rank1_business_no, opening_rank1_amount, opening_rank1_rate, "
            "opening_participant_count, opened_at, opening_checked_at, created_at, "
            "is_current FROM tender_results ORDER BY id"
        )
    ).mappings()
    events = sa.table(
        "tender_result_events",
        sa.column("tender_result_id", sa.Integer()),
        sa.column("project_id", sa.Integer()),
        sa.column("event_key", sa.String()),
        sa.column("event_type", sa.String()),
        sa.column("payload_json", sa.JSON()),
        sa.column("observed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    batch: list[dict] = []
    for row in rows:
        payload = {
            key: (value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in row.items()
            if key not in {"id", "project_id", "created_at"}
        }
        batch.append(
            {
                "tender_result_id": row["id"],
                "project_id": row["project_id"],
                "event_key": hashlib.sha256(
                    f"legacy:{row['id']}".encode("utf-8")
                ).hexdigest(),
                "event_type": "legacy_snapshot",
                "payload_json": payload,
                "observed_at": row["announced_at"] or row["opened_at"],
                "created_at": row["created_at"] or datetime.now(timezone.utc),
            }
        )
        if len(batch) >= 1000:
            bind.execute(events.insert(), batch)
            batch = []
    if batch:
        bind.execute(events.insert(), batch)


def _add_project_notice_identity(bind) -> None:
    trim_function = "btrim" if bind.dialect.name == "postgresql" else "trim"
    duplicate = bind.execute(
        sa.text(
            f"SELECT {trim_function}(notice_number) AS notice_number, COUNT(*) "
            "FROM projects WHERE notice_number IS NOT NULL "
            f"AND {trim_function}(notice_number) <> '' "
            f"GROUP BY {trim_function}(notice_number) HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot add canonical project notice uniqueness; duplicate evidence "
            f"must be reviewed first: {duplicate[0]} count={duplicate[1]}"
        )
    expression = "btrim(notice_number)" if bind.dialect.name == "postgresql" else "trim(notice_number)"
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_projects_notice_number_canonical "
            f"ON projects ({expression}) WHERE notice_number IS NOT NULL "
            f"AND {expression} <> ''"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _add_crawl_accounting()
    _add_monitor_lineage()
    _add_tender_result_grain(bind)
    _add_project_notice_identity(bind)


def downgrade() -> None:
    # Data was deliberately preserved and reclassified. A destructive downgrade
    # could discard lineage/events, so this migration is intentionally one-way.
    pass
