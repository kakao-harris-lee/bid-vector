"""add synthetic experiment tables

Revision ID: 3c5e7b1a9d2f
Revises: 7e85ae125de7
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3c5e7b1a9d2f"
down_revision: Union[str, None] = "7e85ae125de7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "synthetic_experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("operator_slugs_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "synthetic_experiment_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("task_id", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["experiment_id"], ["synthetic_experiments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_synthetic_experiment_runs_experiment_id"),
        "synthetic_experiment_runs",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_synthetic_experiment_runs_status"),
        "synthetic_experiment_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_synthetic_experiment_runs_task_id"),
        "synthetic_experiment_runs",
        ["task_id"],
        unique=False,
    )

    op.create_table(
        "synthetic_experiment_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("operator_slug", sa.String(length=100), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("settlement_sample_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["synthetic_experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_synthetic_experiment_results_run_id"),
        "synthetic_experiment_results",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_synthetic_experiment_results_operator_slug"),
        "synthetic_experiment_results",
        ["operator_slug"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_synthetic_experiment_results_operator_slug"),
        table_name="synthetic_experiment_results",
    )
    op.drop_index(
        op.f("ix_synthetic_experiment_results_run_id"),
        table_name="synthetic_experiment_results",
    )
    op.drop_table("synthetic_experiment_results")

    op.drop_index(
        op.f("ix_synthetic_experiment_runs_task_id"),
        table_name="synthetic_experiment_runs",
    )
    op.drop_index(
        op.f("ix_synthetic_experiment_runs_status"),
        table_name="synthetic_experiment_runs",
    )
    op.drop_index(
        op.f("ix_synthetic_experiment_runs_experiment_id"),
        table_name="synthetic_experiment_runs",
    )
    op.drop_table("synthetic_experiment_runs")

    op.drop_table("synthetic_experiments")
