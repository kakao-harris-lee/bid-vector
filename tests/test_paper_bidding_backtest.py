"""Paper-bidding backtest services."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models.models import (
    CompanyProfile,
    HistoricalData,
    OperatorStrategy,
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    Project,
    TenderResult,
    User,
)
from app.schemas.paper_bidding_audit import BacktestDataAuditCategoryRow
from app.schemas.paper_bidding_items import PaperBiddingSettlementInput
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

    assert report.window_counts.usable_award_count == 1
    assert report.window_counts.pending_or_opening_snapshot_count == 1
    assert report.category_breakdown == [
        BacktestDataAuditCategoryRow(
            category="construction",
            usable_award_count=1,
            distinct_project_count=1,
        )
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


def test_historical_backtest_marks_run_failed_and_reraises_on_error(
    test_db, monkeypatch
):
    """Run-level failure path: an exception raised *after* the run row is created
    fails the persisted run (``_fail_run``) and re-raises unchanged.

    Characterization guard for the prepare/execute extraction: ``_create_run``
    runs in the prepare phase (a create-time failure must propagate *without*
    ``_fail_run``), while the award-processing failure is caught by the execute
    phase's ``except`` and marks the run ``failed`` before re-raising.
    """
    from app.core.single_user import ensure_operator_account

    ensure_operator_account(test_db)
    service = PaperBiddingBacktestService()

    def _explode(*args, **kwargs):
        raise RuntimeError("award processing exploded")

    monkeypatch.setattr(service, "_process_historical_awards", _explode)

    with pytest.raises(RuntimeError, match="award processing exploded"):
        service.run_historical_backtest(
            test_db,
            operator_id=None,
            category="construction",
            start_at=_dt(2025, 3, 1),
            end_at=_dt(2025, 3, 31),
            limit=5,
            persist=True,
        )

    # The run was created in the prepare phase, then marked failed by the execute
    # phase's except branch (status/error_message/completed_at all recorded).
    run = test_db.query(PaperBidRun).one()
    assert run.status == "failed"
    assert run.error_message == "award processing exploded"
    assert run.completed_at is not None


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


def test_backtests_api_tolerates_legacy_and_corrupt_run_payloads(client, test_db):
    """DTO 승격 회귀 가드: 과거/손상된 run payload 하나가 목록·상세를 500 으로 만들면 안 된다.

    ``result_payload``/``request_payload`` 를 ``model_validate_json`` 으로 되읽게 바뀐
    뒤, (1) 지금 지표에 없는 키가 섞인 과거 요약, (2) 깨진 JSON 이 모두 degrade 경로로
    처리되는지 고정한다. 요약은 항상 객체 모양을 유지하고(소비처의 ``summary.<field>``
    접근이 깨지지 않게), 해석 못한 요청 스냅샷은 ``null``(부재)로 남는다.
    """
    headers, operator_id = _bootstrap_and_auth(client, username="legacy-payload-api")
    legacy = PaperBidRun(
        operator_id=operator_id,
        strategy_version="legacy",
        model_version="legacy",
        status="completed",
        mode="historical_backtest",
        scenario="base",
        data_cutoff_policy="deadline_minus_2h",
        started_at=_dt(2025, 1, 1),
        request_payload='{"limit": 5, "legacy_only_flag": true}',
        result_payload='{"settled_count": 3, "legacy_win_count": 9}',
    )
    corrupt = PaperBidRun(
        operator_id=operator_id,
        strategy_version="corrupt",
        model_version="corrupt",
        status="completed",
        mode="forward_paper",
        scenario="base",
        data_cutoff_policy="execution_time",
        started_at=_dt(2025, 1, 2),
        request_payload="{not json",
        result_payload="{not json",
    )
    test_db.add_all([legacy, corrupt])
    test_db.commit()

    listed = client.get("/api/v1/backtests/paper-bidding/runs", headers=headers)
    assert listed.status_code == 200
    summaries = {item["id"]: item["summary"] for item in listed.json()["items"]}
    assert summaries[legacy.id]["settled_count"] == 3
    assert "legacy_win_count" not in summaries[legacy.id]
    # 손상된 payload 는 빈(0) 요약으로 degrade 하고 평균 오차는 부재를 유지한다.
    assert summaries[corrupt.id]["settled_count"] == 0
    assert summaries[corrupt.id]["average_absolute_bid_rate_error"] is None

    legacy_detail = client.get(
        f"/api/v1/backtests/paper-bidding/runs/{legacy.id}", headers=headers
    )
    assert legacy_detail.status_code == 200
    legacy_request = legacy_detail.json()["request"]
    assert legacy_request["limit"] == 5
    # 기록되지 않은 필드는 null 로 남는다 — 기본값으로 채워 "이 run 은 persist=false 로
    # 돌았다"고 오독하게 만들지 않는다.
    assert legacy_request["persist"] is None
    assert legacy_request["scenario"] is None
    assert legacy_request["settle_actions"] is None
    assert "legacy_only_flag" not in legacy_request

    corrupt_detail = client.get(
        f"/api/v1/backtests/paper-bidding/runs/{corrupt.id}", headers=headers
    )
    assert corrupt_detail.status_code == 200
    assert corrupt_detail.json()["request"] is None


def test_backtests_api_summary_without_runs_returns_empty_summary_object(
    client, test_db
):
    """실행이 없어도 ``latest_summary`` 는 객체 모양을 유지한다(종전 ``{}`` 대신 0 요약)."""
    headers, _ = _bootstrap_and_auth(client, username="no-run-summary-api")

    response = client.get("/api/v1/backtests/paper-bidding/summary", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["latest_run"] is None
    assert payload["latest_summary"]["settled_count"] == 0
    assert payload["latest_summary"]["average_absolute_bid_rate_error"] is None


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


def test_forward_paper_skips_zero_budget_project_and_completes(client, test_db):
    """REGRESSION: a single 0-budget project must not abort the entire run.

    Production paper_bid_run 10 (2026-05-28) and 12 (2026-05-30) both failed
    completely because project 19908 had budget_estimate=0 — the run aborted
    after that project, marking the run failed even though preceding projects
    had already produced valid paper_bids.
    """
    headers, _ = _bootstrap_and_auth(client, username="forward-zero-budget")

    good_project = _project(
        title="Open forward paper project",
        category="construction",
        deadline=datetime.now(UTC) + timedelta(days=3),
        created_at=datetime.now(UTC) - timedelta(days=2),
        budget=100_000_000,
    )
    good_project.status = "open"
    bad_project = _project(
        title="Zero budget ebiz4u-link project",
        category="construction",
        deadline=datetime.now(UTC) + timedelta(days=3),
        created_at=datetime.now(UTC) - timedelta(days=1),
        budget=0,
    )
    bad_project.status = "open"
    test_db.add(good_project)
    test_db.add(bad_project)
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
    summary = payload["summary"]
    # Good project produces a paper_bid; bad project is skipped, run completes.
    assert summary["candidate_count"] == 1
    assert summary["skipped_invalid_count"] == 1
    run = test_db.query(PaperBidRun).filter(PaperBidRun.id == payload["run_id"]).one()
    assert run.status == "completed"


def _make_operator_with_profile(
    db,
    *,
    username: str,
    business_type: str,
    license_codes: str = "",
    region_codes: str = "전국",
    annual_revenue: float = 1_000_000_000,
    capacity_score: float = 0.85,
    total_awards: int = 6,
) -> User:
    """Create an operator + company profile + permissive strategy for backtests.

    The strategy intentionally has empty filters so ``_passes_strategy`` lets every
    candidate through and the matched score (driven by the profile) is what varies.
    """
    operator = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username,
        company=username,
        hashed_password="x",
        is_active=True,
    )
    db.add(operator)
    db.flush()
    db.add(
        CompanyProfile(
            user_id=operator.id,
            business_type=business_type,
            license_codes=license_codes,
            region_codes=region_codes,
            annual_revenue=annual_revenue,
            capacity_score=capacity_score,
            total_awards=total_awards,
        )
    )
    db.add(
        OperatorStrategy(
            user_id=operator.id,
            focus_categories="",
            focus_regions="",
            exclude_regions="",
            required_keywords="",
            exclude_keywords="",
            min_budget_estimate=0.0,
            max_budget_estimate=0.0,
            minimum_match_score=0.0,
            minimum_probability_score=0.0,
            bid_now_threshold=0.7,
            review_threshold=0.45,
            auto_workload_penalty_multiplier=1.0,
            category_priority_overrides="{}",
            notify_only_high_priority=False,
            max_recommended_candidates=10,
        )
    )
    db.flush()
    return operator


def _matched_score_for_operator(db, *, operator_id: int) -> float:
    result = PaperBiddingBacktestService().run_historical_backtest(
        db,
        operator_id=operator_id,
        category="construction",
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=5,
        persist=False,
        settle_actions=("bid_now", "review"),
    )
    assert len(result["items"]) == 1
    return result["items"][0]


def test_backtest_matched_score_reflects_operator_profile_difference(test_db):
    """REGRESSION: matched_score must depend on each operator's CompanyProfile.

    Previously ``_estimate_matched_score`` only looked at project field presence, so
    every operator received an identical hardcoded score (0.72 + bonuses). Now the
    score flows through the real classifier, so a profile that fits the construction
    tender (matching business type + required license) must score differently from a
    profile that does not (software business type, no matching license).
    """
    target = _project(
        title="도로 정비 공사 입찰",
        category="construction",
        budget=100_000_000,
        deadline=_dt(2025, 3, 10),
        created_at=_dt(2025, 2, 1),
    )
    # Explicit license requirement so the license axis discriminates between profiles.
    target.requirements = "전기공사업 면허 보유 필수 (ELE001), 지역제한 서울 소재 업체만 참여 가능"
    target.description = "서울 지역 도로 정비 및 전기 공사 통합 수행 대규모 프로젝트"
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

    fit_operator = _make_operator_with_profile(
        test_db,
        username="synthetic-construction-fit",
        business_type="construction",
        license_codes="ELE001, 전기공사업",
        region_codes="서울",
    )
    mismatch_operator = _make_operator_with_profile(
        test_db,
        username="synthetic-software-mismatch",
        business_type="software",
        license_codes="SW001",
        region_codes="부산",
    )
    test_db.commit()

    fit_item = _matched_score_for_operator(test_db, operator_id=fit_operator.id)
    mismatch_item = _matched_score_for_operator(
        test_db, operator_id=mismatch_operator.id
    )

    fit_score = fit_item["matched_score"]
    mismatch_score = mismatch_item["matched_score"]

    # Core regression assertion: a hardcoded score would make these identical.
    assert fit_score != mismatch_score
    # The fitting profile (matching business type, license, region) scores higher.
    assert fit_score > mismatch_score
    # Both came through the real classifier path, not the heuristic fallback.
    assert fit_item["matched_score_source"] == "classifier"
    assert mismatch_item["matched_score_source"] == "classifier"
    # Classifier reasons are surfaced for audit.
    assert fit_item["match_reasons"]
    assert "[프로필 매칭]" in fit_item["reasoning"]


def test_backtest_matched_score_falls_back_to_heuristic_without_profile(
    test_db, monkeypatch
):
    """When no profile resolves, the legacy heuristic path is used and never throws."""
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

    service = PaperBiddingBacktestService()
    # Force the resolver to return no profile to exercise the fallback branch.
    monkeypatch.setattr(
        service, "_resolve_operator_profile", lambda db, *, operator: None
    )

    result = service.run_historical_backtest(
        test_db,
        category="construction",
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=5,
        persist=False,
        settle_actions=("bid_now", "review"),
    )

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["matched_score_source"] == "heuristic"
    # Heuristic score for a category + agency + requirements project = 0.72+0.08+0.05+0.05.
    assert item["matched_score"] == 0.9
    assert item["match_reasons"] == []
    # Reasoning is the bare decision reasoning (no profile-fit note appended).
    assert "[프로필 매칭]" not in item["reasoning"]


def test_backtest_classifier_failure_falls_back_without_aborting_run(
    test_db, monkeypatch
):
    """A classifier exception must degrade to the heuristic, not abort the run."""
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
    operator = _make_operator_with_profile(
        test_db,
        username="synthetic-classifier-boom",
        business_type="construction",
    )
    test_db.commit()

    service = PaperBiddingBacktestService()

    def _boom(*args, **kwargs):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(service.classifier, "classify", _boom)

    result = service.run_historical_backtest(
        test_db,
        operator_id=operator.id,
        category="construction",
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=5,
        persist=False,
        settle_actions=("bid_now", "review"),
    )

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["matched_score_source"] == "heuristic"
    assert 0.0 <= item["matched_score"] <= 1.0


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


# ---------------------------------------------------------------------------
# Settlement eligibility gate (낙찰하한가 기준 판정)
# ---------------------------------------------------------------------------
#
# 예정가격 = mean of the *selected* 복수예비가격. With four 100M reserves all
# selected, ``estimated_price`` == 100M and (construction floor 0.87) the
# 낙찰하한가 == 87M, so the gate boundaries are easy to reason about.


def _settled_historical_for(
    project: Project,
    *,
    reserve_prices: list[float] | None = None,
    selected_numbers: list[int] | None = None,
    predicted_price: float = 0.0,
    category: str = "construction",
    opened_at: datetime = _dt(2025, 2, 2),
) -> HistoricalData:
    return HistoricalData(
        project_id=project.id,
        notice_number=f"GATE-{project.id}",
        agency_name="Seoul",
        category=category,
        base_amount=100_000_000,
        predicted_price=predicted_price,
        bid_rate=0.88,
        reserve_prices=json.dumps(
            reserve_prices
            if reserve_prices is not None
            else [100_000_000, 100_000_000, 100_000_000, 100_000_000]
        ),
        selected_numbers=json.dumps(
            selected_numbers if selected_numbers is not None else [1, 2, 3, 4]
        ),
        opened_at=opened_at,
    )


def _settlement_for(
    test_db, *, paper_bid_amount: float, paper_bid_rate: float, winning_amount: float
) -> dict:
    target = _project(deadline=_dt(2025, 2, 1), created_at=_dt(2025, 1, 20))
    test_db.add(target)
    test_db.flush()
    test_db.add(_settled_historical_for(target))
    result = TenderResult(
        project_id=target.id,
        winning_company="Winner",
        winning_amount=winning_amount,
        winning_rate=winning_amount / 100_000_000,
        result_status="awarded",
        announced_at=_dt(2025, 2, 2),
    )
    test_db.add(result)
    test_db.flush()
    item = PaperBiddingSettlementInput(
        project_id=target.id,
        category="construction",
        budget_estimate=100_000_000,
        paper_bid_amount=paper_bid_amount,
        paper_bid_rate=paper_bid_rate,
    )
    return PaperBiddingBacktestService()._build_settlement_item(
        test_db, item=item, tender_result=result
    )


def test_settlement_gate_eligible_favorable_above_floor_below_winning(test_db):
    # 88M >= floor 87M and <= winning 88.1M -> eligible_favorable
    settlement = _settlement_for(
        test_db,
        paper_bid_amount=88_000_000,
        paper_bid_rate=0.88,
        winning_amount=88_100_000,
    )
    assert settlement.would_have_won_final == "eligible_favorable"
    assert settlement.estimated_price == 100_000_000
    assert settlement.minimum_bid_price == 87_000_000


def test_settlement_gate_disqualified_below_floor_even_when_price_close(test_db):
    # 86.9M < floor 87M -> disqualified, EVEN THOUGH the rate is within 0.3%p of
    # the winning rate (price proximity is not a win once below 낙찰하한가).
    settlement = _settlement_for(
        test_db,
        paper_bid_amount=86_900_000,
        paper_bid_rate=0.869,
        winning_amount=87_000_000,  # winning_rate 0.87, ours 0.869 -> 0.1%p apart
    )
    assert settlement.price_close is True  # within 0.3%p -- price proximity holds
    assert settlement.would_have_won_final == "disqualified"
    assert settlement.minimum_bid_price == 87_000_000


def test_settlement_gate_eligible_but_outbid_above_winning(test_db):
    # 90M >= floor 87M but > winning 88M -> eligible_but_outbid
    settlement = _settlement_for(
        test_db,
        paper_bid_amount=90_000_000,
        paper_bid_rate=0.90,
        winning_amount=88_000_000,
    )
    assert settlement.would_have_won_final == "eligible_but_outbid"


def test_settlement_gate_unknown_when_no_estimate_data(test_db):
    # No HistoricalData for the project -> estimated_price/floor None -> unknown,
    # and the call must not crash.
    target = _project(deadline=_dt(2025, 2, 1), created_at=_dt(2025, 1, 20))
    test_db.add(target)
    test_db.flush()
    result = TenderResult(
        project_id=target.id,
        winning_company="Winner",
        winning_amount=88_100_000,
        winning_rate=0.881,
        result_status="awarded",
        announced_at=_dt(2025, 2, 2),
    )
    test_db.add(result)
    test_db.flush()
    item = PaperBiddingSettlementInput(
        project_id=target.id,
        category="construction",
        budget_estimate=100_000_000,
        paper_bid_amount=88_000_000,
        paper_bid_rate=0.88,
    )
    settlement = PaperBiddingBacktestService()._build_settlement_item(
        test_db, item=item, tender_result=result
    )
    assert settlement.would_have_won_final == "unknown"
    assert settlement.estimated_price is None
    assert settlement.minimum_bid_price is None


def test_settlement_gate_persists_estimated_and_floor_columns(test_db):
    # End-to-end through a historical backtest: the run persists a settlement whose
    # estimated_price/minimum_bid_price columns are populated and gate is applied.
    target = _project(deadline=_dt(2025, 3, 10), created_at=_dt(2025, 2, 1))
    test_db.add(target)
    test_db.flush()
    # Unlinked history feeds the predictor; the linked settled row supplies 예가.
    for index in range(8):
        test_db.add(
            _historical_row(
                None, opened_at=_dt(2025, 1, 1) + timedelta(days=index), bid_rate=0.88
            )
        )
    test_db.add(_settled_historical_for(target))
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

    assert result["summary"]["settled_count"] == 1
    settlement = test_db.query(PaperBidSettlement).one()
    assert settlement.estimated_price == 100_000_000
    assert settlement.minimum_bid_price == 87_000_000
    assert settlement.would_have_won_final in {
        "eligible_favorable",
        "eligible_but_outbid",
        "disqualified",
    }
    # Summary carries the additive gate counts without dropping the legacy proxy.
    assert "would_have_won_price_only_count" in result["summary"]
    assert "would_have_won_final_disqualified_count" in result["summary"]


def _build_candidate_for_target(test_db, target: Project) -> dict:
    """Build a candidate item for ``target`` through the real predictor path.

    Seeds unlinked history so the predictor has a sample (the cutoff loader
    excludes the target's own project_id) and returns the candidate dict.
    """
    return PaperBiddingBacktestService()._build_candidate_item(
        test_db,
        project=target,
        tender_result=None,
        data_cutoff_at=_dt(2025, 3, 9),
        scenario="base",
        strategy_version="leak-test",
        cutoff_hours_before_deadline=0,
        history_limit=80,
        profile=None,
    )


class CapturingPredictionPort:
    def __init__(self) -> None:
        self.calls = []

    def predict_price(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "predicted_bid_rate": 0.88,
            "predictor_name": "fake",
            "predicted_price": 88_000_000.0,
            "price_range_min": 87_000_000.0,
            "price_range_max": 89_000_000.0,
            "confidence_score": 0.75,
            "model_version": "fake",
            "predictor_family": "fake",
            "pricing_mode": "heuristic",
            "historical_sample_size": 0,
            "agency_match_sample_size": 0,
            "bid_rate_candidates": [],
            "price_regime_features": {},
            "review_required": False,
            "explanation": "fake",
        }


def test_candidate_item_uses_injected_prediction_port(test_db):
    target = _project(
        title="Port injected tender",
        category="software",
        budget=100_000_000,
        deadline=_dt(2025, 3, 10),
        created_at=_dt(2025, 2, 1),
    )
    target.description = "port injected description"
    target.requirements = "port injected requirements"
    target.business_type_code = "0621"
    test_db.add(target)
    test_db.flush()

    port = CapturingPredictionPort()
    item = PaperBiddingBacktestService(price_prediction_port=port)._build_candidate_item(
        test_db,
        project=target,
        tender_result=None,
        data_cutoff_at=_dt(2025, 3, 9),
        scenario="base",
        strategy_version="port-test",
        cutoff_hours_before_deadline=0,
        history_limit=80,
        profile=None,
    )

    assert port.calls
    assert port.calls[0]["budget"] == 100_000_000
    assert port.calls[0]["category"] == "software"
    assert "port injected description" in port.calls[0]["description"]
    assert port.calls[0]["business_type_code"] == "0621"
    assert item.predicted_bid_rate == 0.88
    assert item.predictor_name == "fake"


def test_candidate_prediction_feeds_published_award_floor(test_db):
    """REGRESSION: the backtest predict path now feeds the notice's OWN published
    낙찰하한율 (award_floor_rate, #201) into the predictor — the floor the LIVE path
    enforces was previously dropped here, so accuracy was measured on a different
    (floor-less) input than the pipeline that places bids. era-correct: award_floor_rate
    is published at announcement, not future information."""
    target = _project(
        title="Floor wiring tender",
        category="construction",
        budget=100_000_000,
        deadline=_dt(2025, 3, 10),
        created_at=_dt(2025, 2, 1),
    )
    target.description = "floor wiring description"
    target.requirements = "floor wiring requirements"
    target.award_floor_rate = 0.88  # published as a fraction
    test_db.add(target)
    test_db.flush()

    port = CapturingPredictionPort()
    PaperBiddingBacktestService(price_prediction_port=port)._build_candidate_item(
        test_db,
        project=target,
        tender_result=None,
        data_cutoff_at=_dt(2025, 3, 9),
        scenario="base",
        strategy_version="floor-test",
        cutoff_hours_before_deadline=0,
        history_limit=80,
        profile=None,
    )

    assert port.calls
    # The published 하한 reaches the predictor (max()-only fold downstream).
    assert port.calls[0]["legal_floor_bid_rate"] == pytest.approx(0.88)
    # The unified text assembler carries title + description + requirements.
    description = port.calls[0]["description"]
    assert "Floor wiring tender" in description
    assert "floor wiring requirements" in description


def test_candidate_prediction_no_award_floor_passes_none(test_db):
    """A notice with no published 하한 passes legal_floor_bid_rate=None, so the
    configured category floor is preserved downstream (no spurious clamp)."""
    target = _project(
        title="No floor tender",
        category="construction",
        budget=100_000_000,
        deadline=_dt(2025, 3, 10),
        created_at=_dt(2025, 2, 1),
    )
    test_db.add(target)
    test_db.flush()

    port = CapturingPredictionPort()
    PaperBiddingBacktestService(price_prediction_port=port)._build_candidate_item(
        test_db,
        project=target,
        tender_result=None,
        data_cutoff_at=_dt(2025, 3, 9),
        scenario="base",
        strategy_version="floor-test",
        cutoff_hours_before_deadline=0,
        history_limit=80,
        profile=None,
    )

    assert port.calls
    assert port.calls[0]["legal_floor_bid_rate"] is None


def test_candidate_prediction_ignores_target_own_reserve_prices(test_db):
    """REGRESSION (leakage): the prediction/candidate path must NOT read the
    target project's own settled reserve_prices / 예정가격.

    Only the *settlement* gate may consume that settled data. Adding a settled
    HistoricalData row for the target itself (with reserve_prices populated) must
    leave the candidate's prediction inputs/outputs unchanged -- otherwise future
    awards would leak into the bid the model recommends.
    """
    # Two structurally-identical targets so each gets an isolated history set.
    baseline_target = _project(
        title="기준 프로젝트", deadline=_dt(2025, 3, 10), created_at=_dt(2025, 2, 1)
    )
    leaky_target = _project(
        title="기준 프로젝트", deadline=_dt(2025, 3, 10), created_at=_dt(2025, 2, 1)
    )
    test_db.add_all([baseline_target, leaky_target])
    test_db.flush()

    # Identical unlinked history feeds the predictor for BOTH targets.
    for index in range(8):
        opened_at = _dt(2025, 1, 1) + timedelta(days=index)
        test_db.add(_historical_row(None, opened_at=opened_at, bid_rate=0.88))

    # Only the leaky target gets its OWN settled HistoricalData with reserve
    # prices. Its opened_at is AFTER the bid-decision cutoff (2025-03-09): a
    # genuine future-award leak, which must never enter ANY candidate's history.
    # If the predictor read it (via the project_id-exclusion gap), the leaky
    # candidate's prediction would diverge from the baseline.
    test_db.add(
        _settled_historical_for(
            leaky_target,
            reserve_prices=[50_000_000, 50_000_000, 50_000_000, 50_000_000],
            selected_numbers=[1, 2, 3, 4],
            opened_at=_dt(2025, 3, 15),
        )
    )
    test_db.flush()

    baseline_item = _build_candidate_for_target(test_db, baseline_target)
    leaky_item = _build_candidate_for_target(test_db, leaky_target)

    # The prediction-derived fields must be identical -- the target's own settled
    # reserve data did not leak into the candidate.
    for key in (
        "predicted_price",
        "predicted_bid_rate",
        "paper_bid_amount",
        "paper_bid_rate",
        "price_range_min",
        "price_range_max",
        "historical_sample_size",
        "action",
    ):
        assert getattr(baseline_item, key) == getattr(
            leaky_item, key
        ), f"leakage via {key!r}"


@pytest.mark.parametrize(
    ("raw_value", "expected_floats", "expected_ints"),
    [
        (None, [], []),
        ("", [], []),
        ("   ", [], []),
        (
            "[99000000, 100000000]",
            [99_000_000.0, 100_000_000.0],
            [99_000_000, 100_000_000],
        ),
        ([1, 2], [1.0, 2.0], [1, 2]),
        ((1, 2), [1.0, 2.0], [1, 2]),
        ('["3", 4]', [3.0, 4.0], [3, 4]),
        ("[1, null, 2]", [1.0, 2.0], [1, 2]),
        ("not json", [], []),
        ("123", [], []),
        ('{"a": 1}', [], []),
    ],
)
def test_reserve_json_columns_coerce_without_raw_json_calls(
    raw_value, expected_floats, expected_ints
):
    """``reserve_prices``/``selected_numbers`` Text 컬럼 해석 계약.

    ``json.loads`` 직접 호출을 pydantic ``TypeAdapter`` 로 바꾼 뒤에도 관용 범위가
    동일해야 한다: 리스트가 아니거나 깨진 JSON 은 "표본 없음"(``[]``)이고, 원소 단위
    변환 실패는 그 원소만 버린다(예정가 유도가 한 행 때문에 죽지 않는다).
    """
    service = PaperBiddingBacktestService()
    assert service._coerce_float_list(raw_value) == expected_floats
    assert service._coerce_int_list(raw_value) == expected_ints


def test_derive_estimated_price_falls_back_to_predicted_price(test_db):
    """``_derive_estimated_price`` falls back to ``predicted_price`` when the
    selected-reserve mean is unavailable (empty selected_numbers)."""
    target = _project(deadline=_dt(2025, 2, 1), created_at=_dt(2025, 1, 20))
    test_db.add(target)
    test_db.flush()
    # No selected numbers -> cannot average reserves; predicted_price supplies 예가.
    record = _settled_historical_for(
        target,
        reserve_prices=[90_000_000, 95_000_000, 100_000_000],
        selected_numbers=[],
        predicted_price=98_000_000,
    )
    test_db.add(record)
    test_db.flush()

    service = PaperBiddingBacktestService()
    assert service._derive_estimated_price(record) == 98_000_000

    estimated_price, floor_price = service._resolve_settlement_floor(
        test_db, project_id=target.id, category="construction"
    )
    assert estimated_price == 98_000_000
    # construction floor 0.87 applied to the predicted_price fallback.
    assert floor_price == 98_000_000 * 0.87

    result = TenderResult(
        project_id=target.id,
        winning_company="Winner",
        winning_amount=88_000_000,
        winning_rate=0.88,
        result_status="awarded",
        announced_at=_dt(2025, 2, 2),
    )
    test_db.add(result)
    test_db.flush()
    item = PaperBiddingSettlementInput(
        project_id=target.id,
        category="construction",
        budget_estimate=100_000_000,
        paper_bid_amount=90_000_000,  # >= floor (85.26M), > winning 88M
        paper_bid_rate=0.90,
    )
    settlement = service._build_settlement_item(
        test_db, item=item, tender_result=result
    )
    assert settlement.estimated_price == 98_000_000
    assert settlement.would_have_won_final == "eligible_but_outbid"


def test_settlement_gate_unknown_for_category_without_floor_rate(test_db):
    """Estimated price exists but the category has no configured 낙찰하한율
    -> floor is None -> gate is ``unknown``; estimated_price persists, floor NULL."""
    # "other" is not in PREDICTION_CATEGORY_MINIMUM_BID_RATES.
    target = _project(
        category="other", deadline=_dt(2025, 2, 1), created_at=_dt(2025, 1, 20)
    )
    test_db.add(target)
    test_db.flush()
    test_db.add(_settled_historical_for(target, category="other"))
    result = TenderResult(
        project_id=target.id,
        winning_company="Winner",
        winning_amount=88_000_000,
        winning_rate=0.88,
        result_status="awarded",
        announced_at=_dt(2025, 2, 2),
    )
    test_db.add(result)
    test_db.flush()

    service = PaperBiddingBacktestService()
    estimated_price, floor_price = service._resolve_settlement_floor(
        test_db, project_id=target.id, category="other"
    )
    assert estimated_price == 100_000_000
    assert floor_price is None

    item = PaperBiddingSettlementInput(
        project_id=target.id,
        category="other",
        budget_estimate=100_000_000,
        paper_bid_amount=88_000_000,
        paper_bid_rate=0.88,
    )
    settlement = service._build_settlement_item(
        test_db, item=item, tender_result=result
    )
    # Internal consistency: estimated_price non-NULL, minimum_bid_price NULL, gate unknown.
    assert settlement.estimated_price == 100_000_000
    assert settlement.minimum_bid_price is None
    assert settlement.would_have_won_final == "unknown"


# ---------------------------------------------------------------------------
# 과세(VAT) 공고: 투찰가는 기초금액(base) 기준으로 산정돼야 한다
# ---------------------------------------------------------------------------
#
# base_amount(기초금액; 과세면 VAT 포함) > budget_estimate(추정가격; ex-VAT). 과거
# 낙찰률이 base 기준으로 정규화돼 있으므로 predictor budget 도 base 여야 한다. 이전엔
# _resolve_project_budget(=budget_estimate)로 예측해 paper bid ≈ rate × 추정가격이
# 되고, 낙찰하한(≈ base × 0.87) 미만으로 systematically 탈락(disqualified)했다.


def _vat_settled_row(project: Project, *, base_amount: float) -> HistoricalData:
    """Linked settled row carrying the 과세 base + base-relative reserve prices.

    reserve_prices all == ``base_amount`` (selected 1..4) so the derived 예정가격 ==
    base_amount and the 낙찰하한가 == base_amount × 0.87 (construction floor).
    """
    return HistoricalData(
        project_id=project.id,
        notice_number=f"VAT-{project.id}",
        agency_name="Seoul",
        category="construction",
        base_amount=base_amount,
        predicted_price=0.0,
        bid_rate=0.88,
        reserve_prices=json.dumps([base_amount] * 4),
        selected_numbers=json.dumps([1, 2, 3, 4]),
        opened_at=_dt(2025, 2, 2),
    )


def test_paper_bid_anchored_on_base_amount_not_disqualified(test_db):
    """REGRESSION: a 과세 award where base_amount(110M) > budget_estimate(100M).

    The paper bid must be computed on the base (기초금액), so paper_bid ≈ rate ×
    110M — comparable to the base-relative winning_amount and ABOVE the base-relative
    낙찰하한가. Computing it on 추정가격 would put the bid below the floor and cause a
    systematic ``disqualified`` verdict (corrupting would_have_won_final /
    amount_delta / absolute_error_rate for 과세 notices).
    """
    budget_estimate = 100_000_000
    base_amount = 110_000_000  # 과세: 추정가격 × 1.1
    target = _project(
        deadline=_dt(2025, 3, 10),
        created_at=_dt(2025, 2, 1),
        budget=budget_estimate,
    )
    test_db.add(target)
    test_db.flush()
    # Unlinked history feeds the predictor at a ~0.88 base-relative rate.
    for index in range(8):
        test_db.add(
            _historical_row(
                None, opened_at=_dt(2025, 1, 1) + timedelta(days=index), bid_rate=0.88
            )
        )
    test_db.add(_vat_settled_row(target, base_amount=base_amount))
    result = TenderResult(
        project_id=target.id,
        winning_company="Winner",
        winning_amount=97_000_000,  # base-relative (rate 0.8818 on 110M)
        winning_rate=97_000_000 / base_amount,
        result_status="awarded",
        announced_at=_dt(2025, 2, 2),
    )
    test_db.add(result)
    test_db.commit()

    service = PaperBiddingBacktestService()
    item = service._build_candidate_item(
        test_db,
        project=target,
        tender_result=result,
        data_cutoff_at=_dt(2025, 3, 9),
        scenario="base",
        strategy_version="vat-base-test",
        cutoff_hours_before_deadline=0,
        history_limit=80,
        profile=None,
    )
    settlement = service._build_settlement_item(
        test_db,
        item=PaperBiddingSettlementInput.from_candidate(item),
        tender_result=result,
    )

    rate = item.paper_bid_rate
    assert rate > 0
    # The paper bid is anchored on the 과세 base, ~10% higher than rate × 추정가격.
    assert item.paper_bid_amount == pytest.approx(rate * base_amount, rel=1e-3)
    assert item.paper_bid_amount > rate * budget_estimate * 1.05
    # Reported budget_estimate stays the ex-VAT estimate (not the base).
    assert item.budget_estimate == pytest.approx(budget_estimate)
    # Floor is base-relative (construction 0.87).
    assert settlement.minimum_bid_price == pytest.approx(base_amount * 0.87)
    # The buggy ex-VAT bid WOULD have fallen below the floor (systematic
    # disqualification)...
    assert rate * budget_estimate < settlement.minimum_bid_price
    # ...but the base-anchored bid clears it and is NOT disqualified.
    assert item.paper_bid_amount >= settlement.minimum_bid_price
    assert settlement.would_have_won_final != "disqualified"
    assert settlement.would_have_won_final in {
        "eligible_favorable",
        "eligible_but_outbid",
    }


def test_settlement_winning_rate_fallback_divides_by_base(test_db):
    """REGRESSION: a missing TenderResult.winning_rate is derived as
    winning_amount / base_amount (기초금액), consistent with scsbid.py — NOT divided
    by the ex-VAT 추정가격, which would inflate winning_rate by ~10% for 과세 공고."""
    budget_estimate = 100_000_000
    base_amount = 110_000_000
    target = _project(
        deadline=_dt(2025, 2, 1),
        created_at=_dt(2025, 1, 20),
        budget=budget_estimate,
    )
    test_db.add(target)
    test_db.flush()
    test_db.add(_vat_settled_row(target, base_amount=base_amount))
    result = TenderResult(
        project_id=target.id,
        winning_company="Winner",
        winning_amount=97_000_000,
        winning_rate=0.0,  # missing -> forces the derived fallback
        result_status="awarded",
        announced_at=_dt(2025, 2, 2),
    )
    test_db.add(result)
    test_db.flush()

    item = PaperBiddingSettlementInput(
        project_id=target.id,
        category="construction",
        budget_estimate=budget_estimate,  # reported est (ex-VAT)
        paper_bid_amount=96_800_000,
        paper_bid_rate=0.88,
    )
    settlement = PaperBiddingBacktestService()._build_settlement_item(
        test_db, item=item, tender_result=result
    )

    # winning_rate = 97M / 110M (base), NOT 97M / 100M (추정가격).
    assert settlement.winning_rate == pytest.approx(
        97_000_000 / base_amount, abs=1e-5
    )
    assert settlement.winning_rate != pytest.approx(
        97_000_000 / budget_estimate, abs=1e-5
    )
