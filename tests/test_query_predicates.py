"""Characterization tests for the single-sourced query predicates.

These lock the Tier 0 de-duplication of two families of filter that were
previously copy-pasted as inline literals across services and scripts:

- the "open / biddable project" status filter, and
- the two "settled TenderResult" definitions (strict amount vs any signal).

The tests pin the *compiled SQL* of each predicate so a future edit cannot
silently change the row set any call site selects. Behavior preservation is the
whole point of the refactor, so the assertions encode the exact conditions the
call sites used before consolidation.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.constants import ACTIVE_PROJECT_STATUSES, OPEN_PROJECT_STATUS
from app.models.models import Project, TenderResult
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.query_predicates import (
    open_projects,
    settled_any_signal,
    settled_with_amount,
)


def _sql(clause) -> str:
    """Render a clause with literal binds so values are visible for assertions."""
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


# --- constants ---------------------------------------------------------------


def test_active_project_statuses_value():
    assert ACTIVE_PROJECT_STATUSES == frozenset({"open", "re_notice"})
    assert isinstance(ACTIVE_PROJECT_STATUSES, frozenset)


def test_open_project_status_is_open_only():
    assert OPEN_PROJECT_STATUS == "open"


def test_open_project_status_is_strictly_narrower_than_active_set():
    # The scripts filter on the singular "open"; the services filter on the wider
    # {"open", "re_notice"}. The de-duplication must keep these as DIFFERENT
    # scopes ("looks-like-a-duplicate-but-is-not" trap): OPEN_PROJECT_STATUS is a
    # strict subset of ACTIVE_PROJECT_STATUSES and never equal to it.
    assert OPEN_PROJECT_STATUS in ACTIVE_PROJECT_STATUSES
    assert "re_notice" not in {OPEN_PROJECT_STATUS}
    assert ACTIVE_PROJECT_STATUSES != frozenset({OPEN_PROJECT_STATUS})


def test_opportunity_monitoring_references_single_source():
    # Same identity, not just equal value, proves there is one source of truth.
    assert StrategyMonitoringService.ACTIVE_PROJECT_STATUSES is ACTIVE_PROJECT_STATUSES


# --- open_projects() ---------------------------------------------------------


def test_open_projects_filters_exactly_open_and_re_notice():
    sql = _sql(open_projects())
    assert "projects.status IN (" in sql
    assert "'open'" in sql
    assert "'re_notice'" in sql
    # No third status leaks into the IN list.
    assert sql.count("'") == 4


def test_open_projects_composes_into_a_select_where():
    stmt = select(Project.id).where(open_projects())
    where_sql = _sql(stmt)
    assert "WHERE projects.status IN (" in where_sql


# --- settled_with_amount() (STRICT: winning_amount > 0) ----------------------


def test_settled_with_amount_requires_positive_amount():
    sql = _sql(settled_with_amount())
    assert sql == (
        "tender_results.winning_amount IS NOT NULL "
        "AND tender_results.winning_amount > 0"
    )


def test_settled_with_amount_does_not_reference_winning_rate():
    # The strict definition (dashboards / settlement / reporting) must NOT admit a
    # rate-only row — that is the loose definition's job.
    assert "winning_rate" not in _sql(settled_with_amount())


# --- settled_any_signal() (LOOSE: amount > 0 OR rate > 0) --------------------


def test_settled_any_signal_accepts_amount_or_rate():
    sql = _sql(settled_any_signal())
    assert sql == (
        "tender_results.winning_amount > 0 "
        "OR tender_results.winning_rate > 0"
    )


def test_settled_predicates_use_the_same_result_model():
    # Both settled predicates scope to TenderResult so a call site can swap the
    # inline clause for either helper without touching its own project_id join.
    for clause in (settled_with_amount(), settled_any_signal()):
        assert TenderResult.__tablename__ in _sql(clause)
