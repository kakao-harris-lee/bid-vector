"""In-process realtime event fanout for dashboard WebSocket clients."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.time import utc_now


class RealtimeEventManager:
    """Track WebSocket clients and broadcast normalized realtime events."""

    def __init__(self, *, history_limit: int = 100) -> None:
        self._connections: set[WebSocket] = set()
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=max(1, history_limit))

    @property
    def connection_count(self) -> int:
        """Return the current number of connected WebSocket clients."""
        return len(self._connections)

    def recent_events(self) -> list[dict[str, Any]]:
        """Return a copy of the local event history."""
        return list(self._recent_events)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a WebSocket and send a connection acknowledgement."""
        await websocket.accept()
        self._connections.add(websocket)
        await websocket.send_json({
            "event_id": str(uuid4()),
            "event_type": "connection.opened",
            "created_at": utc_now().isoformat(),
            "payload": {
                "connection_count": self.connection_count,
                "replayed_event_count": len(self._recent_events),
            },
        })

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active set."""
        self._connections.discard(websocket)

    def publish_event(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record an event and best-effort broadcast it from sync code."""
        event = self._build_event(event_type, payload)
        self._recent_events.append(event)
        if not self._connections:
            return event

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                from anyio.from_thread import run as run_from_thread

                run_from_thread(self._send_event, event)
            except RuntimeError:
                return event
        else:
            loop.create_task(self._send_event(event))
        return event

    async def broadcast_event(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record and broadcast an event from async code."""
        event = self._build_event(event_type, payload)
        self._recent_events.append(event)
        await self._send_event(event)
        return event

    async def serve(self, websocket: WebSocket) -> None:
        """Serve one realtime client until it disconnects."""
        await self.connect(websocket)
        try:
            while True:
                message = await websocket.receive_json()
                if isinstance(message, dict) and message.get("event_type") == "ping":
                    await websocket.send_json({
                        "event_id": str(uuid4()),
                        "event_type": "pong",
                        "created_at": utc_now().isoformat(),
                        "payload": {"connection_count": self.connection_count},
                    })
        except WebSocketDisconnect:
            self.disconnect(websocket)
        except Exception:
            self.disconnect(websocket)
            raise

    async def _send_event(self, event: dict[str, Any]) -> None:
        """Send one event to all active connections, pruning failed sockets."""
        stale_connections: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_json(event)
            except Exception:
                stale_connections.append(websocket)

        for websocket in stale_connections:
            self.disconnect(websocket)

    def _build_event(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build the shared realtime event envelope."""
        return {
            "event_id": str(uuid4()),
            "event_type": str(event_type or "unknown"),
            "created_at": utc_now().isoformat(),
            "payload": payload or {},
        }


realtime_event_manager = RealtimeEventManager()
