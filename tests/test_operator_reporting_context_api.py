"""Operator-context isolation for reporting read routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.core.time import utc_now
from app.models.models import (
    Analytics,
    BidDecisionRecord,
    PricePrediction,
    Project,
    TenderResult,
    User,
)


REPORTING_PATHS = (
    "/api/v1/analytics/prediction-feedback",
    "/api/v1/analytics/accuracy-report",
    "/api/v1/analytics/decision-funnel",
    "/api/v1/analytics/decision-insights",
    "/api/v1/analytics/recommendation-feedback-labels",
    "/api/v1/analytics/decision-recommendations",
    "/api/v1/operations/decision-samples",
)


def _create_user(
    test_db,
    *,
    username: str,
    password: str = "password123",
    is_admin: bool = False,
) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username,
        company=f"{username} Co",
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=is_admin,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _login(client, username: str, password: str = "password123") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/session",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_users(test_db) -> tuple[User, User, User]:
    canonical = _create_user(test_db, username="operator")
    synthetic = _create_user(test_db, username="synthetic-sw-small-seoul")
    other = _create_user(test_db, username="other-operator")
    return canonical, synthetic, other


def _seed_project(test_db, *, title: str, notice_number: str) -> Project:
    project = Project(
        title=title,
        description="reporting context",
        requirements="",
        budget_estimate=100_000_000.0,
        category="software",
        notice_number=notice_number,
        demand_agency="Agency",
        deadline=utc_now() + timedelta(days=1),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _seed_reporting_row(
    test_db,
    *,
    operator: User,
    title: str,
    notice_number: str,
    verdict: str = "useful",
    created_at: datetime | None = None,
) -> tuple[Project, BidDecisionRecord]:
    created_at = created_at or utc_now()
    project = _seed_project(test_db, title=title, notice_number=notice_number)
    decision = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="submitted",
        initial_action="bid_now",
        initial_decision_status="planned",
        first_decided_at=created_at - timedelta(hours=2),
        recommended_amount=91_000_000.0,
        probability_score=0.82,
        matched_score=0.8,
        priority_score=0.78,
        competitiveness_score=0.64,
        budget_capture_score=0.7,
        expected_margin_score=0.66,
        execution_complexity_score=0.35,
        reasoning=f"{title} reasoning",
        created_at=created_at,
        updated_at=created_at,
    )
    test_db.add(decision)
    test_db.commit()
    test_db.refresh(decision)

    prediction = PricePrediction(
        user_id=operator.id,
        project_id=project.id,
        predicted_price=92_000_000.0,
        price_range_min=90_000_000.0,
        price_range_max=94_000_000.0,
        confidence_score=0.7,
        predictor_name=f"{operator.username}-predictor",
        predictor_family="statistical",
        pricing_mode="heuristic",
        created_at=created_at,
    )
    result = TenderResult(
        project_id=project.id,
        winning_company=f"{operator.username} winner",
        winning_amount=90_500_000.0,
        winning_rate=90.5,
        result_status="awarded",
        announced_at=created_at,
    )
    feedback = Analytics(
        user_id=operator.id,
        event_type="recommendation_feedback",
        event_data=json.dumps(
            {
                "decision_record_id": decision.id,
                "project_id": project.id,
                "verdict": verdict,
            }
        ),
        timestamp=created_at + timedelta(minutes=10),
    )
    test_db.add_all([prediction, result, feedback])
    test_db.commit()
    return project, decision


def test_reporting_routes_allow_canonical_to_target_synthetic(client, test_db):
    _canonical, synthetic, _other = _seed_users(test_db)
    headers = _login(client, "operator")

    for path in REPORTING_PATHS:
        response = client.get(
            path,
            params={"operator_id": synthetic.id, "days": 30},
            headers=headers,
        )
        assert response.status_code == 200, (path, response.text)
        payload = response.json()
        assert payload["operator_id"] == synthetic.id, path
        assert payload["current_operator_id"] == synthetic.id, path
        assert payload["current_operator_username"] == "synthetic-sw-small-seoul", path


def test_reporting_routes_403_for_non_privileged_cross_operator(client, test_db):
    _canonical, synthetic, _other = _seed_users(test_db)
    headers = _login(client, "other-operator")

    for path in REPORTING_PATHS:
        response = client.get(
            path,
            params={"operator_id": synthetic.id},
            headers=headers,
        )
        assert response.status_code == 403, path


def test_reporting_routes_403_for_unauthenticated_cross_operator(client, test_db):
    _canonical, synthetic, _other = _seed_users(test_db)

    for path in REPORTING_PATHS:
        response = client.get(path, params={"operator_id": synthetic.id})
        assert response.status_code == 403, path


def test_reporting_routes_404_for_unknown_operator_id(client, test_db):
    _seed_users(test_db)
    headers = _login(client, "operator")

    for path in REPORTING_PATHS:
        response = client.get(
            path,
            params={"operator_id": 999_999},
            headers=headers,
        )
        assert response.status_code == 404, path


def test_synthetic_reporting_responses_do_not_include_canonical_rows(
    client, test_db
):
    canonical, synthetic, _other = _seed_users(test_db)
    _canonical_project, canonical_decision = _seed_reporting_row(
        test_db,
        operator=canonical,
        title="canonical reporting row",
        notice_number="CANON-001",
        verdict="not_useful",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    synthetic_project, synthetic_decision = _seed_reporting_row(
        test_db,
        operator=synthetic,
        title="synthetic reporting row",
        notice_number="SYNTH-001",
        verdict="useful",
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )

    headers = _login(client, "operator")
    params = {"operator_id": synthetic.id, "days": 30, "limit": 20}

    insights = client.get(
        "/api/v1/analytics/decision-insights",
        params=params,
        headers=headers,
    )
    assert insights.status_code == 200, insights.text
    insight_ids = {
        item["decision_record_id"] for item in insights.json()["recent_decisions"]
    }
    assert insight_ids == {synthetic_decision.id}
    assert canonical_decision.id not in insight_ids

    funnel = client.get(
        "/api/v1/analytics/decision-funnel",
        params=params,
        headers=headers,
    )
    assert funnel.status_code == 200, funnel.text
    funnel_ids = {
        item["decision_record_id"] for item in funnel.json()["recent_submissions"]
    }
    assert funnel_ids == {synthetic_decision.id}

    labels = client.get(
        "/api/v1/analytics/recommendation-feedback-labels",
        params=params,
        headers=headers,
    )
    assert labels.status_code == 200, labels.text
    label_ids = {item["decision_record_id"] for item in labels.json()["items"]}
    assert label_ids == {synthetic_decision.id}

    samples = client.get(
        "/api/v1/operations/decision-samples",
        params=params,
        headers=headers,
    )
    assert samples.status_code == 200, samples.text
    sample_ids = {item["decision_record_id"] for item in samples.json()["samples"]}
    assert sample_ids == {synthetic_decision.id}

    feedback = client.get(
        "/api/v1/analytics/prediction-feedback",
        params=params,
        headers=headers,
    )
    assert feedback.status_code == 200, feedback.text
    feedback_items = feedback.json()["items"]
    assert [item["project_id"] for item in feedback_items] == [synthetic_project.id]
    assert all("canonical" not in item["project_title"] for item in feedback_items)

    accuracy = client.get(
        "/api/v1/analytics/accuracy-report",
        params=params,
        headers=headers,
    )
    assert accuracy.status_code == 200, accuracy.text
    accuracy_items = accuracy.json()["items"]
    assert [item["project_id"] for item in accuracy_items] == [synthetic_project.id]
