"""Analytics event-payload decoding shared across KPI aggregations."""

from __future__ import annotations

import ast
import json
from typing import Any


def parse_analytics_event_data(raw: str | None) -> dict[str, Any]:
    """Decode a persisted ``Analytics.event_data`` string back into a dict.

    Events written after the JSON round-trip fix store valid JSON. Legacy rows
    persisted with ``str(dict)`` (Python repr, single quotes) are recovered via
    ``ast.literal_eval``. Any unparseable or non-mapping payload yields ``{}``.
    """
    if not raw:
        return {}
    text = raw.strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (ValueError, TypeError):
        try:
            decoded = ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError):
            return {}
    return decoded if isinstance(decoded, dict) else {}
