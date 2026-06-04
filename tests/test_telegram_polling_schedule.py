"""Telegram polling beat schedule wiring and inbound chat authorization."""

from __future__ import annotations

from app.core.config import settings
from app.core.single_user import ensure_operator_account
from app.models.models import BidDecisionRecord, Project
from app.services.notifications.telegram import TelegramNotificationService
from app.services.notifications.update_processor import (
    TelegramSyncService,
    TelegramUpdateProcessor,
)
from app.tasks.celery_app import (
    TELEGRAM_POLLING_TASK_NAME,
    build_celery_runtime_config,
    build_telegram_polling_beat_schedule,
)

AUTHORIZED_CHAT_ID = "1594710346"
UNAUTHORIZED_CHAT_ID = 222333444


def _configure_telegram(monkeypatch, *, chat_id: str | None = AUTHORIZED_CHAT_ID):
    """Configure a real Telegram bot token and (optionally) a delivery chat id."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
    if chat_id is None:
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    else:
        monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", chat_id)


def _capture_send(monkeypatch, deliveries: list[dict]):
    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append(
            {"message": message, "chat_id": chat_id, "reply_markup": reply_markup}
        )
        return {"sent": True, "status": "sent", "detail": "Telegram delivery succeeded."}

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)


def _capture_answer(monkeypatch, acknowledgements: list[tuple[str, str]]):
    def fake_answer(self, callback_query_id: str, text: str):
        acknowledgements.append((callback_query_id, text))
        return {"sent": True, "status": "sent", "detail": "ack"}

    monkeypatch.setattr(
        TelegramNotificationService, "answer_callback_query", fake_answer
    )


def _create_active_decision(test_db) -> BidDecisionRecord:
    operator = ensure_operator_account(test_db)
    project = Project(
        title="Authorization Guard Project",
        description="Verify inbound chat authorization",
        requirements="React only to the configured operator chat",
        budget_estimate=120000000.0,
        category="software",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    record = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        recommended_amount=115000000.0,
        probability_score=0.91,
        matched_score=0.84,
        priority_score=0.82,
        reasoning="authorization-guard baseline",
    )
    test_db.add(record)
    test_db.commit()
    test_db.refresh(record)
    return record


def test_telegram_polling_schedule_is_empty_by_default(monkeypatch):
    """Without TELEGRAM_POLLING_SCHEDULE_ENABLED the entry is omitted."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", False)
    assert build_telegram_polling_beat_schedule() == {}


def test_telegram_polling_schedule_builds_when_enabled(monkeypatch):
    """When enabled, the entry polls Telegram updates on the configured cadence."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_INTERVAL_SECONDS", 12)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_LIMIT", 7)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_TIMEOUT_SECONDS", 0)

    schedule = build_telegram_polling_beat_schedule()
    entry = schedule["telegram_polling_periodic"]

    assert entry["task"] == TELEGRAM_POLLING_TASK_NAME
    assert entry["schedule"] == 12.0
    assert entry["kwargs"] == {"limit": 7, "timeout_seconds": 0}


def test_telegram_polling_schedule_has_minimum_interval(monkeypatch):
    """The polling interval is clamped to avoid a tight beat loop."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_POLLING_INTERVAL_SECONDS", 0)

    entry = build_telegram_polling_beat_schedule()["telegram_polling_periodic"]
    assert entry["schedule"] == 5.0


