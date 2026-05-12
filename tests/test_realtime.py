"""Tests for realtime WebSocket events."""

import asyncio

from app.services.realtime import RealtimeEventManager


class _FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def test_realtime_websocket_accepts_connection_and_ping(client):
    """Realtime websocket should accept clients and respond to dashboard pings."""
    with client.websocket_connect("/api/v1/realtime/events") as websocket:
        ack = websocket.receive_json()
        assert ack["event_type"] == "connection.opened"
        assert ack["payload"]["connection_count"] >= 1

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
