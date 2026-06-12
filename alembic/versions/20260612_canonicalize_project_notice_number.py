"""canonicalize projects.notice_number to normalized (upper, whitespace-stripped) form

The ``_find_matching_project`` index fast path probes
``ix_projects_notice_number`` with an equality filter
(``notice_number.in_(...)``). That probe is only exact if the stored column is
always in canonical form -- the same normalization applied to the lookup key:
``upper()`` + all-whitespace removed (see
``KonepsCollectorService._normalize_notice_number``).

``_update_project_from_item`` now writes the column via
``_normalize_notice_number(...)`` so all *new/updated* rows are canonical. This
data migration back-fills any *pre-existing* rows that were persisted before that
fix (the old code stored ``str(notice_number).strip()`` only). KONEPS notice
numbers are already upper-case / whitespace-free in practice, so the affected set
is expected to be small, but canonicalizing guarantees the index probe can never
miss a stored row and create a duplicate project + mis-attributed tender result.

The transformation mirrors ``re.sub(r"\\s+", "", value.strip().upper())`` using
SQL string functions:

    upper(regexp_replace(notice_number, '\\s+', '', 'g'))

``regexp_replace(..., 'g')`` strips every whitespace run anywhere in the value,
matching the Python ``\\s+`` collapse-to-empty behaviour. The ``WHERE`` clause
only touches rows whose stored value differs from its canonical form, so the
update is a no-op on an already-clean table.

Reversible? No -- normalization is lossy (original casing / spacing cannot be
recovered). ``downgrade`` is intentionally a no-op; the canonical value remains a
valid notice number and downgrading the schema does not require restoring the
pre-canonical text.

Revision ID: 1b7d9c4e2a55
Revises: 9a2d6f0b3c11
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1b7d9c4e2a55"
down_revision: Union[str, None] = "9a2d6f0b3c11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _canonical_expr(dialect_name: str) -> str:
    """Return a SQL expression canonicalizing ``notice_number`` for the dialect.

    Canonical form = upper-cased value with every whitespace character removed,
    mirroring ``KonepsCollectorService._normalize_notice_number``
    (``re.sub(r"\\s+", "", value.strip().upper())``).

    PostgreSQL (the production target) has ``regexp_replace`` and strips the full
    ``\\s`` class in one call. SQLite -- used only by the schema-drift test that
    replays ``alembic upgrade head`` on an empty table -- has no ``regexp_replace``,
    so we compose nested ``replace`` calls over the ASCII whitespace characters,
    referenced via ``char(n)`` so no raw control bytes are embedded in the SQL
    text. Both branches yield an identical result for the ASCII notice numbers
    KONEPS emits.
    """
    if dialect_name == "postgresql":
        return "upper(regexp_replace(notice_number, '\\s+', '', 'g'))"

    # ASCII whitespace code points matched by Python's ``\s``: space, tab,
    # newline, carriage return, form feed, vertical tab. ``char(n)`` is supported
    # by both SQLite and PostgreSQL and avoids embedding control bytes.
    expr = "notice_number"
    for code_point in (32, 9, 10, 13, 12, 11):
        expr = f"replace({expr}, char({code_point}), '')"
    return f"upper({expr})"


def upgrade() -> None:
    canonical = _canonical_expr(op.get_bind().dialect.name)
    op.execute(
        f"""
        UPDATE projects
        SET notice_number = {canonical}
        WHERE notice_number IS NOT NULL
          AND notice_number <> {canonical}
        """
    )


def downgrade() -> None:
    # Normalization is lossy: the original casing/whitespace is not recoverable,
    # and the canonical value is still a valid notice number. No-op by design.
    pass
