"""Realtime WebSocket routes."""

from fastapi import APIRouter, WebSocket

from app.services.realtime import realtime_event_manager

router = APIRouter()


@router.websocket("/events")
async def stream_realtime_events(websocket: WebSocket):
    """Stream normalized realtime events to dashboard clients."""
    await realtime_event_manager.serve(websocket)
