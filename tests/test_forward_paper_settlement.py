"""Forward paper-bid settlement service + task + schedule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import json

from app.models.models import (
    HistoricalData,
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    Project,
    TenderResult,
)
from app.services.paper_bidding_backtest import PaperBiddingBacktestService


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _forward_run(test_db, *, operator_id: int = 1) -> PaperBidRun:
    run = PaperBidRun(
        operator_id=operator_id,
        strategy_version="forward-paper",
        model_version="current",
        status="completed",
        mode="forward_paper",
        scenario="base",
        data_cutoff_policy="execution_time",
    )
    test_db.add(run)
    test_db.flush()
    return run


def _open_project(
    test_db,
    *,
    title: str = "Forward settlement project",
    budget: float = 100_000_000,
    deadline: datetime,
) -> Project:
    project = Project(
        title=title,
        description="Road maintenance project",
        requirements="standard qualification",
        budget_estimate=budget,
        category="construction",
        status="open",
        issuing_agency="Seoul",
        created_at=deadline - timedelta(days=10),
        deadline=deadline,
    )
    test_db.add(project)
    test_db.flush()
    return project


def _forward_paper_bid(
    test_db,
    *,
    run: PaperBidRun,
    project: Project,
    operator_id: int = 1,
    paper_bid_amount: float = 88_000_000,
    paper_bid_rate: float = 0.88,
) -> PaperBid:
    paper_bid = PaperBid(
        run_id=run.id,
        project_id=project.id,
        operator_id=operator_id,
        notice_number=project.notice_number,
        action="bid_now",
        decision_status="planned",
        data_cutoff_at=project.created_at,
        paper_bid_amount=paper_bid_amount,
        paper_bid_rate=paper_bid_rate,
        scenario="base",
        strategy_version="forward-paper",
        model_version="current",
    )
    test_db.add(paper_bid)
    test_db.flush()
    return paper_bid


def _tender_result(
    test_db,
    *,
    project: Project,
    winning_amount: float = 88_100_000,
    winning_rate: float = 0.881,
    announced_at: datetime,
) -> TenderResult:
    result = TenderResult(
        project_id=project.id,
        winning_company="Winner",
        winning_amount=winning_amount,
        winning_rate=winning_rate,
        result_status="awarded",
        announced_at=announced_at,
    )
    test_db.add(result)
    test_db.flush()
    return result


def test_forward_settlement_creates_settlement_for_past_deadline_with_result(test_db):
    run = _forward_run(test_db)
    project = _open_project(test_db, deadline=_dt(2025, 2, 1))
    paper_bid = _forward_paper_bid(
        test_db,
        run=run,
        project=project,
        paper_bid_amount=88_000_000,
        paper_bid_rate=0.88,
    )
    _tender_result(
        test_db,
        project=project,
        winning_amount=88_100_000,
        winning_rate=0.881,
        announced_at=_dt(2025, 2, 2),
    )
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(test_db, persist=True)

    assert result["scanned_count"] == 1
    assert result["settled_count"] == 1
    assert result["skipped_count"] == 0
    assert test_db.query(PaperBidSettlement).count() == 1

    settlement = test_db.query(PaperBidSettlement).one()
    assert settlement.paper_bid_id == paper_bid.id
    assert settlement.winning_amount == 88_100_000
    assert settlement.winning_rate == 0.881
    # amount_delta = paper_bid_amount - winning_amount
    assert settlement.amount_delta == 88_000_000 - 88_100_000
    # bid_rate_delta = paper_bid_rate - winning_rate (0.88 - 0.881)
    assert round(settlement.bid_rate_delta, 6) == round(0.88 - 0.881, 6)
    assert settlement.would_have_won_final == "unknown"


def test_forward_settlement_applies_eligibility_gate_with_estimate(test_db):
    # A settled HistoricalData row linked to the project supplies 예정가격
    # (mean of selected reserves == 100M -> 낙찰하한가 87M for construction). The
    # forward settlement path must apply the gate and persist the floor columns.
    run = _forward_run(test_db)
    project = _open_project(test_db, deadline=_dt(2025, 2, 1))
    test_db.add(
        HistoricalData(
            project_id=project.id,
            notice_number=f"GATE-FWD-{project.id}",
            agency_name="Seoul",
            category="construction",
            base_amount=100_000_000,
            predicted_price=0.0,
            bid_rate=0.88,
            reserve_prices=json.dumps(
                [100_000_000, 100_000_000, 100_000_000, 100_000_000]
            ),
            selected_numbers=json.dumps([1, 2, 3, 4]),
            opened_at=_dt(2025, 2, 2),
        )
    )
    _forward_paper_bid(
        test_db,
        run=run,
        project=project,
        paper_bid_amount=88_000_000,  # >= floor 87M and <= winning 88.1M
        paper_bid_rate=0.88,
    )
    _tender_result(
        test_db,
        project=project,
        winning_amount=88_100_000,
        winning_rate=0.881,
        announced_at=_dt(2025, 2, 2),
    )
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(test_db, persist=True)

    assert result["settled_count"] == 1
    settlement = test_db.query(PaperBidSettlement).one()
    assert settlement.estimated_price == 100_000_000
    assert settlement.minimum_bid_price == 87_000_000
    assert settlement.would_have_won_final == "eligible_favorable"


def test_forward_settlement_skips_project_before_deadline(test_db):
    run = _forward_run(test_db)
    future_deadline = datetime.now(UTC) + timedelta(days=3)
    project = _open_project(test_db, deadline=future_deadline)
    _forward_paper_bid(test_db, run=run, project=project)
    # Result already present but deadline not yet passed -> must not settle.
    _tender_result(
        test_db,
        project=project,
        announced_at=datetime.now(UTC),
    )
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(test_db, persist=True)

    assert result["scanned_count"] == 0
    assert result["settled_count"] == 0
    assert test_db.query(PaperBidSettlement).count() == 0


def test_forward_settlement_skips_when_no_tender_result(test_db):
    run = _forward_run(test_db)
    project = _open_project(test_db, deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=run, project=project)
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(test_db, persist=True)

    # A bid whose project has no usable TenderResult is now excluded by the
    # candidate-level EXISTS filter (instead of being scanned-then-skipped), so
    # it never consumes scan budget. The essential outcome is unchanged: it is
    # not settled.
    assert result["scanned_count"] == 0
    assert result["settled_count"] == 0
    assert result["skipped_count"] == 0
    assert test_db.query(PaperBidSettlement).count() == 0


def test_forward_settlement_is_idempotent(test_db):
    run = _forward_run(test_db)
    project = _open_project(test_db, deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=run, project=project)
    _tender_result(test_db, project=project, announced_at=_dt(2025, 2, 2))
    test_db.commit()

    service = PaperBiddingBacktestService()
    first = service.run_forward_settlement(test_db, persist=True)
    assert first["settled_count"] == 1
    assert test_db.query(PaperBidSettlement).count() == 1

    # A second sweep must not create a duplicate settlement.
    second = service.run_forward_settlement(test_db, persist=True)
    assert second["scanned_count"] == 0
    assert second["settled_count"] == 0
    assert test_db.query(PaperBidSettlement).count() == 1


def test_forward_settlement_persist_false_computes_without_writing(test_db):
    run = _forward_run(test_db)
    project = _open_project(test_db, deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=run, project=project)
    _tender_result(
        test_db,
        project=project,
        winning_amount=88_100_000,
        winning_rate=0.881,
        announced_at=_dt(2025, 2, 2),
    )
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(
        test_db, persist=False
    )

    assert result["settled_count"] == 1
    assert len(result["settlements"]) == 1
    assert result["settlements"][0]["winning_amount"] == 88_100_000
    # Nothing persisted when persist=False.
    assert test_db.query(PaperBidSettlement).count() == 0


def test_forward_settlement_summary_counts_are_accurate(test_db):
    run = _forward_run(test_db)
    # Settleable: past deadline + result.
    project_a = _open_project(test_db, title="settleable", deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=run, project=project_a)
    _tender_result(test_db, project=project_a, announced_at=_dt(2025, 2, 2))
    # Not a candidate: past deadline but no usable result -> excluded by the
    # EXISTS filter, so it is neither scanned nor skipped.
    project_b = _open_project(test_db, title="no-result", deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=run, project=project_b)
    # Not a candidate: before deadline (filtered in Python after fetch).
    project_c = _open_project(
        test_db, title="future", deadline=datetime.now(UTC) + timedelta(days=5)
    )
    _forward_paper_bid(test_db, run=run, project=project_c)
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(test_db, persist=True)

    # Only the settle-able bid is a candidate now; the resultless bid is no
    # longer scanned/skipped (EXISTS filter), the future bid is still filtered.
    assert result["scanned_count"] == 1
    assert result["settled_count"] == 1
    assert result["skipped_count"] == 0
    assert result["summary"]["settled_count"] == 1
    assert test_db.query(PaperBidSettlement).count() == 1


def test_forward_settlement_respects_operator_filter(test_db):
    run = _forward_run(test_db, operator_id=1)
    other_run = _forward_run(test_db, operator_id=2)
    project_a = _open_project(test_db, title="op1", deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=run, project=project_a, operator_id=1)
    _tender_result(test_db, project=project_a, announced_at=_dt(2025, 2, 2))
    project_b = _open_project(test_db, title="op2", deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=other_run, project=project_b, operator_id=2)
    _tender_result(test_db, project=project_b, announced_at=_dt(2025, 2, 2))
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(
        test_db, operator_id=1, persist=True
    )

    assert result["operator_id"] == 1
    assert result["settled_count"] == 1
    # Only operator 1's paper bid is settled.
    settlements = test_db.query(PaperBidSettlement).all()
    assert len(settlements) == 1
    assert settlements[0].paper_bid_id == (
        test_db.query(PaperBid).filter(PaperBid.operator_id == 1).one().id
    )


def test_forward_settlement_does_not_starve_on_resultless_old_bids(test_db):
    """Old past-deadline bids without a usable result must not eat the scan budget.

    Regression for head-of-line starvation: before the EXISTS candidate filter,
    ``deadline ASC`` ordering let the oldest resultless bids occupy the bounded
    scan window, so a genuinely settle-able (but later-ordered) bid past the
    ``limit`` cap was never reached and ``settled_count`` stayed 0.
    """
    run = _forward_run(test_db)
    limit = 2

    # limit + 2 oldest past-deadline bids with NO usable TenderResult:
    #   - two with no result at all
    #   - two with a winning_amount=0 (registration-style) result
    starved_bids = []
    for idx in range(limit + 2):
        old_deadline = _dt(2025, 1, 1) + timedelta(days=idx)
        project = _open_project(
            test_db, title=f"resultless-{idx}", deadline=old_deadline
        )
        bid = _forward_paper_bid(test_db, run=run, project=project)
        starved_bids.append(bid)
        if idx % 2 == 0:
            # Half get a non-usable (winning_amount=0) registration-style result.
            _tender_result(
                test_db,
                project=project,
                winning_amount=0,
                winning_rate=0.0,
                announced_at=old_deadline + timedelta(days=1),
            )

    # A newer (later-ordered) settle-able bid WITH a usable result. Its deadline
    # is past but more recent than every starved bid, so deadline ASC pushes it
    # behind all of them and past the limit-based scan cap.
    settleable_deadline = _dt(2025, 6, 1)
    settleable_project = _open_project(
        test_db, title="settleable", deadline=settleable_deadline
    )
    settleable_bid = _forward_paper_bid(
        test_db,
        run=run,
        project=settleable_project,
        paper_bid_amount=88_000_000,
        paper_bid_rate=0.88,
    )
    _tender_result(
        test_db,
        project=settleable_project,
        winning_amount=88_100_000,
        winning_rate=0.881,
        announced_at=settleable_deadline + timedelta(days=1),
    )
    test_db.commit()

    result = PaperBiddingBacktestService().run_forward_settlement(
        test_db, limit=limit, persist=True
    )

    # The settle-able bid is reached and settled despite the starved prefix.
    assert result["settled_count"] >= 1

    settlements = test_db.query(PaperBidSettlement).all()
    settled_bid_ids = {s.paper_bid_id for s in settlements}
    assert settleable_bid.id in settled_bid_ids

    # None of the resultless / winning_amount=0 old bids were settled.
    for bid in starved_bids:
        assert bid.id not in settled_bid_ids


def test_settle_forward_paper_bids_task_runs_under_memory_broker(test_db, monkeypatch):
    run = _forward_run(test_db)
    project = _open_project(test_db, deadline=_dt(2025, 2, 1))
    _forward_paper_bid(test_db, run=run, project=project)
    _tender_result(test_db, project=project, announced_at=_dt(2025, 2, 2))
    test_db.commit()

    import app.tasks.jobs as jobs

    monkeypatch.setattr(jobs, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(test_db, "close", lambda: None)

    result = jobs.settle_forward_paper_bids.apply_async(
        kwargs={"limit": 50, "persist": True}
    )

    payload = result.result
    assert payload["settled_count"] == 1
    assert test_db.query(PaperBidSettlement).count() == 1
