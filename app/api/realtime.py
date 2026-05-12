"""Realtime WebSocket routes."""

from fastapi import APIRouter, Depends, WebSocket, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.realtime import realtime_event_manager
from app.services.realtime_auth import RealtimeAuthenticationError, authenticate_realtime_websocket

router = APIRouter()


@router.websocket("/events")
async def stream_realtime_events(websocket: WebSocket, db: Session = Depends(get_db)):
    """Stream normalized realtime events to dashboard clients."""
    try:
        client_context = authenticate_realtime_websocket(websocket, db)
    except RealtimeAuthenticationError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await realtime_event_manager.serve(websocket, client_context=client_context)
