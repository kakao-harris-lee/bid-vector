"""Collaborator-injection seams for TelegramUpdateProcessor (PR-15).

The processor resolves its notification/decision collaborators from injected
instances when provided, else lazily builds a fresh default at each use site so
an un-injected processor keeps the original per-call instantiation lifetime.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.update_processor import TelegramUpdateProcessor


def test_resolvers_return_injected_collaborators():
    notification = MagicMock()
    decision = MagicMock()
    processor = TelegramUpdateProcessor(
        telegram_service=MagicMock(),
        notification_service=notification,
        decision_service=decision,
    )

    assert processor._resolve_notification_service() is notification
    assert processor._resolve_decision_service() is decision


def test_resolvers_default_to_fresh_per_call_instances():
    """Without injection each resolve builds a new default, preserving the
    original per-call instance lifetime (not a cached singleton)."""
    processor = TelegramUpdateProcessor(telegram_service=MagicMock())

    first = processor._resolve_notification_service()
    second = processor._resolve_notification_service()

    assert first is not second
    assert isinstance(first, OperatorNotificationService)
    assert isinstance(second, OperatorNotificationService)


def test_apply_bid_decision_action_consumes_injected_collaborators():
    """The injected decision + notification services are the ones exercised."""

    class _FakeRecord:
        id = 10
        operator_id = 7
        project_id = 3
        action = "bid_now"
        decision_status = "bid_now"

    class _FakeDecisionService:
        def __init__(self):
            self.calls = []

        def apply_telegram_action(self, db, decision_record_id, requested_action):
            self.calls.append((decision_record_id, requested_action))
            return _FakeRecord()

    class _FakeProject:
        id = 3
        title = "t"

    class _FakeQuery:
        def filter(self, *a, **k):
            return self

        def first(self):
            return _FakeProject()

    class _FakeDB:
        def query(self, *a, **k):
            return _FakeQuery()

    class _FakeNotification:
        def __init__(self):
            self.calls = []

        def create_bid_decision_notification(self, db, *, operator_id, project, decision_record):
            self.calls.append((operator_id, project, decision_record))

    decision = _FakeDecisionService()
    notification = _FakeNotification()
    processor = TelegramUpdateProcessor(
        telegram_service=MagicMock(),
        notification_service=notification,
        decision_service=decision,
    )

    result = processor._apply_bid_decision_action(
        _FakeDB(),
        decision_record_id=10,
        requested_action="submit",
    )

    assert decision.calls == [(10, "submit")]
    assert notification.calls  # injected notification service was used
    assert result["status"] == "processed"
    assert result["decision_record_id"] == 10
