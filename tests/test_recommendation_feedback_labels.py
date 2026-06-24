"""Tests for the recommendation feedback labels analytics endpoint."""

from datetime import UTC, datetime, timedelta

import json

import pytest

from app.models.models import Analytics, BidDecisionRecord, Project
from app.services.decision_analytics import DecisionAnalyticsService


def _bootstrap_operator(
    client,
    username="labels-operator",
    email="labels@example.com",
    password="password123",
):
    return client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "Labels Operator",
            "company": "Labels Corp",
            "password": password,
        },
    )


def _make_project(
    test_db,
    *,
    title,
    category="software",
    business_type_code=None,
    budget=100000000.0,
):
    project = Project(
        title=title,
        description="Recommendation feedback labels fixture",
        requirements="Label collection",
        budget_estimate=budget,
        category=category,
        business_type_code=business_type_code,
    )
    test_db.add(project)
    test_db.flush()
    return project.id


def _log_event(client, event_type, event_data):
    response = client.post(
        "/api/v1/analytics/event",
        json={"event_type": event_type, "event_data": event_data},
    )
    assert response.status_code == 200
    return response


def test_recommendation_feedback_labels_groups_by_category_and_action(client, test_db):
    """Useful + not_useful labels should split by joined category and action."""
    operator_id = _bootstrap_operator(
        client,
        username="labels-basic-operator",
        email="labels-basic@example.com",
    ).json()["id"]
    now = datetime.now(UTC)

    software_project = _make_project(
        test_db, title="Software A", category="software", business_type_code="SW01"
    )
    services_project = _make_project(
        test_db, title="Services A", category="services", business_type_code="SV01"
    )
    services_b = _make_project(
        test_db, title="Services B", category="services", business_type_code=None
    )

    bid_now_useful = BidDecisionRecord(
        project_id=software_project,
        operator_id=operator_id,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        priority_score=0.8,
        reasoning="bid_now reasoning",
        score_breakdown=json.dumps(
            {"strengths": ["budget_fit"], "risk_flags": ["tight_deadline"]}
        ),
        first_decided_at=now - timedelta(hours=3),
        created_at=now - timedelta(hours=3),
        updated_at=now - timedelta(hours=3),
    )
    review_not_useful = BidDecisionRecord(
        project_id=services_project,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.55,
        reasoning="review reasoning",
        score_breakdown=json.dumps({"strengths": [], "risk_flags": ["low_margin"]}),
        first_decided_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )
    review_useful = BidDecisionRecord(
        project_id=services_b,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.7,
        reasoning="x" * 250,  # longer than excerpt limit
        score_breakdown=json.dumps({"strengths": ["scope_fit"], "risk_flags": []}),
        first_decided_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    test_db.add_all([bid_now_useful, review_not_useful, review_useful])
    test_db.flush()
    # Capture ids before logging events so we don't accidentally reference
    # detached attributes after the analytics post commits.
    bid_now_useful_id = bid_now_useful.id
    review_not_useful_id = review_not_useful.id
    review_useful_id = review_useful.id
    test_db.commit()

    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": bid_now_useful_id,
            "project_id": software_project,
            "verdict": "useful",
        },
    )
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": review_not_useful_id,
            "project_id": services_project,
            "verdict": "not_useful",
        },
    )
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": review_useful_id,
            "project_id": services_b,
            "verdict": "useful",
        },
    )

    response = client.get(
        "/api/v1/analytics/recommendation-feedback-labels",
        params={"days": 30, "limit": 100},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] == operator_id
    assert payload["period_days"] == 30
    assert payload["label_count"] == 3
    assert payload["useful_count"] == 2
    assert payload["not_useful_count"] == 1
    assert payload["review_value_rate"] == pytest.approx(2 / 3, abs=0.0001)

    by_category = payload["by_category"]
    assert set(by_category.keys()) == {"software", "services"}
    assert by_category["software"] == {
        "useful": 1,
        "not_useful": 0,
        "total": 1,
        "rate": 1.0,
    }
    assert by_category["services"] == {
        "useful": 1,
        "not_useful": 1,
        "total": 2,
        "rate": pytest.approx(0.5, abs=0.0001),
    }

    by_action = payload["by_action"]
    assert by_action["bid_now"] == {
        "useful": 1,
        "not_useful": 0,
        "total": 1,
        "rate": 1.0,
    }
    assert by_action["review"] == {
        "useful": 1,
        "not_useful": 1,
        "total": 2,
        "rate": pytest.approx(0.5, abs=0.0001),
    }

    items = payload["items"]
    assert len(items) == 3
    # Newest verdict first — review_useful logged last.
    assert items[0]["decision_record_id"] == review_useful_id
    assert items[0]["verdict"] == "useful"
    assert items[0]["project_category"] == "services"
    assert items[0]["project_business_type_code"] is None
    assert items[0]["strengths"] == ["scope_fit"]
    assert items[0]["risk_flags"] == []
    # Long reasoning should be capped at 200 characters.
    assert len(items[0]["reasoning"]) == 200

    middle = items[1]
    assert middle["decision_record_id"] == review_not_useful_id
    assert middle["verdict"] == "not_useful"
    assert middle["risk_flags"] == ["low_margin"]

    last = items[2]
    assert last["decision_record_id"] == bid_now_useful_id
    assert last["project_business_type_code"] == "SW01"
    assert last["strengths"] == ["budget_fit"]


