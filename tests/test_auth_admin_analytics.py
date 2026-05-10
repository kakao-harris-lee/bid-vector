"""Tests for operator-first auth, analytics, and legacy admin compatibility."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.models import BidDecisionRecord, PricePrediction, TenderResult


def _bootstrap_operator(client, username: str = "solo-operator", email: str = "solo@example.com", password: str = "password123"):
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "Solo Operator",
            "company": "Solo Bid Corp",
            "password": password,
        },
    )
    return response


def test_bootstrap_operator_and_get_me(client):
    """Bootstrap should initialize the singleton operator and expose it via /me."""
    response = _bootstrap_operator(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "solo-operator"
    assert payload["email"] == "solo@example.com"

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["username"] == "solo-operator"
    assert me_payload["is_active"] is True


def test_legacy_register_alias_rejects_second_operator(client):
    """The legacy register alias should reject creating a second operator in single-user mode."""
    first = _bootstrap_operator(client)
    assert first.status_code == 200

    second = client.post(
        "/api/v1/auth/register",
        json={
            "username": "another-operator",
            "email": "another@example.com",
            "full_name": "Another Operator",
            "company": "Another Corp",
            "password": "password123",
        },
    )

    assert second.status_code == 400
    assert "single-user mode already has an operator account" in second.json()["detail"].lower()


def test_session_login_accepts_json_body_and_returns_operator_metadata(client):
    """Session creation should work from a JSON body and return operator metadata."""
    bootstrap = _bootstrap_operator(client, username="session-operator", email="session@example.com", password="secret123")
    assert bootstrap.status_code == 200

    response = client.post(
        "/api/v1/auth/session",
        json={"username": "session-operator", "password": "secret123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["operator_id"] >= 1
    assert payload["username"] == "session-operator"


def test_operator_analytics_endpoints_report_single_user_stats(client):
    """Analytics endpoints should report operator-centric counts and keep the legacy alias compatible."""
    bootstrap = _bootstrap_operator(client, username="analytics-operator", email="analytics@example.com")
    operator_id = bootstrap.json()["id"]

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Analytics Project",
            "description": "Used to verify operator analytics",
            "requirements": "Deliver dashboard metrics",
            "budget_estimate": 100000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    bid_response = client.post(
        "/api/v1/bids/",
        json={
            "project_id": project_id,
            "bid_amount": 95000.0,
            "proposed_timeline": 10,
            "description": "Operator bid for analytics verification",
        },
    )
    assert bid_response.status_code == 200

    log_response = client.post(
        "/api/v1/analytics/event",
        json={
            "event_type": "dashboard_opened",
            "event_data": {"source": "tests"},
        },
    )
    assert log_response.status_code == 200

    summary = client.get("/api/v1/analytics/summary")
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["operator_id"] == operator_id
    assert summary_payload["total_projects"] == 1
    assert summary_payload["total_bids"] == 1
    assert summary_payload["total_events"] == 1
    assert summary_payload["mode"] == "single_operator"

    operator_stats = client.get("/api/v1/analytics/operator-stats")
    assert operator_stats.status_code == 200
    operator_stats_payload = operator_stats.json()
    assert operator_stats_payload["operator_id"] == operator_id
    assert operator_stats_payload["total_bids"] == 1
    assert operator_stats_payload["bids_count"] == 1
    assert operator_stats_payload["mode"] == "single_operator"

    legacy_stats = client.get("/api/v1/analytics/user-stats/999")
    assert legacy_stats.status_code == 200
    legacy_stats_payload = legacy_stats.json()
    assert legacy_stats_payload["operator_id"] == operator_id
    assert legacy_stats_payload["requested_user_id"] == 999
    assert legacy_stats_payload["mode"] == "single_operator"


def test_prediction_feedback_endpoint_summarizes_accuracy_against_tender_results(client, test_db):
    """Prediction feedback endpoint should compare latest predictions and decisions with actual winning amounts."""
    bootstrap = _bootstrap_operator(client, username="feedback-operator", email="feedback@example.com")
    operator_id = bootstrap.json()["id"]

    first_project = client.post(
        "/api/v1/projects/",
        json={
            "title": "Feedback Project One",
            "description": "Used for prediction feedback analytics",
            "requirements": "Collect winning amount feedback",
            "budget_estimate": 110000000.0,
            "category": "software",
        },
    ).json()["id"]
    second_project = client.post(
        "/api/v1/projects/",
        json={
            "title": "Feedback Project Two",
            "description": "Used for recommendation feedback analytics",
            "requirements": "Collect decision feedback",
            "budget_estimate": 100000000.0,
            "category": "software",
        },
    ).json()["id"]

    test_db.add_all([
        PricePrediction(
            user_id=operator_id,
            project_id=first_project,
            predicted_price=101000000.0,
            price_range_min=99000000.0,
            price_range_max=103000000.0,
            confidence_score=0.82,
            model_version="v1.1-historical",
        ),
        PricePrediction(
            user_id=operator_id,
            project_id=second_project,
            predicted_price=90000000.0,
            price_range_min=88000000.0,
            price_range_max=94000000.0,
            confidence_score=0.74,
            model_version="v1.1-historical",
        ),
        BidDecisionRecord(
            project_id=first_project,
            operator_id=operator_id,
            pursue_bid=True,
            action="bid_now",
            decision_status="submitted",
            recommended_amount=102000000.0,
            probability_score=0.9,
            matched_score=0.84,
            priority_score=0.91,
            current_active_bids=0,
            max_active_bids=3,
            current_workload_score=0.1,
            reasoning="첫 번째 피드백 검증용 추천값입니다.",
        ),
        BidDecisionRecord(
            project_id=second_project,
            operator_id=operator_id,
            pursue_bid=True,
            action="review",
            decision_status="reviewing",
            recommended_amount=95000000.0,
            probability_score=0.72,
            matched_score=0.8,
            priority_score=0.69,
            current_active_bids=1,
            max_active_bids=3,
            current_workload_score=0.25,
            reasoning="두 번째 피드백 검증용 추천값입니다.",
        ),
        TenderResult(
            project_id=first_project,
            winning_company="테스트 주식회사",
            winning_amount=103000000.0,
            winning_rate=95.2,
            result_status="awarded",
            announced_at=datetime.now(UTC) - timedelta(days=3),
        ),
        TenderResult(
            project_id=second_project,
            winning_company="추천 개선 주식회사",
            winning_amount=100000000.0,
            winning_rate=94.8,
            result_status="awarded",
            announced_at=datetime.now(UTC) - timedelta(days=2),
        ),
    ])
    test_db.commit()

    response = client.get("/api/v1/analytics/prediction-feedback", params={"days": 30, "limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["result_count"] == 2
    assert payload["prediction_sample_count"] == 2
    assert payload["recommendation_sample_count"] == 2
    assert payload["average_prediction_error_rate"] == pytest.approx(0.0597, abs=0.0001)
    assert payload["average_recommendation_error_rate"] == pytest.approx(0.0299, abs=0.0001)
    assert payload["prediction_within_1_percent_count"] == 0
    assert payload["prediction_within_3_percent_count"] == 1
    assert payload["recommendation_within_1_percent_count"] == 1
    assert payload["recommendation_within_3_percent_count"] == 1
    assert payload["recommendation_better_than_prediction_count"] == 2
    assert payload["items"][0]["project_id"] == second_project
    assert payload["items"][0]["recommendation_improved_vs_prediction"] is True
    assert payload["items"][1]["project_id"] == first_project
    assert payload["items"][1]["prediction_error_rate"] == pytest.approx(0.0194, abs=0.0001)


def test_prediction_feedback_endpoint_returns_empty_summary_when_no_linked_results_exist(client):
    """Prediction feedback endpoint should return an empty but well-formed payload when no comparisons are available."""
    bootstrap = _bootstrap_operator(client, username="empty-feedback-operator", email="empty-feedback@example.com")
    operator_id = bootstrap.json()["id"]

    response = client.get("/api/v1/analytics/prediction-feedback")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["result_count"] == 0
    assert payload["prediction_sample_count"] == 0
    assert payload["recommendation_sample_count"] == 0
    assert payload["average_prediction_error_rate"] is None
    assert payload["average_recommendation_error_rate"] is None
    assert payload["items"] == []


def test_legacy_admin_routes_return_single_operator_snapshot(client):
    """Legacy admin routes should now expose singleton operator state instead of multi-user administration."""
    bootstrap = _bootstrap_operator(client, username="admin-operator", email="admin@example.com", password="adminpass123")
    operator_id = bootstrap.json()["id"]

    users_response = client.get("/api/v1/admin/users")
    assert users_response.status_code == 200
    users_payload = users_response.json()
    assert len(users_payload) == 1
    assert users_payload[0]["id"] == operator_id
    assert users_payload[0]["username"] == "admin-operator"

    stats_response = client.get("/api/v1/admin/stats")
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    assert stats_payload["operator_id"] == operator_id
    assert stats_payload["total_users"] == 1
    assert stats_payload["active_users"] == 1
    assert stats_payload["mode"] == "single_operator"

    deactivate_response = client.put(f"/api/v1/admin/users/{operator_id}/deactivate")
    assert deactivate_response.status_code == 200
    deactivate_payload = deactivate_response.json()
    assert deactivate_payload["status"] == "operator deactivated"
    assert deactivate_payload["requested_user_id"] == operator_id

    failed_session = client.post(
        "/api/v1/auth/session",
        json={"username": "admin-operator", "password": "adminpass123"},
    )
    assert failed_session.status_code == 403