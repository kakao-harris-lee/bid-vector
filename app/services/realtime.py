"""Realtime event fanout for dashboard WebSocket clients."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from contextlib import suppress
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.time import utc_now

logger = logging.getLogger(__name__)


class RealtimeFanoutBackend:
    """Base fanout backend used to bridge realtime events across API processes."""

    async def start(self, manager: "RealtimeEventManager") -> None:
        """Start any background subscription loops."""
        del manager

    async def stop(self) -> None:
        """Stop any background subscription loops."""

    def publish(self, event: dict[str, Any], *, publisher_id: str) -> None:
        """Publish one event to other API processes."""
        del event, publisher_id


class LocalRealtimeFanoutBackend(RealtimeFanoutBackend):
    """No-op backend for single-process local development and tests."""


class PostgresNotifyRealtimeFanoutBackend(RealtimeFanoutBackend):
    """PostgreSQL LISTEN/NOTIFY fanout backend for multi-process API deployments."""

    _CHANNEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _MAX_NOTIFY_PAYLOAD_BYTES = 7900

    def __init__(self, *, database_url: str, channel: str) -> None:
        self._database_url = database_url
        self._channel = self._validate_channel(channel)
        self._manager: RealtimeEventManager | None = None
        self._listener_task: asyncio.Task | None = None
        self._stopping = False

    async def start(self, manager: "RealtimeEventManager") -> None:
        """Start listening for events published by sibling API processes."""
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._manager = manager
        self._stopping = False
        self._listener_task = asyncio.create_task(self._listen_loop(), name="realtime-postgres-listener")

    async def stop(self) -> None:
        """Stop the PostgreSQL listener task."""
        self._stopping = True
        if self._listener_task is None:
            return
        self._listener_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._listener_task
        self._listener_task = None

    def publish(self, event: dict[str, Any], *, publisher_id: str) -> None:
        """Publish one event through PostgreSQL NOTIFY."""
        payload = json.dumps(
            {
                "publisher_id": publisher_id,
                "event": event,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload_size = len(payload.encode("utf-8"))
        if payload_size > self._MAX_NOTIFY_PAYLOAD_BYTES:
            logger.warning("Skipping realtime fanout event larger than PostgreSQL NOTIFY limit: %s bytes", payload_size)
            return

        try:
            from sqlalchemy import text

            from app.core.database import engine

            with engine.begin() as connection:
                connection.execute(
                    text("SELECT pg_notify(:channel, :payload)"),
                    {
                        "channel": self._channel,
                        "payload": payload,
                    },
                )
        except Exception as exc:  # pragma: no cover - depends on deployment DB backend
            logger.warning("Realtime PostgreSQL fanout publish failed: %s", exc)

    async def _listen_loop(self) -> None:
        """Keep a PostgreSQL listener active and reconnect on transient failures."""
        try:
            from psycopg import AsyncConnection, sql
        except Exception as exc:  # pragma: no cover - dependency should exist in runtime image
            logger.warning("Realtime PostgreSQL fanout listener unavailable: %s", exc)
            return

        listen_statement = sql.SQL("LISTEN {}").format(sql.Identifier(self._channel))
        database_url = self._normalize_psycopg_url(self._database_url)

        while not self._stopping:
            try:
                async with await AsyncConnection.connect(database_url, autocommit=True) as connection:
                    await connection.execute(listen_statement)
                    async for notification in connection.notifies():
                        if self._stopping:
                            break
                        await self._handle_notification_payload(notification.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - requires a live PostgreSQL outage
                if not self._stopping:
                    logger.warning("Realtime PostgreSQL fanout listener reconnecting after error: %s", exc)
                    await asyncio.sleep(5)

    async def _handle_notification_payload(self, raw_payload: str) -> None:
        """Decode one NOTIFY payload and forward it to the local manager."""
        if self._manager is None:
            return
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed realtime fanout payload")
            return

        publisher_id = str(payload.get("publisher_id") or "")
        event = payload.get("event")
        if not isinstance(event, dict):
            return
        await self._manager.receive_fanout_event(event, publisher_id=publisher_id)

    def _normalize_psycopg_url(self, database_url: str) -> str:
        """Convert SQLAlchemy-style PostgreSQL URLs into psycopg-compatible DSNs."""
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def _validate_channel(self, channel: str) -> str:
        """Validate channel names before interpolating them into LISTEN statements."""
        normalized = (channel or "bid_vector_realtime_events").strip()
        if not self._CHANNEL_PATTERN.match(normalized):
            raise ValueError("REALTIME_POSTGRES_CHANNEL must be a valid PostgreSQL identifier")
        return normalized


def build_realtime_fanout_backend() -> RealtimeFanoutBackend:
    """Build the configured realtime fanout backend."""
    backend_name = str(settings.REALTIME_FANOUT_BACKEND or "local").strip().lower()
    if backend_name in {"local", "memory", "inprocess"}:
        return LocalRealtimeFanoutBackend()
    if backend_name in {"postgres", "postgresql", "pg"}:
        return PostgresNotifyRealtimeFanoutBackend(
            database_url=settings.DATABASE_URL,
            channel=settings.REALTIME_POSTGRES_CHANNEL,
        )
    logger.warning("Unknown REALTIME_FANOUT_BACKEND=%s; falling back to local fanout", backend_name)
    return LocalRealtimeFanoutBackend()


class RealtimeEventManager:
    """Track WebSocket clients and broadcast normalized realtime events."""

    def __init__(
        self,
        *,
        history_limit: int | None = None,
        fanout_backend: RealtimeFanoutBackend | None = None,
    ) -> None:
        self._connections: set[WebSocket] = set()
        self._recent_events: deque[dict[str, Any]] = deque(
            maxlen=max(1, history_limit or settings.REALTIME_HISTORY_LIMIT)
        )
        self._fanout_backend = fanout_backend or build_realtime_fanout_backend()
        self._instance_id = str(uuid4())
        self._started = False

    @property
    def instance_id(self) -> str:
        """Return this manager instance id used to suppress fanout loops."""
        return self._instance_id

    @property
    def fanout_backend_name(self) -> str:
        """Return the active fanout backend class name for diagnostics."""
        return self._fanout_backend.__class__.__name__

    @property
    def connection_count(self) -> int:
        """Return the current number of connected WebSocket clients."""
        return len(self._connections)

    def recent_events(self) -> list[dict[str, Any]]:
        """Return a copy of the local event history."""
        return list(self._recent_events)

    async def start(self) -> None:
        """Start the configured cross-process fanout backend."""
        if self._started:
            return
        await self._fanout_backend.start(self)
        self._started = True

    async def stop(self) -> None:
        """Stop the configured cross-process fanout backend."""
        if not self._started:
            return
        await self._fanout_backend.stop()
        self._started = False

    async def connect(self, websocket: WebSocket, *, client_context: dict[str, Any] | None = None) -> None:
        """Accept a WebSocket and send a connection acknowledgement."""
        context = client_context or {}
        await websocket.accept()
        self._connections.add(websocket)
        await websocket.send_json({
            "event_id": str(uuid4()),
            "event_type": "connection.opened",
            "created_at": utc_now().isoformat(),
            "payload": {
                "connection_count": self.connection_count,
                "replayed_event_count": len(self._recent_events),
                "authenticated": bool(context.get("authenticated")),
                "operator_id": context.get("operator_id"),
                "fanout_backend": self.fanout_backend_name,
            },
        })

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active set."""
        self._connections.discard(websocket)

    def publish_event(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record an event and best-effort broadcast it from sync code."""
        event = self._build_event(event_type, payload)
        self._recent_events.append(event)
        self._fanout_backend.publish(event, publisher_id=self._instance_id)
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
        await asyncio.to_thread(self._fanout_backend.publish, event, publisher_id=self._instance_id)
        await self._send_event(event)
        return event

    async def receive_fanout_event(self, event: dict[str, Any], *, publisher_id: str) -> None:
        """Receive an event from a cross-process fanout backend and send it locally."""
        if publisher_id == self._instance_id:
            return
        normalized_event = self._normalize_external_event(event)
        if normalized_event is None:
            return
        self._recent_events.append(normalized_event)
        await self._send_event(normalized_event)

    async def serve(self, websocket: WebSocket, *, client_context: dict[str, Any] | None = None) -> None:
        """Serve one realtime client until it disconnects."""
        await self.connect(websocket, client_context=client_context)
        try:
            while True:
                message = await websocket.receive_json()
                if isinstance(message, dict) and message.get("event_type") == "ping":
                    await websocket.send_json({
                        "event_id": str(uuid4()),
                        "event_type": "pong",
                        "created_at": utc_now().isoformat(),
                        "payload": {
                            "connection_count": self.connection_count,
                            "fanout_backend": self.fanout_backend_name,
                        },
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

    def _normalize_external_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Validate and normalize an event received from a fanout backend."""
        event_type = event.get("event_type")
        if not event_type:
            return None
        payload = event.get("payload")
        return {
            "event_id": str(event.get("event_id") or uuid4()),
            "event_type": str(event_type),
            "created_at": str(event.get("created_at") or utc_now().isoformat()),
            "payload": payload if isinstance(payload, dict) else {},
        }


realtime_event_manager = RealtimeEventManager()