def test_recommendation_feedback_labels_use_latest_verdict_per_decision(client, test_db):
    """Multiple verdicts on the same decision collapse to the latest one."""
    operator_id = _bootstrap_operator(
        client,
        username="labels-latest-operator",
        email="labels-latest@example.com",
    ).json()["id"]

    project_id = _make_project(test_db, title="Flipped Decision", category="hardware")
    flipped = BidDecisionRecord(
        project_id=project_id,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.6,
    )
    test_db.add(flipped)
    test_db.flush()
    flipped_id = flipped.id
    test_db.commit()

    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": flipped_id,
            "project_id": project_id,
            "verdict": "useful",
        },
    )
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": flipped_id,
            "project_id": project_id,
            "verdict": "not_useful",
        },
    )

    response = client.get(
        "/api/v1/analytics/recommendation-feedback-labels", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["label_count"] == 1
    assert payload["useful_count"] == 0
    assert payload["not_useful_count"] == 1
    assert payload["review_value_rate"] == 0.0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["verdict"] == "not_useful"
    assert payload["by_category"]["hardware"]["not_useful"] == 1
    assert payload["by_category"]["hardware"]["useful"] == 0
    assert payload["by_category"]["hardware"]["total"] == 1


def test_recommendation_feedback_labels_skips_orphan_decisions(client, test_db):
    """Verdicts whose BidDecisionRecord is missing must not appear in counts/items."""
    operator_id = _bootstrap_operator(
        client,
        username="labels-orphan-operator",
        email="labels-orphan@example.com",
    ).json()["id"]

    real_project = _make_project(test_db, title="Real")
    real_decision = BidDecisionRecord(
        project_id=real_project,
        operator_id=operator_id,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        priority_score=0.7,
    )
    test_db.add(real_decision)
    test_db.flush()
    real_id = real_decision.id
    test_db.commit()

    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": real_id,
            "project_id": real_project,
            "verdict": "useful",
        },
    )
    # Orphan: this decision_record_id never existed.
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": 999999,
            "project_id": real_project,
            "verdict": "not_useful",
        },
    )

    response = client.get(
        "/api/v1/analytics/recommendation-feedback-labels", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["label_count"] == 1
    assert payload["useful_count"] == 1
    assert payload["not_useful_count"] == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["decision_record_id"] == real_id


def test_recommendation_feedback_labels_excludes_out_of_window(client, test_db):
    """Verdicts older than ``days`` window must be excluded entirely."""
    operator_id = _bootstrap_operator(
        client,
        username="labels-window-operator",
        email="labels-window@example.com",
    ).json()["id"]
    now = datetime.now(UTC)

    inside_project = _make_project(test_db, title="Inside Window")
    outside_project = _make_project(test_db, title="Outside Window")
    inside = BidDecisionRecord(
        project_id=inside_project,
        operator_id=operator_id,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        priority_score=0.8,
    )
    outside = BidDecisionRecord(
        project_id=outside_project,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.4,
    )
    test_db.add_all([inside, outside])
    test_db.flush()
    inside_id = inside.id
    outside_id = outside.id

    # The /event endpoint always stamps the event with utc_now, so we log the
    # in-window verdict via HTTP and write the out-of-window one directly to DB.
    test_db.add(
        Analytics(
            user_id=operator_id,
            event_type="recommendation_feedback",
            event_data=json.dumps(
                {
                    "decision_record_id": outside_id,
                    "project_id": outside_project,
                    "verdict": "not_useful",
                }
            ),
            timestamp=now - timedelta(days=120),
        )
    )
    test_db.commit()

    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": inside_id,
            "project_id": inside_project,
            "verdict": "useful",
        },
    )

    response = client.get(
        "/api/v1/analytics/recommendation-feedback-labels", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["label_count"] == 1
    assert {item["decision_record_id"] for item in payload["items"]} == {inside_id}


def test_recommendation_feedback_labels_empty_payload_is_safe(client):
    """No feedback events at all should return a well-formed empty payload."""
    operator_id = _bootstrap_operator(
        client,
        username="labels-empty-operator",
        email="labels-empty@example.com",
    ).json()["id"]

    response = client.get(
        "/api/v1/analytics/recommendation-feedback-labels", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "operator_id": operator_id,
        "current_operator_id": operator_id,
        "current_operator_username": "labels-empty-operator",
        "period_days": 30,
        "label_count": 0,
        "useful_count": 0,
        "not_useful_count": 0,
        "review_value_rate": None,
        "by_category": {},
        "by_action": {},
        "items": [],
    }


def test_recommendation_feedback_labels_ignores_invalid_verdicts(client, test_db):
    """Verdict values outside {useful, not_useful} are silently dropped."""
    operator_id = _bootstrap_operator(
        client,
        username="labels-invalid-operator",
        email="labels-invalid@example.com",
    ).json()["id"]

    project_id = _make_project(test_db, title="Invalid Verdict")
    decision = BidDecisionRecord(
        project_id=project_id,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.6,
    )
    test_db.add(decision)
    test_db.flush()
    decision_id = decision.id
    test_db.commit()

    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": decision_id,
            "project_id": project_id,
            "verdict": "maybe",
        },
    )
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": decision_id,
            "project_id": project_id,
            "verdict": "",
        },
    )
    _log_event(
        client,
        "recommendation_feedback",
        {
            "decision_record_id": decision_id,
            "project_id": project_id,
            "verdict": "useful",
        },
    )

    response = client.get(
        "/api/v1/analytics/recommendation-feedback-labels", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["label_count"] == 1
    assert payload["useful_count"] == 1
    assert payload["not_useful_count"] == 0
    assert payload["items"][0]["verdict"] == "useful"


def test_recommendation_feedback_labels_match_kpi_aggregates(client, test_db):
    """Labels endpoint must agree with operations-kpi on useful/not_useful counts."""
    operator_id = _bootstrap_operator(
        client,
        username="labels-kpi-consistency-operator",
        email="labels-kpi-consistency@example.com",
    ).json()["id"]

    p1 = _make_project(test_db, title="Consistency 1", category="software")
    p2 = _make_project(test_db, title="Consistency 2", category="software")
    p3 = _make_project(test_db, title="Consistency 3", category="services")

    d1 = BidDecisionRecord(
        project_id=p1,
        operator_id=operator_id,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        priority_score=0.8,
    )
    d2 = BidDecisionRecord(
        project_id=p2,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.6,
    )
    d3 = BidDecisionRecord(
        project_id=p3,
        operator_id=operator_id,
        action="review",
        decision_status="reviewing",
        initial_action="review",
        initial_decision_status="reviewing",
        priority_score=0.5,
    )
    test_db.add_all([d1, d2, d3])
    test_db.flush()
    d1_id, d2_id, d3_id = d1.id, d2.id, d3.id
    test_db.commit()

    _log_event(
        client,
        "recommendation_feedback",
        {"decision_record_id": d1_id, "project_id": p1, "verdict": "useful"},
    )
    # Flip d2: useful then not_useful — only latest counts.
    _log_event(
        client,
        "recommendation_feedback",
        {"decision_record_id": d2_id, "project_id": p2, "verdict": "useful"},
    )
    _log_event(
        client,
        "recommendation_feedback",
        {"decision_record_id": d2_id, "project_id": p2, "verdict": "not_useful"},
    )
    _log_event(
        client,
        "recommendation_feedback",
        {"decision_record_id": d3_id, "project_id": p3, "verdict": "not_useful"},
    )

    kpi_response = client.get(
        "/api/v1/analytics/operations-kpi", params={"days": 30}
    )
    labels_response = client.get(
        "/api/v1/analytics/recommendation-feedback-labels", params={"days": 30}
    )
    assert kpi_response.status_code == 200
    assert labels_response.status_code == 200

    kpi_feedback = kpi_response.json()["recommendation_feedback"]
    labels = labels_response.json()
    assert labels["useful_count"] == kpi_feedback["useful_count"]
    assert labels["not_useful_count"] == kpi_feedback["not_useful_count"]
    assert labels["label_count"] == kpi_feedback["feedback_count"]
    assert labels["review_value_rate"] == kpi_feedback["review_value_rate"]


def test_recommendation_feedback_labels_service_returns_pure_python_when_empty(test_db):
    """Direct service call without any events should produce an empty, valid payload."""
    payload = DecisionAnalyticsService().build_recommendation_feedback_labels(
        test_db, days=30, limit=10
    )
    assert payload["label_count"] == 0
    assert payload["useful_count"] == 0
    assert payload["not_useful_count"] == 0
    assert payload["review_value_rate"] is None
    assert payload["by_category"] == {}
    assert payload["by_action"] == {}
    assert payload["items"] == []
