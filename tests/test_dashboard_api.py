"""Tests for the authenticated mobile dashboard API."""

from datetime import UTC, datetime, timedelta

from app.models.models import (
    Bid,
    BidDecisionRecord,
    PaperBid,
    PaperBidRun,
    PricePrediction,
    Project,
    TenderResult,
)


def _bootstrap_and_auth(
    client, username: str = "dashboard-operator", password: str = "password123"
):
    bootstrap = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "full_name": "Dashboard Operator",
            "company": "Bid Vector Labs",
            "password": password,
        },
    )
    assert bootstrap.status_code == 200

    session = client.post(
        "/api/v1/auth/session",
        json={"username": username, "password": password},
    )
    assert session.status_code == 200
    payload = session.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}, payload[
        "operator_id"
    ]


def test_dashboard_api_requires_valid_bearer_token(client):
    """Dashboard routes should reject anonymous and invalid dashboard clients."""
    missing = client.get("/api/v1/dashboard/summary")
    assert missing.status_code == 401

    invalid = client.get(
        "/api/v1/dashboard/summary", headers={"Authorization": "Bearer not-a-token"}
    )
    assert invalid.status_code == 401


def test_dashboard_api_returns_stable_empty_schema_for_authenticated_operator(client):
    """Authenticated empty-state responses should keep the mobile dashboard contract stable."""
    headers, operator_id = _bootstrap_and_auth(client)

    summary = client.get("/api/v1/dashboard/summary", headers=headers)
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["operator_id"] == operator_id
    assert summary_payload["work_items"] == []
    assert [section["key"] for section in summary_payload["sections"]] == [
        "opportunities",
        "bids",
        "results",
    ]

    for path in ["opportunities", "bids", "results"]:
        response = client.get(f"/api/v1/dashboard/{path}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["operator_id"] == operator_id
        assert payload["items"] == []
        assert payload["returned_count"] == 0


def test_dashboard_results_include_prediction_and_recommendation_error(client, test_db):
    """Tender results should expose award outcome and prediction/recommendation error fields."""
    headers, operator_id = _bootstrap_and_auth(client, username="dashboard-results")
    now = datetime.now(UTC)
    project = Project(
        title="AI 통합 플랫폼 구축",
        description="Dashboard result project",
        requirements="SW001",
        budget_estimate=100_000_000.0,
        category="software",
        deadline=now + timedelta(hours=8),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    test_db.add_all(
        [
            PricePrediction(
                user_id=operator_id,
                project_id=project.id,
                predicted_price=100_000_000.0,
                price_range_min=98_000_000.0,
                price_range_max=102_000_000.0,
                confidence_score=0.82,
                model_version="dashboard-test",
            ),
            BidDecisionRecord(
                project_id=project.id,
                operator_id=operator_id,
                pursue_bid=True,
                action="review",
                decision_status="reviewing",
                recommended_amount=95_000_000.0,
                probability_score=0.72,
                matched_score=0.8,
                priority_score=0.76,
                urgency_score=0.9,
                reasoning="오차 검증용 추천입니다.",
            ),
            Bid(
                project_id=project.id,
                user_id=operator_id,
                bid_amount=96_000_000.0,
                proposed_timeline=20,
                description="Accepted dashboard test bid",
                status="accepted",
            ),
            TenderResult(
                project_id=project.id,
                winning_company="Bid Vector Labs",
                winning_amount=98_000_000.0,
                winning_rate=94.2,
                result_status="awarded",
                announced_at=now,
            ),
        ]
    )
    test_db.commit()

    response = client.get("/api/v1/dashboard/results", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["returned_count"] == 1
    item = payload["items"][0]
    assert item["project"]["project_id"] == project.id
    assert item["award_outcome"] == "won"
    assert item["winning_amount"] == 98_000_000.0
    assert item["predicted_price"] == 100_000_000.0
    assert item["recommended_amount"] == 95_000_000.0
    assert item["prediction_error_rate"] == 0.0204
    assert item["recommendation_error_rate"] == 0.0306

    summary = client.get("/api/v1/dashboard/summary", headers=headers)
    assert summary.status_code == 200
    work_items = summary.json()["work_items"]
    assert any(item["item_type"] == "opportunity_due" for item in work_items)
    assert any(item["item_type"] == "result_review" for item in work_items)


def test_dashboard_bids_include_latest_decision_status(client, test_db):
    """Bid list rows should include the latest decision record for the same project."""
    headers, operator_id = _bootstrap_and_auth(client, username="dashboard-bids")
    project = Project(
        title="보안 관제 플랫폼 구축",
        description="Dashboard bid project",
        requirements="SEC001",
        budget_estimate=120_000_000.0,
        category="security",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    test_db.add_all(
        [
            BidDecisionRecord(
                project_id=project.id,
                operator_id=operator_id,
                pursue_bid=True,
                action="review",
                decision_status="reviewing",
                recommended_amount=111_000_000.0,
                probability_score=0.61,
                matched_score=0.74,
                priority_score=0.66,
            ),
            BidDecisionRecord(
                project_id=project.id,
                operator_id=operator_id,
                pursue_bid=True,
                action="bid_now",
                decision_status="submitted",
                recommended_amount=112_000_000.0,
                probability_score=0.77,
                matched_score=0.82,
                priority_score=0.84,
            ),
            Bid(
                project_id=project.id,
                user_id=operator_id,
                bid_amount=112_500_000.0,
                proposed_timeline=30,
                description="Dashboard submitted bid",
                status="submitted",
            ),
        ]
    )
    test_db.commit()

    response = client.get("/api/v1/dashboard/bids", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["returned_count"] == 1
    item = payload["items"][0]
    assert item["project"]["project_id"] == project.id
    assert item["status"] == "submitted"
    assert item["decision_status"] == "submitted"
    assert item["recommended_amount"] == 112_000_000.0


def test_dashboard_opportunities_include_latest_forward_paper_candidates(
    client, test_db
):
    """Opportunity tab should include forward paper candidates when no persisted decision exists yet."""
    headers, operator_id = _bootstrap_and_auth(
        client, username="dashboard-paper-opportunities"
    )
    project = Project(
        title="페이퍼 후보 사업",
        description="Forward paper dashboard candidate",
        requirements="AI001",
        budget_estimate=90_000_000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(days=3),
        status="open",
    )
    test_db.add(project)
    test_db.flush()
    run = PaperBidRun(
        operator_id=operator_id,
        status="completed",
        mode="forward_paper",
        strategy_version="forward-paper",
        model_version="current",
        candidate_count=1,
        paper_bid_count=1,
        settled_count=0,
    )
    test_db.add(run)
    test_db.flush()
    test_db.add(
        PaperBid(
            run_id=run.id,
            project_id=project.id,
            operator_id=operator_id,
            action="bid_now",
            decision_status="planned",
            paper_bid_amount=83_000_000.0,
            paper_bid_rate=0.92,
            priority_score=0.86,
            probability_score=0.74,
            matched_score=0.81,
            reasoning="Forward paper candidate should be visible as an opportunity.",
        )
    )
    test_db.commit()

    response = client.get("/api/v1/dashboard/opportunities", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["returned_count"] == 1
    item = payload["items"][0]
    assert item["source"] == "paper_bid"
    assert item["source_label"] == "페이퍼 후보"
    assert item["paper_bid_id"] is not None
    assert item["decision_record_id"] is None
    assert item["decision_status"] == "planned"
    assert item["recommended_amount"] == 83_000_000.0

    summary = client.get("/api/v1/dashboard/summary", headers=headers)
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["sections"][0]["key"] == "opportunities"
    assert summary_payload["sections"][0]["count"] == 1
    assert summary_payload["sections"][1]["key"] == "bids"
    assert summary_payload["sections"][1]["count"] == 0
    assert summary_payload["recent_opportunities"][0]["source"] == "paper_bid"


def test_dashboard_ignores_pending_tender_snapshots(client, test_db):
    """Zero-amount tender snapshots should not count as completed dashboard results."""
    headers, operator_id = _bootstrap_and_auth(
        client, username="dashboard-pending-results"
    )
    project = Project(
        title="개찰 대기 사업",
        description="Dashboard pending result project",
        requirements="SW001",
        budget_estimate=80_000_000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    test_db.add_all(
        [
            Bid(
                project_id=project.id,
                user_id=operator_id,
                bid_amount=76_000_000.0,
                proposed_timeline=14,
                description="Submitted bid waiting for final result",
                status="submitted",
            ),
            TenderResult(
                project_id=project.id,
                winning_company="",
                winning_amount=0.0,
                winning_rate=0.0,
                result_status="pending",
                announced_at=None,
            ),
        ]
    )
    test_db.commit()

    results_response = client.get("/api/v1/dashboard/results", headers=headers)
    assert results_response.status_code == 200
    assert results_response.json()["items"] == []

    summary_response = client.get("/api/v1/dashboard/summary", headers=headers)
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert any(
        item["item_type"] == "bid_pending_result"
        for item in summary_payload["work_items"]
    )
    assert summary_payload["sections"][1]["key"] == "bids"
    assert summary_payload["sections"][1]["count"] == 1
