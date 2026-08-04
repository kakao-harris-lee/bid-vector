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
    # OPEN_PROJECT_STATUS survives for ONE consumer only —
    # scripts/measure_reliable_base_impact.py, whose P2 diagnostic buckets by the
    # literal "open" status on purpose. Every "biddable now" query (services and
    # the reporting / backfill scripts) uses the wider {"open", "re_notice"} set.
    # These stay DIFFERENT scopes ("looks-like-a-duplicate-but-is-not" trap):
    # OPEN_PROJECT_STATUS is a strict subset of ACTIVE_PROJECT_STATUSES, never
    # equal to it.
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
        "tender_results.is_current IS true "
        "AND tender_results.winning_amount IS NOT NULL "
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
        "tender_results.is_current IS true "
        "AND (tender_results.winning_amount > 0 "
        "OR tender_results.winning_rate > 0)"
    )


def test_settled_predicates_use_the_same_result_model():
    # Both settled predicates scope to TenderResult so a call site can swap the
    # inline clause for either helper without touching its own project_id join.
    for clause in (settled_with_amount(), settled_any_signal()):
        assert TenderResult.__tablename__ in _sql(clause)


# --- rate-only divergence (STRICT drops, LOOSE keeps) ------------------------


def test_rate_only_row_is_the_strict_loose_divergence(test_db):
    """A settled row with ``winning_rate > 0`` but ``winning_amount == 0`` is the
    exact row the two predicates disagree on — the reason they are kept separate.

    Regression guard for the "do NOT unify" decision (2026-07-26): the KONEPS
    scsbid / browser paths can persist a rate-only settled row (missing
    ``sucsfbidAmt`` / ``낙찰금액``), which LOOSE (dataset / holdout) must keep and
    STRICT (settlement / dashboards) must reject. Collapsing the two predicates
    would silently drop that signal, so this pins the divergence executable.
    """
    rate_only = Project(title="rate-only", category="service", budget_estimate=1000.0)
    with_amount = Project(title="with-amount", category="service", budget_estimate=1000.0)
    test_db.add_all([rate_only, with_amount])
    test_db.flush()
    test_db.add_all(
        [
            # the divergent row: rate present, amount coerced to 0.0 on persist
            TenderResult(
                project_id=rate_only.id,
                winning_company="w",
                winning_amount=0.0,
                winning_rate=0.88,
            ),
            # control: a concrete amount — both predicates keep it
            TenderResult(
                project_id=with_amount.id,
                winning_company="w",
                winning_amount=880.0,
                winning_rate=0.88,
            ),
        ]
    )
    test_db.commit()

    strict_ids = {
        r.project_id for r in test_db.query(TenderResult).filter(settled_with_amount()).all()
    }
    loose_ids = {
        r.project_id for r in test_db.query(TenderResult).filter(settled_any_signal()).all()
    }

    assert rate_only.id in loose_ids  # rate-only KEPT by LOOSE
    assert rate_only.id not in strict_ids  # rate-only DROPPED by STRICT
    assert with_amount.id in strict_ids and with_amount.id in loose_ids  # both keep amount
    # The rate-only row is exactly the set the two predicates disagree on.
    assert loose_ids - strict_ids == {rate_only.id}
