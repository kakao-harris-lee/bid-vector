"""Tests for the singleton operator workflow."""

from app.core.config import settings
from app.models.models import Bid, BidDecisionRecord, Notification, User
from app.services.notifications.telegram import TelegramNotificationService


def test_get_operator_profile_bootstraps_single_operator(client):
    """The operator profile endpoint should bootstrap the singleton account and profile."""
    response = client.get("/api/v1/operator/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "operator"
    assert payload["business_type"] == "service"
    assert payload["license_codes"] == []
    assert payload["profile_configured"] is False


def test_update_operator_profile_persists_company_fit_settings(client):
    """The operator profile endpoint should persist fit settings used elsewhere in the app."""
    response = client.put(
        "/api/v1/operator/profile",
        json={
            "full_name": "Harris Operator",
            "company": "Bid Vector Labs",
            "business_type": "software",
            "license_codes": ["SW001", "NET001"],
            "region_codes": ["서울특별시", "경기도"],
            "annual_revenue": 1500000000.0,
            "capacity_score": 0.94,
            "total_awards": 11,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["full_name"] == "Harris Operator"
    assert payload["company"] == "Bid Vector Labs"
    assert payload["business_type"] == "software"
    assert payload["license_codes"] == ["SW001", "NET001"]
    assert payload["region_codes"] == ["서울특별시", "경기도"]
    assert payload["profile_configured"] is True


def test_submit_bid_uses_single_operator_account(client, test_db):
    """Bid submission should no longer require an explicit user_id query parameter."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Operator Bid Project",
            "description": "Single-user bid submission flow",
            "requirements": "Fast turnaround",
            "budget_estimate": 50000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/bids/",
        json={
            "project_id": project_id,
            "bid_amount": 47000.0,
            "proposed_timeline": 14,
            "description": "We can deliver this quickly.",
        },
    )

    assert response.status_code == 200
    payload = response.json()

    users = test_db.query(User).all()
    bids = test_db.query(Bid).all()
    decisions = test_db.query(BidDecisionRecord).all()
    notifications = test_db.query(Notification).all()

    assert len(users) == 1
    assert len(bids) == 1
    assert len(decisions) == 1
    assert len(notifications) == 1
    assert payload["operator_id"] == users[0].id
    assert payload["user_id"] == users[0].id
    assert payload["decision_status"] == "submitted"
    assert payload["decision_record_id"] == decisions[0].id
    assert bids[0].user_id == users[0].id
    assert decisions[0].decision_status == "submitted"
    assert decisions[0].recommended_amount == 47000.0
    assert notifications[0].type == "bid_update"
    assert "투찰 완료 알림" in notifications[0].message


def test_submit_bid_promotes_existing_decision_to_submitted(client, test_db):
    """Submitting a bid should promote the active bid-decision record to `submitted` instead of duplicating it."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Decision Promotion Project",
            "description": "Promote active decision on submission",
            "requirements": "Fast approval path",
            "budget_estimate": 88000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    decision_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 83000.0,
            "probability_score": 0.88,
            "matched_score": 0.82,
            "deadline_hours_remaining": 10,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )

    assert decision_response.status_code == 200
    decision_payload = decision_response.json()
    assert decision_payload["decision_status"] == "planned"

    response = client.post(
        "/api/v1/bids/",
        json={
            "project_id": project_id,
            "bid_amount": 82500.0,
            "proposed_timeline": 7,
            "description": "Submitting after decision planning.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_record_id"] == decision_payload["id"]
    assert payload["decision_status"] == "submitted"
    assert test_db.query(BidDecisionRecord).count() == 1

    record = test_db.query(BidDecisionRecord).one()
    assert record.id == decision_payload["id"]
    assert record.decision_status == "submitted"
    assert record.recommended_amount == 82500.0
    assert "제출 상태" in record.reasoning


def test_operator_can_list_and_mark_notifications_from_web(client):
    """The web dashboard should list recent notifications and allow marking them as read."""
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Notification Feed Project",
            "description": "Create a bid decision notification for the web dashboard",
            "requirements": "Need Telegram-style alerts and web visibility",
            "budget_estimate": 92000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    decision_response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 88000.0,
            "probability_score": 0.84,
            "matched_score": 0.78,
            "deadline_hours_remaining": 12,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.2,
        },
    )
    assert decision_response.status_code == 200

    list_response = client.get("/api/v1/operator/notifications")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["type"] == "recommendation"
    assert payload[0]["is_read"] is False
    assert "입찰 판단 알림" in payload[0]["message"]

    notification_id = payload[0]["id"]
    mark_read_response = client.put(f"/api/v1/operator/notifications/{notification_id}/read")
    assert mark_read_response.status_code == 200
    mark_payload = mark_read_response.json()
    assert mark_payload["id"] == notification_id
    assert mark_payload["is_read"] is True

    unread_response = client.get("/api/v1/operator/notifications", params={"unread_only": True})
    assert unread_response.status_code == 200
    assert unread_response.json() == []


def test_bid_decision_triggers_telegram_delivery_when_configured(client, test_db, monkeypatch):
    """Saving a bid decision should attempt Telegram delivery when credentials are configured."""
    deliveries: list[dict] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "test-bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PRIORITY_THRESHOLD", 0.78)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PROBABILITY_THRESHOLD", 0.8)

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({
            "message": message,
            "reply_markup": reply_markup,
            "chat_id": chat_id,
        })
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Telegram Delivery Project",
            "description": "Verify automatic Telegram delivery on bid decisions",
            "requirements": "Alert immediately when worth bidding",
            "budget_estimate": 140000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 132000.0,
            "probability_score": 0.9,
            "matched_score": 0.83,
            "deadline_hours_remaining": 6,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.1,
        },
    )

    assert response.status_code == 200
    assert len(deliveries) == 1
    assert "입찰 판단 알림" in deliveries[0]["message"]
    assert "Telegram Delivery Project" in deliveries[0]["message"]
    reply_markup = deliveries[0]["reply_markup"]
    assert reply_markup is not None
    callback_data = reply_markup["inline_keyboard"][0][0]["callback_data"]
    assert callback_data.startswith("bid-decision:")
    assert callback_data.endswith(":submit")


