"""Tests for realtime WebSocket events."""

import asyncio
from urllib.parse import quote

import pytest
from fastapi import status
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.services.realtime import RealtimeEventManager


class _FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class _FakeFanoutBackend:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.published: list[tuple[dict, str]] = []

    async def start(self, manager) -> None:
        del manager
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def publish(self, event: dict, *, publisher_id: str) -> None:
        self.published.append((event, publisher_id))


def _bootstrap_and_login(client) -> str:
    bootstrap = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "username": "realtime-operator",
            "email": "realtime@example.com",
            "full_name": "Realtime Operator",
            "company": "Realtime Corp",
            "password": "password123",
        },
    )
    assert bootstrap.status_code == 200

    session = client.post(
        "/api/v1/auth/session",
        json={
            "username": "realtime-operator",
            "password": "password123",
        },
    )
    assert session.status_code == 200
    return session.json()["access_token"]


def test_realtime_websocket_rejects_unauthenticated_client(client):
    """Realtime websocket should reject clients without an access token."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/realtime/events"):
            pass

    assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION


def test_realtime_websocket_accepts_authenticated_connection_and_ping(client):
    """Realtime websocket should accept authenticated clients and respond to dashboard pings."""
    token = quote(_bootstrap_and_login(client))

    with client.websocket_connect(f"/api/v1/realtime/events?token={token}") as websocket:
        ack = websocket.receive_json()
        assert ack["event_type"] == "connection.opened"
        assert ack["payload"]["connection_count"] >= 1
        assert ack["payload"]["authenticated"] is True
        assert ack["payload"]["operator_id"] >= 1

        websocket.send_json({"event_type": "ping"})
        pong = websocket.receive_json()
        assert pong["event_type"] == "pong"
        assert "connection_count" in pong["payload"]


def test_realtime_manager_broadcasts_normalized_events():
    """Realtime manager should normalize events and send them to active connections."""
    manager = RealtimeEventManager(history_limit=2)
    websocket = _FakeWebSocket()

    async def exercise_manager():
        await manager.connect(websocket)  # type: ignore[arg-type]
        event = await manager.broadcast_event("test.event", {"ok": True})
        return event

    event = asyncio.run(exercise_manager())

    assert websocket.accepted is True
    assert websocket.sent[0]["event_type"] == "connection.opened"
    assert websocket.sent[-1]["event_type"] == "test.event"
    assert websocket.sent[-1]["payload"] == {"ok": True}
    assert event["event_id"]
    assert manager.recent_events()[-1]["event_type"] == "test.event"


def test_realtime_manager_replays_requested_recent_events():
    """Realtime manager should replay retained local events after a known event id."""
    manager = RealtimeEventManager(history_limit=3)
    first = manager.publish_event("first.event", {"order": 1})
    second = manager.publish_event("second.event", {"order": 2})
    third = manager.publish_event("third.event", {"order": 3})
    websocket = _FakeWebSocket()

    async def exercise_manager():
        await manager.connect(
            websocket,  # type: ignore[arg-type]
            client_context={
                "replay_requested": True,
                "after_event_id": first["event_id"],
                "replay_limit": 10,
            },
        )

    asyncio.run(exercise_manager())

    ack = websocket.sent[0]
    assert ack["payload"]["replay"]["requested"] is True
    assert ack["payload"]["replayed_event_count"] == 2
    assert ack["payload"]["replay"]["after_event_id_found"] is True
    assert [event["event_id"] for event in websocket.sent[1:]] == [second["event_id"], third["event_id"]]


def test_realtime_manager_marks_replay_gap_when_after_event_was_evicted():
    """Reconnect acknowledgements should flag when local retention cannot prove continuity."""
    manager = RealtimeEventManager(history_limit=2)
    evicted = manager.publish_event("evicted.event", {"order": 1})
    retained_second = manager.publish_event("retained.second", {"order": 2})
    retained_third = manager.publish_event("retained.third", {"order": 3})
    websocket = _FakeWebSocket()

    async def exercise_manager():
        await manager.connect(
            websocket,  # type: ignore[arg-type]
            client_context={
                "replay_requested": True,
                "after_event_id": evicted["event_id"],
            },
        )

    asyncio.run(exercise_manager())

    ack = websocket.sent[0]
    assert ack["payload"]["replay"]["after_event_id_found"] is False
    assert ack["payload"]["replay"]["retention_scope"] == "local_process_memory"
    assert ack["payload"]["replay"]["cross_worker_backfill"] is False
    assert [event["event_id"] for event in websocket.sent[1:]] == [retained_second["event_id"], retained_third["event_id"]]


def test_realtime_manager_uses_fanout_backend_for_cross_process_events():
    """Realtime manager should publish local events and accept remote fanout events."""
    fanout = _FakeFanoutBackend()
    manager = RealtimeEventManager(history_limit=3, fanout_backend=fanout)  # type: ignore[arg-type]
    websocket = _FakeWebSocket()

    async def exercise_manager():
        await manager.start()
        await manager.connect(
            websocket,  # type: ignore[arg-type]
            client_context={"authenticated": True, "operator_id": 10},
        )
        local_event = await manager.broadcast_event("local.event", {"local": True})
        await manager.receive_fanout_event(
            {
                "event_id": "remote-event-id",
                "event_type": "remote.event",
                "created_at": "2026-05-12T00:00:00+09:00",
                "payload": {"remote": True},
            },
            publisher_id="other-process",
        )
        await manager.receive_fanout_event(
            {
                "event_id": "loop-event-id",
                "event_type": "loop.event",
                "created_at": "2026-05-12T00:00:00+09:00",
                "payload": {"loop": True},
            },
            publisher_id=manager.instance_id,
        )
        await manager.stop()
        return local_event

    local_event = asyncio.run(exercise_manager())

    assert fanout.started is True
    assert fanout.stopped is True
    assert fanout.published == [(local_event, manager.instance_id)]
    assert websocket.sent[0]["payload"]["authenticated"] is True
    assert websocket.sent[1]["event_type"] == "local.event"
    assert websocket.sent[2]["event_type"] == "remote.event"
    assert all(event["event_type"] != "loop.event" for event in websocket.sent)


def test_realtime_auth_can_be_disabled_for_local_development(client, monkeypatch):
    """Realtime auth can be explicitly disabled for local-only development setups."""
    monkeypatch.setattr(settings, "REALTIME_REQUIRE_AUTH", False)

    with client.websocket_connect("/api/v1/realtime/events") as websocket:
        ack = websocket.receive_json()
        assert ack["event_type"] == "connection.opened"
        assert ack["payload"]["authenticated"] is False