def test_telegram_polling_schedule_included_in_runtime_config(monkeypatch):
    """The runtime config beat_schedule includes the Telegram polling entry when enabled."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_POLLING_SCHEDULE_ENABLED", True)

    beat = build_celery_runtime_config()["beat_schedule"]
    assert "telegram_polling_periodic" in beat


def test_unauthorized_chat_text_action_is_ignored_without_mutation(test_db, monkeypatch):
    """A text decision command from a non-configured chat must not mutate the record."""
    deliveries: list[dict] = []
    _configure_telegram(monkeypatch)
    _capture_send(monkeypatch, deliveries)

    record = _create_active_decision(test_db)
    before_action = record.action
    before_status = record.decision_status

    processor = TelegramUpdateProcessor()
    result = processor.process_update(
        test_db,
        {
            "update_id": 9001,
            "message": {
                "message_id": 1,
                "text": "✅ 투찰",
                "chat": {"id": UNAUTHORIZED_CHAT_ID},
            },
        },
    )

    assert result["status"] == "ignored"
    assert result["detail"] == "unauthorized chat"
    assert result["chat_id"] == UNAUTHORIZED_CHAT_ID
    # No reply is sent to an unauthorized chat (no info leak / spam).
    assert deliveries == []

    test_db.refresh(record)
    assert record.action == before_action == "bid_now"
    assert record.decision_status == before_status == "planned"


def test_unauthorized_chat_callback_is_ignored_without_mutation(test_db, monkeypatch):
    """A bid-decision callback from a non-configured chat must not apply the action."""
    deliveries: list[dict] = []
    acknowledgements: list[tuple[str, str]] = []
    _configure_telegram(monkeypatch)
    _capture_send(monkeypatch, deliveries)
    _capture_answer(monkeypatch, acknowledgements)

    record = _create_active_decision(test_db)

    processor = TelegramUpdateProcessor()
    result = processor.process_update(
        test_db,
        {
            "update_id": 9002,
            "callback_query": {
                "id": "unauthorized-callback",
                "data": f"bid-decision:{record.id}:review",
                "message": {
                    "message_id": 2,
                    "chat": {"id": UNAUTHORIZED_CHAT_ID},
                },
            },
        },
    )

    assert result["status"] == "ignored"
    assert result["detail"] == "unauthorized chat"
    assert result["chat_id"] == UNAUTHORIZED_CHAT_ID
    # Neither a chat reply nor a callback acknowledgement is emitted.
    assert deliveries == []
    assert acknowledgements == []

    test_db.refresh(record)
    assert record.action == "bid_now"
    assert record.decision_status == "planned"


def test_authorized_chat_text_action_still_applies(test_db, monkeypatch):
    """The configured chat retains full decision control (regression guard)."""
    deliveries: list[dict] = []
    _configure_telegram(monkeypatch)
    _capture_send(monkeypatch, deliveries)

    record = _create_active_decision(test_db)

    processor = TelegramUpdateProcessor()
    result = processor.process_update(
        test_db,
        {
            "update_id": 9003,
            "message": {
                "message_id": 3,
                "text": "✅ 투찰",
                "chat": {"id": int(AUTHORIZED_CHAT_ID)},
            },
        },
    )

    assert result["status"] == "processed"
    assert result["action"] == "bid_now"
    assert result["decision_status"] == "submitted"

    test_db.refresh(record)
    assert record.decision_status == "submitted"
    assert deliveries
    assert deliveries[-1]["chat_id"] == AUTHORIZED_CHAT_ID


def test_authorized_chat_callback_still_applies(test_db, monkeypatch):
    """The configured chat retains inline callback control (regression guard)."""
    deliveries: list[dict] = []
    acknowledgements: list[tuple[str, str]] = []
    _configure_telegram(monkeypatch)
    _capture_send(monkeypatch, deliveries)
    _capture_answer(monkeypatch, acknowledgements)

    record = _create_active_decision(test_db)

    processor = TelegramUpdateProcessor()
    result = processor.process_update(
        test_db,
        {
            "update_id": 9004,
            "callback_query": {
                "id": "authorized-callback",
                "data": f"bid-decision:{record.id}:review",
                "message": {
                    "message_id": 4,
                    "chat": {"id": int(AUTHORIZED_CHAT_ID)},
                },
            },
        },
    )

    assert result["status"] == "processed"
    assert result["action"] == "review"
    assert result["decision_status"] == "reviewing"
    assert acknowledgements == [("authorized-callback", "검토 처리 완료")]

    test_db.refresh(record)
    assert record.action == "review"
    assert record.decision_status == "reviewing"


def test_missing_configured_chat_ignores_decision_but_serves_start(test_db, monkeypatch):
    """With no configured chat id, decisions are ignored but `/start` still responds."""
    deliveries: list[dict] = []
    _configure_telegram(monkeypatch, chat_id=None)
    _capture_send(monkeypatch, deliveries)

    record = _create_active_decision(test_db)

    processor = TelegramUpdateProcessor()
    decision_result = processor.process_update(
        test_db,
        {
            "update_id": 9005,
            "message": {
                "message_id": 5,
                "text": "✅ 투찰",
                "chat": {"id": int(AUTHORIZED_CHAT_ID)},
            },
        },
    )

    assert decision_result["status"] == "ignored"
    assert decision_result["detail"] == "unauthorized chat"
    assert deliveries == []

    test_db.refresh(record)
    assert record.action == "bid_now"
    assert record.decision_status == "planned"

    start_result = processor.process_update(
        test_db,
        {
            "update_id": 9006,
            "message": {
                "message_id": 6,
                "text": "/start",
                "chat": {"id": int(AUTHORIZED_CHAT_ID)},
            },
        },
    )

    assert start_result["status"] == "processed"
    assert start_result["detail"] == "Telegram start/help message handled."
    assert len(deliveries) == 1
    assert deliveries[0]["chat_id"] == AUTHORIZED_CHAT_ID
    assert f"감지된 chat id: {AUTHORIZED_CHAT_ID}" in deliveries[0]["message"]


def test_sync_survives_offset_ack_failure(test_db, monkeypatch):
    """A failing final offset acknowledgement must not drop processed updates."""
    deliveries: list[dict] = []
    acknowledgements: list[tuple[str, str]] = []
    _configure_telegram(monkeypatch)
    _capture_send(monkeypatch, deliveries)
    _capture_answer(monkeypatch, acknowledgements)

    record = _create_active_decision(test_db)

    calls: list[dict] = []

    def fake_get_updates(self, offset=None, limit=None, timeout_seconds=None):
        calls.append({"offset": offset, "limit": limit, "timeout_seconds": timeout_seconds})
        if offset is None:
            return [
                {
                    "update_id": 77,
                    "callback_query": {
                        "id": "sync-ack-fail",
                        "data": f"bid-decision:{record.id}:review",
                        "message": {
                            "message_id": 7,
                            "chat": {"id": int(AUTHORIZED_CHAT_ID)},
                        },
                    },
                }
            ]
        # Simulate the Telegram server rejecting the final offset ack.
        raise RuntimeError("Telegram getUpdates failed: offset ack rejected")

    monkeypatch.setattr(TelegramNotificationService, "get_updates", fake_get_updates)

    service = TelegramSyncService()
    payload = service.sync_updates(test_db, limit=10, timeout_seconds=0)

    assert payload["status"] == "processed"
    assert payload["processed_count"] == 1
    assert payload["processed_update_ids"] == [77]
    # First call fetches updates, second is the (failing) offset ack attempt.
    assert calls[0] == {"offset": None, "limit": 10, "timeout_seconds": 0}
    assert calls[1] == {"offset": 78, "limit": 1, "timeout_seconds": 0}

    test_db.refresh(record)
    assert record.action == "review"
    assert record.decision_status == "reviewing"
