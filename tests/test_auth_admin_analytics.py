"""Tests for operator-first auth, analytics, and legacy admin compatibility."""


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