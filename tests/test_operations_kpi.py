"""Tests for the unified operations KPI aggregation endpoint (roadmap C-1)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.models import BidDecisionRecord, PricePrediction, Project, TenderResult


def _bootstrap_operator(
    client, username="kpi-operator", email="kpi@example.com", password="password123"
):
    return client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "KPI Operator",
            "company": "KPI Bid Corp",
            "password": password,
        },
    )


def _make_project(
    test_db, *, title, deadline=None, category="software", budget=100000000.0
):
    project = Project(
        title=title,
        description="Operations KPI fixture project",
        requirements="KPI instrumentation",
        budget_estimate=budget,
        category=category,
        deadline=deadline,
    )
    test_db.add(project)
    test_db.flush()
    return project.id


def test_operations_kpi_reports_manual_override_rate(client, test_db):
    """KPI (d): decisions whose action/status diverged from the initial value are counted as overrides."""
    operator_id = _bootstrap_operator(client).json()["id"]
    now = datetime.now(UTC)

    # Two unchanged decisions (initial == current) and two operator-modified ones.
    unchanged_a = _make_project(test_db, title="Unchanged A")
    unchanged_b = _make_project(test_db, title="Unchanged B")
    action_changed = _make_project(test_db, title="Action Changed")
    status_changed = _make_project(test_db, title="Status Changed")

    test_db.add_all(
        [
            BidDecisionRecord(
                project_id=unchanged_a,
                operator_id=operator_id,
                action="bid_now",
                decision_status="planned",
                initial_action="bid_now",
                initial_decision_status="planned",
                priority_score=0.8,
                first_decided_at=now - timedelta(hours=5),
                created_at=now - timedelta(hours=5),
                updated_at=now - timedelta(hours=5),
            ),
            BidDecisionRecord(
                project_id=unchanged_b,
                operator_id=operator_id,
                action="review",
                decision_status="reviewing",
                initial_action="review",
                initial_decision_status="reviewing",
                priority_score=0.6,
                first_decided_at=now - timedelta(hours=4),
                created_at=now - timedelta(hours=4),
                updated_at=now - timedelta(hours=4),
            ),
            BidDecisionRecord(
                project_id=action_changed,
                operator_id=operator_id,
                action="skip",
                decision_status="skipped",
                initial_action="review",
                initial_decision_status="reviewing",
                priority_score=0.5,
                first_decided_at=now - timedelta(hours=3),
                created_at=now - timedelta(hours=3),
                updated_at=now - timedelta(hours=2),
            ),
            BidDecisionRecord(
                project_id=status_changed,
                operator_id=operator_id,
                action="bid_now",
                decision_status="submitted",
                initial_action="bid_now",
                initial_decision_status="planned",
                priority_score=0.9,
                first_decided_at=now - timedelta(hours=2),
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=1),
            ),
        ]
    )
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["period_days"] == 30
    override = payload["manual_override"]
    assert override["decision_count"] == 4
    assert override["modified_count"] == 2
    assert override["modification_rate"] == pytest.approx(0.5, abs=0.0001)


def test_operations_kpi_flags_missed_opportunities_past_deadline(client, test_db):
    """KPI (b): only recommended (bid_now/review) pending decisions past deadline are missed."""
    operator_id = _bootstrap_operator(
        client, username="missed-operator", email="missed@example.com"
    ).json()["id"]
    now = datetime.now(UTC)

    missed_review = _make_project(
        test_db, title="Missed Review", deadline=now - timedelta(days=2)
    )
    missed_bid_now = _make_project(
        test_db, title="Missed BidNow", deadline=now - timedelta(days=1)
    )
    deadline_future = _make_project(
        test_db, title="Future Deadline", deadline=now + timedelta(days=1)
    )
    already_submitted = _make_project(
        test_db, title="Already Submitted", deadline=now - timedelta(days=3)
    )
    skipped_recommendation = _make_project(
        test_db, title="Skipped", deadline=now - timedelta(days=2)
    )
    no_deadline = _make_project(test_db, title="No Deadline", deadline=None)

    test_db.add_all(
        [
            # Missed: review recommendation still reviewing, deadline passed.
            BidDecisionRecord(
                project_id=missed_review,
                operator_id=operator_id,
                action="review",
                decision_status="reviewing",
                initial_action="review",
                initial_decision_status="reviewing",
                priority_score=0.55,
                first_decided_at=now - timedelta(days=4),
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(days=4),
            ),
            # Missed: bid_now recommendation still planned, deadline passed (higher priority -> first).
            BidDecisionRecord(
                project_id=missed_bid_now,
                operator_id=operator_id,
                action="bid_now",
                decision_status="planned",
                initial_action="bid_now",
                initial_decision_status="planned",
                priority_score=0.92,
                first_decided_at=now - timedelta(days=3),
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=3),
            ),
            # Not missed: deadline still in the future.
            BidDecisionRecord(
                project_id=deadline_future,
                operator_id=operator_id,
                action="bid_now",
                decision_status="planned",
                initial_action="bid_now",
                initial_decision_status="planned",
                priority_score=0.7,
                first_decided_at=now - timedelta(hours=6),
                created_at=now - timedelta(hours=6),
                updated_at=now - timedelta(hours=6),
            ),
            # Not missed: already submitted (not active pending).
            BidDecisionRecord(
                project_id=already_submitted,
                operator_id=operator_id,
                action="bid_now",
                decision_status="submitted",
                initial_action="bid_now",
                initial_decision_status="planned",
                priority_score=0.8,
                first_decided_at=now - timedelta(days=5),
                created_at=now - timedelta(days=5),
                updated_at=now - timedelta(days=4),
            ),
            # Not missed: skip recommendation (not a recommended action).
            BidDecisionRecord(
                project_id=skipped_recommendation,
                operator_id=operator_id,
                action="skip",
                decision_status="skipped",
                initial_action="skip",
                initial_decision_status="skipped",
                priority_score=0.3,
                first_decided_at=now - timedelta(days=4),
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(days=4),
            ),
            # Not missed: no deadline recorded.
            BidDecisionRecord(
                project_id=no_deadline,
                operator_id=operator_id,
                action="review",
                decision_status="reviewing",
                initial_action="review",
                initial_decision_status="reviewing",
                priority_score=0.4,
                first_decided_at=now - timedelta(hours=8),
                created_at=now - timedelta(hours=8),
                updated_at=now - timedelta(hours=8),
            ),
        ]
    )
    test_db.commit()

    response = client.get(
        "/api/v1/analytics/operations-kpi", params={"days": 30, "missed_limit": 10}
    )

    assert response.status_code == 200
    missed = response.json()["missed_opportunities"]
    assert missed["missed_count"] == 2
    project_ids = {item["project_id"] for item in missed["items"]}
    assert project_ids == {missed_review, missed_bid_now}
    # Highest priority should sort first.
    assert missed["items"][0]["project_id"] == missed_bid_now
    assert missed["items"][0]["initial_action"] == "bid_now"
    assert missed["items"][0]["decision_status"] == "planned"
    assert missed["items"][0]["priority_score"] == pytest.approx(0.92, abs=0.0001)


def test_operations_kpi_reuses_funnel_and_feedback_values(client, test_db):
    """KPI (e)(f): conversion and accuracy mirror build_funnel / build_feedback output exactly."""
    from app.services.decision_analytics import DecisionAnalyticsService
    from app.services.prediction_feedback import PredictionFeedbackService

    operator_id = _bootstrap_operator(
        client, username="reuse-operator", email="reuse@example.com"
    ).json()["id"]
    now = datetime.now(UTC)

    conv_project = _make_project(test_db, title="Conversion Project")
    feedback_project = _make_project(
        test_db, title="Feedback Project", deadline=now + timedelta(days=5)
    )

    test_db.add_all(
        [
            # A submitted bid_now decision drives conversion rates.
            BidDecisionRecord(
                project_id=conv_project,
                operator_id=operator_id,
                action="bid_now",
                decision_status="submitted",
                initial_action="bid_now",
                initial_decision_status="planned",
                recommended_amount=102000000.0,
                priority_score=0.9,
                first_decided_at=now - timedelta(hours=12),
                created_at=now - timedelta(hours=12),
                updated_at=now - timedelta(hours=6),
            ),
            # Prediction + decision + tender result drives accuracy metrics.
            PricePrediction(
                user_id=operator_id,
                project_id=feedback_project,
                predicted_price=101000000.0,
                price_range_min=99000000.0,
                price_range_max=103000000.0,
                confidence_score=0.8,
                model_version="v1-historical",
            ),
            BidDecisionRecord(
                project_id=feedback_project,
                operator_id=operator_id,
                action="review",
                decision_status="reviewing",
                initial_action="review",
                initial_decision_status="reviewing",
                recommended_amount=100500000.0,
                priority_score=0.7,
                first_decided_at=now - timedelta(hours=10),
                created_at=now - timedelta(hours=10),
                updated_at=now - timedelta(hours=10),
            ),
            TenderResult(
                project_id=feedback_project,
                winning_company="정확도 검증 주식회사",
                winning_amount=100000000.0,
                winning_rate=95.0,
                result_status="awarded",
                announced_at=now - timedelta(days=2),
            ),
        ]
    )
    test_db.commit()

    funnel = DecisionAnalyticsService().build_funnel(test_db, days=30)
    feedback = PredictionFeedbackService().build_feedback(test_db, days=30, limit=100)

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})
    assert response.status_code == 200
    payload = response.json()

    conversion = payload["conversion"]
    assert conversion["decision_count"] == funnel["decision_count"]
    assert conversion["submitted_count"] == funnel["submitted_count"]
    assert conversion["overall_submission_rate"] == funnel["overall_submission_rate"]
    assert conversion["bid_now_submission_rate"] == funnel["bid_now_submission_rate"]
    assert conversion["review_submission_rate"] == funnel["review_submission_rate"]
    assert conversion["average_hours_to_submit"] == funnel["average_hours_to_submit"]

    accuracy = payload["prediction_accuracy"]
    assert accuracy["result_count"] == feedback["result_count"]
    assert accuracy["prediction_sample_count"] == feedback["prediction_sample_count"]
    assert (
        accuracy["recommendation_sample_count"]
        == feedback["recommendation_sample_count"]
    )
    assert (
        accuracy["average_prediction_error_rate"]
        == feedback["average_prediction_error_rate"]
    )
    assert (
        accuracy["average_recommendation_error_rate"]
        == feedback["average_recommendation_error_rate"]
    )
    assert (
        accuracy["prediction_within_1_percent_count"]
        == feedback["prediction_within_1_percent_count"]
    )
    assert (
        accuracy["prediction_within_3_percent_count"]
        == feedback["prediction_within_3_percent_count"]
    )
    assert (
        accuracy["recommendation_within_1_percent_count"]
        == feedback["recommendation_within_1_percent_count"]
    )
    assert (
        accuracy["recommendation_within_3_percent_count"]
        == feedback["recommendation_within_3_percent_count"]
    )


def test_operations_kpi_is_safe_with_no_data(client):
    """Empty data must yield a well-formed payload with None rates and zero counts, no crash."""
    operator_id = _bootstrap_operator(
        client, username="empty-kpi-operator", email="empty-kpi@example.com"
    ).json()["id"]

    response = client.get("/api/v1/analytics/operations-kpi")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["period_days"] == 30

    assert payload["manual_override"] == {
        "decision_count": 0,
        "modified_count": 0,
        "modification_rate": None,
    }
    conversion = payload["conversion"]
    assert conversion["decision_count"] == 0
    assert conversion["submitted_count"] == 0
    assert conversion["overall_submission_rate"] is None
    assert conversion["bid_now_submission_rate"] is None
    assert conversion["review_submission_rate"] is None
    assert conversion["average_hours_to_submit"] is None

    accuracy = payload["prediction_accuracy"]
    assert accuracy["result_count"] == 0
    assert accuracy["prediction_sample_count"] == 0
    assert accuracy["average_prediction_error_rate"] is None
    assert accuracy["average_recommendation_error_rate"] is None

    assert payload["missed_opportunities"] == {"missed_count": 0, "items": []}
