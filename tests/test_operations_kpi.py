"""Tests for the unified operations KPI aggregation endpoint (roadmap C-1)."""

from datetime import UTC, datetime, timedelta

import pytest

import json

from app.models.models import (
    Analytics,
    BidDecisionRecord,
    PricePrediction,
    Project,
    TenderResult,
)


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

    assert payload["review_time"] == {
        "average_review_minutes": None,
        "sample_count": 0,
    }
    assert payload["recommendation_feedback"] == {
        "useful_count": 0,
        "not_useful_count": 0,
        "review_value_rate": None,
        "feedback_count": 0,
    }


def _log_event(client, event_type, event_data):
    response = client.post(
        "/api/v1/analytics/event",
        json={"event_type": event_type, "event_data": event_data},
    )
    assert response.status_code == 200
    return response


def test_operations_kpi_review_time_joins_views_to_decisions(client, test_db):
    """KPI (a): review minutes average only decisions matched to a prior project_view."""
    operator_id = _bootstrap_operator(
        client, username="review-time-operator", email="review-time@example.com"
    ).json()["id"]
    now = datetime.now(UTC)

    matched_project = _make_project(test_db, title="Matched View")
    no_view_project = _make_project(test_db, title="No View")
    view_after_project = _make_project(test_db, title="View After Decision")

    decided_at = now - timedelta(hours=2)
    test_db.add_all(
        [
            # 30 minutes between earliest view and the decision.
            BidDecisionRecord(
                project_id=matched_project,
                operator_id=operator_id,
                action="bid_now",
                decision_status="planned",
                initial_action="bid_now",
                initial_decision_status="planned",
                priority_score=0.8,
                first_decided_at=decided_at,
                created_at=decided_at,
                updated_at=decided_at,
            ),
            # No matching view event -> excluded from sample.
            BidDecisionRecord(
                project_id=no_view_project,
                operator_id=operator_id,
                action="review",
                decision_status="reviewing",
                initial_action="review",
                initial_decision_status="reviewing",
                priority_score=0.6,
                first_decided_at=now - timedelta(hours=3),
                created_at=now - timedelta(hours=3),
                updated_at=now - timedelta(hours=3),
            ),
            # View happens AFTER the decision -> excluded.
            BidDecisionRecord(
                project_id=view_after_project,
                operator_id=operator_id,
                action="review",
                decision_status="reviewing",
                initial_action="review",
                initial_decision_status="reviewing",
                priority_score=0.5,
                first_decided_at=now - timedelta(hours=4),
                created_at=now - timedelta(hours=4),
                updated_at=now - timedelta(hours=4),
            ),
        ]
    )
    # Two views for matched project: earliest one (40 min before) must be used.
    test_db.add_all(
        [
            Analytics(
                user_id=operator_id,
                event_type="project_view",
                event_data=json.dumps({"project_id": matched_project}),
                timestamp=decided_at - timedelta(minutes=40),
            ),
            Analytics(
                user_id=operator_id,
                event_type="project_view",
                event_data=json.dumps({"project_id": matched_project}),
                timestamp=decided_at - timedelta(minutes=30),
            ),
            # View strictly after the decision -> must not match.
            Analytics(
                user_id=operator_id,
                event_type="project_view",
                event_data=json.dumps({"project_id": view_after_project}),
                timestamp=(now - timedelta(hours=4)) + timedelta(minutes=10),
            ),
        ]
    )
    test_db.commit()

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})
    assert response.status_code == 200
    review_time = response.json()["review_time"]
    # Only the matched decision contributes; earliest view = 40 minutes before.
    assert review_time["sample_count"] == 1
    assert review_time["average_review_minutes"] == pytest.approx(40.0, abs=0.0001)


def test_operations_kpi_recommendation_feedback_uses_latest_verdict(client, test_db):
    """KPI (c): only the latest verdict per decision counts; rate excludes empty denom."""
    operator_id = _bootstrap_operator(
        client, username="rec-feedback-operator", email="rec-feedback@example.com"
    ).json()["id"]

    useful_project = _make_project(test_db, title="Useful Rec")
    flipped_project = _make_project(test_db, title="Flipped Rec")
    not_useful_project = _make_project(test_db, title="Not Useful Rec")

    useful_decision = BidDecisionRecord(
        project_id=useful_project,
        operator_id=operator_id,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        priority_score=0.8,
    )
    flipped_decision = BidDecisionRecord(
        project_id=flipped_project,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.6,
    )
    not_useful_decision = BidDecisionRecord(
        project_id=not_useful_project,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.5,
    )
    test_db.add_all([useful_decision, flipped_decision, not_useful_decision])
    test_db.flush()

    # decision A -> useful (single vote)
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": useful_decision.id,
            "project_id": useful_project,
            "verdict": "useful",
        },
    )
    # decision B -> useful then not_useful: only the latest (not_useful) counts.
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": flipped_decision.id,
            "project_id": flipped_project,
            "verdict": "useful",
        },
    )
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": flipped_decision.id,
            "project_id": flipped_project,
            "verdict": "not_useful",
        },
    )
    # decision C -> not_useful
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": not_useful_decision.id,
            "project_id": not_useful_project,
            "verdict": "not_useful",
        },
    )

    response = client.get("/api/v1/analytics/operations-kpi", params={"days": 30})
    assert response.status_code == 200
    feedback = response.json()["recommendation_feedback"]
    # A useful, B not_useful (latest), C not_useful -> 1 useful, 2 not_useful.
    assert feedback["useful_count"] == 1
    assert feedback["not_useful_count"] == 2
    assert feedback["feedback_count"] == 3
    assert feedback["review_value_rate"] == pytest.approx(1 / 3, abs=0.0001)
