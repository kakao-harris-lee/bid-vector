"""Tests for the settlement-coverage group of the operations KPI endpoint.

Covers the ``settlement_coverage`` block added alongside the existing six KPI
groups: overall paper-bid settlement progress plus the ``forward_paper`` subset
that the automated forward-settlement job is responsible for closing.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.models import (
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    Project,
)


def _bootstrap_operator(
    client,
    username="coverage-operator",
    email="coverage@example.com",
    password="password123",
):
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "Coverage Operator",
            "company": "Coverage Bid Corp",
            "password": password,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _make_project(test_db, *, title):
    project = Project(
        title=title,
        description="Settlement coverage fixture project",
        requirements="coverage instrumentation",
        budget_estimate=100000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.flush()
    return project.id


def _make_run(test_db, *, operator_id, mode):
    run = PaperBidRun(operator_id=operator_id, mode=mode, status="completed")
    test_db.add(run)
    test_db.flush()
    return run.id


def _make_paper_bid(test_db, *, run_id, operator_id, project_id, created_at):
    paper_bid = PaperBid(
        run_id=run_id,
        operator_id=operator_id,
        project_id=project_id,
        action="bid_now",
        decision_status="planned",
        paper_bid_amount=90000000.0,
        created_at=created_at,
    )
    test_db.add(paper_bid)
    test_db.flush()
    return paper_bid.id


def _settle(test_db, *, paper_bid_id):
    test_db.add(
        PaperBidSettlement(
            paper_bid_id=paper_bid_id,
            result_status="settled",
            winning_amount=88000000.0,
        )
    )
    test_db.flush()


def test_settlement_coverage_counts_overall_and_forward_subset(client, test_db):
    """Mixed forward/historical paper bids with partial settlement.

    Overall: 4 paper bids, 2 settled -> coverage 0.5.
    Forward subset: 2 forward paper bids, 1 settled -> forward coverage 0.5.
    """
    operator_id = _bootstrap_operator(client)
    now = datetime.now(UTC)
    recent = now - timedelta(days=2)

    forward_run = _make_run(test_db, operator_id=operator_id, mode="forward_paper")
    backtest_run = _make_run(
        test_db, operator_id=operator_id, mode="historical_backtest"
    )

    # Forward: one settled, one not.
    fwd_settled = _make_paper_bid(
        test_db,
        run_id=forward_run,
        operator_id=operator_id,
        project_id=_make_project(test_db, title="Forward Settled"),
        created_at=recent,
    )
    _make_paper_bid(
        test_db,
        run_id=forward_run,
        operator_id=operator_id,
        project_id=_make_project(test_db, title="Forward Unsettled"),
        created_at=recent,
    )
    # Historical: one settled, one not.
    hist_settled = _make_paper_bid(
        test_db,
        run_id=backtest_run,
        operator_id=operator_id,
        project_id=_make_project(test_db, title="Historical Settled"),
        created_at=recent,
    )
    _make_paper_bid(
        test_db,
        run_id=backtest_run,
        operator_id=operator_id,
        project_id=_make_project(test_db, title="Historical Unsettled"),
        created_at=recent,
    )

    _settle(test_db, paper_bid_id=fwd_settled)
    _settle(test_db, paper_bid_id=hist_settled)
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})
    assert response.status_code == 200, response.text
    coverage = response.json()["settlement_coverage"]

    assert coverage["total_paper_bids"] == 4
    assert coverage["settled_count"] == 2
    assert coverage["coverage_rate"] == pytest.approx(0.5, abs=0.0001)
    assert coverage["forward_paper_bids"] == 2
    assert coverage["forward_settled_count"] == 1
    assert coverage["forward_coverage_rate"] == pytest.approx(0.5, abs=0.0001)


def test_settlement_coverage_excludes_paper_bids_outside_window(client, test_db):
    """Paper bids created before the period start are excluded from all counts."""
    operator_id = _bootstrap_operator(client)
    now = datetime.now(UTC)

    forward_run = _make_run(test_db, operator_id=operator_id, mode="forward_paper")

    # In window: one forward paper bid, settled.
    in_window = _make_paper_bid(
        test_db,
        run_id=forward_run,
        operator_id=operator_id,
        project_id=_make_project(test_db, title="In Window"),
        created_at=now - timedelta(days=3),
    )
    _settle(test_db, paper_bid_id=in_window)

    # Out of window (created 60 days ago, window is 30 days) -> excluded entirely.
    old_settled = _make_paper_bid(
        test_db,
        run_id=forward_run,
        operator_id=operator_id,
        project_id=_make_project(test_db, title="Old Settled"),
        created_at=now - timedelta(days=60),
    )
    _settle(test_db, paper_bid_id=old_settled)
    _make_paper_bid(
        test_db,
        run_id=forward_run,
        operator_id=operator_id,
        project_id=_make_project(test_db, title="Old Unsettled"),
        created_at=now - timedelta(days=90),
    )
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})
    assert response.status_code == 200, response.text
    coverage = response.json()["settlement_coverage"]

    assert coverage["total_paper_bids"] == 1
    assert coverage["settled_count"] == 1
    assert coverage["coverage_rate"] == pytest.approx(1.0, abs=0.0001)
    assert coverage["forward_paper_bids"] == 1
    assert coverage["forward_settled_count"] == 1
    assert coverage["forward_coverage_rate"] == pytest.approx(1.0, abs=0.0001)


def test_settlement_coverage_zero_denominator_returns_null_rates(client, test_db):
    """No paper bids in the period -> zero counts and null coverage rates."""
    _bootstrap_operator(client)

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})
    assert response.status_code == 200, response.text
    coverage = response.json()["settlement_coverage"]

    assert coverage["total_paper_bids"] == 0
    assert coverage["settled_count"] == 0
    assert coverage["coverage_rate"] is None
    assert coverage["forward_paper_bids"] == 0
    assert coverage["forward_settled_count"] == 0
    assert coverage["forward_coverage_rate"] is None


def test_settlement_coverage_does_not_disturb_existing_kpi_groups(client, test_db):
    """The six pre-existing KPI groups remain present alongside settlement_coverage."""
    _bootstrap_operator(client)

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})
    assert response.status_code == 200, response.text
    payload = response.json()

    for group in (
        "manual_override",
        "conversion",
        "prediction_accuracy",
        "missed_opportunities",
        "review_time",
        "recommendation_feedback",
        "settlement_coverage",
    ):
        assert group in payload, f"missing KPI group: {group}"
