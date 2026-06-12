"""add celery_task_id idempotency key to crawl_jobs

Adds a nullable ``celery_task_id`` column to ``crawl_jobs`` so a redelivered
KONEPS collection task can reuse the ``running`` crawl-job row it already
created instead of spawning a fresh orphan row on every redelivery.

Background: scsbid (open-bid result) collection ran past the hard Celery time
limit, which SIGKILLed the worker child. With ``task_acks_late=True`` the broker
redelivered the same message and ``collect_koneps_notices`` created a brand-new
``running`` ``CrawlJob`` each time (``crawl_job_id`` is ``None`` on beat
dispatch), accumulating orphan rows. Stamping the Celery task id on the row lets
the task look up and reuse its own in-flight job.

The column is nullable with no server default, so the migration is a safe
zero-data-loss ADD COLUMN against a populated table. A secondary index keeps the
idempotency lookup cheap.

Revision ID: 9a2d6f0b3c11
Revises: 8f1c4a2d7b9e
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9a2d6f0b3c11"
down_revision: Union[str, None] = "8f1c4a2d7b9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "crawl_jobs",
        sa.Column("celery_task_id", sa.String(length=155), nullable=True),
    )
    op.create_index(
        "ix_crawl_jobs_celery_task_id",
        "crawl_jobs",
        ["celery_task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_crawl_jobs_celery_task_id", table_name="crawl_jobs")
    op.drop_column("crawl_jobs", "celery_task_id")
