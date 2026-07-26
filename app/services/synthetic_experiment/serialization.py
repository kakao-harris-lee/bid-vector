"""JSON (de)serialization helpers for stored experiment payloads."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _json_loads(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        logger.warning("Failed to decode stored synthetic-experiment JSON payload")
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
