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
    require a concrete winning amount, so this is the predicate they share. It
    deliberately DROPS a rate-only settled row (``winning_rate > 0`` with
    ``winning_amount == 0``) that :func:`settled_any_signal` keeps — see that
    function for the live write path that can produce one and why the two stay
    separate.

    Consumers (each keeps its own ``project_id`` scoping and just reuses this
    ``winning_amount`` clause):

    - ``app/services/prediction_reporting.py`` (latest usable result per project)
    - ``app/services/backtest_data_audit.py`` (``_usable_result_filters``)
    - ``app/services/dashboard_summary.py`` (two latest-result loaders)
    - ``app/services/paper_bidding_backtest.py`` (EXISTS gate + two loaders)
    """
    return and_(
        TenderResult.is_current.is_(True),
        TenderResult.winning_amount.isnot(None),
        TenderResult.winning_amount > 0,
    )


def settled_any_signal() -> ColumnElement[bool]:
    """A :class:`TenderResult` carrying ANY settlement signal.

    The LOOSE definition: ``winning_amount > 0 OR winning_rate > 0``. Used by
    dataset / holdout construction, which accept a rate-only label when a
    concrete amount is missing.

    WHY THIS IS KEPT SEPARATE from :func:`settled_with_amount` (investigated
    2026-07-26): a settled ``TenderResult`` can carry ``winning_rate > 0`` while
    ``winning_amount == 0``. This is a LIVE, not historical, write path:

    - ``koneps/scsbid.py._build_scsbid_item`` derives ``winning_amount =
      coerce_amount(sucsfbidAmt)`` (which returns ``None`` when ``sucsfbidAmt``
      is absent) and ``winning_rate = normalize_bid_rate_value(sucsfbidRate)``
      INDEPENDENTLY; ``koneps/html_parsing.parse_opening_detail`` does the same
      for the browser crawl (``낙찰금액`` vs ``낙찰률`` fields).
    - ``koneps/persistence.resolve_tender_result`` then stores
      ``winning_amount = item_metadata.get("winning_amount") or 0.0`` (coercing
      the missing amount to ``0.0``) while keeping the rate, and
      ``_persist_tender_result_for_item`` gates only on ``opening_status`` /
      ``winning_*`` being present (scsbid always sets ``opening_status="낙찰"``),
      so the row is created even with the amount missing.

    A KONEPS settled row reporting a rate but no amount therefore lands as
    ``winning_rate > 0 AND winning_amount == 0`` — a row STRICT drops and LOOSE
    keeps. Re-measured divergence on the production DB is still **0 rows** (of
    76,730; 2026-07-26) because live settled feeds currently carry both fields,
    but the outcome is DATA-dependent on KONEPS, not guaranteed by code. The two
    predicates are deliberately NOT unified: dataset / holdout construction must
    keep the rate-only settled signal that settlement math and dashboards must
    reject.

    Consumers:

    - ``app/services/prediction_dataset.py`` (explicit-label EXISTS gate)
    - ``scripts/report_eligibility_segment_backtest.py``
    - ``scripts/backtest_latest_award_holdouts.py`` (two holdout loaders)
    """
    return and_(
        TenderResult.is_current.is_(True),
        or_(
            TenderResult.winning_amount > 0,
            TenderResult.winning_rate > 0,
        ),
    )
