"""Tests for operator-context support across dashboard + analytics endpoints.

The dashboard (4 routes) and analytics (2 routes) endpoints now accept an
optional ``?operator_id=`` query parameter so canonical/admin callers can fetch
data scoped to synthetic-* companies. Non-privileged callers are limited to
their own data. Every successful response surfaces the resolved operator via
``current_operator_id`` / ``current_operator_username`` so the frontend
switcher can render the active selection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.models.models import (
    Bid,
    BidDecisionRecord,
    CompanyProfile,
    OperatorStrategy,
    Project,
    TenderResult,
    User,
)


def _create_user(
    test_db,
    *,
    username: str,
    password: str = "password123",
    is_admin: bool = False,
    full_name: str | None = None,
    company: str | None = None,
) -> User:
    """Create a User row + matching CompanyProfile so accounts list looks real."""
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=full_name or username,
        company=company or f"{username} Co",
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=is_admin,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    test_db.add(
        CompanyProfile(
            user_id=user.id,
            business_type="software",
            license_codes="",
            region_codes="",
            annual_revenue=0.0,
            capacity_score=0.0,
            total_awards=0,
        )
    )
    test_db.add(
        OperatorStrategy(
            user_id=user.id,
            focus_categories="",
            bid_now_threshold=0.7,
            review_threshold=0.45,
        )
    )
    test_db.commit()
    return user


def _login(client, username: str, password: str = "password123") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_canonical_and_synthetic(test_db) -> tuple[User, User, User]:
    canonical = _create_user(
        test_db,
        username="operator",
        full_name="Canonical Operator",
        company="Bid Vector Labs",
    )
    synthetic = _create_user(
        test_db,
        username="synthetic-sw-small-seoul",
        full_name="Synthetic SW Small Seoul",
        company="Synthetic Co",
    )
    other = _create_user(
        test_db,
        username="other-operator",
        full_name="Some Other Operator",
        company="Other Co",
    )
    return canonical, synthetic, other


def _seed_decision_for(
    test_db, *, operator_id: int, action: str = "review", probability: float = 0.65
) -> tuple[Project, BidDecisionRecord]:
    project = Project(
        title=f"Operator {operator_id} test project",
        description="ctx",
        requirements="",
        budget_estimate=50_000_000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    decision = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator_id,
        pursue_bid=True,
        action=action,
        decision_status="reviewing",
        recommended_amount=45_000_000.0,
        probability_score=probability,
        matched_score=0.7,
        priority_score=0.7,
        urgency_score=0.4,
        reasoning="seed",
    )
    test_db.add(decision)
    test_db.commit()
    return project, decision


# ---------------------------------------------------------------------------
# /api/v1/operator/accounts
# ---------------------------------------------------------------------------


def test_operator_accounts_returns_canonical_plus_synthetic_for_canonical(
    client, test_db
):
    """Canonical operator sees themselves + every synthetic-* row, sorted by id."""
    canonical, synthetic, other = _seed_canonical_and_synthetic(test_db)
    # second synthetic to confirm wildcard match
    _create_user(test_db, username="synthetic-construction-medium")

    headers = _login(client, "operator")
    response = client.get("/api/v1/operator/accounts", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_operator_id"] == canonical.id
    assert payload["current_operator_username"] == "operator"
    assert payload["is_privileged"] is True
    usernames = [item["username"] for item in payload["operators"]]
    assert "operator" in usernames
    assert "synthetic-sw-small-seoul" in usernames
    assert "synthetic-construction-medium" in usernames
    # Non-canonical, non-synthetic accounts are not included even for canonical.
    assert "other-operator" not in usernames
    assert payload["operator_count"] == len(payload["operators"])

    canonical_row = next(item for item in payload["operators"] if item["username"] == "operator")
    assert canonical_row["is_canonical"] is True
    assert canonical_row["is_synthetic"] is False
    synthetic_row = next(
        item for item in payload["operators"] if item["username"] == "synthetic-sw-small-seoul"
    )
    assert synthetic_row["is_canonical"] is False
    assert synthetic_row["is_synthetic"] is True


def test_operator_accounts_returns_self_only_for_non_privileged(client, test_db):
    """Non-canonical/non-admin callers only see themselves in the dropdown."""
    _seed_canonical_and_synthetic(test_db)

    headers = _login(client, "other-operator")
    response = client.get("/api/v1/operator/accounts", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_privileged"] is False
    assert payload["operator_count"] == 1
    assert [item["username"] for item in payload["operators"]] == ["other-operator"]


def test_operator_accounts_works_without_bearer_token(client, test_db):
    """Without a bearer token the API falls back to the canonical operator."""
    _seed_canonical_and_synthetic(test_db)
    response = client.get("/api/v1/operator/accounts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["current_operator_username"] == "operator"
    assert payload["is_privileged"] is True


# ---------------------------------------------------------------------------
# Dashboard endpoints: 4 routes
# ---------------------------------------------------------------------------


_DASHBOARD_PATHS = ("summary", "opportunities", "bids", "results")


def test_dashboard_endpoints_expose_current_operator_for_default_scope(client, test_db):
    """When ``?operator_id`` is omitted the response reports the bearer-token owner."""
    canonical, _synthetic, _other = _seed_canonical_and_synthetic(test_db)
    _seed_decision_for(test_db, operator_id=canonical.id)

    headers = _login(client, "operator")
    for path in _DASHBOARD_PATHS:
        response = client.get(f"/api/v1/dashboard/{path}", headers=headers)
        assert response.status_code == 200, (path, response.text)
        payload = response.json()
        assert payload["current_operator_id"] == canonical.id
        assert payload["current_operator_username"] == "operator"
        assert payload["operator_id"] == canonical.id


def test_dashboard_endpoints_allow_canonical_to_switch_to_synthetic(client, test_db):
    """Canonical operators can fetch a synthetic operator's data via ``?operator_id``."""
    canonical, synthetic, _other = _seed_canonical_and_synthetic(test_db)
    _seed_decision_for(test_db, operator_id=canonical.id, probability=0.55)
    _seed_decision_for(test_db, operator_id=synthetic.id, probability=0.88)

    headers = _login(client, "operator")
    response = client.get(
        "/api/v1/dashboard/opportunities",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["current_operator_id"] == synthetic.id
    assert payload["current_operator_username"] == "synthetic-sw-small-seoul"
    assert payload["operator_id"] == synthetic.id
    assert payload["items"], "synthetic operator's decisions should surface"
    # The canonical-owned decision should not leak through synthetic scope.
    for item in payload["items"]:
        assert item["probability_score"] >= 0.7


def test_dashboard_endpoints_404_for_unknown_operator_id(client, test_db):
    """Unknown ``?operator_id`` values should always 404 even for canonical."""
    _seed_canonical_and_synthetic(test_db)
    headers = _login(client, "operator")
    response = client.get(
        "/api/v1/dashboard/summary",
        params={"operator_id": 999_999},
        headers=headers,
    )
    assert response.status_code == 404


def test_dashboard_endpoints_403_for_non_canonical_cross_operator(client, test_db):
    """Non-privileged callers cannot view another operator's dashboard."""
    canonical, synthetic, _other = _seed_canonical_and_synthetic(test_db)

    headers = _login(client, "other-operator")
    response = client.get(
        "/api/v1/dashboard/summary",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert response.status_code == 403

    # Targeting canonical also denied for non-privileged callers.
    response = client.get(
        "/api/v1/dashboard/results",
        params={"operator_id": canonical.id},
        headers=headers,
    )
    assert response.status_code == 403


def test_dashboard_endpoints_allow_non_canonical_to_target_self(client, test_db):
    """Non-privileged callers may pass their own id explicitly without 403."""
    _seed_canonical_and_synthetic(test_db)
    headers = _login(client, "other-operator")
    self_id = (
        client.get("/api/v1/operator/accounts", headers=headers)
        .json()["current_operator_id"]
    )
    for path in _DASHBOARD_PATHS:
        response = client.get(
            f"/api/v1/dashboard/{path}",
            params={"operator_id": self_id},
            headers=headers,
        )
        assert response.status_code == 200, (path, response.text)
        payload = response.json()
        assert payload["current_operator_id"] == self_id


def test_dashboard_endpoints_admin_can_view_other_operators(client, test_db):
    """Admin-flagged accounts (non-canonical) also count as privileged."""
    _, synthetic, _ = _seed_canonical_and_synthetic(test_db)
    _create_user(test_db, username="admin-user", is_admin=True)

    headers = _login(client, "admin-user")
    response = client.get(
        "/api/v1/dashboard/summary",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["current_operator_id"] == synthetic.id
    assert payload["current_operator_username"] == "synthetic-sw-small-seoul"


# ---------------------------------------------------------------------------
# Analytics endpoints: operations-dashboard + operations-kpi
# ---------------------------------------------------------------------------


def test_analytics_operations_dashboard_default_uses_canonical_when_unauth(
    client, test_db
):
    """Unauthenticated callers retain the legacy canonical-operator behavior."""
    canonical, _synthetic, _other = _seed_canonical_and_synthetic(test_db)
    response = client.get("/api/v1/analytics/operations-dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == canonical.id
    assert payload["current_operator_id"] == canonical.id
    assert payload["current_operator_username"] == "operator"


def test_analytics_operations_dashboard_canonical_can_target_synthetic(client, test_db):
    """Canonical bearer can scope operations-dashboard to a synthetic operator."""
    _canonical, synthetic, _other = _seed_canonical_and_synthetic(test_db)
    headers = _login(client, "operator")
    response = client.get(
        "/api/v1/analytics/operations-dashboard",
        params={"operator_id": synthetic.id},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == synthetic.id
    assert payload["current_operator_id"] == synthetic.id
    assert payload["current_operator_username"] == "synthetic-sw-small-seoul"


def test_analytics_operations_kpi_canonical_can_target_synthetic(client, test_db):
    """Canonical bearer can scope operations-kpi to a synthetic operator."""
    canonical, synthetic, _other = _seed_canonical_and_synthetic(test_db)
    _seed_decision_for(test_db, operator_id=synthetic.id)
    _seed_decision_for(test_db, operator_id=canonical.id)

    headers = _login(client, "operator")
    response = client.get(
        "/api/v1/analytics/operations-kpi",
        params={"operator_id": synthetic.id, "days": 30},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == synthetic.id
    assert payload["current_operator_id"] == synthetic.id
    assert payload["current_operator_username"] == "synthetic-sw-small-seoul"


def test_analytics_endpoints_404_for_unknown_operator_id(client, test_db):
    """Unknown ``?operator_id`` returns 404 for both analytics routes."""
    _seed_canonical_and_synthetic(test_db)
    headers = _login(client, "operator")
    for path in ("operations-dashboard", "operations-kpi"):
        response = client.get(
            f"/api/v1/analytics/{path}",
            params={"operator_id": 999_999},
            headers=headers,
        )
        assert response.status_code == 404, path


def test_analytics_endpoints_403_for_non_privileged_cross_operator(client, test_db):
    """Non-privileged callers are blocked from scoping analytics to other operators."""
    _canonical, synthetic, _other = _seed_canonical_and_synthetic(test_db)
    headers = _login(client, "other-operator")
    for path in ("operations-dashboard", "operations-kpi"):
        response = client.get(
            f"/api/v1/analytics/{path}",
            params={"operator_id": synthetic.id},
            headers=headers,
        )
        assert response.status_code == 403, path


def test_dashboard_results_full_payload_preserves_existing_fields(client, test_db):
    """Existing /dashboard/results fields must remain present alongside new ones."""
    canonical, _synthetic, _other = _seed_canonical_and_synthetic(test_db)
    project = Project(
        title="Context test",
        description="ctx",
        requirements="",
        budget_estimate=100_000_000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=24),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    test_db.add_all(
        [
            BidDecisionRecord(
                project_id=project.id,
                operator_id=canonical.id,
                pursue_bid=True,
                action="review",
                decision_status="reviewing",
                recommended_amount=95_000_000.0,
                probability_score=0.72,
                matched_score=0.8,
                priority_score=0.76,
            ),
            Bid(
                project_id=project.id,
                user_id=canonical.id,
                bid_amount=96_000_000.0,
                proposed_timeline=20,
                description="ctx",
                status="accepted",
            ),
            TenderResult(
                project_id=project.id,
                winning_company="Bid Vector Labs",
                winning_amount=98_000_000.0,
                winning_rate=94.2,
                result_status="awarded",
                announced_at=datetime.now(UTC),
            ),
        ]
    )
    test_db.commit()

    headers = _login(client, "operator")
    response = client.get("/api/v1/dashboard/results", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    # New fields:
    assert payload["current_operator_id"] == canonical.id
    assert payload["current_operator_username"] == "operator"
    # Legacy fields still present:
    assert payload["operator_id"] == canonical.id
    assert payload["returned_count"] == 1
    item = payload["items"][0]
    assert item["winning_amount"] == 98_000_000.0
    assert item["recommended_amount"] == 95_000_000.0
