"""Authentication helpers for realtime dashboard WebSocket streams."""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_token
from app.models.models import User


class RealtimeAuthenticationError(Exception):
    """Raised when a WebSocket client is not allowed to open the realtime stream."""


def authenticate_realtime_websocket(websocket: WebSocket, db: Session) -> dict[str, Any]:
    """Validate the dashboard WebSocket token and return a small client context."""
    if not settings.REALTIME_REQUIRE_AUTH:
        return {
            "authenticated": False,
            "operator_id": None,
            "username": None,
        }

    raw_token = _extract_websocket_token(websocket)
    if not raw_token:
        raise RealtimeAuthenticationError("Missing realtime access token")

    token_payload = verify_token(raw_token)
    if not token_payload or token_payload.get("type") != "access":
        raise RealtimeAuthenticationError("Invalid realtime access token")

    try:
        operator_id = int(token_payload.get("sub"))
    except (TypeError, ValueError) as exc:
        raise RealtimeAuthenticationError("Invalid realtime token subject") from exc

    operator = db.query(User).filter(User.id == operator_id).first()
    if operator is None or not operator.is_active:
        raise RealtimeAuthenticationError("Realtime operator is not active")

    return {
        "authenticated": True,
        "operator_id": int(operator.id),
        "username": operator.username,
    }


def _extract_websocket_token(websocket: WebSocket) -> str | None:
    """Extract an access token from query params or standard authorization headers."""
    query_token = websocket.query_params.get("token") or websocket.query_params.get("access_token")
    if query_token:
        return query_token

    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None

    return None
