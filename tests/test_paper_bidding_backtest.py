"""Paper-bidding backtest services."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.models.models import (
    HistoricalData,
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    Project,
    TenderResult,
)
from app.services.backtest_cutoff import BacktestCutoffService
from app.services.backtest_data_audit import BacktestDataAuditService
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.paper_bidding_scheduler import PaperBiddingForwardScheduler


def _bootstrap_and_auth(
    client, username: str = "backtest-operator", password: str = "password123"
):
    bootstrap = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "full_name": "Backtest Operator",
            "company": "Bid Vector Labs",
            "password": password,
        },
    )
    assert bootstrap.status_code == 200
    session = client.post(
        "/api/v1/auth/session", json={"username": username, "password": password}
    )
    assert session.status_code == 200
    payload = session.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}, payload[
        "operator_id"
    ]


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _project(
    *,
    title: str = "Test construction tender",
    category: str = "construction",
    budget: float = 100_000_000,
    created_at: datetime = _dt(2025, 1, 1),
    deadline: datetime = _dt(2025, 2, 1),
) -> Project:
    return Project(
        title=title,
        description="Road maintenance project",
        requirements="standard qualification",
        budget_estimate=budget,
        category=category,
        status="awarded",
        issuing_agency="Seoul",
        created_at=created_at,
        deadline=deadline,
    )


def _historical_row(
    project: Project | None, *, opened_at: datetime, bid_rate: float = 0.88
) -> HistoricalData:
    return HistoricalData(
        project_id=(
            project.id if project is not None and project.id is not None else None
        ),
        notice_number=f"H-{opened_at:%Y%m%d%H}",
        agency_name="Seoul",
        category="construction",
        base_amount=100_000_000,
        predicted_price=100_000_000 * bid_rate,
        bid_rate=bid_rate,
        reserve_prices=json.dumps([99_000_000, 100_000_000, 101_000_000]),
        selected_numbers=json.dumps([1, 4, 7, 11]),
        opened_at=opened_at,
    )


def test_backtest_data_audit_counts_usable_awards_and_pending_snapshots(test_db):
    project = _project()
    test_db.add(project)
    test_db.flush()
    test_db.add_all(
        [
            TenderResult(
                project_id=project.id,
                winning_company="Winner",
                winning_amount=88_000_000,
                winning_rate=0.88,
                result_status="awarded",
                announced_at=_dt(2025, 2, 2),
            ),
            TenderResult(
                project_id=project.id,
                winning_company="",
                winning_amount=0,
                winning_rate=0,
                result_status="pending",
                announced_at=None,
                created_at=_dt(2025, 2, 1),
            ),
        ]
    )
    test_db.commit()

    report = BacktestDataAuditService().build_report(
        test_db, categories=["construction"]
    )

    assert report["window_counts"]["usable_award_count"] == 1
    assert report["window_counts"]["pending_or_opening_snapshot_count"] == 1
    assert report["category_breakdown"] == [
        {
            "category": "construction",
            "usable_award_count": 1,
            "distinct_project_count": 1,
        }
    ]


def test_cutoff_history_loader_excludes_future_and_current_project_rows(test_db):
    target = _project(deadline=_dt(2025, 2, 10))
    old_project = _project(title="Old tender", deadline=_dt(2025, 1, 15))
    test_db.add_all([target, old_project])
    test_db.flush()
    cutoff = BacktestCutoffService().resolve_data_cutoff_at(
        target, hours_before_deadline=2
    )
    old_row = _historical_row(
        old_project, opened_at=cutoff - timedelta(days=3), bid_rate=0.88
    )
    future_row = _historical_row(
        old_project, opened_at=cutoff + timedelta(hours=1), bid_rate=0.95
    )
    same_project_row = _historical_row(
        target, opened_at=cutoff - timedelta(days=1), bid_rate=0.87
    )
    test_db.add_all([old_row, future_row, same_project_row])
    test_db.commit()

    history = BacktestCutoffService().load_price_history_at_cutoff(
        test_db,
        category="construction",
        cutoff_at=cutoff,
        exclude_project_id=target.id,
        limit=10,
    )

    assert [item["historical_data_id"] for item in history] == [old_row.id]


def test_cutoff_history_loader_falls_back_to_related_price_category(test_db):
    target = _project(
        title="Technical service target",
        category="technical-service",
        deadline=_dt(2025, 2, 10),
    )
    target.issuing_agency = "No Matching Agency"
    test_db.add(target)
    test_db.flush()
    cutoff = BacktestCutoffService().resolve_data_cutoff_at(
        target, hours_before_deadline=2
    )
    unrelated_construction = HistoricalData(
        notice_number="CONST-1",
        agency_name="Other Agency",
        category="construction",
        base_amount=100_000_000,
        predicted_price=90_000_000,
        bid_rate=0.90,
        opened_at=cutoff - timedelta(days=5),
    )
    related_service = HistoricalData(
        notice_number="SERVICE-1",
        agency_name="Other Agency",
        category="service",
        base_amount=100_000_000,
        predicted_price=89_000_000,
        bid_rate=0.89,
        opened_at=cutoff - timedelta(days=4),
    )
    test_db.add_all([unrelated_construction, related_service])
    test_db.commit()

    history = BacktestCutoffService().load_price_history_at_cutoff(
        test_db,
        category="technical-service",
        agency_name=target.issuing_agency,
        cutoff_at=cutoff,
        exclude_project_id=target.id,
        limit=10,
    )

    assert [item["historical_data_id"] for item in history] == [related_service.id]


def test_paper_bidding_backtest_persists_run_bids_and_settlements(test_db):
    target = _project(deadline=_dt(2025, 3, 10), created_at=_dt(2025, 2, 1))
    test_db.add(target)
    test_db.flush()
    for index in range(8):
        opened_at = _dt(2025, 1, 1) + timedelta(days=index)
        test_db.add(
            _historical_row(None, opened_at=opened_at, bid_rate=0.88 + (index * 0.0005))
        )
    test_db.add(
        TenderResult(
            project_id=target.id,
            winning_company="Winner",
            winning_amount=88_100_000,
            winning_rate=0.881,
            result_status="awarded",
            announced_at=_dt(2025, 3, 11),
        )
    )
    test_db.commit()

    result = PaperBiddingBacktestService().run_historical_backtest(
        test_db,
        category="construction",
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=5,
        persist=True,
        settle_actions=("bid_now", "review"),
    )

    assert result["run_id"] is not None
    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["settled_count"] == 1
    assert test_db.query(PaperBidRun).count() == 1
    assert test_db.query(PaperBid).count() == 1
    assert test_db.query(PaperBidSettlement).count() == 1
    paper_bid = test_db.query(PaperBid).one()
    assert paper_bid.action in {"bid_now", "review"}
    assert paper_bid.input_snapshot_hash
    settlement = test_db.query(PaperBidSettlement).one()
    assert settlement.winning_amount == 88_100_000
    assert settlement.would_have_won_final == "unknown"


def test_backtests_api_runs_persisted_historical_backtest_and_returns_detail(
    client, test_db
):
    headers, operator_id = _bootstrap_and_auth(client, username="backtest-api")
    target = _project(deadline=_dt(2025, 3, 10), created_at=_dt(2025, 2, 1))
    test_db.add(target)
    test_db.flush()
    for index in range(8):
        test_db.add(
            _historical_row(
                None, opened_at=_dt(2025, 1, 1) + timedelta(days=index), bid_rate=0.88
            )
        )
    test_db.add(
        TenderResult(
            project_id=target.id,
            winning_company="Winner",
            winning_amount=88_000_000,
            winning_rate=0.88,
            result_status="awarded",
            announced_at=_dt(2025, 3, 11),
        )
    )
    test_db.commit()

    audit = client.get(
        "/api/v1/backtests/data-audit?category=construction", headers=headers
    )
    assert audit.status_code == 200
    assert audit.json()["window_counts"]["usable_award_count"] == 1

    created = client.post(
        "/api/v1/backtests/paper-bidding/runs",
        headers=headers,
        json={
            "category": "construction",
            "start_at": "2025-03-01T00:00:00Z",
            "end_at": "2025-03-31T23:59:59Z",
            "limit": 10,
            "settle_actions": ["bid_now", "review"],
            "persist": True,
        },
    )
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    assert run_id is not None
    assert created.json()["summary"]["settled_count"] == 1

    listed = client.get("/api/v1/backtests/paper-bidding/runs", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["operator_id"] == operator_id
    assert listed.json()["items"][0]["id"] == run_id

    detail = client.get(
        f"/api/v1/backtests/paper-bidding/runs/{run_id}", headers=headers
    )
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["paper_bids"][0]["project_id"] == target.id
    assert payload["settlements"][0]["winning_amount"] == 88_000_000

    summary = client.get("/api/v1/backtests/paper-bidding/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["latest_run"]["id"] == run_id
    settlement_overview = summary.json()["latest_run"]["settlement_overview"]
    assert settlement_overview["status"] == "settled"
    assert settlement_overview["settled_count"] == 1
    assert settlement_overview["unsettled_count"] == 0


def test_backtests_api_forward_run_creates_unsettled_paper_bids(client, test_db):
    headers, _ = _bootstrap_and_auth(client, username="forward-paper-api")
    open_project = _project(
        title="Open forward paper project",
        category="construction",
        deadline=datetime.now(UTC) + timedelta(days=3),
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    open_project.status = "open"
    test_db.add(open_project)
    test_db.flush()
    for index in range(8):
        test_db.add(
            _historical_row(
                None,
                opened_at=datetime.now(UTC) - timedelta(days=20 - index),
                bid_rate=0.88,
            )
        )
    test_db.commit()

    response = client.post(
        "/api/v1/backtests/paper-bidding/forward-runs",
        headers=headers,
        json={"category": "construction", "limit": 5, "persist": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] is not None
    assert payload["summary"]["candidate_count"] == 1
    assert payload["settlements"] == []
    assert (
        test_db.query(PaperBidRun)
        .filter(
            PaperBidRun.id == payload["run_id"], PaperBidRun.mode == "forward_paper"
        )
        .count()
        == 1
    )
    assert test_db.query(PaperBid).count() == 1
    assert test_db.query(PaperBidSettlement).count() == 0

    summary = client.get("/api/v1/backtests/paper-bidding/summary", headers=headers)
    assert summary.status_code == 200
    settlement_overview = summary.json()["latest_run"]["settlement_overview"]
    assert settlement_overview["status"] == "before_deadline"
    assert settlement_overview["before_deadline_count"] == 1
    assert settlement_overview["settled_count"] == 0
    assert settlement_overview["next_deadline_at"] is not None


def test_forward_paper_scheduler_builds_configured_payload(monkeypatch):
    from app.core.config import settings
    from app.tasks.celery_app import (
        PAPER_BIDDING_FORWARD_TASK_NAME,
        build_paper_bidding_forward_beat_schedule,
    )

    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_INTERVAL_MINUTES", 60)
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT", 12)
    monkeypatch.setattr(
        settings, "PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY", "construction"
    )
    monkeypatch.setattr(
        settings, "PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO", "aggressive"
    )
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT", 40)
    monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST", True)

    payload = PaperBiddingForwardScheduler().build_request_payload()
    assert payload["category"] == "construction"
    assert payload["limit"] == 12
    assert payload["scenario"] == "aggressive"
    assert payload["history_limit"] == 40
    assert payload["persist"] is True

    schedule = build_paper_bidding_forward_beat_schedule()
    assert (
        schedule["paper_bidding_forward_periodic"]["task"]
        == PAPER_BIDDING_FORWARD_TASK_NAME
    )
    assert schedule["paper_bidding_forward_periodic"]["schedule"] == 3600.0
    assert (
        schedule["paper_bidding_forward_periodic"]["kwargs"]["request_payload"][
            "category"
        ]
        == "construction"
    )