def test_low_priority_bid_decision_stays_on_web_without_telegram(client, test_db, monkeypatch):
    """Lower-value opportunities should remain web-only without creating Telegram noise."""
    deliveries: list[str] = []

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "test-bot")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PRIORITY_THRESHOLD", 0.78)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_PROBABILITY_THRESHOLD", 0.8)

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append(message)
        return {
            "sent": True,
            "status": "sent",
            "detail": "Telegram delivery succeeded.",
        }

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)

    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Web Only Decision Project",
            "description": "Should not trigger Telegram because priority is too low",
            "requirements": "Keep this in the dashboard only",
            "budget_estimate": 90000.0,
            "category": "software",
        },
    )
    project_id = project_response.json()["id"]

    response = client.post(
        "/api/v1/operations/bid-decisions",
        json={
            "project_id": project_id,
            "recommended_amount": 85000.0,
            "probability_score": 0.62,
            "matched_score": 0.6,
            "deadline_hours_remaining": 36,
            "current_active_bids": 1,
            "max_active_bids": 4,
            "current_workload_score": 0.2,
        },
    )

    assert response.status_code == 200
    assert deliveries == []
    notification = test_db.query(Notification).one()
    assert notification.type == "recommendation"
    assert "입찰 판단 알림" in notification.message


def test_operator_overview_reports_single_user_counts(client):
    """The operator overview endpoint should provide a compact single-user dashboard summary."""
    client.get("/api/v1/operator/profile")
    response = client.get("/api/v1/operator/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_id"] >= 1
    assert payload["project_count"] >= 0
    assert payload["bid_count"] >= 0
    assert payload["profile_configured"] is False