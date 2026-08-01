"""Tests for analytics event JSON round-trip storage and parse fallback (roadmap C-1)."""

import pytest

import app.schemas.analytics as analytics_schema
from app.models.models import Analytics
from app.services.decision_analytics import parse_analytics_event_data
from tests.support.auth import authenticate_client


def _bootstrap_operator(
    client,
    username="event-operator",
    email="event@example.com",
    password="password123",
):
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": username,
            "email": email,
            "full_name": "Event Operator",
            "company": "Event Bid Corp",
            "password": password,
        },
    )
    assert response.status_code == 200, response.text
    # 이 엔드포인트는 bearer 를 요구한다(프론트도 토큰이 있을 때만 발신).
    authenticate_client(client, username=username, password=password)
    return response


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
    # 귀속은 토큰 소유자다(canonical 고정이 아니다).
    operator_id = test_db.query(Analytics).one().user_id
    session = client.post(
        "/api/v1/auth/session",
        json={"username": "event-operator", "password": "password123"},
    )
    assert operator_id == session.json()["operator_id"]


# --- 입력 방어선 (인증 · 어휘 · 크기 상한) ---------------------------------------


def test_log_event_rejects_anonymous_and_invalid_bearer(client, test_db):
    """쓰기 싱크는 익명/잘못된 토큰을 거부한다 — 읽기 경로의 익명 폴백과 다르다."""
    _bootstrap_operator(client)
    body = {"event_type": "project_view", "event_data": {"project_id": 1}}

    del client.headers["Authorization"]
    anonymous = client.post("/api/v1/analytics/event", json=body)
    assert anonymous.status_code == 401

    invalid = client.post(
        "/api/v1/analytics/event",
        json=body,
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert invalid.status_code == 401

    assert test_db.query(Analytics).count() == 0


def test_log_event_rejects_unknown_event_type(client, test_db):
    """프론트가 올리지 않는 event_type 은 422 — 아무 소비처도 읽지 못하는 행을 막는다."""
    _bootstrap_operator(client)

    response = client.post(
        "/api/v1/analytics/event",
        json={"event_type": "dashboard_opened", "event_data": {"source": "tests"}},
    )
    assert response.status_code == 422
    assert test_db.query(Analytics).count() == 0


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("too_many_keys", {f"k{index}": 1 for index in range(200)}),
        ("too_long", {"project_id": 1, "note": "가" * 5000}),
    ],
)
def test_log_event_rejects_oversized_payload(client, test_db, label, payload):
    """키 수 · 직렬화 길이 상한을 넘는 payload 는 422(저장 0건)."""
    _bootstrap_operator(client)

    response = client.post(
        "/api/v1/analytics/event",
        json={"event_type": "project_view", "event_data": payload},
    )
    assert response.status_code == 422, label
    assert test_db.query(Analytics).count() == 0


def test_log_event_accepts_payload_at_the_declared_caps(client, test_db, monkeypatch):
    """상한은 스키마 경계의 선언값이다 — 경계값(상한 이하)은 통과한다."""
    _bootstrap_operator(client)
    monkeypatch.setattr(analytics_schema, "ANALYTICS_EVENT_MAX_PAYLOAD_KEYS", 3)
    monkeypatch.setattr(analytics_schema, "ANALYTICS_EVENT_MAX_PAYLOAD_CHARS", 4096)

    at_cap = client.post(
        "/api/v1/analytics/event",
        json={
            "event_type": "project_view",
            "event_data": {"project_id": 1, "a": 1, "b": 2},
        },
    )
    assert at_cap.status_code == 200

    over_cap = client.post(
        "/api/v1/analytics/event",
        json={
            "event_type": "project_view",
            "event_data": {"project_id": 1, "a": 1, "b": 2, "c": 3},
        },
    )
    assert over_cap.status_code == 422
    assert test_db.query(Analytics).count() == 1


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
