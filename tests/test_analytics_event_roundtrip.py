"""Tests for analytics event JSON round-trip storage and parse fallback (roadmap C-1)."""

from app.models.models import Analytics
from app.services.decision_analytics import parse_analytics_event_data


def _bootstrap_operator(
    client,
    username="event-operator",
    email="event@example.com",
    password="password123",
):
    return client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "Event Operator",
            "company": "Event Bid Corp",
            "password": password,
        },
    )


def test_log_event_persists_valid_json_round_trip(client, test_db):
    """POST /event must store valid JSON that parses back to the original dict."""
    _bootstrap_operator(client)

    payload = {"project_id": 4321, "verdict": "useful", "한글": "값"}
    response = client.post(
        "/api/v1/analytics/event",
        json={"event_type": "recommendation_feedback", "event_data": payload},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "logged"}

    stored = test_db.query(Analytics).filter(
        Analytics.event_type == "recommendation_feedback"
    ).one()
    # Stored as JSON (double quotes, non-ASCII preserved), not a Python repr.
    # Serialization runs through the single ``model_dump_json`` path, whose
    # separators are compact; the contract is parse equivalence (asserted below),
    # not byte equality with ``json.dumps`` default spacing.
    assert stored.event_data.startswith("{")
    assert '"project_id"' in stored.event_data
    assert "한글" in stored.event_data
    assert "'" not in stored.event_data

    assert parse_analytics_event_data(stored.event_data) == payload


def test_parse_analytics_event_data_recovers_legacy_repr():
    """Legacy rows stored via str(dict) (single quotes) must parse via ast fallback."""
    legacy = str({"project_id": 99, "verdict": "not_useful"})
    assert "'" in legacy  # sanity: Python repr uses single quotes
    assert parse_analytics_event_data(legacy) == {
        "project_id": 99,
        "verdict": "not_useful",
    }


def test_parse_analytics_event_data_is_safe_on_garbage_and_empty():
    """None, empty, malformed, and non-mapping payloads degrade to an empty dict."""
    assert parse_analytics_event_data(None) == {}
    assert parse_analytics_event_data("") == {}
    assert parse_analytics_event_data("   ") == {}
    assert parse_analytics_event_data("not json at all") == {}
    assert parse_analytics_event_data("[1, 2, 3]") == {}  # valid JSON but not a dict
    assert parse_analytics_event_data("42") == {}
