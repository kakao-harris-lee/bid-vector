"""Shared read-query predicates (pure SQLAlchemy condition builders).

Single source of truth for filter conditions that were previously duplicated as
inline literals across services and scripts. Each helper returns a SQLAlchemy
``ColumnElement[bool]`` and performs **no I/O**, so it can be dropped into any
``.filter(...)``, ``and_(...)`` or ``exists().where(...)`` unchanged.

Behaviour is preserved on purpose: every helper encodes the EXACT condition its
call sites already used, so the row set each query returns is identical after the
substitution. This module exists only to remove the duplication, not to change
any semantics.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_

from app.core.constants import ACTIVE_PROJECT_STATUSES
from app.models.models import Project, TenderResult


def open_projects() -> ColumnElement[bool]:
    """Projects in a currently *open / biddable* status.

    Matches :data:`app.core.constants.ACTIVE_PROJECT_STATUSES`
    (``{"open", "re_notice"}``) — the single "notices I can bid on now" scope. A
    ``re_notice`` is a re-issued, still-biddable opportunity, so it is included
    everywhere this predicate is used. Consumers:

    - ``app/services/backtest_data_audit.py`` (active-project table count)
    - ``app/services/paper_bidding_backtest.py`` (open-project scan)
    - ``scripts/report_license_eligibility.py`` / ``report_license_gate_impact.py``
    - ``scripts/report_no_candidate_cause.py`` (open-live scan + counters)
    - ``scripts/report_eligibility_segment_backtest.py`` (coverage denominator)
    - ``scripts/backfill_award_floor_rate.py`` (deadline-window target set)

    ``scripts/measure_reliable_base_impact.py`` deliberately does NOT use this
    predicate: its P2 diagnostic buckets by the literal ``"open"`` status
    (:data:`app.core.constants.OPEN_PROJECT_STATUS`) to keep the contamination
    measurement on that exact status question, not the wider biddable set.
    """
    return Project.status.in_(ACTIVE_PROJECT_STATUSES)


def settled_with_amount() -> ColumnElement[bool]:
    """A :class:`TenderResult` with a *usable settled award amount*.

    The STRICT settlement definition: ``winning_amount IS NOT NULL AND
    winning_amount > 0``. Settlement math, dashboards and accuracy reporting all
    require a concrete winning amount, so this is the predicate they share.

    Consumers (each keeps its own ``project_id`` scoping and just reuses this
    ``winning_amount`` clause):

    - ``app/services/prediction_reporting.py`` (latest usable result per project)
    - ``app/services/backtest_data_audit.py`` (``_usable_result_filters``)
    - ``app/services/dashboard_summary.py`` (two latest-result loaders)
    - ``app/services/paper_bidding_backtest.py`` (EXISTS gate + two loaders)
    """
    return and_(
        TenderResult.winning_amount.isnot(None),
        TenderResult.winning_amount > 0,
    )


def settled_any_signal() -> ColumnElement[bool]:
    """A :class:`TenderResult` carrying ANY settlement signal.

    The LOOSE definition: ``winning_amount > 0 OR winning_rate > 0``. Used by
    dataset / holdout construction, which accept a rate-only label when a
    concrete amount is missing.

    Measured delta vs :func:`settled_with_amount` on the production DB
    (2026-07-25): ``winning_rate > 0 AND NOT winning_amount > 0`` = **0 rows**
    (76,728 total). The two definitions therefore select the same rows today, but
    they are kept as SEPARATE named predicates because they encode different
    intent and could diverge again if a winning_rate-without-amount collection
    gap recurs (the historical scsbid fallback scenario). Unifying them is left
    as a follow-up, not done here — this PR only preserves behaviour.

    Consumers:

    - ``app/services/prediction_dataset.py`` (explicit-label EXISTS gate)
    - ``scripts/report_eligibility_segment_backtest.py``
    - ``scripts/backtest_latest_award_holdouts.py`` (two holdout loaders)
    """
    return or_(
        TenderResult.winning_amount > 0,
        TenderResult.winning_rate > 0,
    )
